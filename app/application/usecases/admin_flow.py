from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime

from app.domain.entities.models import (
    User,
    Material,
    ProcurementRequest,
    ProcurementStatus,
    PriceComparison,
    Vendor,
    UsageLog,
)
from app.infrastructure.whatsapp.sender import (
    send_text_message,
    send_button_message,
    send_list_message,
)


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────


async def handle(phone: str, text: str, user: User, session: AsyncSession):
    """
    Router utama untuk semua pesan dari Admin.
    Menangani:
      - Approval / Reject procurement (via button response)
      - Lihat status stok
      - Trigger pengadaan manual
      - Menu utama
    """
    text_upper = text.strip().upper()

    # ── Tangani respons tombol approval / reject ──
    if text_upper.startswith("APPROVE_"):
        # Format: APPROVE_{procurement_id}_{vendor_id}
        await handle_approval(phone, text_upper, session)

    elif text_upper.startswith("REJECT_"):
        # Format: REJECT_{procurement_id}
        await handle_rejection(phone, text_upper, session)

    elif text_upper.startswith("TRIGGER_"):
        # Format: TRIGGER_{material_id}
        try:
            material_id = int(text_upper.split("_")[1])
            await handle_manual_trigger_execute(phone, material_id, session)
        except (IndexError, ValueError):
            await send_text_message(phone, "❌ Format trigger tidak valid.")

    # ── Perintah teks biasa ──
    elif any(k in text_upper for k in ["STOK", "STOCK", "STATUS"]):
        await handle_stock_status(phone, session)

    elif any(k in text_upper for k in ["PENGADAAN", "ORDER", "PROCUREMENT"]):
        await handle_procurement_status(phone, session)

    elif any(k in text_upper for k in ["MANUAL", "TRIGGER"]):
        await handle_manual_trigger_menu(phone, session)

    elif any(k in text_upper for k in ["MENU", "HELP", "BANTUAN", "HI", "HALO"]):
        await send_admin_menu(phone, user)

    else:
        # Fallback — tampilkan menu
        await send_admin_menu(phone, user)


# ─── MENU UTAMA ───────────────────────────────────────────────────────────────


async def send_admin_menu(phone: str, user: User):
    await send_button_message(
        phone=phone,
        title=f"👋 Halo, {user.name}!",
        body=(
            "Selamat datang di *Sediya Agent* — Panel Admin.\n" "Pilih menu di bawah:"
        ),
        buttons=[
            {"buttonId": "STATUS", "buttonText": {"displayText": "📊 Status Stok"}},
            {
                "buttonId": "PENGADAAN",
                "buttonText": {"displayText": "📦 Status Pengadaan"},
            },
            {"buttonId": "MANUAL", "buttonText": {"displayText": "⚡ Trigger Manual"}},
        ],
    )


# ─── STATUS STOK ─────────────────────────────────────────────────────────────


async def handle_stock_status(phone: str, session: AsyncSession):
    result = await session.execute(select(Material).order_by(Material.name))
    materials = result.scalars().all()

    if not materials:
        await send_text_message(phone, "⚠️ Belum ada data material di database.")
        return

    kritis = [m for m in materials if m.current_stock <= m.minimum_stock]
    aman = [m for m in materials if m.current_stock > m.minimum_stock]

    lines = ["📊 *STATUS STOK SAAT INI*\n"]

    if kritis:
        lines.append("🔴 *KRITIS / PERLU SEGERA DIPESAN:*")
        for m in kritis:
            days = (
                round(m.current_stock / m.avg_daily_usage, 1)
                if m.avg_daily_usage and m.avg_daily_usage > 0
                else "?"
            )
            lines.append(
                f"  • {m.name}: {m.current_stock} {m.unit} "
                f"(min {m.minimum_stock}) — ±{days} hari lagi"
            )

    if aman:
        lines.append("\n🟢 *AMAN:*")
        for m in aman:
            lines.append(f"  • {m.name}: {m.current_stock} {m.unit}")

    lines.append(f"\n🕐 Update: {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC")

    await send_text_message(phone, "\n".join(lines))


# ─── STATUS PENGADAAN ─────────────────────────────────────────────────────────


async def handle_procurement_status(phone: str, session: AsyncSession):
    result = await session.execute(
        select(ProcurementRequest)
        .where(
            ProcurementRequest.status.in_(
                [
                    ProcurementStatus.PENDING,
                    ProcurementStatus.WAITING_VENDOR,
                    ProcurementStatus.COMPARING,
                ]
            )
        )
        .order_by(ProcurementRequest.created_at.desc())
    )
    procurements = result.scalars().all()

    if not procurements:
        await send_text_message(
            phone, "✅ Tidak ada pengadaan yang sedang berjalan saat ini."
        )
        return

    lines = ["📦 *PENGADAAN AKTIF*\n"]
    for pr in procurements:
        mat_result = await session.execute(
            select(Material).where(Material.id == pr.material_id)
        )
        material = mat_result.scalar_one_or_none()
        mat_name = material.name if material else f"ID#{pr.material_id}"

        resp_result = await session.execute(
            select(func.count(PriceComparison.id)).where(
                PriceComparison.procurement_id == pr.id,
                PriceComparison.quoted_price != None,  # noqa
            )
        )
        responded = resp_result.scalar() or 0

        total_result = await session.execute(
            select(func.count(PriceComparison.id)).where(
                PriceComparison.procurement_id == pr.id
            )
        )
        total = total_result.scalar() or 0

        status_emoji = {
            ProcurementStatus.PENDING: "⏳",
            ProcurementStatus.WAITING_VENDOR: "📨",
            ProcurementStatus.COMPARING: "🔍",
        }.get(pr.status, "❓")

        lines.append(
            f"{status_emoji} *{mat_name}*\n"
            f"   Qty: {pr.requested_qty} | Status: {pr.status.value}\n"
            f"   Vendor respond: {responded}/{total}\n"
            f"   Dibuat: {pr.created_at.strftime('%d/%m %H:%M')}"
        )

    await send_text_message(phone, "\n\n".join(lines))


# ─── APPROVAL ────────────────────────────────────────────────────────────────


async def handle_approval(phone: str, text: str, session: AsyncSession):
    """
    Format tombol: APPROVE_{procurement_id}_{vendor_id}
    """
    try:
        parts = text.split("_")
        procurement_id = int(parts[1])
        vendor_id = int(parts[2])
    except (IndexError, ValueError):
        await send_text_message(phone, "❌ Format approval tidak valid.")
        return

    pr_result = await session.execute(
        select(ProcurementRequest).where(ProcurementRequest.id == procurement_id)
    )
    pr = pr_result.scalar_one_or_none()

    if not pr:
        await send_text_message(
            phone, f"❌ Pengadaan ID #{procurement_id} tidak ditemukan."
        )
        return

    if pr.status == ProcurementStatus.APPROVED:
        await send_text_message(phone, "ℹ️ Pengadaan ini sudah disetujui sebelumnya.")
        return

    vendor_result = await session.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = vendor_result.scalar_one_or_none()

    comp_result = await session.execute(
        select(PriceComparison).where(
            PriceComparison.procurement_id == procurement_id,
            PriceComparison.vendor_id == vendor_id,
        )
    )
    comparison = comp_result.scalar_one_or_none()

    mat_result = await session.execute(
        select(Material).where(Material.id == pr.material_id)
    )
    material = mat_result.scalar_one_or_none()

    pr.status = ProcurementStatus.APPROVED
    pr.resolved_at = datetime.utcnow()
    await session.commit()

    price_info = (
        f"Rp{comparison.quoted_price:,.0f}"
        if comparison and comparison.quoted_price
        else "N/A"
    )
    lead_info = (
        f"{comparison.lead_time_days} hari"
        if comparison and comparison.lead_time_days
        else "N/A"
    )

    await send_text_message(
        phone,
        f"✅ *PENGADAAN DISETUJUI*\n\n"
        f"📦 Material : {material.name if material else '-'}\n"
        f"🏪 Vendor   : {vendor.name if vendor else f'ID#{vendor_id}'}\n"
        f"💰 Harga    : {price_info}\n"
        f"📅 Estimasi : {lead_info}\n\n"
        f"Notifikasi sedang dikirim ke vendor...",
    )

    # Notifikasi ke vendor pemenang
    if vendor:
        total_price = (
            comparison.quoted_price * pr.requested_qty
            if comparison and comparison.quoted_price
            else None
        )
        await send_text_message(
            vendor.phone_number,
            f"✅ *ORDER DIKONFIRMASI*\n\n"
            f"Halo *{vendor.name}*,\n"
            f"Pesanan berikut telah disetujui:\n\n"
            f"📦 Material : {material.name if material else '-'}\n"
            f"📏 Jumlah   : {pr.requested_qty} {material.unit if material else ''}\n"
            f"💰 Total    : {'Rp{:,.0f}'.format(total_price) if total_price else 'N/A'}\n\n"
            f"Mohon segera diproses. Terima kasih! 🙏",
        )


# ─── REJECTION ───────────────────────────────────────────────────────────────


async def handle_rejection(phone: str, text: str, session: AsyncSession):
    """
    Format tombol: REJECT_{procurement_id}
    """
    try:
        procurement_id = int(text.split("_")[1])
    except (IndexError, ValueError):
        await send_text_message(phone, "❌ Format reject tidak valid.")
        return

    pr_result = await session.execute(
        select(ProcurementRequest).where(ProcurementRequest.id == procurement_id)
    )
    pr = pr_result.scalar_one_or_none()

    if not pr:
        await send_text_message(
            phone, f"❌ Pengadaan ID #{procurement_id} tidak ditemukan."
        )
        return

    if pr.status == ProcurementStatus.REJECTED:
        await send_text_message(phone, "ℹ️ Pengadaan ini sudah ditolak sebelumnya.")
        return

    mat_result = await session.execute(
        select(Material).where(Material.id == pr.material_id)
    )
    material = mat_result.scalar_one_or_none()

    pr.status = ProcurementStatus.REJECTED
    pr.resolved_at = datetime.utcnow()
    await session.commit()

    await send_text_message(
        phone,
        f"❌ *PENGADAAN DITOLAK*\n\n"
        f"📦 Material: {material.name if material else f'ID#{pr.material_id}'}\n"
        f"Pengadaan telah dibatalkan.\n\n"
        f"Ketik *MANUAL* jika ingin trigger pengadaan baru secara manual.",
    )


# ─── TRIGGER MANUAL ──────────────────────────────────────────────────────────


async def handle_manual_trigger_menu(phone: str, session: AsyncSession):
    """Tampilkan daftar material untuk dipilih Admin."""
    result = await session.execute(select(Material).order_by(Material.current_stock))
    materials = result.scalars().all()

    if not materials:
        await send_text_message(phone, "⚠️ Belum ada data material di database.")
        return

    sections = [
        {
            "title": "Pilih Material",
            "rows": [
                {
                    "rowId": f"TRIGGER_{m.id}",
                    "title": m.name,
                    "description": f"Stok: {m.current_stock} {m.unit} (min {m.minimum_stock})",
                }
                for m in materials
            ],
        }
    ]

    await send_list_message(
        phone=phone,
        title="⚡ Trigger Pengadaan Manual",
        body="Pilih material yang ingin segera dipesan:",
        sections=sections,
    )


async def handle_manual_trigger_execute(
    phone: str, material_id: int, session: AsyncSession
):
    """Eksekusi trigger pengadaan manual untuk material tertentu."""
    mat_result = await session.execute(
        select(Material).where(Material.id == material_id)
    )
    material = mat_result.scalar_one_or_none()

    if not material:
        await send_text_message(phone, "❌ Material tidak ditemukan.")
        return

    # Cek apakah sudah ada pengadaan aktif
    existing = await session.execute(
        select(ProcurementRequest).where(
            ProcurementRequest.material_id == material_id,
            ProcurementRequest.status.in_(
                [
                    ProcurementStatus.PENDING,
                    ProcurementStatus.WAITING_VENDOR,
                    ProcurementStatus.COMPARING,
                ]
            ),
        )
    )
    if existing.scalar_one_or_none():
        await send_text_message(
            phone,
            f"ℹ️ Sudah ada pengadaan aktif untuk *{material.name}*.\n"
            f"Ketik *PENGADAAN* untuk melihat statusnya.",
        )
        return

    pr = ProcurementRequest(
        material_id=material.id,
        requested_qty=material.minimum_stock * 3,
        status=ProcurementStatus.PENDING,
        triggered_by=phone,
    )
    session.add(pr)
    await session.commit()
    await session.refresh(pr)

    await send_text_message(
        phone,
        f"⚡ *PENGADAAN MANUAL DIMULAI*\n\n"
        f"📦 Material : {material.name}\n"
        f"📏 Qty      : {pr.requested_qty} {material.unit}\n\n"
        f"🤖 Agent sedang menghubungi vendor...",
    )

    from app.infrastructure.ai.procurement_agent import run_procurement_agent

    await run_procurement_agent(pr.id, material, session)

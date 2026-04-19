# app/application/usecases/vendor_flow.py
import re
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.domain.entities.models import User, Vendor, PriceComparison
from app.infrastructure.whatsapp.sender import send_text_message
from datetime import datetime


async def handle(phone: str, text: str, user: User, session: AsyncSession):
    """Parse respons harga dari vendor."""
    # Cari vendor berdasarkan nomor HP
    vendor_result = await session.execute(
        select(Vendor).where(Vendor.phone_number == phone)
    )
    vendor = vendor_result.scalar_one_or_none()

    if not vendor:
        await send_text_message(phone, "⚠️ Vendor tidak ditemukan dalam sistem.")
        return

    # Parse format: HARGA [nominal] ESTIMASI [hari]
    price_match = re.search(r"HARGA\s+(\d+[\d.,]*)", text.upper())
    lead_match = re.search(r"ESTIMASI\s+(\d+)", text.upper())

    if not price_match:
        await send_text_message(
            phone,
            "❓ Format tidak dikenali.\n"
            "Gunakan format: *HARGA [harga] ESTIMASI [hari]*\n"
            "Contoh: HARGA 85000 ESTIMASI 2",
        )
        return

    price = float(price_match.group(1).replace(",", "").replace(".", ""))
    lead_time = int(lead_match.group(1)) if lead_match else None

    # Cari price comparison yang belum diisi untuk vendor ini
    comp_result = await session.execute(
        select(PriceComparison)
        .where(
            PriceComparison.vendor_id == vendor.id,
            PriceComparison.quoted_price == None,  # noqa
        )
        .order_by(PriceComparison.id.desc())
    )
    comparison = comp_result.scalar_one_or_none()

    if not comparison:
        await send_text_message(
            phone, "ℹ️ Tidak ada permintaan penawaran aktif untuk kamu saat ini."
        )
        return

    # Update dengan harga vendor
    comparison.quoted_price = price
    comparison.lead_time_days = lead_time
    comparison.raw_response = text
    comparison.responded_at = datetime.utcnow()
    await session.commit()

    await send_text_message(
        phone,
        f"✅ Penawaran diterima!\n"
        f"💰 Harga: Rp{price:,.0f}\n"
        f"📅 Estimasi: {lead_time} hari\n\n"
        f"Terima kasih, kami akan segera memproses.",
    )

    # Cek apakah semua vendor sudah respond, jika ya trigger summarize
    from app.infrastructure.ai.procurement_agent import run_procurement_agent

    # Re-run agent untuk update state
    # (Simplified: dalam production gunakan persistent state/Redis)
    

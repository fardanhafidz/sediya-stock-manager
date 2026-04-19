# app/application/usecases/operator_flow.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.domain.entities.models import User, Material, UsageLog
from app.infrastructure.ai.nlp_parser import parse_usage_message
from app.infrastructure.whatsapp.sender import send_text_message
from app.application.usecases.stock_checker import check_and_trigger_procurement
from datetime import datetime


async def handle(phone: str, text: str, user: User, session: AsyncSession):
    await send_text_message(phone, "⏳ Sedang memproses laporan kamu...")

    # Parse pesan dengan AI
    parsed = await parse_usage_message(text)

    if parsed["action"] == "unknown" or parsed["material"] is None:
        await send_text_message(
            phone,
            "❓ Maaf, saya tidak mengerti laporan kamu.\n"
            "Contoh format: *Pakai semen 2 sak* atau *Cat habis 3 liter*",
        )
        return

    # Cari material di database
    result = await session.execute(
        select(Material).where(Material.name.ilike(f"%{parsed['material']}%"))
    )
    material = result.scalar_one_or_none()

    if not material:
        await send_text_message(
            phone, f"⚠️ Material *{parsed['material']}* tidak ditemukan dalam database."
        )
        return

    # Update stok
    material.current_stock = max(0, material.current_stock - parsed["qty"])
    material.updated_at = datetime.utcnow()

    # Catat log
    log = UsageLog(
        material_id=material.id,
        quantity_used=parsed["qty"],
        reported_by=user.id,
        raw_message=text,
    )
    session.add(log)
    await session.commit()

    # Konfirmasi ke operator
    await send_text_message(
        phone,
        f"✅ Tercatat!\n"
        f"📦 Material: *{material.name}*\n"
        f"➖ Terpakai: {parsed['qty']} {material.unit}\n"
        f"📊 Sisa stok: *{material.current_stock} {material.unit}*",
    )

    # Cek apakah perlu pengadaan
    await check_and_trigger_procurement(material, session)

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.domain.entities.models import (
    Material,
    UsageLog,
    ProcurementRequest,
    ProcurementStatus,
)
from app.infrastructure.whatsapp.sender import send_text_message
from app.core.config import settings
from datetime import datetime, timedelta


async def calculate_burn_rate(material: Material, session: AsyncSession) -> dict:
    """
    Hitung rata-rata penggunaan harian (7 hari terakhir).
    Status = Stok Saat Ini / Rata-rata Penggunaan Harian
    """
    seven_days_ago = datetime.utcnow() - timedelta(days=7)

    result = await session.execute(
        select(func.sum(UsageLog.quantity_used)).where(
            UsageLog.material_id == material.id, UsageLog.logged_at >= seven_days_ago
        )
    )
    total_used = result.scalar() or 0
    avg_daily = total_used / 7

    # Update avg di database
    material.avg_daily_usage = avg_daily
    await session.commit()

    days_remaining = (
        (material.current_stock / avg_daily) if avg_daily > 0 else float("inf")
    )

    return {
        "avg_daily_usage": avg_daily,
        "days_remaining": days_remaining,
        "is_critical": material.current_stock <= material.minimum_stock,
    }


async def check_and_trigger_procurement(material: Material, session: AsyncSession):
    """Cek stok dan trigger pengadaan otomatis jika kritis."""
    burn_data = await calculate_burn_rate(material, session)

    is_critical = burn_data["is_critical"]
    days_left = burn_data["days_remaining"]

    if not is_critical and days_left > 3:
        return  # Stok masih aman

    # Cek apakah sudah ada pengadaan yang sedang berjalan
    existing = await session.execute(
        select(ProcurementRequest).where(
            ProcurementRequest.material_id == material.id,
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
        return  # Sudah ada pengadaan aktif

    # Buat procurement request baru
    request = ProcurementRequest(
        material_id=material.id,
        requested_qty=material.minimum_stock * 3,  # Order 3x minimum stock
        status=ProcurementStatus.PENDING,
        triggered_by="AUTO",
    )
    session.add(request)
    await session.commit()
    await session.refresh(request)

    # Notifikasi admin
    alert_msg = (
        f"🚨 *STOK KRITIS: {material.name.upper()}*\n\n"
        f"📊 Stok saat ini: {material.current_stock} {material.unit}\n"
        f"⚠️ Minimum: {material.minimum_stock} {material.unit}\n"
        f"📅 Estimasi habis: {days_left:.1f} hari\n\n"
        f"🤖 Sistem sedang menghubungi vendor untuk penawaran harga..."
    )
    await send_text_message(settings.ADMIN_PHONE, alert_msg)

    # Trigger agentic workflow
    from app.infrastructure.ai.procurement_agent import run_procurement_agent

    await run_procurement_agent(request.id, material, session)

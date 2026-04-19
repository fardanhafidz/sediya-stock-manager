from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional
from datetime import datetime, timedelta
from pydantic import BaseModel

from app.infrastructure.database.connection import get_session
from app.domain.entities.models import (
    Material,
    UsageLog,
    ProcurementRequest,
    ProcurementStatus,
)

router = APIRouter(prefix="/materials", tags=["materials"])


# ─── SCHEMAS ─────────────────────────────────────────────────────────────────


class MaterialCreate(BaseModel):
    name: str
    unit: str
    current_stock: float
    minimum_stock: float


class MaterialUpdate(BaseModel):
    name: Optional[str] = None
    unit: Optional[str] = None
    current_stock: Optional[float] = None
    minimum_stock: Optional[float] = None


class MaterialResponse(BaseModel):
    id: int
    name: str
    unit: str
    current_stock: float
    minimum_stock: float
    avg_daily_usage: float
    days_remaining: Optional[float]
    status: str  # "AMAN", "RENDAH", "KRITIS"
    updated_at: datetime

    class Config:
        from_attributes = True


# ─── HELPER ──────────────────────────────────────────────────────────────────


def compute_status(current: float, minimum: float) -> str:
    if current <= minimum:
        return "KRITIS"
    elif current <= minimum * 1.5:
        return "RENDAH"
    return "AMAN"


def compute_days_remaining(current: float, avg_daily: float) -> Optional[float]:
    if avg_daily and avg_daily > 0:
        return round(current / avg_daily, 1)
    return None


# ─── ENDPOINTS ───────────────────────────────────────────────────────────────


@router.get("", response_model=list[MaterialResponse])
async def get_all_materials(
    status: Optional[str] = Query(None, description="Filter: AMAN | RENDAH | KRITIS"),
    session: AsyncSession = Depends(get_session),
):
    """Ambil semua material, opsional filter berdasarkan status stok."""
    result = await session.execute(select(Material).order_by(Material.name))
    materials = result.scalars().all()

    responses = []
    for m in materials:
        s = compute_status(m.current_stock, m.minimum_stock)
        if status and s != status.upper():
            continue
        responses.append(
            MaterialResponse(
                id=m.id,
                name=m.name,
                unit=m.unit,
                current_stock=m.current_stock,
                minimum_stock=m.minimum_stock,
                avg_daily_usage=m.avg_daily_usage,
                days_remaining=compute_days_remaining(
                    m.current_stock, m.avg_daily_usage
                ),
                status=s,
                updated_at=m.updated_at,
            )
        )
    return responses


@router.get("/{material_id}", response_model=MaterialResponse)
async def get_material(material_id: int, session: AsyncSession = Depends(get_session)):
    """Ambil detail satu material."""
    result = await session.execute(select(Material).where(Material.id == material_id))
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Material tidak ditemukan")

    return MaterialResponse(
        id=m.id,
        name=m.name,
        unit=m.unit,
        current_stock=m.current_stock,
        minimum_stock=m.minimum_stock,
        avg_daily_usage=m.avg_daily_usage,
        days_remaining=compute_days_remaining(m.current_stock, m.avg_daily_usage),
        status=compute_status(m.current_stock, m.minimum_stock),
        updated_at=m.updated_at,
    )


@router.post("", response_model=MaterialResponse, status_code=201)
async def create_material(
    payload: MaterialCreate, session: AsyncSession = Depends(get_session)
):
    """Tambah material baru."""
    # Cek duplikat nama
    existing = await session.execute(
        select(Material).where(Material.name.ilike(payload.name))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409, detail=f"Material '{payload.name}' sudah ada"
        )

    m = Material(
        name=payload.name,
        unit=payload.unit,
        current_stock=payload.current_stock,
        minimum_stock=payload.minimum_stock,
        avg_daily_usage=0.0,
    )
    session.add(m)
    await session.commit()
    await session.refresh(m)

    return MaterialResponse(
        id=m.id,
        name=m.name,
        unit=m.unit,
        current_stock=m.current_stock,
        minimum_stock=m.minimum_stock,
        avg_daily_usage=m.avg_daily_usage,
        days_remaining=None,
        status=compute_status(m.current_stock, m.minimum_stock),
        updated_at=m.updated_at,
    )


@router.patch("/{material_id}", response_model=MaterialResponse)
async def update_material(
    material_id: int,
    payload: MaterialUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update sebagian data material (partial update)."""
    result = await session.execute(select(Material).where(Material.id == material_id))
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Material tidak ditemukan")

    if payload.name is not None:
        m.name = payload.name
    if payload.unit is not None:
        m.unit = payload.unit
    if payload.current_stock is not None:
        m.current_stock = payload.current_stock
    if payload.minimum_stock is not None:
        m.minimum_stock = payload.minimum_stock

    m.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(m)

    return MaterialResponse(
        id=m.id,
        name=m.name,
        unit=m.unit,
        current_stock=m.current_stock,
        minimum_stock=m.minimum_stock,
        avg_daily_usage=m.avg_daily_usage,
        days_remaining=compute_days_remaining(m.current_stock, m.avg_daily_usage),
        status=compute_status(m.current_stock, m.minimum_stock),
        updated_at=m.updated_at,
    )


@router.delete("/{material_id}", status_code=204)
async def delete_material(
    material_id: int, session: AsyncSession = Depends(get_session)
):
    """Hapus material (hanya jika tidak ada pengadaan aktif)."""
    result = await session.execute(select(Material).where(Material.id == material_id))
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Material tidak ditemukan")

    # Cek pengadaan aktif
    active = await session.execute(
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
    if active.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="Tidak bisa menghapus material yang memiliki pengadaan aktif",
        )

    await session.delete(m)
    await session.commit()


@router.get("/{material_id}/usage-history")
async def get_usage_history(
    material_id: int,
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    """
    Ambil riwayat penggunaan material dalam N hari terakhir.
    Berguna untuk grafik di dashboard.
    """
    result = await session.execute(select(Material).where(Material.id == material_id))
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Material tidak ditemukan")

    since = datetime.utcnow() - timedelta(days=days)
    logs_result = await session.execute(
        select(UsageLog)
        .where(UsageLog.material_id == material_id, UsageLog.logged_at >= since)
        .order_by(UsageLog.logged_at.asc())
    )
    logs = logs_result.scalars().all()

    return {
        "material_id": material_id,
        "material_name": m.name,
        "unit": m.unit,
        "period_days": days,
        "total_used": sum(l.quantity_used for l in logs),
        "logs": [
            {
                "id": l.id,
                "quantity_used": l.quantity_used,
                "raw_message": l.raw_message,
                "logged_at": l.logged_at.isoformat(),
            }
            for l in logs
        ],
    }


@router.get("/{material_id}/daily-summary")
async def get_daily_summary(
    material_id: int,
    days: int = Query(default=7, ge=1, le=90),
    session: AsyncSession = Depends(get_session),
):
    """
    Agregasi penggunaan per hari untuk grafik bar chart di dashboard.
    """
    result = await session.execute(select(Material).where(Material.id == material_id))
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Material tidak ditemukan")

    since = datetime.utcnow() - timedelta(days=days)
    logs_result = await session.execute(
        select(UsageLog)
        .where(UsageLog.material_id == material_id, UsageLog.logged_at >= since)
        .order_by(UsageLog.logged_at.asc())
    )
    logs = logs_result.scalars().all()

    # Agregasi per hari
    daily: dict[str, float] = {}
    for log in logs:
        day_key = log.logged_at.strftime("%Y-%m-%d")
        daily[day_key] = daily.get(day_key, 0) + log.quantity_used

    # Isi hari yang kosong dengan 0
    summary = []
    for i in range(days):
        day = (datetime.utcnow() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        summary.append({"date": day, "quantity_used": daily.get(day, 0)})

    return {
        "material_id": material_id,
        "material_name": m.name,
        "unit": m.unit,
        "data": summary,
    }

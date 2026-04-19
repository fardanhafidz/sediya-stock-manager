from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from datetime import datetime
import httpx

from app.infrastructure.database.connection import get_session
from app.core.config import settings

router = APIRouter(prefix="/health", tags=["health"])


# ─── ENDPOINTS ───────────────────────────────────────────────────────────────


@router.get("")
async def health_check():
    """Basic health check — untuk load balancer / uptime monitor."""
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "sediya-agent-api",
    }


@router.get("/detailed")
async def detailed_health(session: AsyncSession = Depends(get_session)):
    """
    Health check lengkap — cek koneksi database dan Evolution API.
    Berguna untuk monitoring dan debugging deployment.
    """
    results = {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "checks": {}}

    # ── Cek Database ──────────────────────────────────────
    try:
        await session.execute(text("SELECT 1"))
        results["checks"]["database"] = {
            "status": "ok",
            "message": "PostgreSQL connected",
        }
    except Exception as e:
        results["checks"]["database"] = {"status": "error", "message": str(e)}
        results["status"] = "degraded"

    # ── Cek Evolution API ─────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.EVOLUTION_API_URL}/instance/fetchInstances",
                headers={"apikey": settings.EVOLUTION_API_KEY},
            )
            if resp.status_code == 200:
                instances = resp.json()
                # Cari instance utama kita
                our_instance = next(
                    (
                        i
                        for i in instances
                        if i.get("name") == settings.EVOLUTION_INSTANCE
                    ),
                    None,
                )
                wa_status = (
                    our_instance.get("connectionStatus", "unknown")
                    if our_instance
                    else "instance_not_found"
                )
                results["checks"]["whatsapp"] = {
                    "status": "ok" if wa_status == "open" else "warning",
                    "connection_status": wa_status,
                    "instance": settings.EVOLUTION_INSTANCE,
                }
                if wa_status != "open":
                    results["status"] = "degraded"
            else:
                results["checks"]["whatsapp"] = {
                    "status": "error",
                    "message": f"Evolution API returned {resp.status_code}",
                }
                results["status"] = "degraded"
    except httpx.ConnectError:
        results["checks"]["whatsapp"] = {
            "status": "error",
            "message": "Evolution API tidak dapat dijangkau",
        }
        results["status"] = "degraded"
    except Exception as e:
        results["checks"]["whatsapp"] = {"status": "error", "message": str(e)}
        results["status"] = "degraded"

    return results


@router.get("/db")
async def database_health(session: AsyncSession = Depends(get_session)):
    """Cek koneksi database saja."""
    try:
        await session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "error", "database": str(e)}


@router.get("/whatsapp")
async def whatsapp_health():
    """Cek status koneksi WhatsApp via Evolution API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.EVOLUTION_API_URL}/instance/fetchInstances",
                headers={"apikey": settings.EVOLUTION_API_KEY},
            )
            instances = resp.json()
            our_instance = next(
                (i for i in instances if i.get("name") == settings.EVOLUTION_INSTANCE),
                None,
            )
            if not our_instance:
                return {
                    "status": "error",
                    "message": f"Instance '{settings.EVOLUTION_INSTANCE}' tidak ditemukan",
                }
            return {
                "status": (
                    "ok"
                    if our_instance.get("connectionStatus") == "open"
                    else "warning"
                ),
                "instance": settings.EVOLUTION_INSTANCE,
                "connection_status": our_instance.get("connectionStatus"),
                "phone": our_instance.get("ownerJid", "").replace(
                    "@s.whatsapp.net", ""
                ),
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}

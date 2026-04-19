from fastapi import APIRouter, Request, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.database.connection import get_session
from app.application.usecases.message_router import route_message

router = APIRouter(prefix="", tags=["webhook"])

@router.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session)
):
    payload = await request.json()

    # Filter hanya event pesan masuk
    if payload.get("event") != "messages.upsert":
        return {"status": "ignored"}

    message_data = payload.get("data", {})

    # Abaikan pesan dari diri sendiri
    if message_data.get("key", {}).get("fromMe"):
        return {"status": "ignored"}

    background_tasks.add_task(route_message, message_data, session)
    return {"status": "received"}
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.domain.entities.models import User, UserRole
from app.application.usecases import operator_flow, admin_flow, vendor_flow
from app.infrastructure.whatsapp.sender import send_text_message


async def route_message(message_data: dict, session: AsyncSession):
    # Ekstrak nomor pengirim
    sender_jid = message_data.get("key", {}).get("remoteJid", "")
    phone_number = sender_jid.replace("@s.whatsapp.net", "").replace("@g.us", "")

    # Ekstrak teks pesan
    text = (
        message_data.get("message", {}).get("conversation")
        or message_data.get("message", {})
        .get("extendedTextMessage", {})
        .get("text", "")
    ).strip()

    if not text:
        return

    # Cari user di database
    result = await session.execute(
        select(User).where(User.phone_number == phone_number, User.is_active == True)
    )
    user = result.scalar_one_or_none()

    if not user:
        await send_text_message(
            phone_number, "❌ Nomor kamu belum terdaftar dalam sistem."
        )
        return

    # Routing berdasarkan role
    match user.role:
        case UserRole.OPERATOR:
            await operator_flow.handle(phone_number, text, user, session)
        case UserRole.ADMIN:
            await admin_flow.handle(phone_number, text, user, session)
        case UserRole.VENDOR:
            await vendor_flow.handle(phone_number, text, user, session)
        case _:
            await send_text_message(phone_number, "⚠️ Role tidak dikenali.")

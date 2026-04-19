import httpx
from app.core.config import settings

BASE_URL = f"{settings.EVOLUTION_API_URL}"
INSTANCE = settings.EVOLUTION_INSTANCE
HEADERS = {"Content-Type": "application/json", "apikey": settings.EVOLUTION_API_KEY}


async def send_text_message(phone: str, text: str):
    """Kirim pesan teks biasa."""
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{BASE_URL}/message/sendText/{INSTANCE}",
            headers=HEADERS,
            json={"number": phone, "text": text},
        )


async def send_button_message(phone: str, title: str, body: str, buttons: list[dict]):
    """
    Kirim pesan dengan tombol pilihan.
    buttons = [{"buttonId": "btn1", "buttonText": {"displayText": "Pilihan 1"}}]
    """
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{BASE_URL}/message/sendButtons/{INSTANCE}",
            headers=HEADERS,
            json={
                "number": phone,
                "title": title,
                "description": body,
                "buttons": buttons,
                "footer": "Stok Manager System",
            },
        )


async def send_list_message(phone: str, title: str, body: str, sections: list[dict]):
    """Kirim pesan dengan list/menu."""
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{BASE_URL}/message/sendList/{INSTANCE}",
            headers=HEADERS,
            json={
                "number": phone,
                "title": title,
                "description": body,
                "buttonText": "Pilih Menu",
                "sections": sections,
            },
        )

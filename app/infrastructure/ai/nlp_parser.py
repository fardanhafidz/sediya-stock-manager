# app/infrastructure/ai/nlp_parser.py
import json
from openai import AsyncAzureOpenAI
from app.core.config import settings

client = AsyncAzureOpenAI(
    azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
    api_key=settings.AZURE_OPENAI_KEY,
    api_version=settings.OPENAI_API_VERSION,
)

SYSTEM_PROMPT = """Kamu adalah parser untuk sistem manajemen stok konstruksi.
Tugasmu: ubah pesan teks menjadi JSON terstruktur.

FORMAT OUTPUT (selalu JSON, tidak ada teks lain):
{
  "material": "nama material dalam bahasa standar",
  "qty": angka_float,
  "unit": "satuan (sak/liter/buah/kg/dll)",
  "action": "used" atau "report",
  "confidence": 0.0-1.0
}

Contoh input → output:
"Pakai semen 2 sak" → {"material": "semen", "qty": 2, "unit": "sak", "action": "used", "confidence": 0.95}
"Cat habis 5 liter" → {"material": "cat", "qty": 5, "unit": "liter", "action": "used", "confidence": 0.92}
"Besi beton terpakai 10 batang" → {"material": "besi beton", "qty": 10, "unit": "batang", "action": "used", "confidence": 0.9}

Jika pesan tidak jelas atau bukan laporan penggunaan, kembalikan:
{"material": null, "qty": null, "unit": null, "action": "unknown", "confidence": 0.0}"""


async def parse_usage_message(raw_text: str) -> dict:
    """Parse pesan operator menjadi data terstruktur."""
    try:
        response = await client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
            temperature=0,
            max_tokens=200,
        )
        result = json.loads(response.choices[0].message.content)
        return result
    except Exception as e:
        return {
            "material": None,
            "qty": None,
            "unit": None,
            "action": "error",
            "confidence": 0.0,
        }

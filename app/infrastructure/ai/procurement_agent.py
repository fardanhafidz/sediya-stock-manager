from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Optional, Annotated
import operator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.domain.entities.models import (
    Material,
    Vendor,
    ProcurementRequest,
    PriceComparison,
    ProcurementStatus,
)


# ─── STATE DEFINITION ────────────────────────────────────
class ProcurementState(TypedDict):
    procurement_id: int
    material_id: int
    material_name: str
    requested_qty: float
    unit: str
    vendors_contacted: List[int]
    responses_received: Annotated[List[dict], operator.add]
    all_vendors_responded: bool
    best_vendor: Optional[dict]
    admin_notified: bool
    error: Optional[str]


# ─── NODE FUNCTIONS ───────────────────────────────────────
async def check_stock_node(
    state: ProcurementState, session: AsyncSession
) -> ProcurementState:
    """Node 1: Validasi dan persiapkan data pengadaan."""
    result = await session.execute(
        select(Material).where(Material.id == state["material_id"])
    )
    material = result.scalar_one_or_none()
    if not material:
        return {**state, "error": "Material tidak ditemukan"}

    return {**state, "material_name": material.name, "unit": material.unit}


async def contact_vendors_node(
    state: ProcurementState, session: AsyncSession
) -> ProcurementState:
    """Node 2: Kirim pesan ke semua vendor yang relevan."""
    from app.infrastructure.whatsapp.sender import send_text_message

    # Ambil vendor aktif yang supply material ini
    result = await session.execute(select(Vendor).where(Vendor.is_active == True))
    vendors = result.scalars().all()

    # Filter vendor yang bisa supply material ini (simplified)
    relevant_vendors = [
        v for v in vendors if str(state["material_id"]) in (v.materials_supplied or "")
    ]

    contacted = []
    for vendor in relevant_vendors:
        # Buat price comparison entry
        comparison = PriceComparison(
            procurement_id=state["procurement_id"], vendor_id=vendor.id
        )
        session.add(comparison)

        # Kirim pesan ke vendor
        msg = (
            f"Halo *{vendor.name}*,\n\n"
            f"Kami membutuhkan penawaran harga untuk:\n"
            f"📦 Material: *{state['material_name']}*\n"
            f"📏 Jumlah: *{state['requested_qty']} {state['unit']}*\n\n"
            f"Mohon balas dengan format:\n"
            f"*HARGA [nominal per {state['unit']}] ESTIMASI [X hari]*\n\n"
            f"Contoh: HARGA 85000 ESTIMASI 2\n\n"
            f"Terima kasih 🙏"
        )
        await send_text_message(vendor.phone_number, msg)
        contacted.append(vendor.id)

    await session.commit()

    # Update status procurement
    pr_result = await session.execute(
        select(ProcurementRequest).where(
            ProcurementRequest.id == state["procurement_id"]
        )
    )
    pr = pr_result.scalar_one()
    pr.status = ProcurementStatus.WAITING_VENDOR
    await session.commit()

    return {**state, "vendors_contacted": contacted}


async def wait_for_responses_node(
    state: ProcurementState, session: AsyncSession
) -> ProcurementState:
    """Node 3: Cek apakah semua vendor sudah respond."""
    result = await session.execute(
        select(PriceComparison).where(
            PriceComparison.procurement_id == state["procurement_id"]
        )
    )
    comparisons = result.scalars().all()

    responded = [c for c in comparisons if c.quoted_price is not None]
    all_responded = len(responded) == len(comparisons) and len(comparisons) > 0

    responses = [
        {
            "vendor_id": c.vendor_id,
            "price": c.quoted_price,
            "lead_time": c.lead_time_days,
            "notes": c.notes,
        }
        for c in responded
    ]

    return {
        **state,
        "responses_received": responses,
        "all_vendors_responded": all_responded,
    }


async def summarize_for_admin_node(
    state: ProcurementState, session: AsyncSession
) -> ProcurementState:
    """Node 4: Analisis penawaran dan rekomendasikan ke Admin."""
    from openai import AsyncAzureOpenAI
    from app.core.config import settings
    from app.infrastructure.whatsapp.sender import send_button_message

    if not state["responses_received"]:
        await send_button_message(
            settings.ADMIN_PHONE,
            "⚠️ Tidak Ada Penawaran",
            f"Tidak ada vendor yang merespons untuk {state['material_name']}.\nPerlu tindakan manual.",
            [
                {
                    "buttonId": "manual_order",
                    "buttonText": {"displayText": "Order Manual"},
                }
            ],
        )
        return state

    # Gunakan AI untuk merangkum dan merekomendasikan
    client = AsyncAzureOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_key=settings.AZURE_OPENAI_KEY,
        api_version="2024-02-01",
    )

    responses_text = "\n".join(
        [
            f"Vendor ID {r['vendor_id']}: Rp{r['price']:,.0f}/{state['unit']}, estimasi {r['lead_time']} hari"
            for r in state["responses_received"]
        ]
    )

    ai_response = await client.chat.completions.create(
        model=settings.AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {
                "role": "user",
                "content": f"""Analisis penawaran vendor berikut untuk {state['material_name']} 
            sebanyak {state['requested_qty']} {state['unit']}:
            
            {responses_text}
            
            Rekomendasikan vendor terbaik dengan mempertimbangkan harga dan kecepatan.
            Format: Rekomendasikan Vendor ID X karena [alasan singkat].
            Total belanja: Rp[total]""",
            }
        ],
        max_tokens=200,
    )

    recommendation = ai_response.choices[0].message.content

    # Kirim ke Admin dengan tombol approval
    best = min(state["responses_received"], key=lambda x: x["price"])

    await send_button_message(
        settings.ADMIN_PHONE,
        f"📊 Perbandingan Harga - {state['material_name']}",
        f"{responses_text}\n\n🤖 Rekomendasi AI:\n{recommendation}",
        [
            {
                "buttonId": f"approve_{state['procurement_id']}_{best['vendor_id']}",
                "buttonText": {"displayText": "✅ Setuju & Order"},
            },
            {
                "buttonId": f"reject_{state['procurement_id']}",
                "buttonText": {"displayText": "❌ Tolak"},
            },
        ],
    )

    # Update status
    pr_result = await session.execute(
        select(ProcurementRequest).where(
            ProcurementRequest.id == state["procurement_id"]
        )
    )
    pr = pr_result.scalar_one()
    pr.status = ProcurementStatus.COMPARING
    await session.commit()

    return {**state, "best_vendor": best, "admin_notified": True}


# ─── GRAPH ASSEMBLY ───────────────────────────────────────
def should_wait_or_summarize(state: ProcurementState) -> str:
    """Edge condition: apakah harus tunggu vendor atau sudah bisa rangkum."""
    if state.get("error"):
        return END
    if state["all_vendors_responded"] or len(state.get("responses_received", [])) > 0:
        return "summarize_for_admin"
    return "wait_for_responses"


def build_procurement_graph():
    graph = StateGraph(ProcurementState)

    graph.add_node("check_stock", check_stock_node)
    graph.add_node("contact_vendors", contact_vendors_node)
    graph.add_node("wait_for_responses", wait_for_responses_node)
    graph.add_node("summarize_for_admin", summarize_for_admin_node)

    graph.set_entry_point("check_stock")
    graph.add_edge("check_stock", "contact_vendors")
    graph.add_edge("contact_vendors", "wait_for_responses")
    graph.add_conditional_edges("wait_for_responses", should_wait_or_summarize)
    graph.add_edge("summarize_for_admin", END)

    return graph.compile()


procurement_graph = build_procurement_graph()


async def run_procurement_agent(
    procurement_id: int, material: Material, session: AsyncSession
):
    """Entry point untuk menjalankan procurement agent."""
    initial_state = ProcurementState(
        procurement_id=procurement_id,
        material_id=material.id,
        material_name=material.name,
        requested_qty=material.minimum_stock * 3,
        unit=material.unit,
        vendors_contacted=[],
        responses_received=[],
        all_vendors_responded=False,
        best_vendor=None,
        admin_notified=False,
        error=None,
    )
    await procurement_graph.ainvoke(initial_state)

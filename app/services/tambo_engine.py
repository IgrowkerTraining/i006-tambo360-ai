"""TamboEngine — AI-powered per-lot merma deviation analysis by category.

Receives ALL lots of an establishment (≥15), groups them by category
(quesos / leches). Python calculates all statistics. The AI only writes
human-readable descriptions for the already-identified outlier lots.
"""

import json
from collections import defaultdict
from app.models.schemas import (
    TamboAnalysisInput,
    TamboAnalysisOutput,
    AlertaLote,
    ChatRequest,
    ChatMessage,
    LoteInput,
)
from app.services.ai_service import ai_service
from app.core.logging import get_logger

logger = get_logger(__name__)


# ---- Statistics (pure Python, no AI) -------------------------------------


def compute_outliers(data: TamboAnalysisInput) -> list[dict]:
    """
    Calculate merma averages per category and identify outlier lots.

    A lot is an outlier if its total merma exceeds the category average by >20%.
    Returns a list of dicts with all data needed to build the prompt and output.
    """
    # Group merma totals by category
    category_mermas: dict[str, list[float]] = defaultdict(list)
    lot_merma_totals: dict[str, float] = {}

    for lote in data.lotes:
        total = sum(m.cantidad for m in lote.mermas)
        lot_merma_totals[lote.idLote] = total
        category_mermas[lote.categoria].append(total)

    # Calculate average per category
    category_avg: dict[str, float] = {
        cat: sum(vals) / len(vals)
        for cat, vals in category_mermas.items()
        if vals
    }

    logger.info(f"Category averages: { {c: round(v, 2) for c, v in category_avg.items()} }")

    # Identify outliers: any lot whose merma exceeds its category average
    outliers = []
    for lote in data.lotes:
        total = lot_merma_totals[lote.idLote]
        avg = category_avg.get(lote.categoria, 0)

        if avg == 0:
            continue

        pct_over = (total - avg) / avg * 100

        # Solo alertar si supera el promedio
        if pct_over <= 0:
            continue

        # Nivel según porcentaje de desvío sobre el promedio
        if pct_over <= 3:
            nivel = "bajo"
        elif pct_over <= 5:
            nivel = "medio"
        else:
            nivel = "alto"

        outliers.append({
            "idLote": lote.idLote,
            "numeroLote": lote.numeroLote,
            "producto": lote.producto,
            "categoria": lote.categoria,
            "unidad": lote.unidad,
            "merma_total": round(total, 2),
            "promedio_categoria": round(avg, 2),
            "porcentaje_sobre_promedio": round(pct_over, 1),
            "nivel": nivel,
        })

    logger.info(f"Outlier lots detected: {len(outliers)}")
    return outliers



# ---- Prompt builder -------------------------------------------------------


def build_prompt(outliers: list[dict], data: TamboAnalysisInput) -> list[ChatMessage]:
    """
    Build system + user messages.
    Python already identified the outlier lots and computed all numbers.
    The AI only writes a short, objective description for each.
    """
    if not outliers:
        return []  # No call needed

    outliers_text = "\n".join([
        f"- numeroLote: {o['numeroLote']} | Producto: {o['producto']} | Categoría: {o['categoria']}"
        f" | Merma: {o['merma_total']} {o['unidad']}"
        f" | Promedio de su categoría: {o['promedio_categoria']} {o['unidad']}"
        f" | Supera el promedio en: {o['porcentaje_sobre_promedio']}%"
        f" | Nivel: {o['nivel']}"
        for o in outliers
    ])

    schema_example = json.dumps(
        [
            {
                "idLote": "<id del lote>",
                "descripcion": "Descripción técnica y objetiva del desvío de merma",
            }
        ],
        ensure_ascii=False,
        indent=2,
    )

    primer_lote = data.lotes[0].numeroLote
    ultimo_lote = data.lotes[-1].numeroLote

    system_message = ChatMessage(
        role="system",
        content=(
            "Eres un analista técnico de producción lechera y quesera.\n\n"
            "Los cálculos ya están hechos. Tu única tarea es redactar una descripción "
            "técnica y objetiva del desvío de merma para cada lote que se te indica.\n\n"
            "REGLAS:\n"
            "1. Responde ÚNICAMENTE con un JSON válido: una lista de objetos con 'idLote' y 'descripcion'. Nota: usa el 'numeroLote' recibido como idLote en tu JSON de respuesta.\n"
            "2. Sin texto adicional, sin markdown, sin explicaciones fuera del JSON.\n"
            "3. La descripción debe mencionar la merma real, el promedio de la categoría y el porcentaje de desvío. Referencia al lote específico anteponiendo una 'L' mayúscula al número (ej: 'el lote L8').\n"
            "4. Máximo 2 oraciones por descripción. Tono técnico.\n"
            f"5. La descripción debe comenzar SIEMPRE con la frase exacta: 'En base al análisis desde el lote L{primer_lote} hasta el L{ultimo_lote}, '\n\n"
            f"Formato exacto:\n{schema_example}"
        ),
    )

    user_message = ChatMessage(
        role="user",
        content=(
            f"Establecimiento: '{data.nombreEstablecimiento}' (ID: {data.idEstablecimiento}).\n\n"
            f"Lotes con desvío de merma detectado:\n{outliers_text}\n\n"
            "Generá la descripción técnica para cada uno."
        ),
    )

    return [system_message, user_message]


# ---- Model call -----------------------------------------------------------


async def call_model(messages: list[ChatMessage]) -> str:
    """Call OpenRouter via ai_service and return raw content string."""
    request = ChatRequest(
        messages=messages,
        temperature=0.1,
        max_tokens=1500,
        stream=False,
    )
    response = await ai_service.chat_completion(request)
    content = response.choices[0]["message"]["content"]
    logger.info("Raw AI response received, proceeding to validate")
    return content


# ---- Response validation -------------------------------------------------


def merge_descriptions(raw: str, outliers: list[dict], data: TamboAnalysisInput) -> list[AlertaLote]:
    """
    Parse AI descriptions and merge with pre-computed outlier data.
    If AI fails, fall back to generating the description from the numbers.
    """
    descriptions: dict[str, str] = {}

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]).strip()

    try:
        parsed = json.loads(cleaned)
        for item in parsed:
            descriptions[str(item.get("idLote", ""))] = item.get("descripcion", "")
    except Exception as e:
        logger.warning(f"Could not parse AI descriptions, using fallback: {e}")

    primer_lote = data.lotes[0].numeroLote
    ultimo_lote = data.lotes[-1].numeroLote

    alertas = []
    for o in outliers:
        desc = descriptions.get(str(o["numeroLote"])) or (
            f"En base al análisis desde el lote L{primer_lote} hasta el L{ultimo_lote}, "
            f"el lote L{o['numeroLote']} presenta una merma de {o['merma_total']} {o['unidad']} superando en "
            f"{o['porcentaje_sobre_promedio']}% el promedio de la categoría "
            f"{o['categoria']} ({o['promedio_categoria']} {o['unidad']})."
        )
        alertas.append(
            AlertaLote(
                idLote=o["idLote"],
                producto=o["producto"],
                categoria=o["categoria"],
                nivel=o["nivel"],
                descripcion=desc,
            )
        )
    return alertas


# ---- Main orchestrator ---------------------------------------------------


async def analyze(data: TamboAnalysisInput) -> TamboAnalysisOutput:
    """Full pipeline: Python computes → AI describes → return structured output."""
    logger.info(
        f"Starting analysis for establishment {data.idEstablecimiento}, "
        f"{len(data.lotes)} lotes"
    )

    # Step 1: Python identifies outliers (no AI needed for math)
    outliers = compute_outliers(data)

    alertas: list[AlertaLote] = []

    if outliers:
        # Step 2: AI only writes descriptions for the identified outliers
        messages = build_prompt(outliers, data)
        raw_response = await call_model(messages)

        # Step 3: Merge AI descriptions with pre-computed data
        alertas = merge_descriptions(raw_response, outliers, data)
    else:
        logger.info("No outliers detected, skipping AI call")

    logger.info(f"Analysis complete: {len(alertas)} alertas detected")
    return TamboAnalysisOutput(
        idEstablecimiento=data.idEstablecimiento,
        alertas_detectadas=alertas,
    )


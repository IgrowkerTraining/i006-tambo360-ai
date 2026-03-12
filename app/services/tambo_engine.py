"""TamboEngine — AI-powered per-lot merma deviation analysis by category.

Receives ALL lots of an establishment (≥15), groups them by category
(quesos / leches). Python calculates all statistics. The AI only writes
human-readable descriptions for the already-identified outlier lots.
"""

import json
from collections import defaultdict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.db_models import PromedioCategoria
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


async def seed_category_averages(data: TamboAnalysisInput, db: AsyncSession) -> list[dict]:
    """
    Initial configuration (Base 15+ lots):
    Calculate merma averages per category, store them in the DB, and identify outlier lots.
    """
    category_totals = defaultdict(lambda: {"merma": 0.0, "produccion": 0.0, "cantidad_lotes": 0})
    lot_merma_pcts = {}
    lot_merma_totals = {}

    for lote in data.lotes:
        merma_total = sum(m.cantidad for m in lote.mermas)
        lot_merma_totals[lote.idLote] = merma_total
        
        if lote.cantidad > 0:
            pct = (merma_total / lote.cantidad) * 100
        else:
            pct = 0.0
            
        lot_merma_pcts[lote.idLote] = pct
        
        category_totals[lote.categoria]["merma"] += merma_total
        category_totals[lote.categoria]["produccion"] += lote.cantidad
        category_totals[lote.categoria]["cantidad_lotes"] += 1

    category_avg_pct: dict[str, float] = {}
    
    # Save/Update DB
    for cat, totals in category_totals.items():
        avg = (totals["merma"] / totals["produccion"]) * 100 if totals["produccion"] > 0 else 0.0
        category_avg_pct[cat] = avg
        
        # Check if exists
        stmt = select(PromedioCategoria).where(
            PromedioCategoria.id_establecimiento == data.idEstablecimiento,
            PromedioCategoria.categoria == cat
        )
        result = await db.execute(stmt)
        record = result.scalars().first()
        
        if not record:
            record = PromedioCategoria(
                id_establecimiento=data.idEstablecimiento,
                categoria=cat,
            )
            db.add(record)
            
        # Overwrite with this new solid baseline
        record.produccion_acumulada = totals["produccion"]
        record.merma_acumulada = totals["merma"]
        record.pct_merma_promedio = avg
        record.cantidad_lotes = totals["cantidad_lotes"]
        
    await db.commit()
    logger.info(f"DB averages seeded: { {c: round(v, 2) for c, v in category_avg_pct.items()} }")

    outliers = []
    for lote in data.lotes:
        total = lot_merma_totals[lote.idLote]
        pct = lot_merma_pcts[lote.idLote]
        avg_pct = category_avg_pct.get(lote.categoria, 0)

        if avg_pct == 0:
            continue

        pct_over = (pct - avg_pct) / avg_pct * 100

        if pct_over <= 0:
            continue

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
            "pct_merma_lote": round(pct, 2),
            "promedio_categoria_pct": round(avg_pct, 2),
            "porcentaje_sobre_promedio": round(pct_over, 1),
            "nivel": nivel,
        })

    logger.info(f"Outlier lots detected in bulk: {len(outliers)}")
    return outliers


async def evaluate_single_lote(data: TamboAnalysisInput, db: AsyncSession) -> list[dict]:
    """
    Continuous Integration (1 lot):
    Fetch category average from DB, evaluate this lot, and then update the DB.
    """
    lote = data.lotes[0]
    merma_total = sum(m.cantidad for m in lote.mermas)
    
    if lote.cantidad > 0:
        pct = (merma_total / lote.cantidad) * 100
    else:
        pct = 0.0

    # Fetch baseline
    stmt = select(PromedioCategoria).where(
        PromedioCategoria.id_establecimiento == data.idEstablecimiento,
        PromedioCategoria.categoria == lote.categoria
    )
    result = await db.execute(stmt)
    record = result.scalars().first()
    
    if not record:
        # DB not initialized yet for this category, skip evaluation but init DB
        logger.warning(f"No previous baseline for category {lote.categoria}. Initializing with this lot.")
        record = PromedioCategoria(
            id_establecimiento=data.idEstablecimiento,
            categoria=lote.categoria,
            produccion_acumulada=lote.cantidad,
            merma_acumulada=merma_total,
            pct_merma_promedio=pct,
            cantidad_lotes=1
        )
        db.add(record)
        await db.commit()
        return []

    # Evaluate against DB
    avg_pct = record.pct_merma_promedio
    outliers = []
    
    if avg_pct > 0:
        pct_over = (pct - avg_pct) / avg_pct * 100
        
        if pct_over > 0:
            if pct_over <= 3: nivel = "bajo"
            elif pct_over <= 5: nivel = "medio"
            else: nivel = "alto"
            
            outliers.append({
                "idLote": lote.idLote,
                "numeroLote": lote.numeroLote,
                "producto": lote.producto,
                "categoria": lote.categoria,
                "unidad": lote.unidad,
                "merma_total": round(merma_total, 2),
                "pct_merma_lote": round(pct, 2),
                "promedio_categoria_pct": round(avg_pct, 2),
                "porcentaje_sobre_promedio": round(pct_over, 1),
                "nivel": nivel,
            })

    # Update DB with this lot's data
    record.produccion_acumulada += lote.cantidad
    record.merma_acumulada += merma_total
    record.cantidad_lotes += 1
    
    if record.produccion_acumulada > 0:
        record.pct_merma_promedio = (record.merma_acumulada / record.produccion_acumulada) * 100
        
    await db.commit()
    logger.info(f"Single lot evaluated. New DB average for {lote.categoria}: {round(record.pct_merma_promedio, 2)}%")
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
        f" | Merma absoluta: {o['merma_total']} {o['unidad']}"
        f" | Porcentaje de merma de este lote: {o['pct_merma_lote']}%"
        f" | Porcentaje de merma del promedio de su categoría: {o['promedio_categoria_pct']}%"
        f" | El porcentaje de este lote supera el promedio en un: {o['porcentaje_sobre_promedio']}%"
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

    system_message = ChatMessage(
        role="system",
        content=(
            "Eres un analista técnico de producción lechera y quesera.\n\n"
            "Los cálculos ya están hechos. Tu única tarea es redactar una descripción "
            "técnica y objetiva del desvío de merma para cada lote que se te indica.\n\n"
            "REGLAS:\n"
            "1. Responde ÚNICAMENTE con un JSON válido: una lista de objetos con 'idLote' y 'descripcion'. Nota: usa el 'numeroLote' recibido como idLote en tu JSON de respuesta.\n"
            "2. Sin texto adicional, sin markdown, sin explicaciones fuera del JSON.\n"
            "3. La descripción debe mencionar la merma absoluta, el % de merma del lote, el % de merma promedio de la categoría, el porcentaje de desvío y EL NOMBRE de la categoría (ej: 'la categoría quesos'). Referencia al lote específico anteponiendo una 'L' mayúscula al número (ej: 'el lote L8').\n"
            "4. Máximo 2 oraciones por descripción. Tono técnico.\n"
            "5. La descripción debe comenzar SIEMPRE nombrando al lote específico, por ejemplo: 'El lote L21 de la categoría leches presentó...'\n\n"
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

    alertas = []
    for o in outliers:
        desc = descriptions.get(str(o["numeroLote"])) or (
            f"El lote L{o['numeroLote']} presenta una merma de {o['merma_total']} {o['unidad']} (que es el {o['pct_merma_lote']}% de su volumen total), "
            f"superando en un {o['porcentaje_sobre_promedio']}% el porcentaje promedio de la categoría "
            f"{o['categoria']} (que es tan solo {o['promedio_categoria_pct']}%)."
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


async def analyze(data: TamboAnalysisInput, db: AsyncSession) -> TamboAnalysisOutput:
    """Full pipeline: DB seeding vs. single lot analysis → AI describes → return structured output."""
    logger.info(
        f"Starting analysis for establishment {data.idEstablecimiento}, "
        f"{len(data.lotes)} lotes"
    )

    # Step 1: Python identifies outliers and manages DB states
    if len(data.lotes) >= 15:
        outliers = await seed_category_averages(data, db)
    else:
        outliers = await evaluate_single_lote(data, db)

    alertas: list[AlertaLote] = []

    if outliers:
        # Step 2: AI only writes descriptions for the identified outliers
        messages = build_prompt(outliers, data)
        if messages:
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


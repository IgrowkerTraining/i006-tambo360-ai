"""TamboEngine API endpoints — HU3 (analyze) and HU4 (alertas)."""

import json
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.models.schemas import (
    TamboAnalysisInput,
    TamboAnalysisOutput,
    AlertaResponse,
    AlertasNoVistasResponse,
)
from app.models.db_models import Alerta
from app.database import get_db
from app.services import tambo_engine
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/tambo", tags=["tambo"])


# ---------------------------------------------------------------------------
# HU3 — POST /api/v1/tambo/analyze
# ---------------------------------------------------------------------------

@router.post("/analyze", response_model=TamboAnalysisOutput)
async def analyze_production(
    data: TamboAnalysisInput,
    db: AsyncSession = Depends(get_db),
):
    """
    Analyze all lots of an establishment and generate one alert per problematic lot.

    - Requires at least 15 lots (validated by schema)
    - Groups lots by category (quesos / leches)
    - Detects lots whose merma exceeds 20% above the category average
    - Saves one Alerta record per problematic lot
    - Returns the full analysis result
    """
    try:
        result = await tambo_engine.analyze(data)

        # Save one DB record per detected alert
        for alerta_lote in result.alertas_detectadas:
            alerta = Alerta(
                id_establecimiento=result.idEstablecimiento,
                id_lote=alerta_lote.idLote,
                producto=alerta_lote.producto,
                categoria=alerta_lote.categoria,
                nivel=alerta_lote.nivel,
                descripcion=alerta_lote.descripcion,
            )
            db.add(alerta)

        if result.alertas_detectadas:
            await db.commit()
            logger.info(
                f"{len(result.alertas_detectadas)} alertas saved "
                f"for establishment {result.idEstablecimiento}"
            )

        return result

    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=503, detail=str(e))

    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"El servicio de IA no pudo completar el análisis: {str(e)}",
        )


# ---------------------------------------------------------------------------
# HU4 — GET /api/v1/tambo/alertas/{idEstablecimiento}
# ---------------------------------------------------------------------------

@router.get("/alertas/{idEstablecimiento}", response_model=List[AlertaResponse])
async def get_alertas(
    idEstablecimiento: str,
    rango: int = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Return all alerts for a given establishment, ordered by most recent first.
    Optionally filter by the last `rango` days.

    Each alert corresponds to a single lot with a detected merma deviation.
    Returns empty list if no alerts exist.
    """
    from datetime import datetime, timedelta

    stmt = select(Alerta).where(Alerta.id_establecimiento == idEstablecimiento)

    if rango is not None:
        fecha_limite = datetime.utcnow() - timedelta(days=rango)
        stmt = stmt.where(Alerta.creado_en >= fecha_limite)

    stmt = stmt.order_by(Alerta.creado_en.desc())
    
    result = await db.execute(stmt)
    alertas = result.scalars().all()

    response = [
        AlertaResponse(
            id=a.id,
            idEstablecimiento=a.id_establecimiento,
            idLote=a.id_lote,
            producto=a.producto,
            categoria=a.categoria,
            nivel=a.nivel,
            descripcion=a.descripcion,
            creado_en=a.creado_en,
            visto=a.visto,
        )
        for a in alertas
    ]

    logger.info(
        f"Retrieved {len(response)} alertas for establishment {idEstablecimiento}"
    )
    return response


# ---------------------------------------------------------------------------
# GET /api/v1/tambo/alertas/{idEstablecimiento}/ultimas
# ---------------------------------------------------------------------------

@router.get("/alertas/{idEstablecimiento}/ultimas", response_model=List[AlertaResponse])
async def get_ultimas_alertas(
    idEstablecimiento: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Return only the last 2 most recent alerts for an establishment.
    Useful for dashboard summaries.
    """
    stmt = (
        select(Alerta)
        .where(Alerta.id_establecimiento == idEstablecimiento)
        .order_by(Alerta.creado_en.desc())
        .limit(2)
    )
    result = await db.execute(stmt)
    alertas = result.scalars().all()

    response = [
        AlertaResponse(
            id=a.id,
            idEstablecimiento=a.id_establecimiento,
            idLote=a.id_lote,
            producto=a.producto,
            categoria=a.categoria,
            nivel=a.nivel,
            descripcion=a.descripcion,
            creado_en=a.creado_en,
            visto=a.visto,
        )
        for a in alertas
    ]

    logger.info(
        f"Retrieved last {len(response)} alertas for establishment {idEstablecimiento}"
    )
    return response


# ---------------------------------------------------------------------------
# HU - PUT /api/v1/tambo/alertas/{idAlerta}/visto
# ---------------------------------------------------------------------------

@router.put("/alertas/{idAlerta}/visto", response_model=AlertaResponse)
async def marcar_alerta_visto(
    idAlerta: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Mark a specific alert as read (visto = True).
    """
    stmt = select(Alerta).where(Alerta.id == idAlerta)
    result = await db.execute(stmt)
    alerta = result.scalars().first()
    
    if not alerta:
        raise HTTPException(status_code=404, detail="Alerta no encontrada")
        
    alerta.visto = True
    await db.commit()
    await db.refresh(alerta)
    
    return AlertaResponse(
        id=alerta.id,
        idEstablecimiento=alerta.id_establecimiento,
        idLote=alerta.id_lote,
        producto=alerta.producto,
        categoria=alerta.categoria,
        nivel=alerta.nivel,
        descripcion=alerta.descripcion,
        creado_en=alerta.creado_en,
        visto=alerta.visto,
    )


# ---------------------------------------------------------------------------
# NUEVO — GET /api/v1/tambo/alertas/{idEstablecimiento}/no-vistas
# ---------------------------------------------------------------------------

@router.get("/alertas/{idEstablecimiento}/no-vistas", response_model=AlertasNoVistasResponse)
async def get_alertas_no_vistas_count(
    idEstablecimiento: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Return the total count of unread alerts (visto = False) for an establishment.
    """
    from sqlalchemy import func
    
    stmt = (
        select(func.count(Alerta.id))
        .where(Alerta.id_establecimiento == idEstablecimiento)
        .where(Alerta.visto == False)
    )
    result = await db.execute(stmt)
    count = result.scalar_one_or_none() or 0
    
    return AlertasNoVistasResponse(cantidad=count)

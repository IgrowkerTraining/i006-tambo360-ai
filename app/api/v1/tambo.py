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
    Analiza todos los lotes de un establecimiento y genera una alerta por cada lote problemático.

    - Requiere al menos 15 lotes (validado por el esquema)
    - Agrupa los lotes por categoría (quesos / leches)
    - Detecta lotes cuya merma supera en un 20% el promedio de la categoría
    - Guarda un registro de Alerta por cada lote problemático
    - Retorna el resultado completo del análisis
    """
    try:
        result = await tambo_engine.analyze(data, db)

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
    Retorna todas las alertas para un establecimiento dado, ordenadas por la más reciente primero.
    Opcionalmente filtra por los últimos `rango` días.

    Cada alerta corresponde a un único lote con un desvío de merma detectado.
    Retorna una lista vacía si no existen alertas.
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
    Retorna solo las últimas 2 alertas más recientes para un establecimiento.
    Útil para resúmenes de tablero (dashboard).
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
    Marca una alerta específica como leída (visto = True).
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
    Retorna el conteo total de alertas no leídas (visto = False) para un establecimiento.
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


# ---------------------------------------------------------------------------
# GET /api/v1/tambo/alertas/{idEstablecimiento}/lote/{idLote}
# ---------------------------------------------------------------------------

@router.get("/alertas/{idEstablecimiento}/lote/{idLote}", response_model=List[AlertaResponse])
async def get_alertas_por_lote(
    idEstablecimiento: str,
    idLote: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna todas las alertas asociadas con un lote de producción específico para un establecimiento.
    """
    stmt = (
        select(Alerta)
        .where(Alerta.id_establecimiento == idEstablecimiento)
        .where(Alerta.id_lote == idLote)
    )
    result = await db.execute(stmt)
    alertas = result.scalars().all()
    
    return [
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

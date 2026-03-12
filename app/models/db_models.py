"""SQLAlchemy ORM models (database tables)."""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Index, Boolean, Float, Integer
from app.database import Base


class Alerta(Base):
    """One alert per problematic lot detected by TamboEngine."""

    __tablename__ = "alertas"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    id_establecimiento = Column(String, nullable=False, index=True)
    id_lote = Column(String, nullable=False)
    producto = Column(String, nullable=False)
    categoria = Column(String, nullable=False)            # "quesos" | "leches"
    nivel = Column(String, nullable=False)                # "bajo" | "medio" | "alto"
    descripcion = Column(Text, nullable=False)             # desvío de merma explicado por la IA
    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)
    visto = Column(Boolean, default=False, nullable=False) # Para marcar como leído en frontend

    __table_args__ = (
        Index("ix_alertas_establecimiento_fecha", "id_establecimiento", "creado_en"),
    )


class PromedioCategoria(Base):
    """Running average of merma percentage per category per establishment."""

    __tablename__ = "promedios_categoria"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    id_establecimiento = Column(String, nullable=False, index=True)
    categoria = Column(String, nullable=False)            # "quesos" | "leches"
    produccion_acumulada = Column(Float, default=0.0)     # Total kg/liters produced
    merma_acumulada = Column(Float, default=0.0)          # Total kg/liters of waste
    pct_merma_promedio = Column(Float, default=0.0)       # Cached running percentage
    cantidad_lotes = Column(Integer, default=0)           # Number of lots analyzed for this category so far



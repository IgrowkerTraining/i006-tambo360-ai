"""SQLAlchemy ORM models (database tables)."""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Index, Boolean
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

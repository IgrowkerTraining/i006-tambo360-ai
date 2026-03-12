"""Pydantic models for request/response schemas."""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


class ChatMessage(BaseModel):
    """Chat message model."""
    role: str = Field(..., description="Message role: 'user', 'assistant', or 'system'")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    """Chat completion request model."""
    model: str = Field(default="arcee-ai/trinity-large-preview:free", description="AI model to use")
    messages: List[ChatMessage] = Field(..., description="List of chat messages")
    max_tokens: Optional[int] = Field(default=1000, ge=1, le=4096, description="Maximum tokens to generate")
    temperature: Optional[float] = Field(default=0.7, ge=0.0, le=2.0, description="Sampling temperature")
    stream: Optional[bool] = Field(default=False, description="Enable streaming response")


class ChatResponse(BaseModel):
    """Chat completion response model."""
    id: str = Field(..., description="Response ID")
    object: str = Field(default="chat.completion", description="Object type")
    created: int = Field(..., description="Creation timestamp")
    model: str = Field(..., description="Model used")
    choices: List[Dict[str, Any]] = Field(..., description="Response choices")
    usage: Optional[Dict[str, Any]] = Field(default=None, description="Token usage information")


class ModelInfo(BaseModel):
    """AI model information."""
    id: str = Field(..., description="Model ID")
    name: Optional[str] = Field(default=None, description="Model display name")
    description: Optional[str] = Field(default=None, description="Model description")
    pricing: Optional[Dict[str, Any]] = Field(default=None, description="Pricing information")


class HealthResponse(BaseModel):
    """Health check response model."""
    status: str = Field(..., description="Service status")
    timestamp: datetime = Field(..., description="Response timestamp")
    version: str = Field(..., description="Application version")
    message: Optional[str] = Field(default=None, description="Additional status message")


class ErrorResponse(BaseModel):
    """Error response model."""
    error: str = Field(..., description="Error type")
    detail: Optional[str] = Field(default=None, description="Error details")
    timestamp: datetime = Field(default_factory=datetime.now, description="Error timestamp")


class RootResponse(BaseModel):
    """Root endpoint response model."""
    message: str = Field(..., description="Welcome message")
    version: str = Field(..., description="Application version")
    docs: str = Field(..., description="Documentation URL")
    health: str = Field(..., description="Health check URL")


# ---------------------------------------------------------------------------
# TamboEngine — Schemas de entrada (alineados con Prisma del backend principal)
# ---------------------------------------------------------------------------

class MermaInput(BaseModel):
    """Represents a waste/loss record within a production lot."""
    descripcion: str = Field(..., description="Descripción de la merma")
    cantidad: float = Field(..., description="Cantidad de merma")
    unidad: str = Field(..., description="Unidad: 'kg' o 'litros'")


class CostoDirectoInput(BaseModel):
    """Represents a direct cost associated with a production lot."""
    concepto: str = Field(..., description="Concepto del costo")
    monto: float = Field(..., description="Monto del costo")
    moneda: str = Field(..., description="Moneda: 'USD', 'EUR' o 'ARS'")


class LoteInput(BaseModel):
    """Represents a production lot from the main backend."""
    idLote: str = Field(..., description="ID del lote (UUID del backend principal)")
    numeroLote: int = Field(..., description="Número correlativo del lote")
    fechaProduccion: str = Field(..., description="Fecha de producción (ISO 8601)")
    producto: str = Field(..., description="Nombre del producto")
    categoria: str = Field(..., description="Categoría: 'quesos' o 'leches'")
    cantidad: float = Field(..., description="Cantidad producida")
    unidad: str = Field(..., description="Unidad: 'kg' o 'litros'")
    mermas: List[MermaInput] = Field(default=[], description="Mermas del lote")
    costosDirectos: List[CostoDirectoInput] = Field(default=[], description="Costos directos del lote")


class TamboAnalysisInput(BaseModel):
    """Input payload sent by the main backend to trigger an AI analysis."""
    idEstablecimiento: str = Field(..., description="ID del establecimiento")
    nombreEstablecimiento: str = Field(..., description="Nombre del establecimiento")
    periodo: Optional[str] = Field(default=None, description="Periodo (opcional, para compatibilidad hacia atrás)")
    lotes: List[LoteInput] = Field(..., min_length=1, description="Lotes de producción a analizar (puede ser individual o en grupo)")


# ---------------------------------------------------------------------------
# TamboEngine — Schemas de salida (contrato de respuesta de la IA)
# ---------------------------------------------------------------------------

class AlertaLote(BaseModel):
    """One alert for a single problematic lot detected by the AI."""
    idLote: str = Field(..., description="ID del lote con desvío")
    producto: str = Field(..., description="Nombre del producto del lote")
    categoria: str = Field(..., description="Categoría: 'quesos' o 'leches'")
    nivel: str = Field(..., description="Nivel de severidad: 'bajo', 'medio' o 'alto'")
    descripcion: str = Field(..., description="Descripción del desvío de merma detectado por la IA")


class TamboAnalysisOutput(BaseModel):
    """Structured output returned by TamboEngine after AI analysis."""
    idEstablecimiento: str = Field(..., description="ID del establecimiento analizado")
    alertas_detectadas: List[AlertaLote] = Field(
        default=[],
        description="Una alerta por cada lote problemático. Vacía si no hay desvíos."
    )


class AlertaResponse(BaseModel):
    """Single lot alert stored in DB and returned by GET /alertas/{id} endpoint."""
    id: str = Field(..., description="ID único de la alerta")
    idEstablecimiento: str = Field(..., description="ID del establecimiento")
    idLote: str = Field(..., description="ID del lote con el desvío")
    producto: str = Field(..., description="Producto del lote")
    categoria: str = Field(..., description="Categoría del lote")
    nivel: str = Field(..., description="Nivel de severidad")
    descripcion: str = Field(..., description="Descripción del desvío")
    creado_en: datetime = Field(..., description="Fecha y hora del análisis")
    visto: bool = Field(False, description="Indica si la alerta ya fue leída por el usuario")


class AlertasNoVistasResponse(BaseModel):
    """Result for counting unread alerts."""
    cantidad: int = Field(..., description="Cantidad de alertas no leídas (visto = False)")

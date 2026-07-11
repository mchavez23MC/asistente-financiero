"""Entidades del dominio (contrato con T2) — Fase 1.

Un solo artefacto Pydantic que sirve para: validar la extracción del agente,
definir el contrato de las tools y documentar la API (decisión §2).

Regla de dependencia (§7.1): este módulo es núcleo puro. NO importa nada de
`adapters/`, `interfaces/` ni SDKs externos (FastAPI, anthropic, supabase...).

Nota sobre dinero: los montos son `Decimal`, nunca `float`, para no arrastrar
error de coma flotante en agregaciones de presupuesto (H2 es *grounded*: el
sistema calcula, Claude solo explica — §1.2).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Teléfono en formato E.164: '+' seguido de 8 a 15 dígitos (el primero no es 0).
E164_PATTERN = r"^\+[1-9]\d{7,14}$"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enumeraciones (valores estables — son parte del contrato congelado)
# ---------------------------------------------------------------------------
class Rol(str, Enum):
    """Rol de un mensaje en el historial conversacional (§3.1)."""

    USUARIO = "user"
    ASISTENTE = "assistant"
    SISTEMA = "system"


class Intencion(str, Enum):
    """Intención detectada por el orquestador/agente (audit trail §7.4)."""

    GASTO = "gasto"                # H1
    PRESUPUESTO = "presupuesto"    # H2
    SOPORTE = "soporte"            # H3
    SENSIBLE = "sensible"          # ruta guardrail → ticket
    CONSENTIMIENTO = "consentimiento"
    OTRO = "otro"


class TransactionStatus(str, Enum):
    """Estado de una transacción; habilita el loop de confirmación de H1 (§3.1)."""

    CONFIRMADA = "confirmada"
    PENDIENTE_CONFIRMACION = "pendiente_confirmacion"
    ANULADA = "anulada"


class Periodo(str, Enum):
    """Periodicidad de un presupuesto."""

    SEMANAL = "semanal"
    MENSUAL = "mensual"
    ANUAL = "anual"


class TicketPrioridad(str, Enum):
    BAJA = "baja"
    MEDIA = "media"
    ALTA = "alta"


class TicketEstado(str, Enum):
    ABIERTO = "abierto"
    EN_PROCESO = "en_proceso"
    RESUELTO = "resuelto"
    CERRADO = "cerrado"


class MotivoEscalacion(str, Enum):
    """Por qué un mensaje terminó en ticket (contexto de escalación del guardrail)."""

    RECLAMO = "reclamo"
    REGULATORIO = "regulatorio"
    CONSEJO_INVERSION = "consejo_inversion"
    FRAUDE = "fraude"
    FUERA_DE_CORPUS = "fuera_de_corpus"   # H3 sin respuesta groundeada (§4)
    GUARDRAIL_FAIL_CLOSED = "guardrail_fail_closed"  # Groq caído/timeout (§7.3.4)
    OTRO = "otro"


# ---------------------------------------------------------------------------
# Entidades persistidas (espejo de las tablas de Supabase — ver db/schema.sql)
# ---------------------------------------------------------------------------
class User(BaseModel):
    """Usuario identificado por su teléfono E.164 (§3.1)."""

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[UUID] = None
    telefono: str = Field(..., description="Identidad natural, formato E.164 (+50370000000).")
    nombre: Optional[str] = None
    # Paso (0) del orquestador: sin consentimiento no se procesa nada más (§7.2).
    consentimiento_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("telefono")
    @classmethod
    def _validar_e164(cls, v: str) -> str:
        import re

        v = v.strip()
        if not re.match(E164_PATTERN, v):
            raise ValueError(f"Teléfono no está en formato E.164: {v!r}")
        return v

    @property
    def tiene_consentimiento(self) -> bool:
        return self.consentimiento_at is not None


class Message(BaseModel):
    """Fila de `messages`: memoria conversacional Y log de auditoría (§7.4).

    Los campos `intencion` y `tool_llamada` son los que convierten esta tabla
    en el audit trail regulatorio; van poblados solo en mensajes del asistente.
    """

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[UUID] = None
    user_id: UUID
    rol: Rol
    contenido: str
    intencion: Optional[Intencion] = None
    tool_llamada: Optional[str] = None
    timestamp: datetime = Field(default_factory=_utcnow)


class Category(BaseModel):
    """Categoría de gasto (catálogo del schema de T2)."""

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[UUID] = None
    nombre: str
    descripcion: Optional[str] = None


class Transaction(BaseModel):
    """Gasto registrado (H1). Si falta info obligatoria → `pendiente_confirmacion` (§3.1)."""

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[UUID] = None
    user_id: UUID
    monto: Optional[Decimal] = Field(default=None, description="Positivo. None mientras esté pendiente.")
    fecha: Optional[date] = None
    categoria: Optional[str] = None
    comercio: Optional[str] = None
    status: TransactionStatus = TransactionStatus.PENDIENTE_CONFIRMACION
    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("monto")
    @classmethod
    def _monto_positivo(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v <= 0:
            raise ValueError("El monto debe ser positivo.")
        return v


class Budget(BaseModel):
    """Presupuesto por categoría y periodo (H2). El sistema agrega; Claude explica."""

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[UUID] = None
    user_id: UUID
    categoria: str
    monto_limite: Decimal
    periodo: Periodo = Periodo.MENSUAL
    # Fracción del límite [0..1] que dispara alerta proactiva del scheduler (§7.6, fase 7).
    umbral_alerta: float = Field(default=0.8, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("monto_limite")
    @classmethod
    def _limite_positivo(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("El límite de presupuesto debe ser positivo.")
        return v


class Ticket(BaseModel):
    """Escalación a humano (guardrail §7.3 o grounding H3 §4). Se ve en el panel (fase 6)."""

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[UUID] = None
    user_id: UUID
    motivo: MotivoEscalacion
    prioridad: TicketPrioridad = TicketPrioridad.MEDIA
    estado: TicketEstado = TicketEstado.ABIERTO
    # Contexto de la escalación: mensaje disparador + lo que sepa el guardrail.
    contexto: str
    mensaje_origen_id: Optional[UUID] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    resuelto_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# DTOs de frontera (no se persisten como tal; son el "formato canónico")
# ---------------------------------------------------------------------------
class IncomingMessage(BaseModel):
    """Formato canónico de mensaje entrante (§7.1 / §7.2).

    Todo `ChannelAdapter` (Twilio, web chat, Telegram futuro) normaliza su
    payload a ESTA forma. El orquestador nunca conoce el canal original.
    """

    canal: str = Field(..., description="Identificador del canal: 'whatsapp', 'web'...")
    telefono: str = Field(..., description="Identidad del remitente en E.164.")
    texto: str
    nombre_perfil: Optional[str] = None
    timestamp: datetime = Field(default_factory=_utcnow)
    # Payload crudo del canal, por si un adaptador necesita metadata extra.
    raw: dict[str, Any] = Field(default_factory=dict)


class GuardrailResult(BaseModel):
    """Salida del clasificador de sensibilidad (§1.4 / §7.3).

    `confianza` alimenta la Capa 2 (umbral): si cae bajo el umbral calibrado
    (fase 8) se fuerza `sensible=True` en código, sin nueva inferencia.
    """

    sensible: bool
    categoria: str = "ninguna"
    confianza: float = Field(default=1.0, ge=0.0, le=1.0)
    # Qué capa disparó la decisión (denylist / clasificador / umbral / fail_closed).
    fuente: str = "clasificador"


class ToolCall(BaseModel):
    """Una llamada a tool emitida por el LLM."""

    id: str
    nombre: str
    argumentos: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    """Respuesta normalizada de un `LLMProvider` (Claude/Groq/…)."""

    texto: Optional[str] = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: Optional[str] = None
    # Metadata de uso para el control de costo (§8.4) y el caché de H3 (§4).
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None


class AgentContext(BaseModel):
    """Contexto reconstruido por request que recibe un `AgentHandler` (§3.1).

    La "memoria" no es sesión: es esto, rearmado en cada mensaje entrante desde
    la tabla `messages` + la transacción pendiente si existe.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    user: User
    incoming: IncomingMessage
    historial: list[Message] = Field(default_factory=list)
    # El modelo decide si el mensaje nuevo completa esta pendiente o abre otra
    # intención (caso frágil de §3.1) — no es una regla rígida.
    transaccion_pendiente: Optional[Transaction] = None


class AgentResult(BaseModel):
    """Resultado del handler que el orquestador persiste y responde al usuario."""

    respuesta: str
    intencion: Intencion = Intencion.OTRO
    tool_llamada: Optional[str] = None

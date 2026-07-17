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
    INGRESO = "ingreso"            # H1 (ingresos — plan-implementacion-ingresos)
    PRESUPUESTO = "presupuesto"    # H2
    SOPORTE = "soporte"            # H3
    SENSIBLE = "sensible"          # ruta guardrail → ticket
    CONSENTIMIENTO = "consentimiento"
    OTRO = "otro"


class TransactionTipo(str, Enum):
    """Distingue plata que sale (gasto) de plata que entra (ingreso). El default
    'gasto' deja correctos, sin migración, los datos previos a los ingresos."""

    GASTO = "gasto"
    INGRESO = "ingreso"


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


class UserFact(BaseModel):
    """Hecho estable del usuario (memoria de largo plazo — Parte A del plan).

    Lo que no cabe en la ventana de mensajes recientes pero conviene recordar
    entre conversaciones: una preferencia ("respuestas cortas"), un hábito
    ("cobra el 30"), un dato ("su sueldo es 450"). Los extrae un job del
    scheduler y se deduplican por similitud contra los hechos ya guardados.
    """

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[UUID] = None
    user_id: UUID
    #: Categoría del hecho: 'preferencia' | 'habito' | 'dato' | 'otro'.
    tipo: str = "otro"
    contenido: str
    fuente_message_id: Optional[UUID] = None
    updated_at: datetime = Field(default_factory=_utcnow)
    created_at: datetime = Field(default_factory=_utcnow)


class ConversationSummary(BaseModel):
    """Resumen de una sesión inactiva (memoria episódica — Parte A del plan).

    Comprime en 2-4 frases lo hablado en una ventana de tiempo. Entra al mismo
    pool de recuperación semántica que los mensajes, para resolver alusiones a
    conversaciones pasadas sin recargar cientos de mensajes.
    """

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[UUID] = None
    user_id: UUID
    resumen: str
    desde_ts: Optional[datetime] = None
    hasta_ts: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)


class Recuerdo(BaseModel):
    """Un fragmento de memoria recuperado por similitud (DTO, no se persiste).

    Unifica lo que devuelven las búsquedas sobre mensajes y sobre resúmenes,
    para inyectarlo como un bloque de contexto al agente (§ retrieval híbrido).
    """

    contenido: str
    #: De dónde vino: 'mensaje' | 'resumen'.
    origen: str
    rol: Optional[str] = None
    timestamp: Optional[datetime] = None
    similitud: float = 0.0


class Category(BaseModel):
    """Categoría de gasto (catálogo del schema de T2)."""

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[UUID] = None
    nombre: str
    descripcion: Optional[str] = None


class Transaction(BaseModel):
    """Movimiento registrado (H1): gasto o ingreso. Si falta info obligatoria →
    `pendiente_confirmacion` (§3.1). Para un ingreso, `comercio` guarda la FUENTE
    (empresa, cliente, "venta bici") — misma columna, semántica según `tipo`."""

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[UUID] = None
    user_id: UUID
    tipo: TransactionTipo = TransactionTipo.GASTO
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


class RecurringIncome(BaseModel):
    """Ingreso fijo mensual (sueldo base, arriendo que entra…). El scheduler envía
    un recordatorio el día configurado; el registro real lo confirma el usuario
    por chat (decisión D3 del plan) — este modelo NO crea transacciones por sí solo."""

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[UUID] = None
    user_id: UUID
    monto: Decimal
    categoria: str = "Salario"
    fuente: Optional[str] = None
    # 1..28: se evita la casuística de febrero/meses de 30 (ver plan).
    dia_del_mes: int = Field(..., ge=1, le=28)
    activo: bool = True
    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("monto")
    @classmethod
    def _monto_positivo(cls, v: Decimal) -> Decimal:
        if v <= 0:
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


class AuthCode(BaseModel):
    """Código OTP de acceso a la webapp, enviado por WhatsApp.

    Prácticas aplicadas (OWASP MFA cheat sheet / NIST 800-63B):
    - Solo se persiste el HASH del código (sha256 con el teléfono como contexto).
    - Expira a los pocos minutos, es de un solo uso y admite pocos intentos.
    """

    id: Optional[UUID] = None
    telefono: str
    codigo_hash: str
    expira_at: datetime
    intentos: int = 0
    usado: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class Session(BaseModel):
    """Sesión web emitida al verificar el OTP. Se guarda el hash del token."""

    token_hash: str
    user_id: UUID
    expira_at: datetime
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# DTOs de frontera (no se persisten como tal; son el "formato canónico")
# ---------------------------------------------------------------------------

#: Tipos MIME que el agente puede procesar como visión/documento (Claude API).
MEDIA_TIPOS_IMAGEN = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
MEDIA_TIPO_PDF = "application/pdf"

#: Tipos que se procesan por SCRIPT (plan de documentos), nunca como bloque de
#: visión: la API de Anthropic no los acepta (riesgo R4).
MEDIA_TIPOS_XML = frozenset({"text/xml", "application/xml"})
MEDIA_TIPOS_TABULAR = frozenset(
    {
        "text/csv",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)
MEDIA_MAX_BYTES_XML = 2 * 1024 * 1024
MEDIA_MAX_BYTES_TABULAR = 10 * 1024 * 1024

#: Límites de tamaño (bytes) antes de base64 — el API de Anthropic rechaza
#: imágenes > 5MB; el PDF se acota para no inflar el request.
MEDIA_MAX_BYTES_IMAGEN = 5 * 1024 * 1024
MEDIA_MAX_BYTES_PDF = 20 * 1024 * 1024


class MediaItem(BaseModel):
    """Adjunto de un mensaje entrante (imagen o documento).

    El adaptador de canal llena `url` + `content_type` al parsear el webhook;
    la descarga (que requiere las credenciales del canal) llena `data_base64`
    después, en background. `data_base64 is None` = no descargado o falló.
    """

    content_type: str
    url: Optional[str] = None
    data_base64: Optional[str] = None
    # Nombre del archivo si el canal lo provee (documentos).
    filename: Optional[str] = None

    @property
    def es_imagen(self) -> bool:
        return self.content_type in MEDIA_TIPOS_IMAGEN

    @property
    def es_pdf(self) -> bool:
        return self.content_type == MEDIA_TIPO_PDF

    @property
    def es_xml(self) -> bool:
        return self.content_type in MEDIA_TIPOS_XML

    @property
    def es_tabular(self) -> bool:
        return self.content_type in MEDIA_TIPOS_TABULAR

    @property
    def soportado(self) -> bool:
        """Solo imágenes y PDF llegan al modelo; audio/video/otros se declinan."""
        return self.es_imagen or self.es_pdf

    @property
    def descargable(self) -> bool:
        """Qué vale la pena bajar del canal cuando el flujo de documentos está
        activo: lo que ve el modelo (soportado) + lo que procesan los scripts
        (XML/CSV/XLSX). `soportado` NO cambia: gobierna los bloques de visión
        (riesgo R4 del plan de documentos)."""
        return self.soportado or self.es_xml or self.es_tabular

    @property
    def limite_bytes(self) -> int:
        if self.es_imagen:
            return MEDIA_MAX_BYTES_IMAGEN
        if self.es_xml:
            return MEDIA_MAX_BYTES_XML
        if self.es_tabular:
            return MEDIA_MAX_BYTES_TABULAR
        return MEDIA_MAX_BYTES_PDF

    @property
    def etiqueta(self) -> str:
        """Descripción corta para el audit trail ('[imagen]', '[documento PDF]'…)."""
        if self.es_imagen:
            return "[imagen]"
        if self.es_pdf:
            return "[documento PDF]"
        return f"[archivo {self.content_type}]"


class IncomingMessage(BaseModel):
    """Formato canónico de mensaje entrante (§7.1 / §7.2).

    Todo `ChannelAdapter` (WhatsApp/Meta, web chat, Telegram futuro) normaliza su
    payload a ESTA forma. El orquestador nunca conoce el canal original.
    """

    canal: str = Field(..., description="Identificador del canal: 'whatsapp', 'web'...")
    telefono: str = Field(..., description="Identidad del remitente en E.164.")
    texto: str
    nombre_perfil: Optional[str] = None
    timestamp: datetime = Field(default_factory=_utcnow)
    # Adjuntos (imágenes/documentos de WhatsApp). Vacío en mensajes solo-texto.
    media: list[MediaItem] = Field(default_factory=list)
    # Payload crudo del canal, por si un adaptador necesita metadata extra.
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def contenido_para_audit(self) -> str:
        """Texto que se persiste en `messages` (el binario nunca se guarda ahí):
        el caption + una etiqueta por adjunto, o solo las etiquetas."""
        etiquetas = " ".join(m.etiqueta for m in self.media)
        if self.texto and etiquetas:
            return f"{self.texto} {etiquetas}"
        return self.texto or etiquetas


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
    # Id del mensaje entrante ya persistido en `messages`, para indexar su vector
    # en la memoria semántica (Parte A). None en flujos que no lo persisten.
    mensaje_id: Optional[UUID] = None
    # El modelo decide si el mensaje nuevo completa esta pendiente o abre otra
    # intención (caso frágil de §3.1) — no es una regla rígida.
    transaccion_pendiente: Optional[Transaction] = None
    # Memoria semántica (Parte A del plan): recuerdos recuperados por similitud
    # con el mensaje entrante (mensajes viejos fuera de la ventana + resúmenes) y
    # hechos estables del usuario. Vacíos si la memoria semántica está apagada.
    memoria_relevante: list[Recuerdo] = Field(default_factory=list)
    hechos_usuario: list[UserFact] = Field(default_factory=list)


class AgentResult(BaseModel):
    """Resultado del handler que el orquestador persiste y responde al usuario."""

    respuesta: str
    intencion: Intencion = Intencion.OTRO
    tool_llamada: Optional[str] = None


# ---------------------------------------------------------------------------
# Documentos financieros (plan de documentos, contrato del módulo 01)
# ---------------------------------------------------------------------------
class TipoDocumento(str, Enum):
    FACTURA_SRI = "factura_sri"
    RETENCION = "retencion"
    NOTA_CREDITO = "nota_credito"
    TRANSFERENCIA = "transferencia"
    PLANILLA_SERVICIO = "planilla_servicio"
    ESTADO_CUENTA = "estado_cuenta"
    ROL_PAGOS = "rol_pagos"
    VOUCHER = "voucher"
    OTRO_RESPALDO = "otro_respaldo"
    SIN_CLASIFICAR = "sin_clasificar"


class DocumentStatus(str, Enum):
    RECIBIDO = "recibido"
    ESPERANDO_CLASIFICACION = "esperando_clasificacion"
    PROCESANDO = "procesando"
    EXTRAIDO = "extraido"
    EN_REVISION = "en_revision"
    CONFIRMADO = "confirmado"
    ERROR = "error"
    DESCARTADO = "descartado"


class Document(BaseModel):
    """Todo archivo recibido, con su original a salvo en Storage (espejo 1:1 de
    la tabla `documents`). Primero persistir, después interpretar: la fila y el
    objeto en Storage existen ANTES de cualquier extracción."""

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[UUID] = None
    user_id: UUID
    tipo_documento: TipoDocumento = TipoDocumento.SIN_CLASIFICAR
    status: DocumentStatus = DocumentStatus.RECIBIDO
    storage_path: str
    filename: Optional[str] = None
    content_type: str
    size_bytes: int
    sha256: str
    clave_acceso: Optional[str] = None
    emisor_ruc: Optional[str] = None
    emisor_nombre: Optional[str] = None
    fecha_emision: Optional[date] = None
    total: Optional[Decimal] = None
    metodo_extraccion: Optional[str] = None
    datos_extraidos: Optional[dict[str, Any]] = None
    error_detalle: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    processed_at: Optional[datetime] = None


class DocumentItemEstado(str, Enum):
    PENDIENTE = "pendiente"
    ACEPTADO = "aceptado"
    RECHAZADO = "rechazado"
    DUPLICADO = "duplicado"


class DocumentItem(BaseModel):
    """Una fila del staging de una carga masiva (estado de cuenta). NO es una
    transacción: vive en `document_items` hasta que el usuario la confirma en la
    webapp, y solo entonces se materializa en `transactions` (riesgo R1)."""

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[UUID] = None
    document_id: UUID
    user_id: UUID
    n_linea: int
    fecha: Optional[date] = None
    descripcion_raw: str
    monto: Optional[Decimal] = None
    tipo: Optional[str] = None  # 'gasto' | 'ingreso'
    categoria_sugerida: Optional[str] = None
    counterparty_id: Optional[UUID] = None
    confianza: Optional[float] = None
    estado: DocumentItemEstado = DocumentItemEstado.PENDIENTE
    transaction_id: Optional[UUID] = None


class ReviewTaskTipo(str, Enum):
    CARGA_MASIVA = "carga_masiva"
    MAPEO = "mapeo"
    DATO_FALTANTE = "dato_faltante"
    AJUSTE_NC = "ajuste_nc"
    DIVISION_ITEMS = "division_items"


class ReviewTaskStatus(str, Enum):
    PENDIENTE = "pendiente"
    EN_PROGRESO = "en_progreso"
    COMPLETADA = "completada"
    EXPIRADA = "expirada"
    DESCARTADA = "descartada"


class ReviewTask(BaseModel):
    """Una tarea en la cola de revisión de la webapp (estado de cuenta con muchos
    movimientos → el usuario los confirma en bloque en su panel)."""

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[UUID] = None
    user_id: UUID
    document_id: UUID
    tipo: ReviewTaskTipo = ReviewTaskTipo.CARGA_MASIVA
    status: ReviewTaskStatus = ReviewTaskStatus.PENDIENTE
    resumen: str
    created_at: datetime = Field(default_factory=_utcnow)
    completed_at: Optional[datetime] = None

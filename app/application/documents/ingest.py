"""Orquestador de ingesta de documentos (plan de documentos, E1).

Análogo a `ProcessMessage` pero para archivos: guarda el original (Storage +
fila en `documents`) ANTES de interpretar nada, deduplica por sha256, aplica el
rate limit diario, y clasifica por el camino más barato:

  - caption claro → deja pasar al pipeline A (visión + tools, el actual);
    el documento ya quedó guardado como respaldo.
  - adjunto "mudo" (sin caption) → menú A–E de texto FIJO (cero tokens);
    el usuario clasifica con una letra y el documento se re-inyecta al agente
    con esa clasificación como contexto.
  - XML/CSV → guardados como respaldo (sus pipelines de script llegan en E2/E3).

Todo detrás de DOCS_HABILITADO: con el flag apagado este módulo ni se instancia
y el sistema se comporta EXACTAMENTE como antes.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.domain.models import (
    AgentContext,
    Document,
    DocumentStatus,
    IncomingMessage,
    Intencion,
    MediaItem,
    Message,
    Rol,
    TipoDocumento,
    User,
)
from app.domain.ports import ChannelAdapter, DocumentStorage, Repository

log = logging.getLogger("e5.documentos")

#: Menú de clasificación — texto fijo, sin LLM (constante como AVISO_LEGAL).
MENU_CLASIFICACION = (
    "📄 ¡Recibí tu documento! Para guardarlo bien, dime qué es:\n"
    "*A)* Factura, recibo o comprobante (de una compra o una venta)\n"
    "*B)* Comprobante de transferencia\n"
    "*C)* Estado de cuenta del banco (varios movimientos)\n"
    "*D)* Rol de pagos u otro ingreso\n"
    "*E)* Planilla de luz, agua o teléfono\n\n"
    "Responde con la letra 🙂"
)

RESPUESTA_DUPLICADO = "Ese archivo ya lo tengo guardado ✅ ¿Querías registrar algo de ahí?"
RESPUESTA_RATE_LIMIT = (
    "¡Uy! Ya me mandaste varios documentos hoy 😅 Guardo hasta cierto número "
    "por día — mañana seguimos con este, o escríbeme el dato y lo anoto ya."
)
RESPUESTA_XML_TABULAR = (
    "Recibí tu archivo y lo guardé de respaldo 📁 El procesamiento automático "
    "de este tipo de archivo llega prontito; mientras tanto, si me dices qué "
    "registrar, lo anoto de una."
)

#: Respuesta al menú: una letra a–e, con o sin paréntesis/punto.
_RE_LETRA = re.compile(r"^\s*([a-eA-E])[\s).\.]*$")

#: Letra → (tipo_documento, contexto que ve el agente al re-inyectar).
_DISPATCH_LETRA = {
    "a": (
        TipoDocumento.VOUCHER,
        "El usuario clasificó este documento que envió antes: es una FACTURA, "
        "RECIBO O COMPROBANTE. OJO: una factura puede ser una COMPRA suya (gasto) "
        "o una que ÉL EMITIÓ por una venta o cobro (ingreso). NO asumas la "
        "dirección del dinero: si el usuario no la dijo, pregúntale si es algo "
        "que compró o que vendió/cobró antes de registrar. Extrae los datos y "
        "regístralo con la tool que corresponda solo cuando la dirección esté clara.",
    ),
    "b": (
        TipoDocumento.TRANSFERENCIA,
        "El usuario clasificó este documento que envió antes: es un COMPROBANTE "
        "DE TRANSFERENCIA. Recuerda: la dirección del dinero la confirma el "
        "usuario — si no está clara, pregúntale si la hizo o la recibió.",
    ),
    "c": (
        TipoDocumento.ESTADO_CUENTA,
        "El usuario clasificó este documento que envió antes: es un ESTADO DE "
        "CUENTA con varios movimientos. NO registres nada de una: resume lo que "
        "ves y pregunta cuáles quiere registrar.",
    ),
    "d": (
        TipoDocumento.ROL_PAGOS,
        "El usuario clasificó este documento que envió antes: es un ROL DE PAGOS "
        "U OTRO INGRESO (plata que le entra). Extrae los datos y regístralo como "
        "ingreso según tus reglas.",
    ),
    "e": (
        TipoDocumento.PLANILLA_SERVICIO,
        "El usuario clasificó este documento que envió antes: es una PLANILLA DE "
        "SERVICIO BÁSICO (luz, agua, teléfono, internet) — un gasto en la "
        "categoría de servicios. Extrae los datos y regístralo según tus reglas.",
    ),
}


class DocumentIngest:
    """Solo conoce puertos. `main.py` lo instancia únicamente con el flag activo."""

    def __init__(
        self,
        repo: Repository,
        storage: DocumentStorage,
        channel: ChannelAdapter,
        max_por_dia: int = 20,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._channel = channel
        self._max_por_dia = max_por_dia

    # ------------------------------------------------------- mensajes con media
    async def atender_media(self, context: AgentContext) -> bool:
        """Corre en background tras `fetch_media` (riesgo R5). Persiste el
        original y decide el camino. Devuelve True si el mensaje quedó atendido
        aquí (menú/duplicado/respaldo); False = que siga el pipeline A (visión).

        Regla de oro: un fallo de ESTE flujo nunca rompe el pipeline — ante
        cualquier excepción se devuelve False y el agente atiende como siempre.
        v1 procesa el primer adjunto descargado (WhatsApp manda uno por mensaje)."""
        try:
            return await self._atender_media(context)
        except Exception:
            log.exception("Ingesta de documento falló; sigue el pipeline normal.")
            return False

    async def _atender_media(self, context: AgentContext) -> bool:
        user, incoming = context.user, context.incoming
        item = next((m for m in incoming.media if m.data_base64), None)
        if item is None:
            return False  # nada descargado: el agente explica, como siempre

        contenido = base64.b64decode(item.data_base64)
        sha = hashlib.sha256(contenido).hexdigest()

        duplicado = await asyncio.to_thread(self._repo.find_document_by_sha, user.id, sha)
        if duplicado is not None:
            await self._responder(user, RESPUESTA_DUPLICADO)
            return True

        hace_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        recibidos = await asyncio.to_thread(
            self._repo.count_documents_desde, user.id, hace_24h
        )
        if recibidos >= self._max_por_dia:
            await self._responder(user, RESPUESTA_RATE_LIMIT)
            return True

        # Primero persistir, después interpretar: Storage y fila `documents`
        # antes de cualquier decisión. Si Storage falla, la excepción sube y el
        # pipeline A atiende sin respaldo (mejor responder que perder el turno).
        doc_id = uuid4()
        hoy = datetime.now(timezone.utc)
        ext = _extension(item)
        path = f"{user.id}/{hoy:%Y}/{hoy:%m}/{doc_id}{ext}"
        await asyncio.to_thread(self._storage.guardar, path, contenido, item.content_type)
        doc = await asyncio.to_thread(
            self._repo.save_document,
            Document(
                id=doc_id,
                user_id=user.id,
                storage_path=path,
                filename=item.filename,
                content_type=item.content_type,
                size_bytes=len(contenido),
                sha256=sha,
            ),
        )

        if item.es_xml or item.es_tabular:
            # Sus pipelines de script llegan en E2/E3; por ahora, respaldo.
            await asyncio.to_thread(
                self._repo.update_document,
                user.id,
                doc.id,
                {"tipo_documento": TipoDocumento.OTRO_RESPALDO.value, "status": DocumentStatus.CONFIRMADO.value},
            )
            await self._responder(user, RESPUESTA_XML_TABULAR)
            return True

        if incoming.texto.strip():
            # Caption claro: el pipeline A sigue su curso normal por el agente;
            # el original ya quedó guardado como respaldo.
            return False

        # Adjunto mudo → menú (texto fijo, cero tokens).
        await asyncio.to_thread(
            self._repo.update_document,
            user.id,
            doc.id,
            {"status": DocumentStatus.ESPERANDO_CLASIFICACION.value},
        )
        await self._responder(user, MENU_CLASIFICACION)
        return True

    # ------------------------------------------------- respuesta de letra (menú)
    async def atender_letra(
        self, user: User, incoming: IncomingMessage
    ) -> IncomingMessage | None:
        """Para `preprocess` (mensajes SOLO texto). Devuelve:
        - None: no aplica (no es letra, o no hay documento esperando) → flujo normal.
        - IncomingMessage: letra A–E → mensaje REESCRITO (documento desde Storage
          + contexto de la clasificación) para que el pipeline normal del agente
          lo procese — sin duplicar la maquinaria de run_agent."""
        m = _RE_LETRA.match(incoming.texto or "")
        if incoming.media or m is None:
            return None
        doc = await asyncio.to_thread(self._repo.find_document_esperando, user.id)
        if doc is None:
            return None  # una 'b' suelta sin documento pendiente: mensaje normal
        letra = m.group(1).lower()

        tipo, contexto = _DISPATCH_LETRA[letra]
        contenido = await asyncio.to_thread(self._storage.leer, doc.storage_path)
        await asyncio.to_thread(
            self._repo.update_document,
            user.id,
            doc.id,
            {"tipo_documento": tipo.value, "status": DocumentStatus.PROCESANDO.value},
        )
        return incoming.model_copy(
            update={
                "texto": f"[{contexto}]",
                "media": [
                    MediaItem(
                        content_type=doc.content_type,
                        data_base64=base64.b64encode(contenido).decode("ascii"),
                        filename=doc.filename,
                    )
                ],
            }
        )

    # ------------------------------------------------------------------ internos
    async def _responder(self, user: User, texto: str) -> None:
        """Envía y audita (mismo contrato que el orquestador: toda respuesta
        queda en `messages`; intención 'otro' — riesgo R2, el check de la tabla
        no admite valores nuevos sin migración)."""
        try:
            await self._channel.send(user, texto)
        except Exception:
            log.exception("No se pudo entregar la respuesta de documentos a %s", user.id)
        await asyncio.to_thread(
            self._repo.save_message,
            Message(
                user_id=user.id,
                rol=Rol.ASISTENTE,
                contenido=texto,
                intencion=Intencion.OTRO,
            ),
        )


def _extension(item: MediaItem) -> str:
    if item.filename and "." in item.filename:
        return "." + item.filename.rsplit(".", 1)[1].lower()
    por_mime = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "application/pdf": ".pdf",
        "text/xml": ".xml",
        "application/xml": ".xml",
        "text/csv": ".csv",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    }
    return por_mime.get(item.content_type, "")

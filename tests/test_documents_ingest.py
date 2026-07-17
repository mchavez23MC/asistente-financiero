"""Tests del orquestador de ingesta de documentos (plan de documentos, E1).

Patrón del repo: fakes en memoria, sin APIs reales. Cubre la máquina de
estados del flujo E1: guardar original, dedupe por sha256, rate limit, menú
A–F, dispatch por letra y la regla de compatibilidad con el pipeline A
("caption claro devuelve False").
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.application.documents.ingest import (
    MENU_CLASIFICACION,
    RESPUESTA_DUPLICADO,
    RESPUESTA_RATE_LIMIT,
    DocumentIngest,
)
from app.domain.models import (
    AgentContext,
    Document,
    IncomingMessage,
    MediaItem,
    Message,
    User,
)


# --- fakes -------------------------------------------------------------------
class FakeRepo:
    def __init__(self) -> None:
        self.documents: dict[UUID, Document] = {}
        self.messages: list[Message] = []

    def save_document(self, document: Document) -> Document:
        doc = document.model_copy(update={"id": document.id or uuid4()})
        self.documents[doc.id] = doc
        return doc

    def find_document_by_sha(self, user_id, sha256):
        return next(
            (d for d in self.documents.values() if d.user_id == user_id and d.sha256 == sha256),
            None,
        )

    def find_document_esperando(self, user_id):
        candidatos = [
            d
            for d in self.documents.values()
            if d.user_id == user_id and d.status == "esperando_clasificacion"
        ]
        return max(candidatos, key=lambda d: d.created_at) if candidatos else None

    def update_document(self, user_id, document_id, cambios):
        doc = self.documents.get(document_id)
        if doc is None or doc.user_id != user_id:
            return None
        actualizado = doc.model_copy(update=cambios)
        self.documents[document_id] = actualizado
        return actualizado

    def count_documents_desde(self, user_id, desde: datetime) -> int:
        return sum(
            1
            for d in self.documents.values()
            if d.user_id == user_id and d.created_at >= desde
        )

    def save_message(self, message: Message) -> Message:
        self.messages.append(message)
        return message


class FakeStorage:
    def __init__(self, fallar: bool = False) -> None:
        self.objetos: dict[str, bytes] = {}
        self.fallar = fallar

    def guardar(self, path, contenido, content_type):
        if self.fallar:
            raise RuntimeError("storage caído")
        self.objetos[path] = contenido

    def leer(self, path):
        return self.objetos[path]

    def signed_url(self, path, expira_s=600):
        return f"https://fake/{path}"

    def borrar(self, path):
        self.objetos.pop(path, None)


class FakeChannel:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, user, texto):
        self.enviados.append(texto)


# --- helpers -----------------------------------------------------------------
_PDF = base64.b64encode(b"%PDF-fake bytes del original").decode()


def _user() -> User:
    return User(id=uuid4(), telefono="+593987654321", nombre="Ana")


def _ctx(user: User, texto: str = "", con_media: bool = True) -> AgentContext:
    media = (
        [MediaItem(content_type="application/pdf", url="https://x", data_base64=_PDF, filename="doc.pdf")]
        if con_media
        else []
    )
    return AgentContext(
        user=user,
        incoming=IncomingMessage(canal="whatsapp", telefono=user.telefono, texto=texto, media=media),
        historial=[],
    )


def _ingest(repo=None, storage=None, channel=None, **kw):
    return DocumentIngest(
        repo=repo or FakeRepo(),
        storage=storage or FakeStorage(),
        channel=channel or FakeChannel(),
        **kw,
    )


# --- atender_media -----------------------------------------------------------
async def test_adjunto_mudo_guarda_original_y_manda_menu():
    repo, storage, channel = FakeRepo(), FakeStorage(), FakeChannel()
    ingest = DocumentIngest(repo=repo, storage=storage, channel=channel)
    user = _user()

    atendido = await ingest.atender_media(_ctx(user, texto=""))

    assert atendido is True
    assert channel.enviados == [MENU_CLASIFICACION]
    doc = next(iter(repo.documents.values()))
    assert doc.status == "esperando_clasificacion"
    assert doc.user_id == user.id
    # El original quedó en storage ANTES de cualquier otra cosa, bajo el user_id.
    assert storage.objetos[doc.storage_path] == base64.b64decode(_PDF)
    assert doc.storage_path.startswith(str(user.id))
    # La respuesta quedó auditada.
    assert any(m.contenido == MENU_CLASIFICACION for m in repo.messages)


async def test_caption_claro_devuelve_false_y_guarda_respaldo():
    """La clave de compatibilidad: con caption, el pipeline A sigue su curso —
    pero el original ya quedó guardado."""
    repo, channel = FakeRepo(), FakeChannel()
    ingest = _ingest(repo=repo, channel=channel)
    user = _user()

    atendido = await ingest.atender_media(_ctx(user, texto="gasté esto en el súper"))

    assert atendido is False
    assert channel.enviados == []  # nada respondido aquí: responde el agente
    assert len(repo.documents) == 1


async def test_mismo_archivo_dos_veces_dice_ya_lo_tengo():
    repo, channel = FakeRepo(), FakeChannel()
    ingest = _ingest(repo=repo, channel=channel)
    user = _user()

    await ingest.atender_media(_ctx(user))
    atendido = await ingest.atender_media(_ctx(user))

    assert atendido is True
    assert channel.enviados[-1] == RESPUESTA_DUPLICADO
    assert len(repo.documents) == 1  # sin reproceso ni fila nueva


async def test_rate_limit_diario():
    repo, channel = FakeRepo(), FakeChannel()
    ingest = _ingest(repo=repo, channel=channel, max_por_dia=1)
    user = _user()
    # Un documento previo de HOY ya registrado (otro contenido, otro sha).
    repo.save_document(
        Document(
            user_id=user.id,
            storage_path="x",
            content_type="application/pdf",
            size_bytes=1,
            sha256="otro",
            created_at=datetime.now(timezone.utc),
        )
    )

    atendido = await ingest.atender_media(_ctx(user))

    assert atendido is True
    assert channel.enviados == [RESPUESTA_RATE_LIMIT]
    assert len(repo.documents) == 1  # no se guardó el nuevo


async def test_fallo_de_storage_no_rompe_el_pipeline():
    """Regla de oro: si la ingesta falla, devuelve False y el agente atiende."""
    ingest = _ingest(storage=FakeStorage(fallar=True))
    assert await ingest.atender_media(_ctx(_user())) is False


async def test_media_sin_descargar_sigue_al_agente():
    user = _user()
    ctx = _ctx(user)
    ctx.incoming.media[0].data_base64 = None  # descarga falló
    assert await _ingest().atender_media(ctx) is False


# --- atender_letra -----------------------------------------------------------
async def _con_doc_esperando(repo, storage, channel) -> User:
    ingest = DocumentIngest(repo=repo, storage=storage, channel=channel)
    user = _user()
    await ingest.atender_media(_ctx(user, texto=""))
    return user


async def test_letra_b_reescribe_el_mensaje_con_el_documento():
    repo, storage, channel = FakeRepo(), FakeStorage(), FakeChannel()
    user = await _con_doc_esperando(repo, storage, channel)
    ingest = DocumentIngest(repo=repo, storage=storage, channel=channel)

    incoming = IncomingMessage(canal="whatsapp", telefono=user.telefono, texto="b)")
    resultado = await ingest.atender_letra(user, incoming)

    assert isinstance(resultado, IncomingMessage)
    assert "TRANSFERENCIA" in resultado.texto
    assert resultado.media[0].data_base64 == _PDF  # el original, desde storage
    doc = next(iter(repo.documents.values()))
    assert doc.tipo_documento == "transferencia"
    assert doc.status == "procesando"


async def test_letra_fuera_de_rango_no_es_dispatch():
    """La 'f' ya no existe en el menú: se trata como texto normal, no dispatch."""
    repo, storage, channel = FakeRepo(), FakeStorage(), FakeChannel()
    user = await _con_doc_esperando(repo, storage, channel)
    ingest = DocumentIngest(repo=repo, storage=storage, channel=channel)

    incoming = IncomingMessage(canal="whatsapp", telefono=user.telefono, texto="f")
    assert await ingest.atender_letra(user, incoming) is None


async def test_letra_sin_documento_pendiente_no_secuestra_el_mensaje():
    ingest = _ingest()
    incoming = IncomingMessage(canal="whatsapp", telefono="+593987654321", texto="b")
    assert await ingest.atender_letra(_user(), incoming) is None


async def test_texto_normal_no_es_letra():
    repo, storage, channel = FakeRepo(), FakeStorage(), FakeChannel()
    user = await _con_doc_esperando(repo, storage, channel)
    ingest = DocumentIngest(repo=repo, storage=storage, channel=channel)

    incoming = IncomingMessage(
        canal="whatsapp", telefono=user.telefono, texto="es una factura del súper"
    )
    assert await ingest.atender_letra(user, incoming) is None


# --- XML/CSV: guardados como respaldo (pipelines de script llegan en E2/E3) --
async def test_xml_se_guarda_como_respaldo():
    repo, channel = FakeRepo(), FakeChannel()
    ingest = _ingest(repo=repo, channel=channel)
    user = _user()
    ctx = AgentContext(
        user=user,
        incoming=IncomingMessage(
            canal="whatsapp",
            telefono=user.telefono,
            texto="",
            media=[
                MediaItem(
                    content_type="text/xml",
                    url="https://x",
                    data_base64=base64.b64encode(b"<factura/>").decode(),
                    filename="factura.xml",
                )
            ],
        ),
        historial=[],
    )

    atendido = await ingest.atender_media(ctx)

    assert atendido is True
    doc = next(iter(repo.documents.values()))
    assert doc.tipo_documento == "otro_respaldo"
    assert "guardé" in channel.enviados[0]

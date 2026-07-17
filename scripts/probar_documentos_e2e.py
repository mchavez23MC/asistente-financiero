"""Verificación end-to-end del flujo de documentos contra Supabase REAL.

Ejercita DocumentIngest con el repo y el Storage de verdad (usa .env), con un
canal que captura las respuestas en vez de mandarlas por WhatsApp. Crea filas
de prueba bajo el usuario demo (+50300000001); bórralas del panel si estorban.

Uso:  python -m scripts.probar_documentos_e2e
"""

from __future__ import annotations

import asyncio
import base64
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.adapters.storage.supabase_storage import SupabaseDocumentStorage
from app.adapters.persistence.supabase_repo import SupabaseRepository
from app.application.documents.ingest import DocumentIngest
from app.domain.models import AgentContext, IncomingMessage, MediaItem
from app.infra.config import Settings

TEL = "+50300000001"


class CanalCaptura:
    canal = "whatsapp"

    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, user, texto: str) -> None:
        self.enviados.append(texto)


def _ctx(user, contenido: bytes, content_type: str, filename: str, texto: str = "") -> AgentContext:
    return AgentContext(
        user=user,
        incoming=IncomingMessage(
            canal="whatsapp",
            telefono=user.telefono,
            texto=texto,
            media=[
                MediaItem(
                    content_type=content_type,
                    data_base64=base64.b64encode(contenido).decode(),
                    filename=filename,
                )
            ],
        ),
        historial=[],
    )


def _factura_xml() -> bytes:
    # Clave con dígito verificador válido (misma helper que los tests).
    from tests.test_sri import _clave

    clave = _clave()
    return (
        '<?xml version="1.0"?><factura id="comprobante">'
        "<infoTributaria><razonSocial>SUPERMAXI</razonSocial>"
        f"<ruc>1790016919001</ruc><claveAcceso>{clave}</claveAcceso></infoTributaria>"
        "<infoFactura><fechaEmision>10/07/2026</fechaEmision>"
        "<importeTotal>32.50</importeTotal></infoFactura></factura>"
    ).encode()


_CSV = (
    "Fecha,Descripcion,Monto\n"
    "10/07/2026,SUPERMAXI QUITO,-32.50\n"
    "12/07/2026,SUELDO ACME,1200.00\n"
    "13/07/2026,NETFLIX,-12.99\n"
).encode()

_PDF_MUDO = b"%PDF-1.4 documento de prueba sin caption"


async def main() -> None:
    s = Settings.from_env()
    repo = SupabaseRepository(s.supabase_url, s.supabase_key)
    storage = SupabaseDocumentStorage(s.supabase_url, s.supabase_key, s.docs_bucket)
    canal = CanalCaptura()
    ingest = DocumentIngest(repo, storage, canal, public_base_url="https://demo.luca")
    user = repo.get_or_create_user(TEL, "Prueba E2E")

    print("=== 1. Adjunto mudo (PDF sin caption) → menú ===")
    canal.enviados.clear()
    atendido = await ingest.atender_media(_ctx(user, _PDF_MUDO, "application/pdf", "recibo.pdf"))
    print("atendido:", atendido)
    print("respuesta:", canal.enviados[-1][:80], "...")

    print("\n=== 2. Respuesta 'B' al menú → reinyecta transferencia ===")
    reesc = await ingest.atender_letra(
        user, IncomingMessage(canal="whatsapp", telefono=TEL, texto="b")
    )
    print("mensaje reescrito contiene 'TRANSFERENCIA':", "TRANSFERENCIA" in (reesc.texto if reesc else ""))

    print("\n=== 3. Factura electrónica XML → extrae y reinyecta ===")
    canal.enviados.clear()
    ctx = _ctx(user, _factura_xml(), "text/xml", "factura.xml")
    atendido = await ingest.atender_media(ctx)
    print("atendido (False = va al agente):", atendido)
    print("texto reinyectado contiene total 32.50:", "32.50" in ctx.incoming.texto)
    print("pide dirección:", "NO asumas la dirección" in ctx.incoming.texto)

    print("\n=== 4. Estado de cuenta CSV → staging + tarea de revisión ===")
    canal.enviados.clear()
    atendido = await ingest.atender_media(_ctx(user, _CSV, "text/csv", "estado.csv"))
    print("atendido:", atendido)
    print("respuesta:", canal.enviados[-1][:120])
    tareas = repo.list_review_tasks(user.id, status="pendiente")
    print("tareas de revisión pendientes:", len(tareas))
    if tareas:
        items = repo.list_document_items(tareas[0].document_id, user.id)
        print("movimientos en staging:", len(items))
        for i in items:
            print(f"   - {i.fecha} {i.descripcion_raw} ${i.monto} [{i.tipo}] {i.estado}")

    print("\n✅ Flujo de documentos ejercitado contra Supabase real.")


if __name__ == "__main__":
    asyncio.run(main())

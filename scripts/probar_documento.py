"""Prueba manual de documentos e imágenes contra el agente REAL (diagnóstico).

Manda un archivo local (PDF o imagen) directo al agente principal, como si el
usuario lo hubiera enviado por WhatsApp, SIN necesitar Twilio ni túnel. Usa los
servicios reales de .env (Claude + Supabase): las transacciones que el agente
registre quedan en la base, bajo el usuario demo del plan B (+50300000001),
igual que el chat web — bórralas desde el panel si estorban.

Uso:
    python -m scripts.probar_documento factura.pdf
    python -m scripts.probar_documento recibo.jpg --texto "esto fue de ayer"
    python -m scripts.probar_documento estado_cuenta.pdf --telefono +503XXXXXXXX

Además de la respuesta de Luca, imprime si el PDF viajó como TEXTO EXTRAÍDO
(pipeline nuevo) o como documento base64 (visión, fallback para escaneados).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import mimetypes
import sys
from pathlib import Path

# La consola de Windows suele ser cp1252: sin esto, los emojis de Luca y las
# flechas del diagnóstico revientan el print.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.adapters.llm.claude import ClaudeProvider
from app.adapters.persistence.supabase_repo import SupabaseRepository
from app.application.agents.principal import MainAgent, _bloques_media
from app.application.agents.soporte_rag import SoporteRAG
from app.domain.models import AgentContext, IncomingMessage, MediaItem
from app.infra.config import Settings

#: Mismo usuario sintético que el chat web plan B: aislado de números reales.
TELEFONO_DEMO = "+50300000001"


def _incoming(archivo: Path, texto: str, telefono: str) -> IncomingMessage:
    content_type = mimetypes.guess_type(archivo.name)[0]
    if content_type is None:
        raise SystemExit(f"No pude deducir el tipo de {archivo.name}; usa .pdf/.jpg/.png")
    return IncomingMessage(
        canal="whatsapp",
        telefono=telefono,
        texto=texto,
        media=[
            MediaItem(
                content_type=content_type,
                url=f"file://{archivo}",
                data_base64=base64.b64encode(archivo.read_bytes()).decode(),
                filename=archivo.name,
            )
        ],
    )


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("archivo", help="ruta a un PDF o imagen local")
    ap.add_argument("--texto", default="", help="caption que acompaña al adjunto")
    ap.add_argument("--telefono", default=TELEFONO_DEMO)
    args = ap.parse_args()

    archivo = Path(args.archivo)
    if not archivo.is_file():
        raise SystemExit(f"No existe: {archivo}")
    incoming = _incoming(archivo, args.texto, args.telefono)

    # Diagnóstico: ¿cómo va a viajar el adjunto al modelo?
    for bloque in _bloques_media(incoming) or []:
        if bloque["type"] == "text" and bloque["text"].startswith("[CONTENIDO EXTRAÍDO"):
            print(f"→ PDF digital: viaja como TEXTO extraído ({len(bloque['text'])} chars).")
        elif bloque["type"] == "document":
            print("→ PDF escaneado/ilegible: viaja como documento base64 (visión).")
        elif bloque["type"] == "image":
            print("→ Imagen: viaja como bloque de visión.")

    settings = Settings.from_env()
    repo = SupabaseRepository(settings.supabase_url, settings.supabase_key)
    claude = ClaudeProvider(
        settings.anthropic_api_key, settings.claude_model, settings.claude_max_tokens
    )
    agente = MainAgent(llm=claude, repo=repo, soporte=SoporteRAG(claude))

    user = repo.get_or_create_user(args.telefono, "Prueba")
    result = await agente.handle(
        AgentContext(user=user, incoming=incoming, historial=[])
    )

    print(f"\n[intención: {result.intencion} · tool: {result.tool_llamada or '—'}]")
    print(f"\nLuca: {result.respuesta}")


if __name__ == "__main__":
    asyncio.run(main())

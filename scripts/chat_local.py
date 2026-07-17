"""Chat interactivo con Luca en la terminal (prueba local, sin Twilio).

Conversación multi-turno contra el agente REAL (Claude + Supabase de .env),
manteniendo el historial de la sesión en memoria — como chatear por WhatsApp
pero desde la consola. En cualquier momento puedes adjuntar un PDF o imagen.

Uso:
    python -m scripts.chat_local
    python -m scripts.chat_local --telefono +503XXXXXXXX --nombre Ana

Dentro del chat:
    /adjuntar C:\ruta\factura.pdf            manda un archivo (sin caption)
    /adjuntar C:\ruta\recibo.jpg esto fue ayer   ... con caption
    /salir                                    termina

Igual que scripts/probar_documento.py: lo que Luca registre queda de verdad
en Supabase bajo el usuario demo (+50300000001) salvo que pases --telefono.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import mimetypes
import sys
from pathlib import Path

from app.adapters.llm.claude import ClaudeProvider
from app.adapters.persistence.supabase_repo import SupabaseRepository
from app.application.agents.principal import MainAgent
from app.application.agents.soporte_rag import SoporteRAG
from app.domain.models import (
    AgentContext,
    IncomingMessage,
    MediaItem,
    Message,
    Rol,
)
from app.infra.config import Settings

# Consola de Windows (cp1252): sin esto revientan los emojis de Luca.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TELEFONO_DEMO = "+50300000001"


def _media_de_archivo(ruta: Path) -> MediaItem:
    content_type = mimetypes.guess_type(ruta.name)[0]
    if content_type is None:
        raise ValueError(f"No pude deducir el tipo de {ruta.name}; usa .pdf/.jpg/.png")
    return MediaItem(
        content_type=content_type,
        url=f"file://{ruta}",
        data_base64=base64.b64encode(ruta.read_bytes()).decode(),
        filename=ruta.name,
    )


def _parse_entrada(linea: str) -> tuple[str, list[MediaItem]]:
    """'/adjuntar ruta [caption]' → (caption, [media]); texto normal → (texto, [])."""
    if not linea.startswith("/adjuntar"):
        return linea, []
    resto = linea[len("/adjuntar"):].strip()
    if not resto:
        raise ValueError("Uso: /adjuntar <ruta> [caption]")
    # La ruta puede venir entre comillas (espacios en el nombre) o ser la
    # primera palabra; el resto es el caption.
    if resto.startswith('"'):
        cierre = resto.index('"', 1)
        ruta, caption = resto[1:cierre], resto[cierre + 1:].strip()
    else:
        partes = resto.split(maxsplit=1)
        ruta, caption = partes[0], partes[1] if len(partes) > 1 else ""
    archivo = Path(ruta)
    if not archivo.is_file():
        raise ValueError(f"No existe: {archivo}")
    return caption, [_media_de_archivo(archivo)]


async def main() -> None:
    ap = argparse.ArgumentParser(description="Chat interactivo local con Luca")
    ap.add_argument("--telefono", default=TELEFONO_DEMO)
    ap.add_argument("--nombre", default="Prueba", help="tu nombre (como el perfil de WhatsApp)")
    args = ap.parse_args()

    settings = Settings.from_env()
    repo = SupabaseRepository(settings.supabase_url, settings.supabase_key)
    claude = ClaudeProvider(
        settings.anthropic_api_key, settings.claude_model, settings.claude_max_tokens
    )
    agente = MainAgent(llm=claude, repo=repo, soporte=SoporteRAG(claude))
    user = repo.get_or_create_user(args.telefono, args.nombre)
    if args.nombre and user.nombre != args.nombre:
        # El usuario demo puede existir con otro nombre; para probar la regla de
        # transferencias (comparar nombres del voucher) se usa el de --nombre.
        user = user.model_copy(update={"nombre": args.nombre})

    historial: list[Message] = []
    print(f"Chat local con Luca — usuario {user.nombre} ({user.telefono}).")
    print("Escribe tu mensaje, /adjuntar <ruta> [caption] para mandar un archivo, /salir para terminar.\n")

    while True:
        try:
            linea = input("Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not linea:
            continue
        if linea.lower() in ("/salir", "/exit", "/quit"):
            break
        try:
            texto, media = _parse_entrada(linea)
        except ValueError as e:
            print(f"  ⚠ {e}")
            continue

        incoming = IncomingMessage(
            canal="whatsapp", telefono=user.telefono, texto=texto, media=media
        )
        historial.append(
            Message(user_id=user.id, rol=Rol.USUARIO, contenido=incoming.contenido_para_audit)
        )
        try:
            result = await agente.handle(
                AgentContext(user=user, incoming=incoming, historial=list(historial))
            )
        except Exception as e:
            print(f"  ⚠ Error del agente: {e}")
            historial.pop()
            continue
        historial.append(
            Message(user_id=user.id, rol=Rol.ASISTENTE, contenido=result.respuesta)
        )
        extra = f"  [tool: {result.tool_llamada}]" if result.tool_llamada else ""
        print(f"\nLuca: {result.respuesta}{extra}\n")


if __name__ == "__main__":
    asyncio.run(main())

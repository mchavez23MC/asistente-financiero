"""Páginas de la webapp como ENDPOINTS con URL limpia (rama webapp).

Nada de servir el árbol de archivos con rutas .html: cada vista es una ruta
explícita del API (los assets css/js sí van como estáticos bajo /assets).

Estructura de URLs:
  /                   Login (teléfono → código OTP por WhatsApp)
  /app/inicio         Dashboard
  /app/chat           Chat con Luca (pipeline real)
  /app/movimientos    Movimientos + bandeja por confirmar
  /app/presupuestos   Presupuestos (H2)
  /app/tickets        Mis tickets (H3)
  /legal              Términos y política de privacidad (LOPDP)
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

WEBAPP_DIR = Path(__file__).resolve().parents[3] / "webapp"

router = APIRouter()

# ruta → archivo (el archivo es un detalle interno; la URL es el contrato).
_PAGINAS: dict[str, str] = {
    "/": "index.html",
    "/app/inicio": "app/inicio.html",
    "/app/chat": "app/chat.html",
    "/app/movimientos": "app/movimientos.html",
    "/app/presupuestos": "app/presupuestos.html",
    "/app/tickets": "app/tickets.html",
    "/app/documentos": "app/documentos.html",
    "/app/perfil": "app/perfil.html",
    "/legal": "legal.html",
}


def _servir(relativo: str) -> FileResponse:
    archivo = WEBAPP_DIR / relativo
    if not archivo.exists():
        raise HTTPException(status_code=404)
    return FileResponse(archivo, media_type="text/html")


def _registrar(ruta: str, relativo: str) -> None:
    @router.get(ruta, include_in_schema=False)
    async def pagina(_relativo: str = relativo) -> FileResponse:
        return _servir(_relativo)


for _ruta, _archivo in _PAGINAS.items():
    _registrar(_ruta, _archivo)


# Ruta parametrizada (plan de documentos, módulo 04): sirve el mismo HTML; el JS
# lee el task_id de location.pathname. Se registra aparte para no tocar el dict
# de rutas fijas (riesgo R12).
@router.get("/app/revisar/{task_id}", include_in_schema=False)
async def revisar(task_id: str) -> FileResponse:
    return _servir("app/revisar.html")

"""API del perfil financiero verificable (plan de documentos, E5).

Auth de sesión existente (identidad SIEMPRE de la sesión — §7.3.2). El perfil se
calcula AL VUELO sobre las transacciones respaldadas: no hay tabla de perfil que
pueda divergir de la verdad. Sin LLM.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.application.perfil import calcular_perfil
from app.interfaces.api.webapp_api import _usuario_actual

router = APIRouter(prefix="/api")


@router.get("/perfil")
async def obtener_perfil(request: Request) -> JSONResponse:
    user = _usuario_actual(request)
    repo = request.app.state.repo
    try:
        movimientos = repo.movimientos_para_perfil(user.id)
    except Exception:
        # Con la migración de documentos ausente, degrada a "sin datos".
        return JSONResponse({"estado": "sin_datos", "perfil": calcular_perfil([])})
    return JSONResponse({"estado": "ok", "perfil": calcular_perfil(movimientos)})

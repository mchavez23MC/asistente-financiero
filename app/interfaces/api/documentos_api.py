"""API JSON de documentos y revisión (plan de documentos, módulo 04).

Mismo patrón de auth que webapp_api.py: la identidad SIEMPRE sale de la sesión
(nunca del payload — §7.3.2). Una tarea/documento de otro usuario devuelve 404
(no se filtra existencia).

Endpoints (v1 de la revisión de estados de cuenta):
  GET   /api/documentos                 lista de respaldos del usuario
  GET   /api/tareas                     tareas de revisión pendientes (+ badge)
  GET   /api/tareas/{id}                documento + items del staging
  PATCH /api/tareas/{id}/items          ediciones/aceptaciones en bloque
  POST  /api/tareas/{id}/confirmar      materializa los aceptados (idempotente)
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.domain.models import Transaction
from app.interfaces.api.webapp_api import _usuario_actual

router = APIRouter(prefix="/api")

_CAMPOS_EDITABLES = {"estado", "tipo", "categoria_sugerida", "fecha", "monto"}


def _uuid(valor: str) -> UUID:
    try:
        return UUID(str(valor))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="No encontrado")


@router.get("/documentos")
async def listar_documentos(request: Request) -> JSONResponse:
    user = _usuario_actual(request)
    repo = request.app.state.repo
    docs = repo.list_documents(user.id, limite=50)
    return JSONResponse(
        {
            "documentos": [
                {
                    "id": str(d.id),
                    "tipo": d.tipo_documento,
                    "status": d.status,
                    "emisor": d.emisor_nombre,
                    "fecha": d.fecha_emision.isoformat() if d.fecha_emision else None,
                    "total": float(d.total) if d.total is not None else None,
                    "archivo": d.filename,
                }
                for d in docs
            ]
        }
    )


@router.get("/tareas")
async def listar_tareas(request: Request) -> JSONResponse:
    user = _usuario_actual(request)
    tareas = request.app.state.repo.list_review_tasks(user.id, status="pendiente")
    return JSONResponse(
        {
            "pendientes": len(tareas),
            "tareas": [
                {
                    "id": str(t.id),
                    "tipo": t.tipo,
                    "resumen": t.resumen,
                    "created_at": t.created_at.isoformat(),
                }
                for t in tareas
            ],
        }
    )


@router.get("/tareas/{task_id}")
async def obtener_tarea(task_id: str, request: Request) -> JSONResponse:
    user = _usuario_actual(request)
    repo = request.app.state.repo
    tarea = repo.get_review_task(user.id, _uuid(task_id))
    if tarea is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    items = repo.list_document_items(tarea.document_id, user.id)
    return JSONResponse(
        {
            "id": str(tarea.id),
            "status": tarea.status,
            "resumen": tarea.resumen,
            "items": [
                {
                    "id": str(i.id),
                    "n_linea": i.n_linea,
                    "fecha": i.fecha.isoformat() if i.fecha else None,
                    "descripcion": i.descripcion_raw,
                    "monto": float(i.monto) if i.monto is not None else None,
                    "tipo": i.tipo,
                    "categoria": i.categoria_sugerida,
                    "confianza": i.confianza,
                    "estado": i.estado,
                }
                for i in items
            ],
        }
    )


@router.patch("/tareas/{task_id}/items")
async def editar_items(task_id: str, request: Request) -> JSONResponse:
    user = _usuario_actual(request)
    repo = request.app.state.repo
    tarea = repo.get_review_task(user.id, _uuid(task_id))
    if tarea is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    body = await request.json()
    cambios = []
    for item in body.get("items", []):
        if "id" not in item:
            continue
        limpio = {"id": item["id"]}
        for campo, valor in item.items():
            if campo in _CAMPOS_EDITABLES:
                limpio[campo] = valor
        cambios.append(limpio)
    repo.update_document_items(user.id, cambios)
    return JSONResponse({"actualizados": len(cambios)})


@router.post("/tareas/{task_id}/confirmar")
async def confirmar_tarea(task_id: str, request: Request) -> JSONResponse:
    """Materializa los items aceptados como transacciones confirmadas. Idempotente:
    una tarea ya completada devuelve 409 sin duplicar. La validación es
    server-side: solo se materializan items en estado 'aceptado' (nunca
    duplicados/rechazados), aunque el cliente diga otra cosa."""
    user = _usuario_actual(request)
    repo = request.app.state.repo
    tarea = repo.get_review_task(user.id, _uuid(task_id))
    if tarea is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    if tarea.status == "completada":
        return JSONResponse({"detail": "La tarea ya fue confirmada", "registrados": 0}, status_code=409)

    items = repo.list_document_items(tarea.document_id, user.id)
    aceptados = [i for i in items if i.estado == "aceptado" and i.monto and i.monto > 0]
    transacciones = [
        Transaction(
            user_id=user.id,
            tipo=i.tipo or "gasto",
            monto=i.monto,
            fecha=i.fecha,
            categoria=i.categoria_sugerida,
            comercio=i.descripcion_raw[:120],
            status="confirmada",
        )
        for i in aceptados
    ]
    repo.insert_transactions_batch(transacciones, document_id=tarea.document_id)
    repo.complete_review_task(user.id, tarea.id)

    # Aviso por WhatsApp (best-effort, tolera fallo de entrega — riesgo R7).
    try:
        await request.app.state.channel.send(
            user, f"Listo ✅ registré {len(transacciones)} movimientos de tu estado de cuenta."
        )
    except Exception:
        pass
    return JSONResponse({"registrados": len(transacciones)})

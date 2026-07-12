"""API JSON para la webapp de Luca (rama webapp) — centro de vista del usuario.

Expone el MISMO núcleo que WhatsApp/chat web, pero como API para el frontend
HTML de `webapp/`:

  - POST /api/chat    → pipeline completo (consentimiento + guardrail + agente),
                        idéntico a /chat/send pero con la identidad del cliente.
  - GET  /api/estado  → snapshot del usuario: movimientos, presupuestos (con
                        gastado calculado POR EL SISTEMA, §1.2), tickets y resumen.

Identidad (demo): el teléfono E.164 que la webapp guarda en localStorage tras el
"login". Sin contraseñas — la identidad natural del producto es el teléfono,
igual que en WhatsApp (§3.1). En producción esto se reemplaza por sesión/OTP.
El aislamiento por usuario se mantiene: todo se filtra por el user_id resuelto
desde ese teléfono en el Repository (§7.3.2).
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.adapters.channels.web_chat import WebChatCapturingChannel
from app.application.process_message import ProcessMessage
from app.domain.models import E164_PATTERN, Budget

router = APIRouter(prefix="/api")


def _telefono_valido(telefono: str) -> str:
    telefono = (telefono or "").strip().replace(" ", "")
    if not re.match(E164_PATTERN, telefono):
        raise HTTPException(status_code=422, detail="Teléfono inválido (formato E.164, ej. +593987651234)")
    return telefono


@router.post("/chat")
async def chat(request: Request) -> JSONResponse:
    """Mismo pipeline que el chat web plan B (§9), con la identidad del cliente."""
    body = await request.json()
    telefono = _telefono_valido(body.get("telefono", ""))
    canal = WebChatCapturingChannel(telefono)

    pm = ProcessMessage(
        repo=request.app.state.repo,
        guardrail=request.app.state.guardrail,
        registry=request.app.state.registry,
        channel=canal,
    )
    incoming = canal.parse({"texto": body.get("texto", ""), "telefono": telefono})
    if incoming.texto:
        await pm(incoming)
    return JSONResponse({"respuestas": canal.enviados})


@router.post("/presupuestos")
async def crear_presupuesto(request: Request) -> JSONResponse:
    """Crea/actualiza un presupuesto (H2: 'definir al menos un presupuesto
    mensual por categoría' con 'umbral configurable'). Upsert por
    (usuario, categoría, periodo)."""
    body = await request.json()
    telefono = _telefono_valido(body.get("telefono", ""))
    repo = request.app.state.repo
    user = repo.get_or_create_user(telefono)
    try:
        budget = Budget(
            user_id=user.id,
            categoria=str(body.get("categoria", "")).strip().lower(),
            monto_limite=body.get("monto_limite"),
            periodo=body.get("periodo", "mensual"),
            umbral_alerta=float(body.get("umbral_alerta", 0.8)),
        )
    except Exception as exc:  # validación Pydantic → 422 legible
        raise HTTPException(status_code=422, detail=str(exc))
    if not budget.categoria:
        raise HTTPException(status_code=422, detail="Falta la categoría")
    guardado = repo.save_budget(budget)
    return JSONResponse({"id": str(guardado.id), "categoria": guardado.categoria}, status_code=201)


@router.get("/estado")
async def estado(request: Request, telefono: str) -> JSONResponse:
    """Snapshot para el panel del usuario. Los números los calcula el sistema
    (sum_gastos en Postgres), nunca el modelo — coherente con H2 grounded."""
    telefono = _telefono_valido(telefono)
    repo = request.app.state.repo
    user = repo.get_or_create_user(telefono)

    transactions = repo.list_transactions(user.id, limit=100)
    budgets = []
    for b in repo.get_budgets(user.id):
        gastado = repo.sum_gastos(user.id, categoria=b.categoria, periodo=b.periodo)
        budgets.append(
            {
                "id": str(b.id),
                "categoria": b.categoria,
                "monto_limite": float(b.monto_limite),
                "periodo": b.periodo,
                "umbral_alerta": b.umbral_alerta,
                "gastado": float(gastado),
            }
        )
    tickets = [t for t in repo.list_tickets() if t.user_id == user.id]
    gastado_mes = repo.sum_gastos(user.id, periodo="mensual")

    return JSONResponse(
        {
            "user": {
                "telefono": user.telefono,
                "nombre": user.nombre,
                "consentimiento": user.tiene_consentimiento,
            },
            "resumen": {"gastado_mes": float(gastado_mes)},
            "transactions": [
                {
                    "id": str(t.id),
                    "monto": float(t.monto) if t.monto is not None else None,
                    "fecha": t.fecha.isoformat() if t.fecha else None,
                    "categoria": t.categoria,
                    "comercio": t.comercio,
                    "status": t.status,
                    "created_at": t.created_at.isoformat(),
                }
                for t in transactions
            ],
            "budgets": budgets,
            "tickets": [
                {
                    "id": str(t.id),
                    "motivo": t.motivo,
                    "prioridad": t.prioridad,
                    "estado": t.estado,
                    "contexto": t.contexto,
                    "created_at": t.created_at.isoformat(),
                }
                for t in tickets
            ],
        }
    )

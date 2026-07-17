"""API JSON de la webapp de Luca (rama webapp) — con autenticación OTP.

Flujo de acceso (diseño en app/application/auth.py):
  1. POST /api/auth/solicitar {telefono}         → envía OTP por WhatsApp (202)
  2. POST /api/auth/verificar {telefono, codigo} → {token} de sesión (200)
  3. Resto de endpoints: header `Authorization: Bearer <token>`; la identidad
     sale SIEMPRE de la sesión — el cliente no puede pedir datos de otro número.

Endpoints autenticados:
  - POST /api/chat {texto}      → pipeline completo (guardrail + agente Claude)
  - GET  /api/estado            → snapshot (números calculados por el sistema)
  - POST /api/presupuestos      → crear/actualizar presupuesto (H2)
  - POST /api/auth/salir        → revoca la sesión
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.adapters.channels.web_chat import WebChatCapturingChannel
from app.application.auth import CooldownActivo
from app.application.process_message import ProcessMessage
from app.domain.models import Budget, User

router = APIRouter(prefix="/api")


def _normalizar_ec(raw: str) -> str | None:
    """Normaliza con generosidad a un celular ecuatoriano, o None si no lo es.
    Acepta 0987654321, 987654321, +593987654321, 593…, 00593…; ignora espacios
    y guiones. La defensa REAL vive aquí (nunca se confía en el front): un
    número de otro país no puede salir de este formulario (evita el SMS pumping
    a números premium extranjeros — brecha S1). v1 = solo Ecuador, solo celular."""
    digitos = re.sub(r"\D", "", raw or "")
    for prefijo in ("00593", "593"):
        if digitos.startswith(prefijo):
            digitos = digitos[len(prefijo):]
            break
    digitos = re.sub(r"^0", "", digitos)  # 0 inicial del formato local
    # Celular EC: 9 dígitos empezando en 9 (los fijos no tienen WhatsApp).
    if re.fullmatch(r"9\d{8}", digitos):
        return "+593" + digitos
    return None


def _telefono_valido(telefono: str) -> str:
    normalizado = _normalizar_ec(telefono)
    if normalizado is None:
        raise HTTPException(
            status_code=422,
            detail="Necesito un celular ecuatoriano válido: 9 dígitos que empiezan en 9 (ej. 0987654321).",
        )
    return normalizado


def _bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    return auth[7:] if auth.startswith("Bearer ") else ""


def _usuario_actual(request: Request) -> User:
    """Resuelve la sesión → User. 401 si no hay sesión válida (fail-closed)."""
    user = request.app.state.auth.usuario_de_token(_bearer(request))
    if user is None:
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada")
    return user


# ------------------------------------------------------------------ auth
@router.post("/auth/solicitar")
async def solicitar_codigo(request: Request) -> JSONResponse:
    body = await request.json()
    telefono = _telefono_valido(body.get("telefono", ""))
    try:
        await request.app.state.auth.solicitar_codigo(telefono)
    except CooldownActivo as cd:
        return JSONResponse(
            {"detail": f"Espera {cd.segundos}s para pedir otro código", "reintentar_en": cd.segundos},
            status_code=429,
        )
    # 202 siempre: no se revela si el número tiene cuenta.
    return JSONResponse({"enviado": True, "canal": "whatsapp"}, status_code=202)


@router.post("/auth/verificar")
async def verificar_codigo(request: Request) -> JSONResponse:
    body = await request.json()
    telefono = _telefono_valido(body.get("telefono", ""))
    token = await request.app.state.auth.verificar_codigo(
        telefono,
        str(body.get("codigo", "")),
        recordar=bool(body.get("recordar", False)),
    )
    if token is None:
        raise HTTPException(status_code=401, detail="Código incorrecto o expirado")
    return JSONResponse({"token": token})


@router.post("/auth/salir")
async def salir(request: Request) -> JSONResponse:
    request.app.state.auth.cerrar_sesion(_bearer(request))
    return JSONResponse({"ok": True})


# ------------------------------------------------------------------ chat
@router.post("/chat")
async def chat(request: Request) -> JSONResponse:
    """Mismo pipeline que WhatsApp (consentimiento → guardrail → agente),
    con la identidad tomada de la sesión."""
    user = _usuario_actual(request)
    body = await request.json()
    canal = WebChatCapturingChannel(user.telefono)

    pm = ProcessMessage(
        repo=request.app.state.repo,
        guardrail=request.app.state.guardrail,
        registry=request.app.state.registry,
        channel=canal,
    )
    incoming = canal.parse({"texto": body.get("texto", "")})
    if incoming.texto:
        await pm(incoming)
    return JSONResponse({"respuestas": canal.enviados})


# ------------------------------------------------------------------ estado
@router.get("/estado")
async def estado(request: Request) -> JSONResponse:
    """Snapshot para el panel del usuario de la sesión. Los números los calcula
    el sistema (sum_gastos en Postgres), nunca el modelo — H2 grounded."""
    user = _usuario_actual(request)
    repo = request.app.state.repo

    transactions = repo.list_transactions(user.id, limite=100, solo_confirmadas=False)
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
    ingresos_mes = repo.sum_ingresos(user.id, periodo="mensual")

    return JSONResponse(
        {
            "user": {
                "telefono": user.telefono,
                "nombre": user.nombre,
                "consentimiento": user.tiene_consentimiento,
            },
            "resumen": {
                "gastado_mes": float(gastado_mes),
                "ingresos_mes": float(ingresos_mes),
                "balance_mes": float(ingresos_mes - gastado_mes),
            },
            "transactions": [
                {
                    "id": str(t.id),
                    "tipo": t.tipo,
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


# --------------------------------------------------------------- presupuestos
@router.post("/presupuestos")
async def crear_presupuesto(request: Request) -> JSONResponse:
    """Crea/actualiza un presupuesto (H2: umbral configurable). Upsert por
    (usuario de la sesión, categoría, periodo)."""
    user = _usuario_actual(request)
    body = await request.json()
    repo = request.app.state.repo
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

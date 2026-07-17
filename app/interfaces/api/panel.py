"""Panel humano (§5) — Fase 6 + handoff IA↔humano. FastAPI + Jinja2 + HTMX.

Vistas: cola de tickets AGRUPADA POR USUARIO (con polling HTMX), detalle
(conversación en tiempo real con tres voces —usuario, Luca IA y agente humano—,
contexto de escalación, tomar/cerrar y responder) y audit trail. Auth mínima:
basic auth contra credenciales de entorno.

Handoff (plan-paginas/08):
- Al RESPONDER o TOMAR, el ticket pasa a 'en_proceso' → el orquestador pausa la
  IA para ese usuario (`_en_atencion_humana`). Los dos no hablan a la vez.
- Al CERRAR (en_proceso → resuelto/cerrado), se avisa al usuario en la voz de
  Luca que la IA retoma. Deja de haber 'en_proceso' → la IA se reactiva sola.
- Cada respuesta humana se GUARDA como mensaje del asistente (tool='panel_humano')
  → entra al audit trail y la IA la ve en su contexto al retomar (bug B1).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from jinja2 import DictLoader, Environment, select_autoescape

from app.domain.models import Message, Rol, TicketEstado

router = APIRouter(prefix="/panel")
security = HTTPBasic()

# Ecuador = UTC-5, sin horario de verano: convertir restando 5h (evita depender
# de tzdata en Windows). Las fechas de la BD son UTC (timestamptz).
_EC_OFFSET = timedelta(hours=-5)

# Mensaje de transición al cerrar el handoff: la IA retoma en la voz de Luca.
MSG_IA_RETOMA = (
    "Listo 🙌 mi compañero de equipo ya te ayudó con eso. Sigo yo por aquí "
    "para lo de tus gastos, ingresos y presupuesto, como siempre."
)

_ESTADOS_VALIDOS = {e.value for e in TicketEstado}


def _fmt_ec(dt: datetime | None, fmt: str = "%d/%m %H:%M") -> str:
    """Formatea una fecha en hora de Ecuador (UTC-5)."""
    if dt is None:
        return "—"
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return (dt + _EC_OFFSET).strftime(fmt)


def _hace(dt: datetime | None) -> str:
    """Antigüedad relativa ('hace 5 días', 'hace 2 h', 'hace 10 min')."""
    if dt is None:
        return ""
    ahora = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    seg = (ahora - dt).total_seconds()
    if seg < 60:
        return "ahora"
    if seg < 3600:
        return f"hace {int(seg // 60)} min"
    if seg < 86400:
        return f"hace {int(seg // 3600)} h"
    return f"hace {int(seg // 86400)} d"


def _mask_tel(telefono: str | None) -> str:
    """Teléfono enmascarado (privacidad): +593 ••• ••34."""
    if not telefono:
        return "—"
    return f"{telefono[:4]} ••• ••{telefono[-2:]}" if len(telefono) >= 6 else telefono


_PRIORIDAD_PESO = {"alta": 0, "media": 1, "baja": 2}

# --- plantillas (Tailwind por CDN, HTMX por CDN; sin build step, §5) ----------
_BASE = """
<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}Panel{% endblock %} · Luca</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/htmx.org@1.9.10"></script>
</head><body class="bg-slate-100 text-slate-800">
<nav style="background:#1F3A5F" class="text-white px-6 py-3 flex gap-6 items-center">
  <span class="font-bold">Luca · Panel humano</span>
  <a href="/panel" class="hover:underline">Tickets</a>
  <a href="/panel/audit" class="hover:underline">Audit trail</a>
</nav>
<main class="max-w-6xl mx-auto p-6">{% block body %}{% endblock %}</main>
</body></html>
"""

_COLA = """
{% extends "base" %}{% block title %}Cola de tickets{% endblock %}
{% block body %}
<div class="flex items-center justify-between mb-4 flex-wrap gap-3">
  <h1 class="text-2xl font-bold">Cola de tickets</h1>
  <div class="flex gap-2 text-sm flex-wrap">
    {% for e in ['','abierto','en_proceso','resuelto','cerrado'] %}
    <a href="/panel?estado={{e}}"
       class="px-3 py-1 rounded {{ 'bg-slate-900 text-white' if estado==e else 'bg-white border' }}">
       {{ e or 'todos' }} <span class="opacity-60">({{ conteos.get(e, 0) }})</span></a>
    {% endfor %}
  </div>
</div>
<div hx-get="/panel/cola{{ '?estado='+estado if estado else '' }}"
     hx-trigger="every 10s" hx-swap="innerHTML">
  {% include "cola_grupos" %}
</div>
{% endblock %}
"""

# Cola AGRUPADA POR USUARIO: una tarjeta por persona, expandible a sus tickets.
_COLA_GRUPOS = """
<div class="space-y-3">
{% for g in grupos %}
<div class="bg-white rounded shadow {{ 'ring-2 ring-red-300' if g.max_prioridad=='alta' }}">
  <div class="flex items-center justify-between p-4 border-b">
    <div class="flex items-center gap-3">
      <span class="w-9 h-9 rounded-full grid place-items-center text-white font-bold"
            style="background:#1F3A5F">{{ g.inicial }}</span>
      <div>
        <div class="font-semibold">{{ g.nombre }}
          {% if g.en_atencion %}
          <span class="ml-1 px-2 py-0.5 rounded-full text-xs bg-emerald-100 text-emerald-700">👤 humano</span>
          {% else %}
          <span class="ml-1 px-2 py-0.5 rounded-full text-xs bg-slate-100 text-slate-500">🤖 bot</span>
          {% endif %}
        </div>
        <div class="text-xs text-slate-500">{{ g.telefono }} · {{ g.n }} ticket(s) · último {{ g.ultimo_hace }}</div>
      </div>
    </div>
    <span class="px-2 py-0.5 rounded text-xs
      {{ 'bg-red-100 text-red-700' if g.max_prioridad=='alta'
         else 'bg-amber-100 text-amber-700' if g.max_prioridad=='media'
         else 'bg-slate-100 text-slate-600' }}">prioridad {{ g.max_prioridad }}</span>
  </div>
  <table class="w-full text-sm">
  <tbody>
  {% for t in g.tickets %}
    <tr class="border-b last:border-0 hover:bg-slate-50 cursor-pointer"
        onclick="location.href='/panel/ticket/{{ t.id }}'">
      <td class="p-3 text-slate-500 w-28">{{ t.created_at | ec }}</td>
      <td class="p-3">{{ t.motivo }}</td>
      <td class="p-3 text-slate-500 max-w-xs truncate">{{ (t.contexto or '')[:70] }}</td>
      <td class="p-3">
        <span class="px-2 py-0.5 rounded text-xs
          {{ 'bg-blue-100 text-blue-700' if t.estado=='abierto'
             else 'bg-emerald-100 text-emerald-700' if t.estado=='en_proceso'
             else 'bg-slate-100 text-slate-500' }}">{{ t.estado }}</span>
      </td>
      <td class="p-3 text-right"><span class="text-blue-600">abrir →</span></td>
    </tr>
  {% endfor %}
  </tbody></table>
</div>
{% else %}
<div class="p-6 text-center text-slate-400 bg-white rounded shadow">Sin tickets.</div>
{% endfor %}
</div>
"""

_DETALLE = """
{% extends "base" %}{% block title %}Ticket{% endblock %}
{% block body %}
<a href="/panel" class="text-blue-600 hover:underline text-sm">← volver a la cola</a>
<div class="grid md:grid-cols-3 gap-6 mt-4">
  <section class="md:col-span-2 bg-white rounded shadow p-5">
    <div class="flex items-center justify-between mb-3">
      <h2 class="font-bold">Conversación · {{ nombre }}</h2>
      {% if ticket.estado=='en_proceso' %}
      <span class="px-2 py-0.5 rounded-full text-xs bg-emerald-100 text-emerald-700">👤 IA en pausa — la atiendes tú</span>
      {% else %}
      <span class="px-2 py-0.5 rounded-full text-xs bg-slate-100 text-slate-500">🤖 la atiende Luca</span>
      {% endif %}
    </div>
    <!-- hilo en tiempo real: se refresca solo cada 3s -->
    <div hx-get="/panel/ticket/{{ ticket.id }}/hilo" hx-trigger="every 3s" hx-swap="innerHTML"
         class="max-h-[60vh] overflow-y-auto pr-1">
      {% include "hilo" %}
    </div>
    <form method="post" action="/panel/ticket/{{ ticket.id }}/responder" class="mt-4 flex gap-2">
      <input name="texto" required autocomplete="off"
             placeholder="Responder al usuario por WhatsApp… (esto pausa a Luca)"
             class="flex-1 border rounded px-3 py-2 text-sm">
      <button class="bg-blue-600 text-white px-4 rounded text-sm">Enviar</button>
    </form>
  </section>
  <aside class="bg-white rounded shadow p-5 text-sm space-y-3">
    <div class="flex gap-2">
      {% if ticket.estado!='en_proceso' %}
      <form method="post" action="/panel/ticket/{{ ticket.id }}/estado" class="flex-1">
        <input type="hidden" name="estado" value="en_proceso">
        <button class="w-full bg-emerald-600 text-white px-3 py-2 rounded">Tomar conversación</button>
      </form>
      {% else %}
      <form method="post" action="/panel/ticket/{{ ticket.id }}/estado" class="flex-1">
        <input type="hidden" name="estado" value="resuelto">
        <button class="w-full bg-slate-900 text-white px-3 py-2 rounded">Cerrar y devolver a Luca</button>
      </form>
      {% endif %}
    </div>
    <div class="pt-2 border-t"><span class="text-slate-500">Motivo:</span> <b>{{ ticket.motivo }}</b></div>
    <div><span class="text-slate-500">Prioridad:</span> {{ ticket.prioridad }}</div>
    <div><span class="text-slate-500">Estado:</span> {{ ticket.estado }}</div>
    <div><span class="text-slate-500">Creado:</span> {{ ticket.created_at | ec('%d/%m/%Y %H:%M') }} ({{ ticket.created_at | hace }})</div>
    <div><span class="text-slate-500">Contexto de escalación:</span>
      <p class="mt-1 bg-slate-50 p-2 rounded">{{ ticket.contexto }}</p></div>
    <form method="post" action="/panel/ticket/{{ ticket.id }}/estado" class="flex gap-2 pt-2 border-t">
      <select name="estado" class="border rounded px-2 py-1 flex-1">
        {% for e in ['abierto','en_proceso','resuelto','cerrado'] %}
        <option value="{{ e }}" {{ 'selected' if ticket.estado==e }}>{{ e }}</option>
        {% endfor %}
      </select>
      <button class="bg-slate-700 text-white px-3 rounded">Cambiar estado</button>
    </form>
    {% if aviso %}<p class="text-green-600">{{ aviso }}</p>{% endif %}
  </aside>
</div>
{% endblock %}
"""

# Hilo con TRES voces: usuario (der.), Luca IA (izq. gris), agente humano
# (izq. verde, etiqueta 'Agente'). El humano = mensaje del asistente con
# tool_llamada 'panel_humano'/'panel_transicion'.
_HILO = """
<div class="space-y-2">
{% for m in mensajes %}
  {% set es_humano = m.tool_llamada in ('panel_humano','panel_transicion') %}
  <div class="flex {{ 'justify-end' if m.rol=='user' else 'justify-start' }}">
    <div class="max-w-[80%] px-3 py-2 rounded-lg text-sm
         {{ 'bg-blue-600 text-white' if m.rol=='user'
            else 'bg-emerald-100 text-emerald-900 border border-emerald-200' if es_humano
            else 'bg-slate-100' }}">
      {% if es_humano %}<div class="text-[11px] font-semibold text-emerald-700 mb-0.5">👤 Agente</div>{% endif %}
      {{ m.contenido }}
      {% if m.tool_llamada and not es_humano %}<div class="text-xs opacity-60 mt-1">tool: {{ m.tool_llamada }}</div>{% endif %}
      <div class="text-[10px] opacity-50 mt-1 text-right">{{ m.timestamp | ec('%H:%M') }}</div>
    </div>
  </div>
{% else %}<p class="text-slate-400">Sin mensajes.</p>{% endfor %}
</div>
"""

_AUDIT = """
{% extends "base" %}{% block title %}Audit trail{% endblock %}
{% block body %}
<h1 class="text-2xl font-bold mb-4">Audit trail (últimos mensajes)</h1>
<table class="w-full bg-white rounded shadow text-sm">
<thead class="text-left bg-slate-50 border-b"><tr>
  <th class="p-3">Hora (EC)</th><th class="p-3">Usuario</th><th class="p-3">Rol</th>
  <th class="p-3">Intención</th><th class="p-3">Tool</th><th class="p-3">Contenido</th></tr></thead>
<tbody>
{% for m in mensajes %}
<tr class="border-b {{ 'bg-emerald-50' if m.tool_llamada in ('panel_humano','panel_transicion')
                       else 'bg-amber-50' if m.intencion=='sensible' }}">
  <td class="p-3 text-slate-500 whitespace-nowrap">{{ m.timestamp | ec }}</td>
  <td class="p-3 text-slate-500 whitespace-nowrap">{{ usuarios.get(m.user_id, '—') }}</td>
  <td class="p-3">{{ 'agente' if m.tool_llamada in ('panel_humano','panel_transicion') else m.rol }}</td>
  <td class="p-3">{{ m.intencion or '—' }}</td>
  <td class="p-3">{{ m.tool_llamada or '—' }}</td>
  <td class="p-3" title="{{ m.contenido }}">{{ m.contenido[:90] }}</td>
</tr>
{% else %}<tr><td colspan="6" class="p-6 text-center text-slate-400">Sin actividad.</td></tr>{% endfor %}
</tbody></table>
{% endblock %}
"""

_env = Environment(
    loader=DictLoader(
        {
            "base": _BASE,
            "cola": _COLA,
            "cola_grupos": _COLA_GRUPOS,
            "detalle": _DETALLE,
            "hilo": _HILO,
            "audit": _AUDIT,
        }
    ),
    autoescape=select_autoescape(["html"]),
)
_env.filters["ec"] = _fmt_ec
_env.filters["hace"] = _hace


def _auth(request: Request, creds: HTTPBasicCredentials = Depends(security)) -> None:
    esperado = request.app.state.panel_auth  # (user, password)
    ok_user = secrets.compare_digest(creds.username, esperado[0])
    ok_pass = secrets.compare_digest(creds.password, esperado[1])
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autorizado",
            headers={"WWW-Authenticate": "Basic"},
        )


def _render(_tpl: str, **ctx) -> HTMLResponse:
    return HTMLResponse(_env.get_template(_tpl).render(**ctx))


def _agrupar(repo, estado: str):
    """Agrupa los tickets (filtrados por estado) por usuario, con datos derivados
    para la tarjeta: nombre, teléfono enmascarado, prioridad máxima, si está en
    atención humana (algún ticket en_proceso), y orden por prioridad + reciente."""
    tickets = repo.list_tickets(estado or None)
    por_user: dict = {}
    for t in tickets:
        por_user.setdefault(t.user_id, []).append(t)

    grupos = []
    for uid, ts in por_user.items():
        ts.sort(key=lambda t: t.created_at, reverse=True)
        user = repo.get_user(uid)
        nombre = (user.nombre if user and user.nombre and user.nombre != "Web" else None) or "Usuario"
        max_prio = min((_PRIORIDAD_PESO.get(t.prioridad, 1) for t in ts), default=1)
        prio_txt = next((k for k, v in _PRIORIDAD_PESO.items() if v == max_prio), "media")
        grupos.append(
            {
                "user_id": uid,
                "nombre": nombre,
                "inicial": nombre[0].upper(),
                "telefono": _mask_tel(user.telefono if user else None),
                "tickets": ts,
                "n": len(ts),
                "max_prioridad": prio_txt,
                "en_atencion": any(t.estado == "en_proceso" for t in ts),
                "ultimo_hace": _hace(ts[0].created_at),
                "_orden": (max_prio, -ts[0].created_at.timestamp()),
            }
        )
    grupos.sort(key=lambda g: g["_orden"])
    return grupos


@router.get("", response_class=HTMLResponse)
async def cola(request: Request, estado: str = "", _: None = Depends(_auth)) -> HTMLResponse:
    repo = request.app.state.repo
    conteos = {"": len(repo.list_tickets(None))}
    for e in ("abierto", "en_proceso", "resuelto", "cerrado"):
        conteos[e] = len(repo.list_tickets(e))
    return _render("cola", grupos=_agrupar(repo, estado), estado=estado, conteos=conteos)


@router.get("/cola", response_class=HTMLResponse)
async def cola_parcial(request: Request, estado: str = "", _: None = Depends(_auth)) -> HTMLResponse:
    """Fragmento para el polling HTMX (solo los grupos)."""
    return _render("cola_grupos", grupos=_agrupar(request.app.state.repo, estado), estado=estado)


@router.get("/ticket/{ticket_id}", response_class=HTMLResponse)
async def detalle(request: Request, ticket_id: UUID, aviso: str = "", _: None = Depends(_auth)) -> HTMLResponse:
    repo = request.app.state.repo
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    user = repo.get_user(ticket.user_id)
    nombre = (user.nombre if user and user.nombre and user.nombre != "Web" else None) or "Usuario"
    mensajes = repo.get_last_n_messages(ticket.user_id, 30)
    return _render("detalle", ticket=ticket, mensajes=mensajes, nombre=nombre, aviso=aviso)


@router.get("/ticket/{ticket_id}/hilo", response_class=HTMLResponse)
async def hilo_parcial(request: Request, ticket_id: UUID, _: None = Depends(_auth)) -> HTMLResponse:
    """Fragmento del hilo de conversación para el polling en tiempo real."""
    repo = request.app.state.repo
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    return _render("hilo", mensajes=repo.get_last_n_messages(ticket.user_id, 30))


@router.post("/ticket/{ticket_id}/estado")
async def cambiar_estado(request: Request, ticket_id: UUID, estado: str = Form(...), _: None = Depends(_auth)):
    repo = request.app.state.repo
    if estado not in _ESTADOS_VALIDOS:  # B5: no reventar contra el check de la BD
        raise HTTPException(status_code=422, detail=f"Estado inválido: {estado}")
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    previo = ticket.estado
    repo.update_ticket_estado(ticket_id, estado)
    aviso = "Estado actualizado."
    # Handoff cerrado: la IA retoma → avisar al usuario en la voz de Luca.
    if previo == "en_proceso" and estado in ("resuelto", "cerrado"):
        user = repo.get_user(ticket.user_id)
        if user is not None:
            try:
                await request.app.state.channel.send(user, MSG_IA_RETOMA)
                repo.save_message(
                    Message(
                        user_id=user.id,
                        rol=Rol.ASISTENTE,
                        contenido=MSG_IA_RETOMA,
                        tool_llamada="panel_transicion",
                    )
                )
                aviso = "Cerrado. Luca retoma la conversación."
            except Exception:
                aviso = "Cerrado, pero no se pudo avisar al usuario (¿ventana de 24h?)."
    return RedirectResponse(f"/panel/ticket/{ticket_id}?aviso={aviso}", status_code=303)


@router.post("/ticket/{ticket_id}/responder")
async def responder(request: Request, ticket_id: UUID, texto: str = Form(...), _: None = Depends(_auth)):
    repo = request.app.state.repo
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    user = repo.get_user(ticket.user_id)
    aviso = "Respuesta enviada."
    try:
        await request.app.state.channel.send(user, texto)
        # B1: la respuesta humana ENTRA al audit trail y al historial (la IA la
        # verá al retomar). Marcada con tool 'panel_humano' → burbuja de 'Agente'.
        repo.save_message(
            Message(
                user_id=user.id,
                rol=Rol.ASISTENTE,
                contenido=texto,
                tool_llamada="panel_humano",
            )
        )
        # Responder = tomar la conversación: pausa a la IA mientras se atiende.
        if ticket.estado == "abierto":
            repo.update_ticket_estado(ticket_id, "en_proceso")
    except Exception:
        # Ventana de 24h de WhatsApp cerrada u otro error de envío (§7.6).
        aviso = "No se pudo enviar (¿ventana de 24h cerrada?)."
    return RedirectResponse(f"/panel/ticket/{ticket_id}?aviso={aviso}", status_code=303)


@router.get("/audit", response_class=HTMLResponse)
async def audit(request: Request, _: None = Depends(_auth)) -> HTMLResponse:
    repo = request.app.state.repo
    mensajes = repo.recent_messages(100)
    # Mapa user_id → teléfono enmascarado (V10): seguir una conversación en el audit.
    usuarios: dict = {}
    for uid in {m.user_id for m in mensajes}:
        u = repo.get_user(uid)
        usuarios[uid] = _mask_tel(u.telefono if u else None)
    return _render("audit", mensajes=mensajes, usuarios=usuarios)

"""Panel humano (§5) — Fase 6. FastAPI + Jinja2 + HTMX, Tailwind por CDN.

Tres vistas: cola de tickets (con polling HTMX), detalle (conversación +
contexto de escalación + cambiar estado + responder al usuario) y audit trail
(lectura de `messages` con intención y tool). Vive en el mismo contenedor que
el webhook (§5). Auth mínima: basic auth contra credenciales de entorno.
"""

from __future__ import annotations

import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from jinja2 import DictLoader, Environment, select_autoescape

router = APIRouter(prefix="/panel")
security = HTTPBasic()

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
<main class="max-w-5xl mx-auto p-6">{% block body %}{% endblock %}</main>
</body></html>
"""

_COLA = """
{% extends "base" %}{% block title %}Cola de tickets{% endblock %}
{% block body %}
<div class="flex items-center justify-between mb-4">
  <h1 class="text-2xl font-bold">Cola de tickets</h1>
  <div class="flex gap-2 text-sm">
    {% for e in ['','abierto','en_proceso','resuelto','cerrado'] %}
    <a href="/panel?estado={{e}}"
       class="px-3 py-1 rounded {{ 'bg-slate-900 text-white' if estado==e else 'bg-white border' }}">
       {{ e or 'todos' }}</a>
    {% endfor %}
  </div>
</div>
<div hx-get="/panel/cola{{ '?estado='+estado if estado else '' }}"
     hx-trigger="every 10s" hx-swap="innerHTML">
  {% include "cola_tbody" %}
</div>
{% endblock %}
"""

_COLA_TBODY = """
<table class="w-full bg-white rounded shadow text-sm">
<thead class="text-left bg-slate-50 border-b"><tr>
  <th class="p-3">Creado</th><th class="p-3">Motivo</th><th class="p-3">Prioridad</th>
  <th class="p-3">Estado</th><th class="p-3"></th></tr></thead>
<tbody>
{% for t in tickets %}
<tr class="border-b hover:bg-slate-50">
  <td class="p-3 text-slate-500">{{ t.created_at.strftime('%d/%m %H:%M') }}</td>
  <td class="p-3">{{ t.motivo }}</td>
  <td class="p-3">
    <span class="px-2 py-0.5 rounded text-xs
      {{ 'bg-red-100 text-red-700' if t.prioridad=='alta'
         else 'bg-amber-100 text-amber-700' if t.prioridad=='media'
         else 'bg-slate-100 text-slate-600' }}">{{ t.prioridad }}</span>
  </td>
  <td class="p-3">{{ t.estado }}</td>
  <td class="p-3"><a class="text-blue-600 hover:underline" href="/panel/ticket/{{ t.id }}">abrir →</a></td>
</tr>
{% else %}
<tr><td colspan="5" class="p-6 text-center text-slate-400">Sin tickets.</td></tr>
{% endfor %}
</tbody></table>
"""

_DETALLE = """
{% extends "base" %}{% block title %}Ticket{% endblock %}
{% block body %}
<a href="/panel" class="text-blue-600 hover:underline text-sm">← volver a la cola</a>
<div class="grid md:grid-cols-3 gap-6 mt-4">
  <section class="md:col-span-2 bg-white rounded shadow p-5">
    <h2 class="font-bold mb-3">Conversación</h2>
    <div class="space-y-2">
    {% for m in mensajes %}
      <div class="flex {{ 'justify-end' if m.rol=='user' else 'justify-start' }}">
        <div class="max-w-[80%] px-3 py-2 rounded-lg text-sm
             {{ 'bg-blue-600 text-white' if m.rol=='user' else 'bg-slate-100' }}">
          {{ m.contenido }}
          {% if m.tool_llamada %}<div class="text-xs opacity-60 mt-1">tool: {{ m.tool_llamada }}</div>{% endif %}
        </div>
      </div>
    {% else %}<p class="text-slate-400">Sin mensajes.</p>{% endfor %}
    </div>
    <form method="post" action="/panel/ticket/{{ ticket.id }}/responder" class="mt-4 flex gap-2">
      <input name="texto" required placeholder="Responder al usuario por WhatsApp…"
             class="flex-1 border rounded px-3 py-2 text-sm">
      <button class="bg-blue-600 text-white px-4 rounded text-sm">Enviar</button>
    </form>
  </section>
  <aside class="bg-white rounded shadow p-5 text-sm space-y-3">
    <div><span class="text-slate-500">Motivo:</span> <b>{{ ticket.motivo }}</b></div>
    <div><span class="text-slate-500">Prioridad:</span> {{ ticket.prioridad }}</div>
    <div><span class="text-slate-500">Estado:</span> {{ ticket.estado }}</div>
    <div><span class="text-slate-500">Contexto de escalación:</span>
      <p class="mt-1 bg-slate-50 p-2 rounded">{{ ticket.contexto }}</p></div>
    <form method="post" action="/panel/ticket/{{ ticket.id }}/estado" class="flex gap-2 pt-2 border-t">
      <select name="estado" class="border rounded px-2 py-1 flex-1">
        {% for e in ['abierto','en_proceso','resuelto','cerrado'] %}
        <option value="{{ e }}" {{ 'selected' if ticket.estado==e }}>{{ e }}</option>
        {% endfor %}
      </select>
      <button class="bg-slate-900 text-white px-3 rounded">Actualizar</button>
    </form>
    {% if aviso %}<p class="text-green-600">{{ aviso }}</p>{% endif %}
  </aside>
</div>
{% endblock %}
"""

_AUDIT = """
{% extends "base" %}{% block title %}Audit trail{% endblock %}
{% block body %}
<h1 class="text-2xl font-bold mb-4">Audit trail (últimos mensajes)</h1>
<table class="w-full bg-white rounded shadow text-sm">
<thead class="text-left bg-slate-50 border-b"><tr>
  <th class="p-3">Timestamp</th><th class="p-3">Rol</th><th class="p-3">Intención</th>
  <th class="p-3">Tool</th><th class="p-3">Contenido</th></tr></thead>
<tbody>
{% for m in mensajes %}
<tr class="border-b">
  <td class="p-3 text-slate-500 whitespace-nowrap">{{ m.timestamp.strftime('%d/%m %H:%M') }}</td>
  <td class="p-3">{{ m.rol }}</td>
  <td class="p-3">{{ m.intencion or '—' }}</td>
  <td class="p-3">{{ m.tool_llamada or '—' }}</td>
  <td class="p-3">{{ m.contenido[:90] }}</td>
</tr>
{% else %}<tr><td colspan="5" class="p-6 text-center text-slate-400">Sin actividad.</td></tr>{% endfor %}
</tbody></table>
{% endblock %}
"""

_env = Environment(
    loader=DictLoader(
        {
            "base": _BASE,
            "cola": _COLA,
            "cola_tbody": _COLA_TBODY,
            "detalle": _DETALLE,
            "audit": _AUDIT,
        }
    ),
    autoescape=select_autoescape(["html"]),
)


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


def _render(nombre: str, **ctx) -> HTMLResponse:
    return HTMLResponse(_env.get_template(nombre).render(**ctx))


@router.get("", response_class=HTMLResponse)
async def cola(request: Request, estado: str = "", _: None = Depends(_auth)) -> HTMLResponse:
    tickets = request.app.state.repo.list_tickets(estado or None)
    return _render("cola", tickets=tickets, estado=estado)


@router.get("/cola", response_class=HTMLResponse)
async def cola_parcial(request: Request, estado: str = "", _: None = Depends(_auth)) -> HTMLResponse:
    """Fragmento para el polling HTMX (solo la tabla)."""
    tickets = request.app.state.repo.list_tickets(estado or None)
    return _render("cola_tbody", tickets=tickets, estado=estado)


@router.get("/ticket/{ticket_id}", response_class=HTMLResponse)
async def detalle(request: Request, ticket_id: UUID, aviso: str = "", _: None = Depends(_auth)) -> HTMLResponse:
    repo = request.app.state.repo
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    mensajes = repo.get_last_n_messages(ticket.user_id, 30)
    return _render("detalle", ticket=ticket, mensajes=mensajes, aviso=aviso)


@router.post("/ticket/{ticket_id}/estado")
async def cambiar_estado(request: Request, ticket_id: UUID, estado: str = Form(...), _: None = Depends(_auth)):
    request.app.state.repo.update_ticket_estado(ticket_id, estado)
    return RedirectResponse(f"/panel/ticket/{ticket_id}", status_code=303)


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
    except Exception:
        # Ventana de 24h de WhatsApp cerrada u otro error de envío (§7.6).
        aviso = "No se pudo enviar (¿ventana de 24h cerrada?)."
    return RedirectResponse(f"/panel/ticket/{ticket_id}?aviso={aviso}", status_code=303)


@router.get("/audit", response_class=HTMLResponse)
async def audit(request: Request, _: None = Depends(_auth)) -> HTMLResponse:
    mensajes = request.app.state.repo.recent_messages(100)
    return _render("audit", mensajes=mensajes)

"""Interfaz del chat web (plan B, §9) — Fase 9.

Una página HTML mínima + un endpoint request/response que corre el MISMO
pipeline del núcleo (`ProcessMessage`) que el webhook, pero con un canal que
captura la respuesta en banda. No depende de Twilio ni del webhook — es el
respaldo para demo si la nube falla (§9). Reutiliza repo/guardrail/registry de
`app.state`; solo cambia el `ChannelAdapter`.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from app.adapters.channels.web_chat import WebChatCapturingChannel
from app.application.process_message import ProcessMessage
from app.domain.models import Intencion, Message, Rol

router = APIRouter()

# raw string: los '\n' del JS de streaming deben llegar literales al navegador,
# no convertirse en saltos de línea de Python (rompería el <script>).
_PAGINA = r"""
<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>E5 · Chat web (plan B)</title>
<script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-slate-100">
<div class="max-w-lg mx-auto p-4">
  <h1 class="text-xl font-bold mb-2">E5 · Asistente financiero <span class="text-slate-400 text-sm">(plan B)</span></h1>
  <div id="chat" class="bg-white rounded shadow p-3 h-96 overflow-y-auto space-y-2 text-sm"></div>
  <form id="f" class="mt-3 flex gap-2">
    <input id="t" required placeholder="Escribe un mensaje…" class="flex-1 border rounded px-3 py-2">
    <button class="bg-blue-600 text-white px-4 rounded">Enviar</button>
  </form>
</div>
<script>
const chat = document.getElementById('chat');
function burbuja(txt, mine){
  const d = document.createElement('div');
  d.className = 'flex ' + (mine ? 'justify-end' : 'justify-start');
  d.innerHTML = '<div class="max-w-[80%] px-3 py-2 rounded-lg '
    + (mine ? 'bg-blue-600 text-white' : 'bg-slate-100') + '">' + txt + '</div>';
  chat.appendChild(d); chat.scrollTop = chat.scrollHeight;
}
function typing(){
  const d = document.createElement('div');
  d.id = 'typing';
  d.className = 'flex justify-start';
  d.innerHTML = '<div class="px-3 py-2 rounded-lg bg-slate-100 text-slate-400 italic">Luca está escribiendo…</div>';
  chat.appendChild(d); chat.scrollTop = chat.scrollHeight;
}
// Burbuja del asistente a la que se le van agregando los fragmentos del stream.
function nuevaBurbujaBot(){
  const d = document.createElement('div');
  d.className = 'flex justify-start';
  const inner = document.createElement('div');
  inner.className = 'max-w-[80%] px-3 py-2 rounded-lg bg-slate-100 whitespace-pre-wrap';
  d.appendChild(inner); chat.appendChild(d);
  return inner;
}
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const t = document.getElementById('t'); const texto = t.value; t.value = '';
  const btn = e.target.querySelector('button');
  burbuja(texto, true);
  typing(); btn.disabled = true;
  try {
    const r = await fetch('/chat/stream', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({texto})});
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buffer = '', bot = null;
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += dec.decode(value, {stream:true});
      const eventos = buffer.split('\n\n'); buffer = eventos.pop();
      for (const ev of eventos) {
        const linea = ev.split('\n').find(l => l.startsWith('data:'));
        if (!linea) continue;
        const frag = JSON.parse(linea.slice(5).trim());
        const tp = document.getElementById('typing'); if (tp) tp.remove();
        if (!bot) bot = nuevaBurbujaBot();
        bot.textContent += frag;
        chat.scrollTop = chat.scrollHeight;
      }
    }
  } catch (err) {
    burbuja('⚠️ No se pudo enviar. Intenta de nuevo.', false);
  } finally {
    const tp = document.getElementById('typing'); if (tp) tp.remove();
    btn.disabled = false; t.focus();
  }
});
</script></body></html>
"""


@router.get("/chat", response_class=HTMLResponse)
async def pagina() -> HTMLResponse:
    return HTMLResponse(_PAGINA)


@router.post("/chat/send")
async def enviar(request: Request) -> JSONResponse:
    body = await request.json()
    # Teléfono sintético estable por sesión de demo (identidad del web chat).
    telefono = body.get("telefono") or "+50300000001"
    canal = WebChatCapturingChannel(telefono)

    pm = ProcessMessage(
        repo=request.app.state.repo,
        guardrail=request.app.state.guardrail,
        registry=request.app.state.registry,
        channel=canal,
    )
    incoming = canal.parse(body)
    if incoming.texto:
        await pm(incoming)  # pipeline completo, síncrono (no hay webhook aquí)
    return JSONResponse({"respuestas": canal.enviados})


def _sse(texto: str) -> str:
    """Un evento SSE con el fragmento serializado en JSON (preserva saltos de línea)."""
    return f"data: {json.dumps(texto, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def stream(request: Request) -> StreamingResponse:
    """Chat web con streaming (§plan-latencia C3). El guardrail corre igual (en
    `preprocess`, síncrono); solo la respuesta del agente se emite token a token
    por SSE, para que el usuario vea a Luca escribir desde ~0.5s en vez de esperar
    en blanco. Fallback sin streaming: POST /chat/send (JSON)."""
    body = await request.json()
    telefono = body.get("telefono") or "+50300000001"
    canal = WebChatCapturingChannel(telefono)
    pm = ProcessMessage(
        repo=request.app.state.repo,
        guardrail=request.app.state.guardrail,
        registry=request.app.state.registry,
        channel=canal,
    )
    incoming = canal.parse(body)
    repo = request.app.state.repo
    registry = request.app.state.registry

    async def gen():
        if not incoming.texto:
            return
        # (1) consentimiento + guardrail, síncrono antes de nada (§7.5).
        context = await pm.preprocess(incoming)
        if context is None:
            # Aviso legal o ruta sensible: el canal ya capturó la respuesta.
            for texto in canal.enviados:
                yield _sse(texto)
            return

        # (2) agente en streaming (si lo soporta; si no, respuesta de una).
        agente = registry.get("principal")
        capture: dict = {}
        if hasattr(agente, "handle_stream"):
            async for frag in agente.handle_stream(context, capture):
                yield _sse(frag)
        else:
            result = await agente.handle(context)
            capture = {
                "respuesta": result.respuesta,
                "intencion": result.intencion,
                "tool_llamada": result.tool_llamada,
            }
            yield _sse(result.respuesta)

        # (3) audit trail de la respuesta del asistente (§7.4).
        await asyncio.to_thread(
            repo.save_message,
            Message(
                user_id=context.user.id,
                rol=Rol.ASISTENTE,
                contenido=capture.get("respuesta", ""),
                intencion=capture.get("intencion", Intencion.OTRO),
                tool_llamada=capture.get("tool_llamada"),
            ),
        )

    return StreamingResponse(gen(), media_type="text/event-stream")

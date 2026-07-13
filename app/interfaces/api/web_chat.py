"""Interfaz del chat web (plan B, §9) — Fase 9.

Una página HTML mínima + un endpoint request/response que corre el MISMO
pipeline del núcleo (`ProcessMessage`) que el webhook, pero con un canal que
captura la respuesta en banda. No depende de Twilio ni del webhook — es el
respaldo para demo si la nube falla (§9). Reutiliza repo/guardrail/registry de
`app.state`; solo cambia el `ChannelAdapter`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.adapters.channels.web_chat import WebChatCapturingChannel
from app.application.process_message import ProcessMessage

router = APIRouter()

_PAGINA = """
<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Luca · Chat web (plan B)</title>
<script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-slate-100">
<div class="max-w-lg mx-auto p-4">
  <h1 class="text-xl font-bold mb-2">Luca · Asistente financiero <span class="text-slate-400 text-sm">(plan B)</span></h1>
  <div id="chat" class="bg-white rounded shadow p-3 h-96 overflow-y-auto space-y-2 text-sm"></div>
  <form id="f" class="mt-3 flex gap-2">
    <input id="t" required placeholder="Escribe un mensaje…" class="flex-1 border rounded px-3 py-2">
    <button class="text-slate-900 px-4 rounded font-semibold" style="background:#F5B301">Enviar</button>
  </form>
</div>
<script>
const chat = document.getElementById('chat');
function burbuja(txt, mine){
  const d = document.createElement('div');
  d.className = 'flex ' + (mine ? 'justify-end' : 'justify-start');
  d.innerHTML = '<div class="max-w-[80%] px-3 py-2 rounded-lg '
    + (mine ? 'text-white" style="background:#1F3A5F' : 'bg-slate-100') + '">' + txt + '</div>';
  chat.appendChild(d); chat.scrollTop = chat.scrollHeight;
}
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const t = document.getElementById('t'); const texto = t.value; t.value = '';
  burbuja(texto, true);
  const r = await fetch('/chat/send', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({texto})});
  const data = await r.json();
  (data.respuestas || []).forEach(x => burbuja(x, false));
});
</script></body></html>
"""


@router.get("/chat", response_class=HTMLResponse)
async def pagina() -> HTMLResponse:
    return HTMLResponse(_PAGINA)


@router.post("/chat/send")
async def enviar(request: Request) -> JSONResponse:
    body = await request.json()
    # Identidad FIJA del plan B: un único usuario sintético de demo. No se
    # acepta un teléfono del cliente — eso permitiría leer/escribir la
    # conversación de otro número sin autenticarse (la webapp usa /api/chat
    # con sesión OTP; este endpoint existe solo para demostrar sin WhatsApp).
    telefono = "+50300000001"
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

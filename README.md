# E5 — Asistente financiero conversacional (Luca)

**Luca** es un asistente financiero personal ecuatoriano que opera sobre
WhatsApp. Registra y categoriza gastos (H1), da insights de presupuesto con
alertas proactivas (H2) y responde soporte con base en una KB aprobada (H3),
escalando a un humano todo lo sensible (reclamos, temas regulatorios, asesoría
de inversión).

Arquitectura **hexagonal** (puertos y adaptadores): `domain/` no importa de
nadie; los adaptadores concretos se cablean solo en el composition root
(`app/main.py`). Diseño completo en [`../E5-arquitectura.md`](../E5-arquitectura.md),
plan por fases en [`../E5-plan-implementacion.md`](../E5-plan-implementacion.md) y
las reglas del agente/guardrail en
[`../comportamiento-blindaje-clasificador-y-agente-principal.md`](../comportamiento-blindaje-clasificador-y-agente-principal.md).

## Estado: Fases 1–9 implementadas ✅

El pipeline completo está en verde: **63/63 tests pasan** (`pytest -q`). No es
un scaffold — el webhook de WhatsApp, el guardrail, el agente Claude, el RAG de
soporte, el panel humano, el scheduler y el chat web plan B están cableados y
probados con fakes/LLMs scripteados (sin llamar a APIs reales en los tests).

| Fase | Qué hace | Dónde |
|---|---|---|
| 1 | Contratos congelados: modelos, 5 puertos, tools | `app/domain/` + `db/schema.sql` |
| 2 | Walking skeleton: orquestador + webhook + Supabase | `app/application/process_message.py`, `interfaces/api/webhook_twilio.py`, `adapters/persistence/` |
| 3 | Guardrail fail-closed en capas | `app/adapters/guardrail/` |
| 4 | Agente principal Claude con tools (H1+H2) | `app/application/agents/principal.py` |
| 5 | Soporte RAG con grounding (H3) | `app/application/agents/soporte_rag.py`, `app/kb/` |
| 6 | Panel humano (cola de tickets, respuesta, audit) | `app/interfaces/api/panel.py` |
| 7 | Scheduler de alertas proactivas de presupuesto | `app/infra/scheduler.py` |
| 8 | Calibración del guardrail (recall 'sensible' 100% @ umbral 0.7) | `scripts/eval_guardrail.py` |
| 9 | Chat web (plan B, sin WhatsApp) + hosting local + túnel | `app/interfaces/api/web_chat.py`, `scripts/run_local.sh` |

## Cómo funciona un mensaje

El orquestador (`ProcessMessage`) parte el pipeline en dos etapas (§7.5), para
responder el `200 OK` del webhook rápido y hacer el trabajo del LLM en background:

1. **`preprocess`** (síncrono, antes del 200):
   - **Consentimiento** — un usuario nuevo recibe solo el aviso legal (en la voz
     de Luca) y se registra su consentimiento; nada más.
   - **Guardrail** en capas, *fail-closed*: `denylist` determinística → clasificador
     Groq → umbral de confianza. Si es sensible → se crea un `Ticket` con contexto
     y se responde con una escalación cálida; **el mensaje nunca llega al agente**.
     Si Groq falla/timeoutea (con reintentos + backoff), se falla cerrado.
2. **`run_agent`** (background):
   - El **agente principal Claude** (un solo agente, opción C) resuelve H1/H2 y
     deriva H3 vía tools. Puede llamar varias tools en una pasada (p. ej. registrar
     un gasto *y* consultar presupuesto). Actúa como **cuarta capa** de revisión de
     sensibilidad.
   - **Audit trail** completo en `messages` (§7.4): input → intención → tool →
     respuesta. La entrega por el canal es resiliente (un 4xx/5xx de Twilio no
     tira el webhook ni genera duplicados).

### Tools del agente (contrato congelado, `app/domain/tools.py`)

- `registrar_gasto` — crea la transacción (nace `pendiente_confirmacion`).
- `consultar_presupuesto` — **el sistema calcula los números**; Luca solo los explica.
- `responder_soporte` — RAG con grounding sobre `app/kb/`; si no está en el corpus,
  no inventa: señala para escalar.
- `crear_ticket` — escalación a humano.

> Invariante de aislamiento (§7.3.2): **ninguna tool recibe `user_id` ni `telefono`** —
> el `user_id` se resuelve desde el contexto de la conversación, nunca desde el modelo.

## Canales e interfaces

- **WhatsApp vía Twilio** (canal activo) — `POST /webhook/whatsapp` (form-encoded;
  firma opcional `X-Twilio-Signature`). Adaptador: `app/adapters/channels/whatsapp_twilio.py`.
- **WhatsApp Cloud API (Meta)** — se conserva como **ejemplo** para empresas con
  acceso directo a la Cloud API (`whatsapp_meta.py` + `interfaces/api/webhook.py`,
  con handshake GET y firma `X-Hub-Signature-256`). **No se cablea** en `main.py`.
- **Webapp de usuario (rama webapp)** — vistas como endpoints con URL limpia
  (`/`, `/app/inicio`, `/app/chat`, `/app/movimientos`, `/app/presupuestos`,
  `/app/tickets`, `/legal`) sobre un API JSON autenticado con **OTP por
  WhatsApp** (`/api/auth/*`, `/api/estado`, `/api/chat`, `/api/presupuestos`).
  Estructura completa de URLs y políticas de auth en
  [`webapp/README.md`](webapp/README.md).
- **Chat web (plan B)** — `GET /chat` + `POST /chat/send`, mismo núcleo, para
  demostrar sin depender de WhatsApp (usuario sintético fijo).
- **Panel humano** — `/panel` (auth básica): cola de tickets, detalle, cambiar
  estado, responder al usuario y vista de audit trail.
- **Legales** — `/privacidad` y `/terminos`.
- **Salud** — `/health`.
- **Scheduler proactivo** — APScheduler in-process; revisa presupuestos cada
  `SCHEDULER_INTERVALO_MIN` y notifica cruces de umbral (idempotente vía tabla `alerts`).

## Stack

Python ≥3.11 · FastAPI + Uvicorn · **Anthropic** (agentes, `claude-sonnet-5`) ·
**Groq** (guardrail, `openai/gpt-oss-20b`) · **Supabase** (única fuente de estado) ·
APScheduler · Jinja2. Gestión de deps con **uv** (`uv.lock`).

Tablas Supabase (`db/schema.sql`): `users`, `categories`, `messages`,
`transactions`, `budgets`, `tickets`, `alerts`, `auth_codes`, `sessions`.

> **Migración (rama webapp):** si el proyecto Supabase ya tenía el schema
> anterior, ejecuta [`db/migracion-webapp-auth.sql`](db/migracion-webapp-auth.sql)
> en el SQL Editor — agrega solo `auth_codes` y `sessions` (idempotente, no
> toca lo existente). Instrucciones paso a paso dentro del archivo.

## Puesta en marcha (local)

```bash
# 1. Instalar deps (uv)
uv sync --extra dev

# 2. Configurar entorno
cp .env.example .env    # rellenar las claves reales (ver comentarios del archivo)

# 3. Ejecutar el schema en tu proyecto Supabase
#    (pega db/schema.sql en el SQL editor de Supabase)

# 4. Correr los tests
uv run --extra dev python -m pytest -q     # 71 passed

# 5. Levantar la app
uv run uvicorn app.main:app --reload --port 8080
```

### Conectar WhatsApp (Twilio) con túnel público

El hosting es **local + túnel** (`scripts/run_local.sh`): arranca uvicorn si hace
falta y abre un túnel público (ngrok por defecto, `cloudflared` opcional). Con un
dominio estático de ngrok la Callback URL de Twilio no cambia entre demos.

```bash
./scripts/run_local.sh                          # ngrok (usa NGROK_DOMAIN si está)
TUNNEL=cloudflared ./scripts/run_local.sh       # sin cuenta
```

Configuración en Twilio (una vez):

1. En [console.twilio.com](https://console.twilio.com) copia tu **Account SID** y
   **Auth Token** → `TWILIO_ACCOUNT_SID` y `TWILIO_AUTH_TOKEN` en `.env`.
2. Activa el **WhatsApp Sandbox** (Messaging → Try it out → Send a WhatsApp message)
   y, desde tu teléfono, envía el código `join <palabra>` al número del sandbox para
   unirte. Ese número compartido va en `TWILIO_WHATSAPP_FROM` (sandbox: `+14155238886`).
3. En la config del Sandbox, pega en **"When a message comes in"** la URL del túnel
   `https://<URL>/webhook/whatsapp` con método **POST**.
4. (Producción) Para validar la firma de Twilio pon `TWILIO_VALIDATE_SIGNATURE=true`
   y `PUBLIC_BASE_URL=https://<URL>` (debe coincidir exacto con la Callback URL).

Ya puedes escribirle al número del sandbox desde WhatsApp y Luca responde.

> **Volver a Meta:** el adaptador y el webhook de Meta siguen en el repo como
> ejemplo. Para reactivarlo, en `app/main.py` importa `WhatsAppMetaAdapter` y el
> router `webhook` en vez de sus equivalentes `*_twilio`, y define las variables
> `WHATSAPP_*` del `.env`.
>
> `Dockerfile` y `fly.toml` se conservan por si se vuelve a Fly.io, pero el flujo
> por defecto es el túnel. CI (`.github/workflows/ci.yml`) corre los tests en cada
> push a `main` / PR.

## Configuración (`.env`)

Todas las claves viven en `.env` (local, en `.gitignore`) o en el vault del host —
nunca en la imagen ni en el repo. Variables clave (defaults y detalle en
[`.env.example`](.env.example)):

| Grupo | Variables |
|---|---|
| Anthropic | `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `CLAUDE_MAX_TOKENS` |
| Groq (guardrail) | `GROQ_API_KEY`, `GROQ_MODEL` |
| Supabase | `SUPABASE_URL`, `SUPABASE_KEY` |
| WhatsApp/Twilio (activo) | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`, `TWILIO_VALIDATE_SIGNATURE`, `PUBLIC_BASE_URL` |
| WhatsApp/Meta (ejemplo, opcional) | `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `GRAPH_API_VERSION` |
| Guardrail | `GUARDRAIL_UMBRAL_CONFIANZA` (0.7), `GUARDRAIL_TIMEOUT_MS`, `GUARDRAIL_REINTENTOS`, `GUARDRAIL_BACKOFF_MS` |
| Panel | `PANEL_USER`, `PANEL_PASSWORD` |
| Scheduler | `SCHEDULER_HABILITADO`, `SCHEDULER_INTERVALO_MIN` |

## Tests

```bash
uv run --extra dev python -m pytest -q
```

| Archivo | Cubre |
|---|---|
| `test_contracts.py` | Contratos congelados (modelos, puertos, invariante de tools) |
| `test_walking_skeleton.py` | Pipeline del orquestador con fakes en memoria |
| `test_guardrail.py` | Guardrail fail-closed en capas |
| `test_agente.py` | Agente Claude con tools (LLM scripteado, H1+H2) |
| `test_soporte_rag.py` | Grounding de H3 (dentro/fuera del corpus) |
| `test_panel_y_webchat.py` | Panel humano y chat web plan B |
| `test_scheduler.py` | Alertas proactivas de presupuesto |

## Evaluación del guardrail

```bash
uv run python scripts/eval_guardrail.py    # frases etiquetadas en scripts/frases_eval.py
```

Calibrado en fase 8: recall de la clase `sensible` al 100% con umbral 0.7 (se
prioriza no dejar pasar nada sensible aunque cueste algún falso positivo).

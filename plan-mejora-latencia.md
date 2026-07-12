# Plan de mejora de latencia — E5 / Luca

**Fecha:** 2026-07-12 · **Estado:** Fase A + B1 + C1 + C2 **implementadas** ✅

## 0. Resultados medidos (post-implementación)

| Escenario | Antes | Después | |
|---|---|---|---|
| "hola" (sin tool) | ~3.6 s | **~2.8 s** | −22 % |
| gasto (con tool) | ~5.6 s | **~5.0 s** | −11 % |

Verificaciones:
- **Prompt caching activo:** `cache_read` pasó de `0` → `3531` tokens; input de
  `3607` → `~80` tokens por turno. **Ahorra ~90 % del costo de input, pero NO
  latencia** a este tamaño de prompt (3.5k tokens es pequeño; el turno de Claude
  sigue en ~1.8 s, dominado por generación, no por procesar el input).
- **Telemetría (C1):** una medición real de un gasto arrojó
  `preprocess 1421ms` + `agente 4619ms`. **El agente (2 turnos de Claude) es el
  ~77 % del tiempo.**

**Conclusión honesta:** las optimizaciones de I/O (paralelizar, cachear usuario,
no bloquear el loop) ya se aplicaron y recortaron lo que había que recortar
—guardrail y Supabase—. **El piso restante es la generación de Claude**
(~1.8 s/turno; un gasto son 2 turnos ≈ 3.6 s solo de LLM). Bajar de ahí requiere
un modelo más rápido (B3) o streaming (C3), no más optimización de I/O.

## 1. Diagnóstico con números reales

Medido contra el servidor local con claves reales (`/chat/send`, misma ruta que
recorre WhatsApp):

| Escenario | Latencia total |
|---|---|
| Saludo simple ("hola cómo estás") | **3.6 s** |
| Mensaje con tool ("gasté 12 en almuerzo") | **5.6 s** |

Desglose por componente (medición aislada):

| Componente | Tiempo | Notas |
|---|---|---|
| Claude, 1 turno | **~2 000 ms** | `in=3607` tokens, **`cache_read=0`** → sin prompt caching |
| Groq guardrail | ~500 ms | síncrono, antes del agente |
| Supabase `get_or_create_user` | ~490 ms | 1 round-trip REST por mensaje |
| Supabase historial (`get_last_n_messages`) | ~220 ms | |
| Supabase transacción pendiente | ~210 ms | |
| Supabase `save_message` (×2: entrada + audit) | ~200 ms c/u | |

**Anatomía de un "hola" (3.6 s):** todo corre EN SERIE —
`user (0.5) → guardrail (0.5) → save_msg (0.2) → historial (0.2) → pendiente (0.2) → Claude (2.0) → audit (0.2)`.

**Anatomía de un gasto (5.6 s):** lo mismo + un **segundo turno de Claude**
(tool use → ejecutar tool → realimentar) ≈ +2 s.

Conclusión: dos culpables dominan — (a) Claude sin caché reprocesando ~3.6k
tokens de system+tools en cada turno (y 2 turnos si hay tool), y (b) ~1.3 s de
round-trips secuenciales a Supabase/Groq que podrían solaparse.

## 2. Fase A — Quick wins (≈1 h de trabajo, mayor impacto)

### A1. Prompt caching en el agente principal ⭐ el más importante
`app/adapters/llm/claude.py` / `app/application/agents/principal.py`.
Hoy solo H3 (soporte RAG) usa `cache_control`. El system prompt de Luca (~1.4k
tokens) + las 4 tools (~2k tokens) son **idénticos en cada request** y no se
cachean.

- Marcar `cache_control: {"type": "ephemeral"}` en el último bloque del
  `system` y en la última tool del array `tools`.
- Ganancia esperada: el TTFT de Claude baja sensiblemente (~30–50 % del turno)
  y el costo de input se reduce ~90 % en hits. Con 2 turnos por tool use, la
  ganancia se duplica.

### A2. Paralelizar el `preprocess` (`app/application/process_message.py`)
El guardrail (Groq, 0.5 s) **no depende del usuario ni del historial**:

```python
user, veredicto = await asyncio.gather(
    asyncio.to_thread(repo.get_or_create_user, ...),  # ver A4
    guardrail.classify(incoming.texto),
)
```
y luego historial + transacción pendiente en paralelo. Ahorro: **~0.7–0.9 s**
por mensaje sin tocar la semántica (el veredicto se aplica igual antes del
agente).

### A3. Auditoría fuera del camino crítico
`_audit_respuesta` (save_message del asistente) corre HOY antes del `send`.
Enviarla después del envío (o como background task). Ahorro: **~0.2 s** de
latencia percibida. El mensaje ya está garantizado en memoria; si el insert
falla, se loguea (mismo criterio que `_enviar_seguro`).

### A4. No bloquear el event loop con Supabase
`supabase-py` v2 es síncrono y corre dentro de handlers async: cada query
**congela el loop entero** (afecta a todos los usuarios concurrentes, al
scheduler y al panel). Envolver las llamadas del orquestador con
`asyncio.to_thread(...)`. No baja la latencia individual, pero elimina el
efecto dominó bajo carga.

### A5. Reutilizar la conexión HTTP en Twilio
`whatsapp_twilio.py` crea un `httpx.AsyncClient` **por cada send** → handshake
TLS nuevo (~100–300 ms). Crear el cliente una vez en `__init__` y reutilizarlo.
(Aplica solo al camino WhatsApp, no al chat web.)

**Resultado esperado Fase A:** "hola" ~3.6 s → **~1.8–2.2 s**; gasto con tool
~5.6 s → **~3.5 s**.

## 3. Fase B — Estructural (medio día)

### B1. Cache in-memory de usuario por teléfono
`get_or_create_user` cuesta ~0.5 s y el mismo usuario escribe N mensajes
seguidos. Un dict con TTL corto (2–5 min) en el repo lo baja a ~0 en mensajes
subsecuentes. Invalidar en `registrar_consentimiento`.

### B2. Recortar el historial que se envía a Claude
`historial_n=10` mensajes completos por turno. Evaluar 6–8; menos tokens de
input = menos TTFT (medir impacto en calidad del loop de confirmación H1 antes
de fijar).

### B3. Evaluar modelo más rápido para el agente
El tono de Luca hoy corre sobre `claude-sonnet-5`. Probar `claude-haiku-4-5`
con el mismo system prompt en un A/B corto (mismas 20 frases del eval): si el
tono y el tool use se sostienen, el turno de LLM puede bajar a menos de la
mitad. Decisión reversible vía `CLAUDE_MODEL` en `.env`, cero código.

### B4. Groq: bajar el timeout con datos
`GUARDRAIL_TIMEOUT_MS=1200` con reintento. Medido: ~500 ms p50. Cuando haya
telemetría (C1), evaluar 900 ms para acotar la cola sin subir los fail-closed.

## 4. Fase C — Percepción y observabilidad

### C1. Telemetría por etapa (prerequisito para iterar)
Loguear en `ProcessMessage` la duración de cada etapa (`user`, `guardrail`,
`historial`, `claude_turno_1..n`, `tools`, `send`, `audit`) en una línea por
mensaje. Sin esto, las siguientes decisiones son a ciegas.

### C2. Chat web: indicador de "escribiendo…"
`web_chat.py` deja al usuario ante una pantalla muda 3–5 s. Añadir una burbuja
"Luca está escribiendo…" al enviar (2 líneas de JS). No baja la latencia real
pero transforma la percepción — es la mejora de UX más barata de todas.

### C3. Chat web: streaming (opcional, más trabajo)
SSE con `client.messages.stream()` para pintar la respuesta token a token.
Solo si C2 + Fase A no bastan; toca el contrato `LLMProvider`.

### C4. WhatsApp: nada que hacer en el webhook
El webhook ya responde 200 al instante y el agente va en background (§7.5) —
la espera del usuario es exactamente el pipeline anterior. Todo lo de Fase A/B
aplica 1:1.

## 5. Orden recomendado y metas

| # | Acción | Estado | Resultado real |
|---|---|---|---|
| 1 | A1 prompt caching agente | ✅ hecho | `cache_read` 0→3531; ahorro de **costo**, no de latencia |
| 2 | A2 preprocess en paralelo | ✅ hecho | solapa guardrail+usuario (visible en 1er msg) |
| 3 | A3 audit post-send + A5 cliente Twilio | ✅ hecho | ~0.2–0.5 s en WhatsApp |
| 4 | C1 telemetría + C2 typing indicator | ✅ hecho | logs por etapa + UX del chat web |
| 5 | A4 `to_thread` Supabase | ✅ hecho | loop no se bloquea bajo carga |
| 6 | B1 cache de usuario | ✅ hecho | user fetch 0.5 s → ~0 en msgs repetidos |
| 7 | **B3 A/B de modelo (Haiku)** | ✅ probado → **descartado** | latencia −11% pero rompe grounding H3 y tono |
| 8 | C3 streaming en chat web (SSE) | ✅ hecho | beneficio real solo en respuestas largas (ver abajo) |

**Meta original:** "hola" < 2 s, con tool < 3.5 s. **Estado:** "hola" ~2.8 s
(cerca), con tool ~5.0 s (bloqueado por los 2 turnos de Claude).

### Resultado del A/B Sonnet vs Haiku (12 frases no-sensibles, agente real)

| | Acierto de tool | media | p50 | max |
|---|---|---|---|---|
| **Sonnet** | **12/12** (funcional)* | 3732 ms | 3675 ms | 6678 ms |
| **Haiku** | **9/12** | 3315 ms | 2510 ms | 9631 ms |

\* La métrica marcó a Sonnet 11/12, pero el "fallo" fue el mensaje mixto donde SÍ
llamó a las 2 tools correctas (el marcador solo mira la última). Funcionalmente
Sonnet acertó todo.

**Por qué se descarta Haiku** (la latencia solo bajó 11% en media / 32% en p50,
NO "a la mitad" como se esperaba, y a cambio degrada garantías del sistema):

1. **Rompe el grounding de H3 (§4):** ante "cómo registro un gasto" y "qué
   categorías existen", Haiku NO llamó a `responder_soporte` — respondió de su
   propio conocimiento e **inventó una lista de 8 categorías** que este sistema
   no tiene fija. Es exactamente lo que H3 existe para impedir.
2. **Falló el mensaje mixto:** "gasté 25 y cómo voy este mes" → no registró el
   gasto (pidió la categoría en vez de usar `registrar_gasto`).
3. **Rompe la disciplina de tono:** usó voseo ("querés", "tenés") que el system
   prompt prohíbe explícitamente; Sonnet mantuvo el tuteo ecuatoriano.

**Veredicto:** el ahorro de latencia de Haiku (~1.2 s en p50) no compensa perder
grounding y tono. **Se mantiene `claude-sonnet-5` en el agente.**

### Resultado de C3 (streaming SSE) — implementado y medido

Endpoint nuevo `POST /chat/stream` (SSE); `/chat/send` (JSON) se conserva como
fallback. El agente emite token a token vía `ClaudeProvider.stream` +
`MainAgent.handle_stream`. El guardrail sigue corriendo síncrono antes (§7.5).

Time-to-first-token medido (Claude real):

| Mensaje | 1er token | Completo |
|---|---|---|
| "hola" (respuesta corta) | ~3.5 s | ~3.8 s |
| presupuesto (respuesta media) | ~4.0 s | ~4.8 s |

**Hallazgo honesto:** el primer token NO llega en ~0.5 s. Antes de poder emitir
nada hay dos costos irreducibles: **~1.4 s de guardrail** (obligatorio por
seguridad, no se puede saltar) **+ ~2 s de TTFT del propio modelo**. El streaming
solo reparte el texto DESPUÉS de eso.

- **Respuestas cortas** (confirmaciones de gasto, "hola"): beneficio casi nulo —
  el texto es tan breve que se genera de golpe. Aquí lo que ayuda es el
  indicador "escribiendo…" (C2), no el streaming.
- **Respuestas largas** (explicación de presupuesto, soporte detallado): SÍ se
  ve el texto formándose progresivamente (~1 s de escritura visible) en vez de
  aparecer todo al final.

**Conclusión global de latencia:** el piso de este sistema es
`guardrail (~1.4s) + TTFT de Claude (~2s)` ≈ **3.5 s antes del primer carácter**,
y ninguna de las dos partes es negociable sin cambiar el modelo (Haiku:
descartado) o debilitar el guardrail (no). Lo entregado —caching, paralelización,
cache de usuario, streaming, typing indicator— es el máximo exprimible sin tocar
esas dos restricciones. Para bajar de 3.5 s haría falta un modelo con menor TTFT
que mantenga el grounding y el tono, o relajar el guardrail síncrono (no
recomendado).

## 6. Qué NO tocar

- El guardrail **sigue síncrono antes del agente** (§7.3/§7.5): es una garantía
  de seguridad, no un cuello negociable. Se paraleliza con I/O que no depende
  de él, nunca se pospone.
- El orden consentimiento → guardrail → agente no cambia.
- Los contratos de `domain/` no cambian (todo esto es adaptadores + orquestador).

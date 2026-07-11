# E5 — Asistente financiero conversacional

Asistente sobre WhatsApp que registra gastos (H1), da insights de presupuesto
(H2) y responde soporte (H3), escalando lo sensible a un humano. Arquitectura
hexagonal (puertos y adaptadores). Ver `../E5-arquitectura.md` y
`../E5-plan-implementacion.md`.

## Estado: Fase 1 — Contratos congelados ✅

Los 3 contratos de §8.2 están escritos y verificados (`domain/` no importa nada
de `adapters/`; 10/10 checks de contrato en verde). A partir de aquí, ramas
paralelas.

| Contrato | Archivo | Congelado |
|---|---|---|
| Schema de datos (T2) | `app/domain/models.py` + `db/schema.sql` | ✅ |
| Puertos del núcleo (5) | `app/domain/ports.py` | ✅ |
| Tools del agente (T1) | `app/domain/tools.py` | ✅ |

Invariantes verificados:
- `User.telefono` valida E.164; `Transaction` nace `pendiente_confirmacion`.
- **Ninguna tool acepta `user_id` ni `telefono`** (§7.3.2) — lo resuelve el `Repository`.
- Los 5 puertos existen como `Protocol` ligeros.
- Regla de dependencia unidireccional respetada.

## Verificar los contratos

```bash
# con las deps instaladas (pip install -e ".[dev]"):
python3 -m pytest tests/test_contracts.py -q
```

`tests/test_contracts.py` cubre modelos, puertos y el invariante de las tools.

## Mapa de implementación por fase

| Ruta | Fase | Estado |
|---|---|---|
| `app/domain/` | 1 | ✅ implementado |
| `db/schema.sql` | 1 | ✅ ejecutar en Supabase |
| `app/infra/config.py` | 0/1 | ✅ carga de entorno |
| `app/application/process_message.py`, `router.py` | 2 | stub |
| `app/adapters/persistence/supabase_repo.py` | 2 | stub |
| `app/adapters/channels/whatsapp_twilio.py` | 2 | stub |
| `app/interfaces/api/webhook.py` | 2 | stub |
| `app/adapters/guardrail/groq_classifier.py`, `llm/groq.py` | 3 | stub |
| `app/adapters/llm/claude.py`, `application/agents/{gasto,presupuesto}.py` | 4 | stub |
| `app/application/agents/soporte_rag.py`, `app/kb/` | 5 | stub |
| `app/interfaces/api/panel.py` | 6 | stub |
| `app/infra/scheduler.py` | 7 | stub |
| `app/adapters/channels/web_chat.py` | 9 | stub |

## Pendiente de Fase 0 (infra, aún no hecho)

Este scaffold cubre los entregables de código de la Fase 1. Falta la infra de
Fase 0 para cerrar su gate: crear el proyecto Supabase y ejecutar `db/schema.sql`,
las 6 claves reales en `.env`, `Dockerfile` + `fly.toml`, endpoint `/health`,
`fly secrets set`, pre-commit con `gitleaks` y la GitHub Action de deploy.

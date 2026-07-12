#!/usr/bin/env bash
# Corre el asistente en local con túnel público (Plan B, §9) — reemplaza a Fly.io.
# Uso:  ./scripts/run_local.sh          (usa ngrok)
#       TUNNEL=cloudflared ./scripts/run_local.sh   (sin cuenta)
#
# Requisito ngrok (una vez): crear cuenta gratis y correr en tu terminal:
#   ngrok config add-authtoken <TU_AUTHTOKEN>
# (authtoken en https://dashboard.ngrok.com/get-started/your-authtoken)
#
# Dominio estático (URL fija para la demo): reclámalo en dashboard.ngrok.com/domains
# y pásalo por NGROK_DOMAIN para que la Callback URL de Meta no cambie nunca.
set -euo pipefail

PORT="${PORT:-8080}"
TUNNEL="${TUNNEL:-ngrok}"
NGROK="${NGROK:-$HOME/.local/bin/ngrok}"
NGROK_DOMAIN="${NGROK_DOMAIN:-discard-mooing-unrented.ngrok-free.dev}"
cd "$(dirname "$0")/.."

# Arranca uvicorn si no responde /health.
if ! curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "▶ Arrancando uvicorn en :${PORT}"
  uv run uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" &
  for _ in $(seq 1 15); do
    curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 && break
    sleep 1
  done
fi
echo "✓ Servidor local sano en http://127.0.0.1:${PORT}/health"

echo "▶ Abriendo túnel público con ${TUNNEL} → :${PORT}"
echo "  Copia la URL https://... y pégala en Meta → WhatsApp → Configuration"
echo "  como Callback URL:  https://<URL>/webhook/whatsapp"
echo
if [ "${TUNNEL}" = "cloudflared" ]; then
  exec cloudflared tunnel --url "http://localhost:${PORT}"
elif [ -n "${NGROK_DOMAIN}" ]; then
  exec "${NGROK}" http "${PORT}" --url "https://${NGROK_DOMAIN}"
else
  exec "${NGROK}" http "${PORT}"
fi

# 🖥️ Luca — Webapp (vistas del backend)

Centro de vista de usuario de **Luca**. Las vistas son **endpoints del backend
FastAPI con URL limpia** (nada de rutas `.html`; los archivos de esta carpeta
son un detalle interno de implementación — la URL es el contrato). Solo los
assets css/js se sirven como estáticos bajo `/assets`.

## Estructura de URLs

| URL | Vista | Datos |
|---|---|---|
| `/` | Login en 2 pasos: teléfono → **código OTP por WhatsApp** | `POST /api/auth/solicitar` · `POST /api/auth/verificar` |
| `/app/inicio` | Dashboard (gastado, donut, presupuestos, alertas) | `GET /api/estado` |
| `/app/chat` | Chat con Luca — pipeline real (guardrail → agente Claude) | `POST /api/chat` |
| `/app/movimientos` | Movimientos + bandeja por confirmar | `GET /api/estado` · confirmaciones vía `POST /api/chat` |
| `/app/presupuestos` | Presupuestos con umbral configurable (H2) | `GET /api/estado` · `POST /api/presupuestos` |
| `/app/tickets` | Mis tickets (H3) | `GET /api/estado` · "Contactar" vía `POST /api/chat` |
| `/legal` | Términos y política de privacidad (LOPDP) | estático |

**API JSON** (todas menos `/api/auth/*` exigen `Authorization: Bearer <token>`):
`POST /api/auth/solicitar` · `POST /api/auth/verificar` · `POST /api/auth/salir` ·
`GET /api/estado` · `POST /api/chat` · `POST /api/presupuestos`.

## Autenticación (cómo entra un usuario)

1. Escribe su teléfono → el backend genera un **código de 6 dígitos** y se lo
   envía **por WhatsApp** (el mismo canal Twilio del producto).
2. Escribe el código → recibe un token de sesión (7 días) con el que la UI llama
   al API. **La identidad sale siempre de la sesión**: nadie puede consultar
   datos de un número que no controla.

Políticas aplicadas (OWASP MFA / NIST 800-63B): código hasheado en reposo, TTL
5 min, un solo uso, máx. 5 intentos, comparación en tiempo constante, cooldown
de reenvío 60 s, token de 256 bits guardado como hash. Diseño en
`app/application/auth.py`; tablas `auth_codes` y `sessions` en `db/schema.sql`.

> **Demo sin sandbox:** si el número no está unido al sandbox de Twilio, el OTP
> no llega. Para demos existe `AUTH_DEMO_OTP` en `.env` (código maestro,
> apagado por defecto; jamás en producción).

## Cómo abrirlo

```bash
# 1. Ejecutar db/schema.sql en Supabase (incluye auth_codes y sessions)
# 2. Desde la raíz del repo:
uv run uvicorn app.main:app --port 8080
# abre http://localhost:8080/  → teléfono → código por WhatsApp → app
```

Los gastos que registres por WhatsApp aparecen en la web y viceversa (misma DB,
mismo usuario por teléfono).

## Decisiones técnicas (por qué así)

- **Sin dependencias en runtime.** HTML + CSS propio + JS vanilla. Sin Tailwind/Alpine/Chart.js por CDN: en un hackathon la demo no puede depender de que haya internet. Los gráficos son **SVG a mano** (`charts.js`). La única fuente externa es Manrope (Google Fonts), que degrada a `system-ui` sin romper nada.
- **Portable a Jinja2/HTMX** (el stack real de E5): es HTML plano con clases, se copia casi 1:1 a plantillas del backend.
- **Solo tokens del design kit.** `assets/css/tokens.css` es copia de `design/tokens.css`. Ningún color inventado.

## Estructura de archivos (interno — las URLs de arriba son el contrato)

```
webapp/
  index.html          Vista del login OTP (servida en /)
  legal.html          Términos y privacidad LOPDP (servida en /legal)
  app/                Vistas de la app (servidas en /app/*)
    inicio.html · chat.html · movimientos.html · presupuestos.html · tickets.html
  assets/             Únicos estáticos públicos (/assets/*)
    css/tokens.css    Design tokens (copia del kit)
    css/app.css       Componentes y responsive
    js/data.js        Catálogo de categorías + helpers de formato (sin datos de ejemplo)
    js/api.js         Sesión Bearer + llamadas al API + mapeo de schema
    js/ui.js          Íconos (Lucide inline), toasts, modales, tema
    js/shell.js       Sidebar (desktop) + bottom nav (mobile) + topbar + notificaciones
    js/charts.js      Donut, proyección (SVG a mano)
```

## Qué está cubierto (las 3 historias del PDF)

- **H1 Registro conversacional** → `/app/chat` (agente Claude real: interpreta, clasifica, pide confirmación) + `/app/movimientos` (bandeja "Por confirmar" que completa datos por el mismo pipeline).
- **H2 Presupuesto e insights** → `/app/presupuestos` (límite + umbral configurable persistidos) + alertas calculadas por el sistema en el dashboard, sin consejo de inversión.
- **H3 Soporte + escalamiento** → el chat escala lo sensible vía guardrail; `/app/tickets` muestra el ticket con contexto y prioridad (cola humana en `/panel`).

## Reglas de marca aplicadas (checklist)

- Montos con cifras tabulares (`tnum`) y formato `$1,234.56`.
- Dorado `#F5B301` **siempre con texto oscuro** (botón enviar, CTA, avatar).
- Gastos en color **neutro** (no rojo); naranja solo para alertas de presupuesto; rojo solo para errores/acciones destructivas del sistema.
- Toda alerta lleva **ícono + texto**, nunca solo color.
- Modo claro y oscuro (toggle en topbar y en Preferencias); tokens ya resuelven ambos.
- Responsive: sidebar en desktop, bottom nav de 5 con chat central en mobile.

## Pendiente / siguiente iteración

- Assets de la mascota (ardilla) — hoy el avatar es el placeholder "L" dorado.
- Ingresos, saldo e importación de CSV (gap conocido de H1 — contratos en E2 §7 y E7).
- Vistas de insights avanzados, recurrentes y cuenta (se retiraron los mocks;
  se reconstruyen cuando exista el backend que las alimente).

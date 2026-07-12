# 🖥️ Luca — Frontend web (conectado al backend)

Centro de vista de usuario de **Luca**. En esta rama la webapp está **servida por
el propio backend FastAPI** (montada como estáticos en `/`) y las vistas clave
consumen datos reales vía `assets/js/api.js`:

| Página | Estado | Fuente de datos |
|---|---|---|
| `index.html` (login) | ✅ real | teléfono E.164 → localStorage (identidad demo, igual que WhatsApp) |
| `app/chat.html` | ✅ real | `POST /api/chat` → pipeline completo (consentimiento → guardrail → agente Claude) |
| `app/inicio.html` | ✅ real | `GET /api/estado` (gastos, presupuestos, alertas calculadas por el sistema) |
| `app/movimientos.html` | ✅ real | `GET /api/estado` · "Nuevo" y confirmaciones van por `POST /api/chat` |
| `app/presupuestos.html` | ✅ real | `GET /api/estado` + `POST /api/presupuestos` (upsert) |
| `app/tickets.html` | ✅ real | `GET /api/estado` · "Contactar" va por el pipeline (guardrail escala) |
| resto (insights, recurrentes, soporte, cuenta, reporte…) | 🔶 demo | mock de `data.js`, con banner visible "vista de demostración" |

## Cómo abrirlo

```bash
uv run uvicorn app.main:app --port 8080     # desde la raíz del repo
# abre http://localhost:8080/  → login → escribe tu teléfono → app
```

El primer mensaje que envíes por el chat te pedirá el consentimiento (LOPDP),
igual que por WhatsApp: es el MISMO orquestador. Los gastos que registres por
WhatsApp aparecen en la web y viceversa (misma DB, mismo `user_id` por teléfono).

## Decisiones técnicas (por qué así)

- **Sin dependencias en runtime.** HTML + CSS propio + JS vanilla. Sin Tailwind/Alpine/Chart.js por CDN: en un hackathon la demo no puede depender de que haya internet. Los gráficos son **SVG a mano** (`charts.js`). La única fuente externa es Manrope (Google Fonts), que degrada a `system-ui` sin romper nada.
- **Portable a Jinja2/HTMX** (el stack real de E5): es HTML plano con clases, se copia casi 1:1 a plantillas del backend.
- **Solo tokens del design kit.** `assets/css/tokens.css` es copia de `design/tokens.css`. Ningún color inventado.

## Estructura

```
webapp/
  index.html          Login
  registro.html       Registro + verificación OTP
  recuperar.html      Recuperar contraseña (solicitud → OTP → nueva → éxito)
  onboarding.html     Wizard de primera sesión (5 pasos)
  legal.html          Términos y privacidad (LOPDP)
  app/
    inicio.html       Dashboard (flujo neto, donut, cash flow, presupuestos, insights)
    chat.html         Chat con Luca (motor de respuestas mock por palabras clave)
    movimientos.html  Lista + filtros + "Por confirmar" + detalle + nuevo/editar + eliminar
    presupuestos.html Lista + crear/editar + detalle con proyección
    insights.html     Feed de alertas e insights + detalle + modal de duplicado
    recurrentes.html  Suscripciones + recordatorios
    soporte.html      Centro de ayuda (búsqueda con respuesta directa) + artículos
    tickets.html      Mis tickets + detalle con transcript (H3)
    cuenta.html       Perfil, preferencias, notificaciones, seguridad, datos/privacidad, canales
    reporte.html      Reporte mensual
    404.html · error.html
  assets/
    css/tokens.css    Design tokens (copia del kit)
    css/app.css       Componentes y responsive
    js/data.js        Datos mock (usuario demo + movimientos con los patrones de E2 §15)
    js/ui.js          Íconos (Lucide inline), toasts, modales, tema
    js/shell.js       Sidebar (desktop) + bottom nav (mobile) + topbar + notificaciones
    js/charts.js      Donut, cash flow, proyección, mini-barras, sparkline (SVG)
```

## Qué está cubierto (las 3 historias del PDF)

- **H1 Registro conversacional** → `chat.html` (interpreta, clasifica, pide confirmación) + `movimientos.html` (bandeja "Por confirmar", detalle con texto original y confianza).
- **H2 Presupuesto e insights** → `presupuestos.html` (límite + umbral configurable) + `insights.html` (alertas, proyección, duplicados, sin consejo de inversión) + banner de alerta en el dashboard.
- **H3 Soporte + escalamiento** → `soporte.html` (KB aprobada) + `tickets.html` (ticket con historial, contexto y prioridad). El chat escala consultas sensibles a ticket directo.

## Reglas de marca aplicadas (checklist)

- Montos con cifras tabulares (`tnum`) y formato `$1,234.56`.
- Dorado `#F5B301` **siempre con texto oscuro** (botón enviar, CTA, avatar).
- Gastos en color **neutro** (no rojo); naranja solo para alertas de presupuesto; rojo solo para errores/acciones destructivas del sistema.
- Toda alerta lleva **ícono + texto**, nunca solo color.
- Modo claro y oscuro (toggle en topbar y en Preferencias); tokens ya resuelven ambos.
- Responsive: sidebar en desktop, bottom nav de 5 con chat central en mobile.

## Conectar el backend real

Reemplazar `assets/js/data.js` y las llamadas mock por `fetch` al API de E5. Contratos clave que el front ya espera:
- Chat: `POST /api/chat {user_id, text}` → `{reply, cards?}` (ver `chat.html`).
- El resto de vistas consumen listas (`transactions`, `budgets`, `insights`, `tickets`) con la forma de `data.js`.

## Pendiente / siguiente iteración

- Assets de la mascota (ardilla) — hoy el avatar es el placeholder "L" dorado.
- Importar CSV: el wizard de 3 pasos está diseñado en [PLAN.md](PLAN.md) §3 (C8) pero implementado como acceso directo (toast) — construir la vista completa.
- Coach marks del tour (B6) y swipe actions en móvil (P2).

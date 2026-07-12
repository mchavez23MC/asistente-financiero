"""Páginas legales públicas — política de privacidad y términos.

Meta exige una URL de política de privacidad para publicar la app de WhatsApp
(App → Settings → Basic). Se sirven desde la propia app para que sean accesibles
por la URL pública (ngrok), sin hosting aparte.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_VIGENCIA = date.today().strftime("%d/%m/%Y")

_ESTILO = """
<style>
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1e293b;
       max-width:760px;margin:0 auto;padding:2.5rem 1.25rem;line-height:1.6}
  h1{font-size:1.6rem;margin-bottom:.25rem}
  h2{font-size:1.15rem;margin-top:2rem;color:#0f172a}
  .meta{color:#64748b;font-size:.9rem;margin-bottom:1.5rem}
  a{color:#2563eb}
  ul{padding-left:1.25rem}
  footer{margin-top:2.5rem;color:#94a3b8;font-size:.85rem;border-top:1px solid #e2e8f0;padding-top:1rem}
</style>
"""

_PRIVACIDAD = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Política de Privacidad — Luca, asistente financiero</title>{_ESTILO}</head><body>
<h1>Política de Privacidad</h1>
<p class="meta">Luca — asistente financiero por WhatsApp · Vigente desde {_VIGENCIA}</p>

<p>Esta política describe qué datos tratamos cuando usas <strong>Luca</strong>, un
asistente financiero personal que opera a través de WhatsApp, y con qué finalidad.</p>

<h2>1. Datos que recopilamos</h2>
<ul>
  <li><strong>Número de teléfono</strong> (identificador de tu cuenta) y <strong>nombre de perfil</strong> de WhatsApp.</li>
  <li><strong>Mensajes</strong> que intercambias con el asistente.</li>
  <li><strong>Transacciones y presupuestos</strong> que registras (monto, fecha, categoría, comercio).</li>
</ul>

<h2>2. Para qué usamos tus datos</h2>
<ul>
  <li>Prestarte el servicio: registrar gastos, explicar tu presupuesto y responder soporte.</li>
  <li>Mantener la continuidad de la conversación (memoria de contexto).</li>
  <li>Registro de auditoría de las decisiones del asistente, por seguridad y trazabilidad.</li>
  <li>Escalar tu caso a una persona de nuestro equipo cuando corresponda.</li>
</ul>

<h2>3. Base para el tratamiento</h2>
<p>Tratamos tus datos con tu <strong>consentimiento</strong>, que otorgas al aceptar
el aviso inicial y continuar la conversación.</p>

<h2>4. Quién puede acceder</h2>
<p>Solo tú y, cuando escalas un caso, el agente humano que te atiende. Tus datos
están <strong>aislados por usuario</strong>: nadie más puede consultarlos desde el asistente.</p>

<h2>5. Proveedores que hacen posible el servicio</h2>
<p>Usamos proveedores de infraestructura que procesan datos por nuestra cuenta,
únicamente para operar el servicio: WhatsApp/Meta (mensajería), Supabase
(almacenamiento), y proveedores de modelos de IA (Anthropic y Groq) para procesar
los mensajes. No vendemos tus datos ni los usamos con fines publicitarios.</p>

<h2>6. Conservación</h2>
<p>Conservamos tus datos mientras uses el servicio. Puedes solicitar su eliminación
en cualquier momento (ver punto 8).</p>

<h2>7. Seguridad</h2>
<p>Los temas sensibles (reclamos, sospecha de fraude, asuntos regulatorios) se
derivan automáticamente a un agente humano. El asistente <strong>no brinda asesoría
de inversión</strong> ni recomendaciones sobre en qué colocar tu dinero, y nunca te
pide contraseñas, PIN, CVV ni el número completo de tu tarjeta.</p>

<h2>8. Tus derechos</h2>
<p>Puedes solicitar acceso a tus datos o su eliminación escribiendo
<em>"quiero dar de baja mis datos"</em> por el mismo chat; un agente humano procesará
tu solicitud.</p>

<h2>9. Cambios</h2>
<p>Podemos actualizar esta política; la fecha de vigencia arriba refleja la última versión.</p>

<footer>Este servicio es un proyecto en desarrollo. Para consultas sobre privacidad,
escribe por el mismo canal de WhatsApp.</footer>
</body></html>"""

_TERMINOS = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Términos del Servicio — Luca</title>{_ESTILO}</head><body>
<h1>Términos del Servicio</h1>
<p class="meta">Luca — asistente financiero por WhatsApp · Vigente desde {_VIGENCIA}</p>
<p>Luca te ayuda a registrar gastos, entender tu presupuesto y resolver dudas de
soporte por WhatsApp. Al usarlo aceptas estos términos.</p>
<h2>1. Uso del servicio</h2>
<p>Luca es una herramienta informativa de finanzas personales. <strong>No constituye
asesoría de inversión, financiera ni legal</strong>, y no ejecuta operaciones con dinero real.</p>
<h2>2. Responsabilidad</h2>
<p>La información se ofrece "tal cual". Las decisiones sobre tu dinero son tuyas.
Los temas sensibles se derivan a un agente humano.</p>
<h2>3. Privacidad</h2>
<p>El tratamiento de tus datos se rige por nuestra
<a href="/privacidad">Política de Privacidad</a>.</p>
<footer>Proyecto en desarrollo. Consultas por el mismo canal de WhatsApp.</footer>
</body></html>"""


@router.get("/privacidad", response_class=HTMLResponse)
async def privacidad() -> HTMLResponse:
    return HTMLResponse(_PRIVACIDAD)


@router.get("/terminos", response_class=HTMLResponse)
async def terminos() -> HTMLResponse:
    return HTMLResponse(_TERMINOS)

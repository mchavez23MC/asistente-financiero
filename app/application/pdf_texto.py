"""Extracción de texto de PDFs digitales (ahorro de tokens).

La mayoría de PDFs financieros (estados de cuenta, facturas electrónicas,
roles de pago) son digitales: el texto ya vive dentro del archivo. Enviarlos
al modelo como documento base64 cuesta muchos tokens por página; extraer el
texto con pdfplumber es gratis y sin riesgo de lectura errónea.

Regla de oro (misma que la memoria semántica): esto NUNCA rompe el pipeline.
Si el PDF es escaneado (sin texto extraíble), está corrupto, o pdfplumber
falla por lo que sea, se devuelve None y el llamador manda el PDF como
documento base64 al modelo, exactamente como antes de esta feature.
"""

from __future__ import annotations

import base64
import io
import logging

log = logging.getLogger("e5.pdf_texto")

#: Menos que esto en todo el PDF = casi seguro un escaneo (una foto envuelta
#: en PDF) → mejor que lo vea el modelo con visión.
MIN_CHARS_PDF_DIGITAL = 80

#: Tope de texto que se inyecta al modelo. Un estado de cuenta enorme no
#: necesita ir completo: el agente resume y pregunta de todos modos.
MAX_CHARS_TEXTO_PDF = 15_000


def extraer_texto_pdf(
    data_base64: str,
    *,
    min_chars: int = MIN_CHARS_PDF_DIGITAL,
    max_chars: int = MAX_CHARS_TEXTO_PDF,
) -> str | None:
    """Texto plano de un PDF digital, o None si no se puede/no conviene
    (escaneado, corrupto, ilegible) — en ese caso el PDF debe ir al modelo
    como documento base64 (visión)."""
    try:
        import pdfplumber

        data = base64.b64decode(data_base64, validate=True)
        paginas: list[str] = []
        total = 0
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for pagina in pdf.pages:
                texto = (pagina.extract_text() or "").strip()
                if texto:
                    paginas.append(texto)
                    total += len(texto)
                if total >= max_chars:
                    break
        completo = "\n\n".join(paginas).strip()
        if len(completo) < min_chars:
            log.info(
                "PDF con poco texto extraíble (%d chars): se trata como escaneado.",
                len(completo),
            )
            return None
        if len(completo) > max_chars:
            completo = completo[:max_chars] + "\n[... documento truncado ...]"
        return completo
    except Exception:
        log.warning("Extracción de texto del PDF falló; irá como documento.", exc_info=True)
        return None

"""Tests de la extracción de texto de PDFs digitales (ahorro de tokens).

Cubre el contrato completo: PDF digital → texto; PDF escaneado/corrupto →
None (y en `_bloques_media`, fallback al bloque document base64 de siempre).
"""

import base64

from app.application.agents.principal import _bloques_media
from app.application.pdf_texto import extraer_texto_pdf
from app.domain.models import IncomingMessage, MediaItem


def _pdf_minimo(texto: str) -> bytes:
    """Arma un PDF de una página, válido y con texto real, sin dependencias:
    los offsets del xref se calculan al ensamblar."""
    stream = f"BT /F1 12 Tf 72 720 Td ({texto}) Tj ET".encode("latin-1")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, cuerpo in enumerate(objetos, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + cuerpo + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref_pos).encode()
        + b"\n%%EOF"
    )
    return bytes(out)


_TEXTO_LARGO = "Factura electronica 001-002-000123 Supermaxi total 32.50 del 2026-07-10 " * 3


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# --- extraer_texto_pdf --------------------------------------------------------
def test_pdf_digital_devuelve_su_texto():
    texto = extraer_texto_pdf(_b64(_pdf_minimo(_TEXTO_LARGO)))
    assert texto is not None
    assert "Supermaxi" in texto and "32.50" in texto


def test_pdf_con_poco_texto_se_trata_como_escaneado():
    # Un PDF cuyo texto no llega al mínimo = probablemente una foto envuelta
    # en PDF: debe ir al modelo como documento (None aquí).
    assert extraer_texto_pdf(_b64(_pdf_minimo("hola"))) is None


def test_pdf_corrupto_devuelve_none():
    assert extraer_texto_pdf(_b64(b"esto no es un PDF")) is None


def test_base64_invalido_devuelve_none():
    assert extraer_texto_pdf("@@@no-es-base64@@@") is None


def test_texto_largo_se_trunca():
    texto = extraer_texto_pdf(
        _b64(_pdf_minimo(_TEXTO_LARGO)), min_chars=10, max_chars=50
    )
    assert texto is not None
    assert texto.endswith("[... documento truncado ...]")


# --- _bloques_media: texto extraído vs fallback a document -------------------
def _mensaje_con_pdf(data_base64: str) -> IncomingMessage:
    return IncomingMessage(
        canal="whatsapp",
        telefono="+50370000000",
        texto="te mando la factura",
        media=[
            MediaItem(
                content_type="application/pdf",
                url="https://x",
                data_base64=data_base64,
                filename="factura.pdf",
            )
        ],
    )


def test_bloques_media_pdf_digital_va_como_texto():
    bloques = _bloques_media(_mensaje_con_pdf(_b64(_pdf_minimo(_TEXTO_LARGO))))
    tipos = [b["type"] for b in bloques]
    assert "document" not in tipos  # no viaja el binario: puro texto
    assert bloques[0]["type"] == "text"
    assert bloques[0]["text"].startswith("[CONTENIDO EXTRAÍDO DEL PDF 'factura.pdf']")
    assert "Supermaxi" in bloques[0]["text"]


def test_bloques_media_pdf_escaneado_cae_a_document():
    # PDF sin texto extraíble → el bloque document base64 de siempre (visión).
    b64_escaneado = _b64(_pdf_minimo("x"))
    bloques = _bloques_media(_mensaje_con_pdf(b64_escaneado))
    assert bloques[0]["type"] == "document"
    assert bloques[0]["source"]["data"] == b64_escaneado

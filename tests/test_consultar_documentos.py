"""Tests de la tool consultar_documentos (plan de documentos, módulo 05)."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.application.documents.consultas import consultar_documentos
from app.domain.models import Document


class FakeRepo:
    def __init__(self, docs=None, revienta=False):
        self._docs = docs or []
        self.revienta = revienta
        self.ultima_llamada = None

    def list_documents(self, user_id, desde=None, hasta=None, tipo=None, limite=10):
        if self.revienta:
            raise RuntimeError("relation \"documents\" does not exist")
        self.ultima_llamada = {"desde": desde, "hasta": hasta, "tipo": tipo, "limite": limite}
        docs = self._docs
        if tipo:
            docs = [d for d in docs if d.tipo_documento == tipo]
        return docs[:limite]


def _doc(user_id, tipo="factura_sri", emisor="Supermaxi", total="32.50"):
    return Document(
        user_id=user_id,
        tipo_documento=tipo,
        storage_path="x",
        content_type="application/pdf",
        size_bytes=1,
        sha256=str(uuid4()),
        emisor_nombre=emisor,
        fecha_emision=date(2026, 6, 15),
        total=Decimal(total),
    )


def test_lista_los_documentos_del_usuario():
    uid = uuid4()
    repo = FakeRepo([_doc(uid), _doc(uid, emisor="Farmacia", total="8.00")])
    res = consultar_documentos(repo, uid)
    assert res["cuantos"] == 2
    assert res["documentos"][0]["emisor"] == "Supermaxi"
    assert res["documentos"][0]["total"] == 32.5
    assert res["documentos"][0]["fecha"] == "2026-06-15"


def test_filtra_por_tipo_valido_y_pasa_rango():
    uid = uuid4()
    repo = FakeRepo([_doc(uid, tipo="factura_sri"), _doc(uid, tipo="transferencia")])
    res = consultar_documentos(repo, uid, desde="2026-06-01", hasta="2026-06-30", tipo="factura_sri")
    assert res["cuantos"] == 1
    assert repo.ultima_llamada["desde"] == "2026-06-01"
    assert repo.ultima_llamada["hasta"] == "2026-06-30"


def test_tipo_invalido_se_ignora():
    uid = uuid4()
    repo = FakeRepo([_doc(uid)])
    consultar_documentos(repo, uid, tipo="inventado")
    assert repo.ultima_llamada["tipo"] is None


def test_limite_se_acota():
    uid = uuid4()
    repo = FakeRepo([_doc(uid)])
    consultar_documentos(repo, uid, limite=999)
    assert repo.ultima_llamada["limite"] == 50
    consultar_documentos(repo, uid, limite="no-numero")
    assert repo.ultima_llamada["limite"] == 10


def test_tabla_inexistente_se_degrada_a_vacio():
    """Con DOCS_HABILITADO apagado la tabla puede no existir: no debe romper."""
    res = consultar_documentos(FakeRepo(revienta=True), uuid4())
    assert res == {"documentos": [], "cuantos": 0}

"""Smoke test de métodos de SupabaseRepository que tocan el esquema real.

Los tests con fakes NO ejercitan el adaptador Supabase, así que un NameError o
un typo en esos métodos pasa desapercibido hasta runtime (como ocurrió con
`_utcnow` en complete_review_task). Aquí se instancia el repo real con un `_db`
falso encadenable: no hay red, pero SÍ se ejecuta el código Python del método,
cazando NameErrors y armando mal los payloads.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.adapters.persistence.supabase_repo import SupabaseRepository
from app.domain.models import Transaction


class _FakeQuery:
    """Query builder encadenable que registra la última operación y payload."""

    def __init__(self, registro: dict):
        self._r = registro

    def table(self, nombre):
        self._r["tabla"] = nombre
        return self

    def update(self, datos):
        self._r["op"] = "update"
        self._r["payload"] = datos
        return self

    def insert(self, datos):
        self._r["op"] = "insert"
        self._r["payload"] = datos
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        class R:
            data = []
        return R()


class _FakeDB:
    def __init__(self):
        self.registro = {}

    def table(self, nombre):
        return _FakeQuery(self.registro).table(nombre)


def _repo() -> tuple[SupabaseRepository, _FakeDB]:
    repo = SupabaseRepository.__new__(SupabaseRepository)  # sin conectar
    db = _FakeDB()
    repo._db = db
    return repo, db


def test_complete_review_task_no_revienta_y_marca_completada():
    repo, db = _repo()
    repo.complete_review_task(uuid4(), uuid4())
    assert db.registro["tabla"] == "review_tasks"
    assert db.registro["payload"]["status"] == "completada"
    assert "completed_at" in db.registro["payload"]  # timestamp bien resuelto


def test_insert_transactions_batch_inyecta_source_y_document_id():
    repo, db = _repo()
    doc_id = uuid4()
    tx = Transaction(
        user_id=uuid4(), tipo="gasto", monto=Decimal("18.75"),
        fecha=date(2026, 7, 15), categoria="otros", status="confirmada",
    )
    repo.insert_transactions_batch([tx], document_id=doc_id)
    fila = db.registro["payload"][0]
    assert fila["source"] == "documento"
    assert fila["document_id"] == str(doc_id)
    assert fila["status"] == "confirmada"


def test_insert_transactions_batch_vacio_no_llama_db():
    repo, db = _repo()
    assert repo.insert_transactions_batch([]) == []
    assert db.registro == {}  # no tocó la base

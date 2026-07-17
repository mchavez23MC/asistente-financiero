"""API de revisión de documentos (plan de documentos, módulo 04): materialización,
idempotencia y aislamiento por sesión. Mismo harness OTP que test_webapp_api.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.auth import AuthService
from app.domain.models import (
    Document,
    DocumentItem,
    DocumentItemEstado,
    ReviewTask,
    Transaction,
)
from app.interfaces.api import documentos_api, webapp_api

from tests.test_walking_skeleton import FakeChannel, FakeRepo
from tests.test_webapp_api import _login


class DocsRepo(FakeRepo):
    """FakeRepo + staging de documentos en memoria."""

    def __init__(self) -> None:
        super().__init__()
        self.documents: dict[UUID, Document] = {}
        self.items: dict[UUID, DocumentItem] = {}
        self.tasks: dict[UUID, ReviewTask] = {}
        self.batch_insertado: list[Transaction] = []
        self.batch_document_id = None

    def sembrar(self, user_id, items_data):
        doc = Document(
            id=uuid4(), user_id=user_id, storage_path="x",
            content_type="text/csv", size_bytes=1, sha256=str(uuid4()),
            tipo_documento="estado_cuenta", status="en_revision",
        )
        self.documents[doc.id] = doc
        task = ReviewTask(id=uuid4(), user_id=user_id, document_id=doc.id, resumen="2 movimientos")
        self.tasks[task.id] = task
        for n, (monto, tipo, estado) in enumerate(items_data, start=1):
            it = DocumentItem(
                id=uuid4(), document_id=doc.id, user_id=user_id, n_linea=n,
                fecha=date(2026, 7, n), descripcion_raw=f"MOV {n}",
                monto=Decimal(str(monto)), tipo=tipo, confianza=0.9,
                estado=DocumentItemEstado(estado),
            )
            self.items[it.id] = it
        return task

    def list_documents(self, user_id, desde=None, hasta=None, tipo=None, limite=10):
        return [d for d in self.documents.values() if d.user_id == user_id][:limite]

    def get_review_task(self, user_id, task_id):
        t = self.tasks.get(task_id)
        return t if t and t.user_id == user_id else None

    def list_review_tasks(self, user_id, status=None):
        return [
            t for t in self.tasks.values()
            if t.user_id == user_id and (status is None or t.status == status)
        ]

    def list_document_items(self, document_id, user_id):
        return [i for i in self.items.values() if i.document_id == document_id and i.user_id == user_id]

    def update_document_items(self, user_id, cambios):
        for c in cambios:
            it = self.items.get(UUID(str(c["id"])))
            if it and it.user_id == user_id:
                self.items[it.id] = it.model_copy(update={k: v for k, v in c.items() if k != "id"})

    def insert_transactions_batch(self, transacciones, document_id=None):
        self.batch_insertado = list(transacciones)
        self.batch_document_id = document_id
        return transacciones

    def complete_review_task(self, user_id, task_id):
        t = self.tasks.get(task_id)
        if t and t.user_id == user_id:
            self.tasks[task_id] = t.model_copy(update={"status": "completada"})


def _app():
    app = FastAPI()
    repo = DocsRepo()
    canal = FakeChannel()
    app.state.repo = repo
    app.state.channel = canal
    app.state.auth = AuthService(repo, canal, demo_otp="")
    app.include_router(webapp_api.router)
    app.include_router(documentos_api.router)
    return app, repo, canal


TEL = "+593987651234"


def test_confirmar_materializa_solo_los_aceptados():
    app, repo, canal = _app()
    client = TestClient(app)
    headers = _login(client, canal)
    user = repo.get_or_create_user(TEL)
    task = repo.sembrar(user.id, [(32.50, "gasto", "aceptado"), (12.99, "gasto", "rechazado"), (1200, "ingreso", "aceptado")])

    r = client.post(f"/api/tareas/{task.id}/confirmar", headers=headers)
    assert r.status_code == 200
    assert r.json()["registrados"] == 2  # el rechazado no entra
    assert {float(t.monto) for t in repo.batch_insertado} == {32.50, 1200.0}
    assert all(t.status == "confirmada" for t in repo.batch_insertado)
    assert repo.batch_document_id == task.document_id
    assert repo.tasks[task.id].status == "completada"
    # Aviso por WhatsApp best-effort.
    assert any("registré 2" in msg[1] for msg in canal.enviados)


def test_confirmar_es_idempotente():
    app, repo, canal = _app()
    client = TestClient(app)
    headers = _login(client, canal)
    user = repo.get_or_create_user(TEL)
    task = repo.sembrar(user.id, [(10, "gasto", "aceptado")])

    assert client.post(f"/api/tareas/{task.id}/confirmar", headers=headers).status_code == 200
    r2 = client.post(f"/api/tareas/{task.id}/confirmar", headers=headers)
    assert r2.status_code == 409  # doble click no duplica


def test_patch_items_edita_estado_y_tipo():
    app, repo, canal = _app()
    client = TestClient(app)
    headers = _login(client, canal)
    user = repo.get_or_create_user(TEL)
    task = repo.sembrar(user.id, [(10, "gasto", "pendiente")])
    item = next(iter(repo.items.values()))

    r = client.patch(
        f"/api/tareas/{task.id}/items",
        json={"items": [{"id": str(item.id), "estado": "aceptado", "categoria_sugerida": "comida"}]},
        headers=headers,
    )
    assert r.status_code == 200
    assert repo.items[item.id].estado == "aceptado"
    assert repo.items[item.id].categoria_sugerida == "comida"


def test_tarea_de_otro_usuario_da_404():
    app, repo, canal = _app()
    client = TestClient(app)
    headers = _login(client, canal)  # sesión de TEL
    otro = repo.get_or_create_user("+593999999999")
    task = repo.sembrar(otro.id, [(10, "gasto", "aceptado")])

    assert client.get(f"/api/tareas/{task.id}", headers=headers).status_code == 404
    assert client.post(f"/api/tareas/{task.id}/confirmar", headers=headers).status_code == 404
    # Y no se materializó nada del otro usuario.
    assert repo.batch_insertado == []


def test_endpoints_de_documentos_exigen_sesion():
    app, repo, canal = _app()
    client = TestClient(app)
    assert client.get("/api/tareas").status_code == 401
    assert client.get("/api/documentos").status_code == 401

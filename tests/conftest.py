"""Pytest setup: point the app at a temp SQLite DB before import."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_tmp_dir = Path(tempfile.mkdtemp(prefix="afc-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir / 'test.db'}"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["AI_API_KEY"] = ""  # deterministic analyzer only
os.environ["HIGH_VALUE_THRESHOLD"] = "50000"
os.environ["SETTLEMENT_DELAY_WINDOW_DAYS"] = "3"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import app  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_token(client: TestClient) -> str:
    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
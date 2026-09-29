"""
End-to-end API tests against a real FastAPI app instance (TestClient),
using an isolated on-disk SQLite database so nothing here touches the real
dev database (teacher_ai.db). Exercises the full auth + upload + per-user
isolation flow described in the deployment checklist.
"""
import os

# A throwaway SECRET_KEY so auth.py doesn't require a real .env file to be
# present in whatever environment runs the tests. setdefault() means a real
# configured value (e.g. from backend/.env) always wins.
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_api.db")
TEST_DATABASE_URL = f"sqlite:///{TEST_DB_PATH}"

test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(bind=test_engine)

import main
from database import get_db
from models import Base


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


main.app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(scope="session", autouse=True)
def test_database():
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    Base.metadata.create_all(bind=test_engine)
    yield
    test_engine.dispose()
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)


@pytest.fixture(scope="session")
def client(test_database):
    # Using it as a context manager triggers the FastAPI lifespan (loads
    # the AI models once for the whole test session, matching production).
    with TestClient(main.app) as c:
        yield c


def _register(client, username, password):
    return client.post("/register", json={"username": username, "password": password})


def _login(client, username, password):
    return client.post("/login", data={"username": username, "password": password})


def _auth_headers(client, username, password):
    res = _login(client, username, password)
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_register(client):
    res = _register(client, "apitestuser1", "apitestpass123")
    assert res.status_code == 200
    assert res.json() == {"message": "User registered successfully"}

    # Duplicate username is rejected
    res = _register(client, "apitestuser1", "apitestpass123")
    assert res.status_code == 400


def test_login(client):
    _register(client, "apiloginuser", "apiloginpass1")

    res = _login(client, "apiloginuser", "apiloginpass1")
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_bad_login_message(client):
    _register(client, "apibaduser", "apibadpass123")

    # Wrong password
    res = _login(client, "apibaduser", "wrongpassword")
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid username or password"

    # Unknown username — same generic message, doesn't confirm/deny the
    # username exists.
    res = _login(client, "no_such_user_at_all", "whatever123")
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid username or password"


def test_upload_list_isolation_and_delete(client):
    # Two separate accounts
    _register(client, "apiowner", "apiownerpass1")
    _register(client, "apiother", "apiotherpass1")
    headers_owner = _auth_headers(client, "apiowner", "apiownerpass1")
    headers_other = _auth_headers(client, "apiother", "apiotherpass1")

    # Owner uploads a small TXT
    file_content = b"Bees pollinate flowering plants and are essential for many food crops around the world."
    res = client.post(
        "/upload-book",
        headers=headers_owner,
        files={"file": ("bees.txt", file_content, "text/plain")}
    )
    assert res.status_code == 200, res.text
    upload_data = res.json()
    assert upload_data["chunks"] >= 1
    assert upload_data["pages"] >= 1
    book_id = upload_data["book_id"]

    # Owner sees it in their book list
    res = client.get("/books", headers=headers_owner)
    assert res.status_code == 200
    owner_books = res.json()
    assert any(b["id"] == book_id for b in owner_books)

    # The other account does NOT see it — per-user isolation
    res = client.get("/books", headers=headers_other)
    assert res.status_code == 200
    other_books = res.json()
    assert all(b["id"] != book_id for b in other_books)

    # The other account cannot delete it either (404 — existence isn't
    # leaked to a non-owner)
    res = client.delete(f"/books/{book_id}", headers=headers_other)
    assert res.status_code == 404

    # It's still there for the owner after the failed delete attempt
    res = client.get("/books", headers=headers_owner)
    assert any(b["id"] == book_id for b in res.json())

    # The owner can delete it
    res = client.delete(f"/books/{book_id}", headers=headers_owner)
    assert res.status_code == 200

    # And it's gone
    res = client.get("/books", headers=headers_owner)
    assert all(b["id"] != book_id for b in res.json())


def test_endpoints_require_auth(client):
    res = client.get("/books")
    assert res.status_code == 401

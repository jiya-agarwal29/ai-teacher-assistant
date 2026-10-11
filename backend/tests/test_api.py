"""
End-to-end API tests against a real FastAPI app instance (TestClient),
using an isolated on-disk SQLite database so nothing here touches the real
dev database (teacher_ai.db). Exercises the full auth + upload + per-user
isolation flow described in the deployment checklist.
"""
import json
import os
import re
import shutil
from pathlib import Path

# Dummy SECRET_KEY / GEMINI_API_KEY are set in tests/conftest.py (loaded
# before this module) so this file doesn't need to set them itself.

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_api.db")
TEST_DATABASE_URL = f"sqlite:///{TEST_DB_PATH}"

test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(bind=test_engine)

import database
import main
from database import get_db
from models import Base, DocumentPage, Page

# Redirect uploaded-file storage to a test-only folder -- otherwise uploads
# made here would land in the same backend/uploads/ used by a real dev
# server, where book ids from this throwaway test database could collide
# with (and overwrite) a real book's saved file.
TEST_UPLOAD_DIR = Path(os.path.dirname(__file__)) / "test_uploads"
main.UPLOAD_DIR = TEST_UPLOAD_DIR

# main.py's lifespan (interrupted-job cleanup) and jobs.py's background
# worker each open their own session via `database.SessionLocal()` looked
# up at call time rather than imported by name, specifically so this
# redirect reaches them too -- otherwise they'd silently operate on the
# real dev database (teacher_ai.db) instead of this test one.
database.SessionLocal = TestSessionLocal


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
    shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)
    Base.metadata.create_all(bind=test_engine)
    yield
    test_engine.dispose()
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)


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


@pytest.fixture(scope="session")
def llm_mock_headers(client):
    """
    One shared account, registered and logged in once, for every test below
    that mocks llm.generate()/generate_json() -- /register and /login are
    each rate-limited to 10/minute, so giving each of those tests its own
    account would blow through that budget within one pytest run.
    """
    _register(client, "llm_mock_test_user", "llmmocktestpass1")
    return _auth_headers(client, "llm_mock_test_user", "llmmocktestpass1")


@pytest.fixture(scope="session")
def phase3a_headers(client):
    """Shared account (same rate-limit-budget reasoning as llm_mock_headers above) for the background-processing tests that only need one account."""
    _register(client, "phase3a_test_user", "phase3apass1")
    return _auth_headers(client, "phase3a_test_user", "phase3apass1")


@pytest.fixture(scope="session")
def phase3a_other_headers(client):
    """A second shared account, for the one background-processing test that needs to check cross-user isolation (404 for a non-owner)."""
    _register(client, "phase3a_other_user", "phase3aotherpass1")
    return _auth_headers(client, "phase3a_other_user", "phase3aotherpass1")


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
    assert res.status_code == 202, res.text
    upload_data = res.json()
    # JOBS_SYNC=true in tests (see conftest.py) runs the background
    # processing job inline, so by the time this response is built the
    # book has already reached a terminal status.
    assert upload_data["status"] == "ready"
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


@pytest.mark.real_embeddings_path
def test_upload_processing_fails_when_embedding_service_stays_rate_limited(client, monkeypatch):
    import embeddings
    import jobs
    import llm as llm_module
    from google.genai import errors as genai_errors

    _register(client, "apibusyuser", "apibusypass1")
    headers = _auth_headers(client, "apibusyuser", "apibusypass1")

    class _AlwaysRateLimitedModels:
        def embed_content(self, **kwargs):
            raise genai_errors.APIError(code=429, response_json={"message": "rate limited"}, response=None)

    class _AlwaysRateLimitedClient:
        models = _AlwaysRateLimitedModels()

    monkeypatch.setattr(llm_module, "get_client", lambda: _AlwaysRateLimitedClient())
    monkeypatch.setattr(embeddings.time, "sleep", lambda seconds: None)

    # /upload-book itself still succeeds (202) -- the file is saved and the
    # book created immediately, before any embedding is attempted. The
    # rate limit only ever surfaces in the background job's outcome.
    res = client.post(
        "/upload-book",
        headers=headers,
        files={"file": ("busy.txt", b"some content that needs to be embedded", "text/plain")}
    )
    assert res.status_code == 202
    data = res.json()
    assert data["status"] == "failed"  # JOBS_SYNC=true already ran the job inline
    book_id = data["book_id"]

    status_res = client.get(f"/books/{book_id}/status", headers=headers)
    assert status_res.status_code == 200
    assert status_res.json()["status"] == "failed"
    assert status_res.json()["error"] == jobs.EMBEDDING_BUSY_ERROR

    # Unlike the old synchronous 503 behaviour, the book row (and its saved
    # upload) is kept so the user can retry it later.
    books_res = client.get("/books", headers=headers)
    assert any(b["id"] == book_id and b["status"] == "failed" for b in books_res.json())


BEES_CONTENT = b"Bees pollinate flowering plants and are essential for many food crops around the world."


def _upload_bees_doc(client, headers, filename="bees.txt"):
    res = client.post(
        "/upload-book",
        headers=headers,
        files={"file": (filename, BEES_CONTENT, "text/plain")}
    )
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "ready"  # JOBS_SYNC=true: already processed
    return res.json()["book_id"]


def test_chat_grounded_answer_includes_citations(client, llm_mock_headers, monkeypatch):
    import llm as llm_module

    headers = llm_mock_headers
    book_id = _upload_bees_doc(client, headers, "chat_bees.txt")

    captured = {}

    def fake_generate(prompt, system=None, temperature=0.3, max_tokens=1024):
        captured["prompt"] = prompt
        captured["system"] = system
        return "Bees pollinate plants [1], supporting food crops worldwide."

    monkeypatch.setattr(llm_module, "generate", fake_generate)

    res = client.get("/chat", headers=headers, params={"question": "What do bees do?"})
    assert res.status_code == 200
    data = res.json()
    assert "[1]" in data["answer"]
    assert len(data["sources"]) >= 1
    assert data["sources"][0]["book_name"] == "chat_bees.txt"
    # The prompt sent to the LLM includes a numbered, book+page-labelled source.
    assert "[1]" in captured["prompt"]
    assert "chat_bees.txt" in captured["prompt"]
    assert captured["system"] == main.CHAT_SYSTEM_INSTRUCTION

    client.delete(f"/books/{book_id}", headers=headers)


def test_chat_llm_reports_not_enough_information(client, llm_mock_headers, monkeypatch):
    import llm as llm_module

    headers = llm_mock_headers
    book_id = _upload_bees_doc(client, headers, "chat_bees2.txt")

    monkeypatch.setattr(
        llm_module, "generate",
        lambda *a, **kw: "The uploaded documents do not contain enough information for this question."
    )

    res = client.get("/chat", headers=headers, params={"question": "What do bees do?"})
    assert res.status_code == 200
    data = res.json()
    assert data["answer"] == "The uploaded documents do not contain enough information for this question."
    assert data["sources"] == []

    client.delete(f"/books/{book_id}", headers=headers)


def test_chat_returns_503_on_llm_unavailable(client, llm_mock_headers, monkeypatch):
    import llm as llm_module

    headers = llm_mock_headers
    book_id = _upload_bees_doc(client, headers, "chat_bees3.txt")

    def raise_unavailable(*a, **kw):
        raise llm_module.LLMUnavailableError("boom")

    monkeypatch.setattr(llm_module, "generate", raise_unavailable)

    res = client.get("/chat", headers=headers, params={"question": "What do bees do?"})
    assert res.status_code == 503
    assert res.json()["detail"] == "AI service is busy. Please try again in a minute."

    client.delete(f"/books/{book_id}", headers=headers)


def test_summarize_returns_bullets(client, llm_mock_headers, monkeypatch):
    import llm as llm_module

    headers = llm_mock_headers

    monkeypatch.setattr(
        llm_module, "generate_json",
        lambda prompt, schema, system=None: {"bullets": ["Point one.", "Point two.", "Point three."]}
    )

    res = client.post(
        "/tools/summarize",
        headers=headers,
        json={"text": "Some pasted paragraph about photosynthesis and plant biology."}
    )
    assert res.status_code == 200
    assert res.json()["bullets"] == ["Point one.", "Point two.", "Point three."]


def test_flashcards_drops_cards_with_invalid_source_index(client, llm_mock_headers, monkeypatch):
    import llm as llm_module

    headers = llm_mock_headers
    book_id = _upload_bees_doc(client, headers, "flash_bees.txt")

    monkeypatch.setattr(
        llm_module, "generate_json",
        lambda prompt, schema, system=None: {
            "cards": [
                {"term": "Pollination", "definition": "The process bees help with.", "source_index": 1},
                {"term": "Bad card", "definition": "Should be dropped.", "source_index": 99},
                {"term": "Also bad", "definition": "Should be dropped too.", "source_index": 0},
            ]
        }
    )

    res = client.post("/tools/flashcards", headers=headers, json={"topic": "bees"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["cards"]) == 1
    assert data["cards"][0]["term"] == "Pollination"
    assert "flash_bees.txt" in data["cards"][0]["source"]

    client.delete(f"/books/{book_id}", headers=headers)


def test_semantic_search_returns_503_when_query_embedding_stays_rate_limited(client, llm_mock_headers, monkeypatch, real_embed_query):
    import embeddings
    import llm as llm_module
    from google.genai import errors as genai_errors

    headers = llm_mock_headers
    # Uses the autouse fake embed_documents for a fast, network-free upload;
    # embed_query is restored to the real implementation below, just for
    # the search call, so the rate-limit/retry logic actually runs.
    book_id = _upload_bees_doc(client, headers, "querybusy_bees.txt")

    class _AlwaysRateLimitedModels:
        def embed_content(self, **kwargs):
            raise genai_errors.APIError(code=429, response_json={"message": "rate limited"}, response=None)

    class _AlwaysRateLimitedClient:
        models = _AlwaysRateLimitedModels()

    monkeypatch.setattr(llm_module, "get_client", lambda: _AlwaysRateLimitedClient())
    monkeypatch.setattr(embeddings, "embed_query", real_embed_query)
    monkeypatch.setattr(embeddings.time, "sleep", lambda seconds: None)

    res = client.get("/semantic-search", headers=headers, params={"query": "bees"})
    assert res.status_code == 503
    assert res.json()["detail"] == "AI service is busy. Please try again in a minute."


WATER_CYCLE_CONTENT = b"""The Water Cycle

The water cycle describes the continuous movement of water on, above, and below the surface of the Earth. Water evaporates from oceans, lakes, and rivers due to heat from the sun, turning into water vapor.

Condensation is the process by which water vapor cools and turns into tiny droplets, forming clouds. When enough droplets gather, precipitation falls back to Earth as rain, snow, sleet, or hail.

Plants release water vapor into the air through a process called transpiration, where water absorbed by roots evaporates from leaves. Groundwater is water stored underground in aquifers after infiltrating the soil."""


def _upload_water_cycle_doc(client, headers, filename="water_cycle.txt"):
    res = client.post(
        "/upload-book",
        headers=headers,
        files={"file": (filename, WATER_CYCLE_CONTENT, "text/plain")}
    )
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "ready"  # JOBS_SYNC=true: already processed
    return res.json()["book_id"]


def test_quiz_retries_once_before_falling_back(client, llm_mock_headers, monkeypatch):
    import llm as llm_module
    import main as main_module

    headers = llm_mock_headers
    book_id = _upload_water_cycle_doc(client, headers, "quiz_retry.txt")

    quiz_questions = [
        {
            "id": 1, "type": "short_answer", "bloom_level": "understand",
            "question": "What causes water to evaporate?",
            "correctAnswer": "Heat from the sun.",
            "explanation": "Explained in source 1.", "source_index": 1
        },
        {
            "id": 2, "type": "long_answer", "bloom_level": "analyze",
            "question": "Describe condensation.",
            "correctAnswer": "Water vapor cools into droplets.",
            "explanation": "Explained in source 1.", "source_index": 1
        },
        {
            "id": 3, "type": "scenario", "bloom_level": "apply",
            "question": "A hiker sees clouds forming overhead. What process is at work?",
            "correctAnswer": "Condensation.",
            "explanation": "Explained in source 1.", "source_index": 1
        },
    ]

    quiz_call_count = {"n": 0}
    classifier_call_count = {"n": 0}

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            classifier_call_count["n"] += 1
            # The checker agrees with every intended level -- no mismatch,
            # no regeneration, so the retry-count assertion below stays clean.
            return {
                "items": [
                    {"id": i, "level": q["bloom_level"]}
                    for i, q in enumerate(quiz_questions, start=1)
                ]
            }

        quiz_call_count["n"] += 1
        if quiz_call_count["n"] == 1:
            return {"questions": []}  # first attempt: nothing usable
        return {"questions": quiz_questions}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    # Request exactly the 3 levels fake_generate_json fills (one each) --
    # otherwise the Phase 4B top-up step would (correctly) make an extra
    # call for the other three default levels, which isn't what this test
    # is about.
    res = client.get(
        "/generate-quiz", headers=headers,
        params={"topic": "water cycle", "levels": "understand,apply,analyze", "count": 3}
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["questions"]) == 3
    assert quiz_call_count["n"] == 2  # exactly one retry happened
    assert classifier_call_count["n"] == 1
    assert all(q["bloom_check"] == "verified" for q in data["questions"])

    client.delete(f"/books/{book_id}", headers=headers)


def test_quiz_falls_back_to_hybrid_generator_when_gemini_yields_too_few(client, llm_mock_headers, monkeypatch):
    import llm as llm_module

    headers = llm_mock_headers
    book_id = _upload_water_cycle_doc(client, headers, "quiz_fallback.txt")

    # Gemini returns no usable questions on both the initial attempt and
    # the retry -- the route must fall back to the local hybrid generator
    # rather than returning an empty or broken quiz.
    monkeypatch.setattr(
        llm_module, "generate_json",
        lambda prompt, schema, system=None: {"questions": []}
    )

    res = client.get("/generate-quiz", headers=headers, params={"topic": "water cycle"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["questions"]) >= 3
    assert data["quiz"]  # non-empty legacy text format too

    client.delete(f"/books/{book_id}", headers=headers)


def test_quiz_returns_503_on_llm_unavailable(client, llm_mock_headers, monkeypatch):
    import llm as llm_module

    headers = llm_mock_headers
    book_id = _upload_water_cycle_doc(client, headers, "quiz_busy.txt")

    def raise_unavailable(*a, **kw):
        raise llm_module.LLMUnavailableError("boom")

    monkeypatch.setattr(llm_module, "generate_json", raise_unavailable)

    res = client.get("/generate-quiz", headers=headers, params={"topic": "water cycle"})
    assert res.status_code == 503
    assert res.json()["detail"] == "AI service is busy. Please try again in a minute."

    client.delete(f"/books/{book_id}", headers=headers)


# -----------------------------
# PHASE 3A: BACKGROUND PROCESSING
# -----------------------------
def test_upload_with_no_extractable_text_ends_failed(client, phase3a_headers):
    import jobs

    headers = phase3a_headers

    res = client.post(
        "/upload-book",
        headers=headers,
        files={"file": ("blank.txt", b"   ", "text/plain")}
    )
    assert res.status_code == 202
    data = res.json()
    assert data["status"] == "failed"  # JOBS_SYNC=true: already processed
    book_id = data["book_id"]

    status_res = client.get(f"/books/{book_id}/status", headers=headers)
    assert status_res.status_code == 200
    assert status_res.json()["status"] == "failed"
    assert status_res.json()["error"] == jobs.NO_READABLE_TEXT_ERROR


def test_book_status_and_retry_require_ownership(client, phase3a_headers, phase3a_other_headers):
    headers_owner = phase3a_headers
    headers_other = phase3a_other_headers

    book_id = _upload_bees_doc(client, headers_owner, "status_bees.txt")

    res = client.get(f"/books/{book_id}/status", headers=headers_other)
    assert res.status_code == 404

    res = client.post(f"/books/{book_id}/retry", headers=headers_other)
    assert res.status_code == 404

    res = client.get(f"/books/{book_id}/status", headers=headers_owner)
    assert res.status_code == 200
    assert res.json()["status"] == "ready"

    client.delete(f"/books/{book_id}", headers=headers_owner)


def test_retry_reprocesses_failed_book_to_ready(client, phase3a_headers, monkeypatch):
    import embeddings
    import jobs

    headers = phase3a_headers

    # The autouse fake, captured here so the wrapper below can fall through
    # to it once "should_fail" flips off.
    fake_embed_documents = embeddings.embed_documents
    state = {"should_fail": True}

    def flaky_embed_documents(texts, max_wait_seconds=None, on_batch_done=None):
        if state["should_fail"]:
            raise embeddings.EmbeddingServiceBusyError("busy")
        return fake_embed_documents(texts, max_wait_seconds=max_wait_seconds, on_batch_done=on_batch_done)

    monkeypatch.setattr(embeddings, "embed_documents", flaky_embed_documents)

    res = client.post(
        "/upload-book",
        headers=headers,
        files={"file": ("retry_bees.txt", BEES_CONTENT, "text/plain")}
    )
    assert res.status_code == 202
    data = res.json()
    assert data["status"] == "failed"
    book_id = data["book_id"]

    status_res = client.get(f"/books/{book_id}/status", headers=headers)
    assert status_res.json()["error"] == jobs.EMBEDDING_BUSY_ERROR

    # The original content was always fine -- only the embedding call was
    # failing. Once that clears, retrying should succeed using the same
    # saved file (no new upload).
    state["should_fail"] = False
    retry_res = client.post(f"/books/{book_id}/retry", headers=headers)
    assert retry_res.status_code == 202
    assert retry_res.json()["status"] == "ready"

    status_res2 = client.get(f"/books/{book_id}/status", headers=headers)
    data2 = status_res2.json()
    assert data2["status"] == "ready"
    assert data2["error"] is None
    assert data2["pages_total"] == data2["pages_done"] > 0

    client.delete(f"/books/{book_id}", headers=headers)


def test_retry_rejects_a_book_that_is_not_failed(client, phase3a_headers):
    headers = phase3a_headers

    book_id = _upload_bees_doc(client, headers, "notfailed_bees.txt")

    res = client.post(f"/books/{book_id}/retry", headers=headers)
    assert res.status_code == 400

    client.delete(f"/books/{book_id}", headers=headers)


def test_delete_book_removes_upload_folder(client, phase3a_headers):
    headers = phase3a_headers

    book_id = _upload_bees_doc(client, headers, "folder_bees.txt")

    matching_dirs = list(Path(main.UPLOAD_DIR).glob(f"*/{book_id}"))
    assert len(matching_dirs) == 1
    book_dir = matching_dirs[0]
    assert list(book_dir.glob("original.*"))  # the saved original file is there

    res = client.delete(f"/books/{book_id}", headers=headers)
    assert res.status_code == 200
    assert not book_dir.exists()


def test_interrupted_processing_books_become_failed_on_startup():
    # Simulates a server restart finding a book stuck mid-upload from a
    # worker thread that no longer exists -- main._fail_interrupted_books()
    # is the same function the real lifespan calls on startup.
    db = TestSessionLocal()
    try:
        book = main.Book(name="interrupted.txt", user_id=None, status="processing")
        db.add(book)
        db.commit()
        db.refresh(book)
        book_id = book.id

        failed_count = main._fail_interrupted_books(db)
        assert failed_count >= 1

        db.refresh(book)
        assert book.status == "failed"
        assert book.error == "Processing was interrupted. Please re-upload."
    finally:
        db.query(main.Book).filter(main.Book.id == book_id).delete()
        db.commit()
        db.close()


# -----------------------------
# PHASE 3B: OCR
# -----------------------------
def _make_test_image_bytes(color=(200, 40, 40), size=(20, 20), fmt="PNG"):
    from PIL import Image
    import io
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _get_document_pages(book_id):
    db = TestSessionLocal()
    try:
        rows = db.query(DocumentPage).filter(
            DocumentPage.book_id == book_id
        ).order_by(DocumentPage.page_number).all()
        return [
            {"page_number": r.page_number, "extracted_text": r.extracted_text,
             "method": r.method, "review_status": r.review_status}
            for r in rows
        ]
    finally:
        db.close()


def _get_pages(book_id):
    db = TestSessionLocal()
    try:
        return db.query(Page).filter(Page.book_id == book_id).all()
    finally:
        db.close()


def test_pdf_mixed_text_and_scanned_pages(client, phase3a_headers, monkeypatch):
    import document_parsers
    import llm as llm_module
    import ocr as ocr_module

    headers = phase3a_headers

    monkeypatch.setattr(document_parsers, "parse_pdf", lambda f: [
        {"page_number": 1, "content": "A" * 100},  # plenty of real text -> stays "text"
        {"page_number": 2, "content": "hi"},         # far below OCR_MIN_CHARS -> OCR'd
    ])
    monkeypatch.setattr(ocr_module, "render_pdf_page_to_png", lambda path, page_index: _make_test_image_bytes())
    monkeypatch.setattr(llm_module, "read_image", lambda image_bytes, mime_type, max_wait_seconds=None: "Transcribed handwriting.")

    res = client.post(
        "/upload-book",
        headers=headers,
        files={"file": ("scan.pdf", b"not a real pdf -- parse_pdf is mocked", "application/pdf")}
    )
    assert res.status_code == 202
    data = res.json()
    book_id = data["book_id"]
    # review_ocr defaults to True -- the OCR'd page needs review, so the
    # whole book stops at "needs_review" (JOBS_SYNC=true already ran this).
    assert data["status"] == "needs_review"

    status_res = client.get(f"/books/{book_id}/status", headers=headers)
    status_data = status_res.json()
    assert status_data["status"] == "needs_review"
    assert status_data["source_type"] == "mixed"  # one text page + one OCR'd page

    doc_pages = _get_document_pages(book_id)
    assert len(doc_pages) == 2
    assert doc_pages[0]["method"] == "text"
    assert doc_pages[0]["review_status"] == "auto_approved"
    assert doc_pages[1]["method"] == "ocr"
    assert doc_pages[1]["review_status"] == "needs_review"
    assert doc_pages[1]["extracted_text"] == "Transcribed handwriting."

    # Nothing is chunked/embedded while any page still needs review.
    assert _get_pages(book_id) == []


def test_multiple_images_become_one_book_in_order(client, phase3a_headers, monkeypatch):
    import llm as llm_module

    headers = phase3a_headers

    call_order = []

    def fake_read_image(image_bytes, mime_type, max_wait_seconds=None):
        call_order.append(image_bytes)
        return f"Page text #{len(call_order)}"

    monkeypatch.setattr(llm_module, "read_image", fake_read_image)

    images = [
        ("page_a.png", _make_test_image_bytes(color=(10, 10, 10)), "image/png"),
        ("page_b.png", _make_test_image_bytes(color=(20, 20, 20)), "image/png"),
        ("page_c.png", _make_test_image_bytes(color=(30, 30, 30)), "image/png"),
    ]

    res = client.post(
        "/upload-book",
        headers=headers,
        files=[("files", img) for img in images],
        data={"review_ocr": "false"}
    )
    assert res.status_code == 202
    data = res.json()
    book_id = data["book_id"]
    assert data["status"] == "ready"  # review_ocr=false -- no review gate

    doc_pages = _get_document_pages(book_id)
    assert len(doc_pages) == 3
    assert [p["page_number"] for p in doc_pages] == [1, 2, 3]
    assert [p["method"] for p in doc_pages] == ["ocr", "ocr", "ocr"]
    assert [p["review_status"] for p in doc_pages] == ["auto_approved"] * 3
    # The three images were OCR'd in submission order, not some other order.
    assert doc_pages[0]["extracted_text"] == "Page text #1"
    assert doc_pages[1]["extracted_text"] == "Page text #2"
    assert doc_pages[2]["extracted_text"] == "Page text #3"

    assert len(_get_pages(book_id)) >= 1  # chunked + embedded since all pages were auto-approved

    client.delete(f"/books/{book_id}", headers=headers)


def test_heic_image_extension_is_accepted(client, phase3a_headers, monkeypatch):
    import llm as llm_module
    import ocr as ocr_module

    headers = phase3a_headers

    # A real .heic file isn't needed to prove the upload path accepts the
    # extension -- prepare_image_for_ocr (which would otherwise need to
    # actually decode it) is mocked out here too.
    monkeypatch.setattr(ocr_module, "prepare_image_for_ocr", lambda raw_bytes: _make_test_image_bytes())
    monkeypatch.setattr(llm_module, "read_image", lambda image_bytes, mime_type, max_wait_seconds=None: "Handwritten note.")

    res = client.post(
        "/upload-book",
        headers=headers,
        files={"files": ("photo.heic", b"not real heic bytes -- prepare_image_for_ocr is mocked", "image/heic")},
        data={"review_ocr": "false"}
    )
    assert res.status_code == 202
    assert res.json()["status"] == "ready"

    client.delete(f"/books/{res.json()['book_id']}", headers=headers)


def test_review_ocr_true_leaves_needs_review_with_no_pages(client, phase3a_headers, monkeypatch):
    import llm as llm_module

    headers = phase3a_headers
    monkeypatch.setattr(llm_module, "read_image", lambda image_bytes, mime_type, max_wait_seconds=None: "Some note.")

    res = client.post(
        "/upload-book",
        headers=headers,
        files={"files": ("note.png", _make_test_image_bytes(), "image/png")},
        data={"review_ocr": "true"}
    )
    assert res.status_code == 202
    book_id = res.json()["book_id"]
    assert res.json()["status"] == "needs_review"
    assert _get_pages(book_id) == []
    assert _get_document_pages(book_id)[0]["review_status"] == "needs_review"


def test_review_ocr_false_processes_straight_to_ready(client, phase3a_headers, monkeypatch):
    import llm as llm_module

    headers = phase3a_headers
    monkeypatch.setattr(llm_module, "read_image", lambda image_bytes, mime_type, max_wait_seconds=None: "Some note.")

    res = client.post(
        "/upload-book",
        headers=headers,
        files={"files": ("note.png", _make_test_image_bytes(), "image/png")},
        data={"review_ocr": "false"}
    )
    assert res.status_code == 202
    book_id = res.json()["book_id"]
    assert res.json()["status"] == "ready"
    assert len(_get_pages(book_id)) >= 1
    assert _get_document_pages(book_id)[0]["review_status"] == "auto_approved"

    client.delete(f"/books/{book_id}", headers=headers)


def test_illegible_ocr_text_is_kept(client, phase3a_headers, monkeypatch):
    import llm as llm_module

    headers = phase3a_headers
    monkeypatch.setattr(
        llm_module, "read_image",
        lambda image_bytes, mime_type, max_wait_seconds=None: "Dear [illegible], see you soon."
    )

    res = client.post(
        "/upload-book",
        headers=headers,
        files={"files": ("note.png", _make_test_image_bytes(), "image/png")},
        data={"review_ocr": "false"}
    )
    assert res.status_code == 202
    book_id = res.json()["book_id"]

    pages = _get_pages(book_id)
    assert any("[illegible]" in p.content for p in pages)

    client.delete(f"/books/{book_id}", headers=headers)


def test_ocr_fails_with_friendly_message_on_local_provider(client, phase3a_headers, monkeypatch):
    import llm as llm_module

    headers = phase3a_headers
    monkeypatch.setattr(llm_module, "_provider", lambda: "local")

    res = client.post(
        "/upload-book",
        headers=headers,
        files={"files": ("note.png", _make_test_image_bytes(), "image/png")},
        data={"review_ocr": "false"}
    )
    assert res.status_code == 202
    book_id = res.json()["book_id"]
    assert res.json()["status"] == "failed"

    status_res = client.get(f"/books/{book_id}/status", headers=headers)
    assert status_res.json()["error"] == "Reading scanned or handwritten pages needs the Gemini provider."


def test_max_ocr_pages_enforced(client, phase3a_headers, monkeypatch):
    headers = phase3a_headers
    monkeypatch.setattr(main, "MAX_OCR_PAGES", 2)

    images = [
        ("p1.png", _make_test_image_bytes(), "image/png"),
        ("p2.png", _make_test_image_bytes(), "image/png"),
        ("p3.png", _make_test_image_bytes(), "image/png"),
    ]
    res = client.post(
        "/upload-book",
        headers=headers,
        files=[("files", img) for img in images]
    )
    assert res.status_code == 400
    assert "limit is 2" in res.json()["detail"]


# -----------------------------
# PHASE 3C: OCR REVIEW
# -----------------------------
def test_review_endpoints_require_ownership(client, phase3a_headers, phase3a_other_headers, monkeypatch):
    import llm as llm_module

    headers_owner = phase3a_headers
    headers_other = phase3a_other_headers
    monkeypatch.setattr(llm_module, "read_image", lambda image_bytes, mime_type, max_wait_seconds=None: "Some note.")

    res = client.post(
        "/upload-book",
        headers=headers_owner,
        files={"files": ("ownership_note.png", _make_test_image_bytes(), "image/png")},
        data={"review_ocr": "true"}
    )
    book_id = res.json()["book_id"]
    assert res.json()["status"] == "needs_review"

    assert client.get(f"/books/{book_id}/pages", headers=headers_other).status_code == 404
    assert client.get(f"/books/{book_id}/pages/1/image", headers=headers_other).status_code == 404
    assert client.put(
        f"/books/{book_id}/pages/1", headers=headers_other, json={"extracted_text": "hijacked"}
    ).status_code == 404
    assert client.post(f"/books/{book_id}/pages/1/approve", headers=headers_other).status_code == 404
    assert client.post(f"/books/{book_id}/approve-all", headers=headers_other).status_code == 404
    assert client.post(f"/books/{book_id}/pages/1/reread", headers=headers_other).status_code == 404

    owner_res = client.get(f"/books/{book_id}/pages", headers=headers_owner)
    assert owner_res.status_code == 200
    assert len(owner_res.json()) == 1

    client.delete(f"/books/{book_id}", headers=headers_owner)


def test_page_image_path_traversal_is_blocked(client, phase3a_headers, monkeypatch):
    import llm as llm_module

    headers = phase3a_headers
    monkeypatch.setattr(llm_module, "read_image", lambda image_bytes, mime_type, max_wait_seconds=None: "Some note.")

    res = client.post(
        "/upload-book",
        headers=headers,
        files={"files": ("traversal_note.png", _make_test_image_bytes(), "image/png")},
        data={"review_ocr": "true"}
    )
    book_id = res.json()["book_id"]

    # Simulate a corrupted/malicious image_path pointing outside this book's
    # own upload folder -- the endpoint must refuse to serve it regardless
    # of what's stored in the database.
    db = TestSessionLocal()
    try:
        outside_path = os.path.abspath(__file__)  # a real file, just not inside any book's upload folder
        db.query(DocumentPage).filter(DocumentPage.book_id == book_id).update({"image_path": outside_path})
        db.commit()
    finally:
        db.close()

    res2 = client.get(f"/books/{book_id}/pages/1/image", headers=headers)
    assert res2.status_code == 404

    client.delete(f"/books/{book_id}", headers=headers)


def test_edit_and_approve_page_ends_ready_with_edited_text(client, phase3a_headers, monkeypatch):
    import llm as llm_module

    headers = phase3a_headers
    monkeypatch.setattr(llm_module, "read_image", lambda image_bytes, mime_type, max_wait_seconds=None: "Original OCR text.")

    res = client.post(
        "/upload-book",
        headers=headers,
        files={"files": ("edit_approve_note.png", _make_test_image_bytes(), "image/png")},
        data={"review_ocr": "true"}
    )
    book_id = res.json()["book_id"]
    assert res.json()["status"] == "needs_review"

    edit_res = client.put(
        f"/books/{book_id}/pages/1", headers=headers,
        json={"extracted_text": "Corrected handwriting text."}
    )
    assert edit_res.status_code == 200
    assert edit_res.json()["review_status"] == "needs_review"  # editing a still-pending page doesn't change its status

    approve_res = client.post(f"/books/{book_id}/pages/1/approve", headers=headers)
    assert approve_res.status_code == 200
    data = approve_res.json()
    assert data["pages_remaining"] == 0
    assert data["status"] == "ready"  # JOBS_SYNC=true -- the indexing job already ran inline

    pages = _get_pages(book_id)
    assert len(pages) >= 1
    assert any("Corrected handwriting text." in p.content for p in pages)

    client.delete(f"/books/{book_id}", headers=headers)


def test_approve_all_pages(client, phase3a_headers, monkeypatch):
    import llm as llm_module

    headers = phase3a_headers
    call_count = {"n": 0}

    def fake_read_image(image_bytes, mime_type, max_wait_seconds=None):
        call_count["n"] += 1
        return f"Page {call_count['n']} text."

    monkeypatch.setattr(llm_module, "read_image", fake_read_image)

    images = [
        ("approve_all_p1.png", _make_test_image_bytes(color=(1, 1, 1)), "image/png"),
        ("approve_all_p2.png", _make_test_image_bytes(color=(2, 2, 2)), "image/png"),
    ]
    res = client.post(
        "/upload-book",
        headers=headers,
        files=[("files", img) for img in images],
        data={"review_ocr": "true"}
    )
    book_id = res.json()["book_id"]
    assert res.json()["status"] == "needs_review"

    approve_all_res = client.post(f"/books/{book_id}/approve-all", headers=headers)
    assert approve_all_res.status_code == 200
    data = approve_all_res.json()
    assert data["pages_remaining"] == 0
    assert data["status"] == "ready"

    doc_pages = _get_document_pages(book_id)
    assert len(doc_pages) == 2
    assert all(p["review_status"] == "approved" for p in doc_pages)
    assert len(_get_pages(book_id)) >= 1

    client.delete(f"/books/{book_id}", headers=headers)


def test_reread_page_runs_ocr_again_and_reopens_ready_book(client, phase3a_headers, monkeypatch):
    import llm as llm_module

    headers = phase3a_headers
    responses = {"text": "First OCR pass."}
    monkeypatch.setattr(
        llm_module, "read_image",
        lambda image_bytes, mime_type, max_wait_seconds=None: responses["text"]
    )

    res = client.post(
        "/upload-book",
        headers=headers,
        files={"files": ("reread_note.png", _make_test_image_bytes(), "image/png")},
        data={"review_ocr": "false"}  # auto-approved straight to ready, to also exercise the reopen-to-needs_review path
    )
    book_id = res.json()["book_id"]
    assert res.json()["status"] == "ready"

    responses["text"] = "Second OCR pass (re-read)."
    reread_res = client.post(f"/books/{book_id}/pages/1/reread", headers=headers)
    assert reread_res.status_code == 202
    data = reread_res.json()
    assert data["review_status"] == "needs_review"
    assert data["status"] == "needs_review"  # the book was "ready" -- reopened by the reread

    pages_res = client.get(f"/books/{book_id}/pages", headers=headers)
    page1 = next(p for p in pages_res.json() if p["page_number"] == 1)
    assert page1["review_status"] == "needs_review"
    assert page1["extracted_text"] == "Second OCR pass (re-read)."  # JOBS_SYNC=true -- the reread job already ran inline

    client.delete(f"/books/{book_id}", headers=headers)


def test_editing_ready_book_reindexes_only_that_book(client, phase3a_headers, monkeypatch):
    import llm as llm_module

    headers = phase3a_headers
    monkeypatch.setattr(llm_module, "read_image", lambda image_bytes, mime_type, max_wait_seconds=None: "Initial text about bees.")

    res_a = client.post(
        "/upload-book",
        headers=headers,
        files={"files": ("reindex_note.png", _make_test_image_bytes(color=(5, 5, 5)), "image/png")},
        data={"review_ocr": "false"}
    )
    book_a_id = res_a.json()["book_id"]
    assert res_a.json()["status"] == "ready"

    book_b_id = _upload_bees_doc(client, headers, "reindex_unrelated_bees.txt")
    pages_b_before_ids = {p.id for p in _get_pages(book_b_id)}
    assert pages_b_before_ids

    edit_res = client.put(
        f"/books/{book_a_id}/pages/1", headers=headers,
        json={"extracted_text": "Updated text about wasps."}
    )
    assert edit_res.status_code == 200
    assert edit_res.json()["review_status"] == "needs_review"
    assert edit_res.json()["status"] == "needs_review"

    assert client.get(f"/books/{book_a_id}/status", headers=headers).json()["status"] == "needs_review"
    # Book B is completely untouched by editing book A.
    assert client.get(f"/books/{book_b_id}/status", headers=headers).json()["status"] == "ready"
    assert {p.id for p in _get_pages(book_b_id)} == pages_b_before_ids

    approve_res = client.post(f"/books/{book_a_id}/pages/1/approve", headers=headers)
    assert approve_res.json()["status"] == "ready"

    pages_a_after = _get_pages(book_a_id)
    assert any("Updated text about wasps." in p.content for p in pages_a_after)
    assert not any("Initial text about bees." in p.content for p in pages_a_after)  # old Pages replaced, not appended

    client.delete(f"/books/{book_a_id}", headers=headers)
    client.delete(f"/books/{book_b_id}", headers=headers)


# -----------------------------
# SESSION EXPIRY: /refresh-token
# -----------------------------
def test_refresh_token_returns_new_token_with_later_expiry(client, phase3a_headers):
    import time as time_module
    from jose import jwt as jose_jwt

    headers = phase3a_headers
    old_token = headers["Authorization"].split(" ", 1)[1]
    old_exp = jose_jwt.get_unverified_claims(old_token)["exp"]

    # A real clock tick so the new token's exp (computed from "now" +
    # ACCESS_TOKEN_EXPIRE_MINUTES) lands strictly later than the old one's,
    # not just tied to the same second.
    time_module.sleep(1.1)

    res = client.post("/refresh-token", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    new_exp = jose_jwt.get_unverified_claims(data["access_token"])["exp"]
    assert new_exp > old_exp


def test_refresh_token_rejects_expired_token(client):
    import os
    from datetime import datetime, timedelta, timezone
    from jose import jwt as jose_jwt

    expired_token = jose_jwt.encode(
        {"sub": "nonexistent_refresh_test_user", "exp": datetime.now(timezone.utc) - timedelta(minutes=5)},
        os.environ["SECRET_KEY"],
        algorithm="HS256"
    )

    res = client.post("/refresh-token", headers={"Authorization": f"Bearer {expired_token}"})
    assert res.status_code == 401


# -----------------------------
# PHASE 3 FIX: "DOCUMENTS STILL PENDING" MESSAGE
# -----------------------------
def test_pending_message_shown_when_documents_not_ready(client, phase3a_headers, monkeypatch):
    import llm as llm_module

    headers = phase3a_headers
    monkeypatch.setattr(llm_module, "read_image", lambda image_bytes, mime_type, max_wait_seconds=None: "Some note.")

    res = client.post(
        "/upload-book",
        headers=headers,
        files={"files": ("pending_message_note.png", _make_test_image_bytes(), "image/png")},
        data={"review_ocr": "true"}
    )
    book_id = res.json()["book_id"]
    assert res.json()["status"] == "needs_review"  # has a document, but it isn't "ready"

    expected = "Your documents are still being processed or waiting for review."

    assert client.get("/chat", headers=headers, params={"question": "anything"}).json()["message"] == expected
    assert client.post("/tools/tutor", headers=headers, json={"question": "anything"}).json()["message"] == expected
    assert client.get("/semantic-search", headers=headers, params={"query": "anything"}).json()["message"] == expected
    assert client.post("/tools/flashcards", headers=headers, json={"topic": "anything"}).json()["message"] == expected
    assert client.get("/generate-quiz", headers=headers, params={"topic": "anything"}).json()["quiz"] == expected

    client.delete(f"/books/{book_id}", headers=headers)


def test_no_documents_message_unchanged_when_user_has_none(client, phase3a_other_headers):
    # phase3a_other_headers is only ever used elsewhere as the "other user"
    # checking a 404 on someone else's book -- it never uploads one of its
    # own, so it reliably has zero books here without needing (and burning
    # the register-rate-limit budget on) yet another throwaway account.
    headers = phase3a_other_headers

    assert client.get("/chat", headers=headers, params={"question": "anything"}).json()["message"] == "No documents uploaded"
    assert client.get("/generate-quiz", headers=headers, params={"topic": "anything"}).json()["quiz"] == \
        "Not enough information found in uploaded documents."


# -----------------------------
# PHASE 3 FIX: ROTATE A REVIEW PAGE IMAGE
# -----------------------------
def test_rotate_page_requires_ownership(client, phase3a_headers, phase3a_other_headers, monkeypatch):
    import llm as llm_module

    headers_owner = phase3a_headers
    headers_other = phase3a_other_headers
    monkeypatch.setattr(llm_module, "read_image", lambda image_bytes, mime_type, max_wait_seconds=None: "Some note.")

    res = client.post(
        "/upload-book",
        headers=headers_owner,
        files={"files": ("rotate_ownership.png", _make_test_image_bytes(size=(40, 20)), "image/png")},
        data={"review_ocr": "true"}
    )
    book_id = res.json()["book_id"]

    other_res = client.post(
        f"/books/{book_id}/pages/1/rotate", headers=headers_other, json={"direction": "left"}
    )
    assert other_res.status_code == 404

    client.delete(f"/books/{book_id}", headers=headers_owner)


def test_rotate_page_actually_rotates_image(client, phase3a_headers, monkeypatch):
    import io
    import llm as llm_module
    from PIL import Image

    headers = phase3a_headers
    monkeypatch.setattr(llm_module, "read_image", lambda image_bytes, mime_type, max_wait_seconds=None: "Some note.")

    res = client.post(
        "/upload-book",
        headers=headers,
        files={"files": ("rotate_test.png", _make_test_image_bytes(size=(40, 20)), "image/png")},
        data={"review_ocr": "true"}
    )
    book_id = res.json()["book_id"]

    before_res = client.get(f"/books/{book_id}/pages/1/image", headers=headers)
    assert before_res.status_code == 200
    before_image = Image.open(io.BytesIO(before_res.content))
    assert before_image.size == (40, 20)

    rotate_res = client.post(f"/books/{book_id}/pages/1/rotate", headers=headers, json={"direction": "left"})
    assert rotate_res.status_code == 200
    assert rotate_res.json()["direction"] == "left"

    # A 90-degree rotation swaps width/height -- the clearest proof the
    # saved image file was actually replaced with a rotated version, not
    # just that the endpoint returned 200.
    after_res = client.get(f"/books/{book_id}/pages/1/image", headers=headers)
    assert after_res.status_code == 200
    after_image = Image.open(io.BytesIO(after_res.content))
    assert after_image.size == (20, 40)

    # Rotating the other way should swap the dimensions back.
    rotate_back_res = client.post(f"/books/{book_id}/pages/1/rotate", headers=headers, json={"direction": "right"})
    assert rotate_back_res.status_code == 200
    final_res = client.get(f"/books/{book_id}/pages/1/image", headers=headers)
    final_image = Image.open(io.BytesIO(final_res.content))
    assert final_image.size == (40, 20)

    client.delete(f"/books/{book_id}", headers=headers)


def test_rotate_page_rejects_invalid_direction(client, phase3a_headers, monkeypatch):
    import llm as llm_module

    headers = phase3a_headers
    monkeypatch.setattr(llm_module, "read_image", lambda image_bytes, mime_type, max_wait_seconds=None: "Some note.")

    res = client.post(
        "/upload-book",
        headers=headers,
        files={"files": ("rotate_invalid.png", _make_test_image_bytes(size=(40, 20)), "image/png")},
        data={"review_ocr": "true"}
    )
    book_id = res.json()["book_id"]

    bad_res = client.post(f"/books/{book_id}/pages/1/rotate", headers=headers, json={"direction": "sideways"})
    assert bad_res.status_code == 400

    client.delete(f"/books/{book_id}", headers=headers)


# -----------------------------
# UPLOAD SIZE LIMIT (MAX_UPLOAD_MB)
# -----------------------------
def test_upload_size_limit_boundary(client, phase3a_headers, monkeypatch):
    # A tiny monkeypatched limit keeps the test files themselves small --
    # no need to actually construct a multi-MB upload to prove the 413/202
    # boundary works.
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 1000 / (1024 * 1024))

    headers = phase3a_headers

    over_limit_res = client.post(
        "/upload-book",
        headers=headers,
        files={"file": ("too_big.txt", b"x" * 1200, "text/plain")}
    )
    assert over_limit_res.status_code == 413

    under_limit_res = client.post(
        "/upload-book",
        headers=headers,
        files={"file": ("just_right.txt", b"x" * 500, "text/plain")}
    )
    assert under_limit_res.status_code == 202

    client.delete(f"/books/{under_limit_res.json()['book_id']}", headers=headers)


# -----------------------------
# PHASE 4B: BLOOM-AWARE QUIZ GENERATION + QUESTION TABLE
# -----------------------------
@pytest.fixture(scope="session")
def phase4b_headers(client):
    # /register is rate-limited per IP (10/minute) across the WHOLE test
    # session's accounts -- reset first so this registration doesn't fail
    # just because earlier tests already used up that budget.
    main.limiter.reset()
    _register(client, "phase4b_test_user", "phase4bpass1")
    return _auth_headers(client, "phase4b_test_user", "phase4bpass1")


@pytest.fixture(scope="session")
def phase4b_other_headers(client):
    """Second account, only for the cross-user question-isolation checks."""
    main.limiter.reset()
    _register(client, "phase4b_other_user", "phase4bopass1")
    return _auth_headers(client, "phase4b_other_user", "phase4bopass1")


def test_bloom_levels_endpoint_shape(client, phase4b_headers):
    res = client.get("/bloom/levels", headers=phase4b_headers)
    assert res.status_code == 200
    data = res.json()
    assert [lvl["key"] for lvl in data] == [
        "remember", "understand", "apply", "analyze", "evaluate", "create"
    ]
    for lvl in data:
        assert set(lvl.keys()) == {"key", "label", "order", "description", "question_types"}
        assert isinstance(lvl["question_types"], list) and lvl["question_types"]


def test_generate_quiz_rejects_invalid_levels_param(client, phase4b_headers):
    # /generate-quiz is rate-limited per user (5/minute); phase4b_headers is
    # shared across several tests below, so each one resets the limiter's
    # counters first to get its own clean budget rather than exhausting a
    # budget shared with tests that ran earlier in the same minute.
    main.limiter.reset()
    res = client.get(
        "/generate-quiz", headers=phase4b_headers,
        params={"topic": "anything", "levels": "apply,bogus"}
    )
    assert res.status_code == 422
    assert "bogus" in res.json()["detail"]


def test_generate_quiz_rejects_out_of_range_count(client, phase4b_headers):
    main.limiter.reset()
    too_low = client.get("/generate-quiz", headers=phase4b_headers, params={"topic": "x", "count": 0})
    assert too_low.status_code == 422

    too_high = client.get("/generate-quiz", headers=phase4b_headers, params={"topic": "x", "count": 13})
    assert too_high.status_code == 422


def test_generate_quiz_distributes_requested_levels_into_the_prompt(client, phase4b_headers, monkeypatch):
    import bloom
    import llm as llm_module
    import main as main_module

    headers = phase4b_headers
    main.limiter.reset()
    book_id = _upload_water_cycle_doc(client, headers, "bloom_distribute.txt")

    captured_prompts = []

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": []}
        captured_prompts.append(prompt)
        return {"questions": []}  # force the fallback -- only the prompt text matters here

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    client.get(
        "/generate-quiz", headers=headers,
        params={"topic": "water cycle", "levels": "apply,analyze", "count": 5}
    )

    assert len(captured_prompts) == 2  # one retry, same breakdown both times
    expected_counts = bloom.distribute(5, ["apply", "analyze"])
    for level_key, count in expected_counts.items():
        level = bloom.get_level(level_key)
        for prompt in captured_prompts:
            assert f"write exactly {count} question(s) at this level" in prompt
            assert level.label in prompt

    client.delete(f"/books/{book_id}", headers=headers)


def test_generate_quiz_fallback_path_labels_levels_with_verb_classifier(client, phase4b_headers, monkeypatch):
    import bloom
    import llm as llm_module

    headers = phase4b_headers
    main.limiter.reset()
    book_id = _upload_water_cycle_doc(client, headers, "bloom_fallback.txt")

    monkeypatch.setattr(
        llm_module, "generate_json",
        lambda prompt, schema, system=None: {"questions": []}
    )

    res = client.get("/generate-quiz", headers=headers, params={"topic": "water cycle"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["questions"]) >= 3
    for q in data["questions"]:
        assert q["bloom_check"] == "unchecked"
        assert q["detected_level"] is None
        assert q["bloom_level"] in bloom.level_keys()

    client.delete(f"/books/{book_id}", headers=headers)


def test_generate_quiz_checker_unavailable_leaves_questions_unchecked(client, phase4b_headers, monkeypatch):
    import llm as llm_module
    import main as main_module

    headers = phase4b_headers
    main.limiter.reset()
    book_id = _upload_water_cycle_doc(client, headers, "bloom_checker_down.txt")

    questions = [
        {"id": 1, "type": "true_false", "bloom_level": "remember",
         "question": "True or False: evaporation is caused by heat.",
         "options": ["True", "False"], "correctAnswer": "A",
         "explanation": "Explained in source 1.", "source_index": 1},
        {"id": 2, "type": "short_answer", "bloom_level": "understand",
         "question": "Explain condensation in your own words.",
         "correctAnswer": "Water vapor cools into droplets.",
         "explanation": "Explained in source 1.", "source_index": 1},
        {"id": 3, "type": "scenario", "bloom_level": "apply",
         "question": "A hiker sees clouds. Apply the water cycle to explain why.",
         "correctAnswer": "Condensation is occurring.",
         "explanation": "Explained in source 1.", "source_index": 1},
    ]

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            raise llm_module.LLMUnavailableError("checker is down")
        return {"questions": questions}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    res = client.get(
        "/generate-quiz", headers=headers,
        params={"topic": "water cycle", "levels": "remember,understand,apply", "count": 3}
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["questions"]) == 3
    for q in data["questions"]:
        assert q["bloom_check"] == "unchecked"
        assert q["detected_level"] is None

    client.delete(f"/books/{book_id}", headers=headers)


def test_generate_quiz_mismatch_regenerates_once_then_flags_remaining(client, phase4b_headers, monkeypatch):
    import llm as llm_module
    import main as main_module

    headers = phase4b_headers
    main.limiter.reset()
    book_id = _upload_water_cycle_doc(client, headers, "bloom_mismatch.txt")

    initial = [
        {"id": 1, "type": "true_false", "bloom_level": "remember",
         "question": "True or False: water evaporates due to heat from the sun.",
         "options": ["True", "False"], "correctAnswer": "A",
         "explanation": "Explained in source 1.", "source_index": 1},
        {"id": 2, "type": "short_answer", "bloom_level": "understand",
         "question": "Explain what causes condensation.",
         "correctAnswer": "Water vapor cooling into droplets.",
         "explanation": "Explained in source 1.", "source_index": 1},
        {"id": 3, "type": "scenario", "bloom_level": "apply",
         "question": "A hiker notices clouds forming. Apply the water cycle to explain why.",
         "correctAnswer": "Condensation is occurring.",
         "explanation": "Explained in source 1.", "source_index": 1},
    ]
    regenerated = [
        {"id": 10, "type": "short_answer", "bloom_level": "understand",
         "question": "Summarize why condensation happens in your own words.",
         "correctAnswer": "Water vapor cools into droplets.",
         "explanation": "Explained in source 1.", "source_index": 1},
        {"id": 11, "type": "scenario", "bloom_level": "apply",
         "question": "A farmer wants to use the water cycle to plan irrigation. How would they apply it?",
         "correctAnswer": "They would track evaporation and precipitation patterns.",
         "explanation": "Explained in source 1.", "source_index": 1},
    ]

    quiz_call = {"n": 0}
    classifier_call = {"n": 0}

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            classifier_call["n"] += 1
            if classifier_call["n"] == 1:
                # Agrees on the "remember" question; disagrees on the other two.
                return {"items": [
                    {"id": 1, "level": "remember"},
                    {"id": 2, "level": "apply"},
                    {"id": 3, "level": "create"},
                ]}
            # Recheck of the two regenerated replacements: agrees on the
            # first, still disagrees on the second.
            return {"items": [
                {"id": 1, "level": "understand"},
                {"id": 2, "level": "create"},
            ]}

        quiz_call["n"] += 1
        if quiz_call["n"] == 1:
            return {"questions": initial}
        return {"questions": regenerated}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    res = client.get(
        "/generate-quiz", headers=headers,
        params={"topic": "water cycle", "levels": "remember,understand,apply", "count": 3}
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["questions"]) == 3

    by_level = {q["bloom_level"]: q for q in data["questions"]}
    assert by_level["remember"]["bloom_check"] == "verified"
    assert by_level["remember"]["detected_level"] is None

    assert by_level["understand"]["bloom_check"] == "verified"
    assert by_level["understand"]["question"] == "Summarize why condensation happens in your own words."
    assert by_level["understand"]["detected_level"] is None

    assert by_level["apply"]["bloom_check"] == "flagged"
    assert by_level["apply"]["detected_level"] == "create"
    assert by_level["apply"]["question"] == (
        "A farmer wants to use the water cycle to plan irrigation. How would they apply it?"
    )

    assert quiz_call["n"] == 2  # initial generation + one regeneration
    assert classifier_call["n"] == 2  # initial check + one recheck

    client.delete(f"/books/{book_id}", headers=headers)


def _bloom_fix_question(qid, q_type, level, question, answer, options=None):
    q = {
        "id": qid, "type": q_type, "bloom_level": level,
        "question": question, "correctAnswer": answer,
        "explanation": "Explained in source 1.", "source_index": 1
    }
    if q_type == "true_false":
        q["options"] = ["True", "False"]
    elif q_type == "mcq":
        q["options"] = options or ["Alpha", "Beta", "Gamma", "Delta"]
    return q


def test_generate_quiz_top_up_fills_a_missing_level(client, phase4b_headers, monkeypatch):
    import llm as llm_module
    import main as main_module

    headers = phase4b_headers
    main.limiter.reset()
    book_id = _upload_water_cycle_doc(client, headers, "bloom_topup_fills.txt")

    # The initial batch covers remember/understand/apply but leaves analyze
    # completely missing even though it was requested.
    initial = [
        _bloom_fix_question(1, "true_false", "remember", "True or False: heat causes evaporation.", "A"),
        _bloom_fix_question(2, "short_answer", "understand", "Explain condensation in your own words.", "Water vapor cools into droplets."),
        _bloom_fix_question(3, "scenario", "apply", "A hiker sees clouds. Apply the water cycle to explain why.", "Condensation is occurring."),
    ]
    topped_up = [
        _bloom_fix_question(4, "long_answer", "analyze", "Compare evaporation and condensation as opposing processes.", "They are reverse phase changes of water."),
    ]

    quiz_call = {"n": 0}
    classifier_call = {"n": 0}
    captured_topup_prompt = {"text": None}

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            classifier_call["n"] += 1
            # Whatever batch gets sent, the checker agrees with it -- this
            # test is only about the top-up mechanics, not mismatches.
            if classifier_call["n"] == 1:
                return {"items": [{"id": i, "level": q["bloom_level"]} for i, q in enumerate(initial, start=1)]}
            return {"items": [{"id": i, "level": q["bloom_level"]} for i, q in enumerate(topped_up, start=1)]}

        quiz_call["n"] += 1
        if quiz_call["n"] == 1:
            return {"questions": initial}
        captured_topup_prompt["text"] = prompt
        return {"questions": topped_up}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    res = client.get(
        "/generate-quiz", headers=headers,
        params={"topic": "water cycle", "levels": "remember,understand,apply,analyze", "count": 4}
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["questions"]) == 4
    assert data["level_counts"] == {"remember": 1, "understand": 1, "apply": 1, "analyze": 1}
    assert data["warnings"] == []
    assert all(q["bloom_check"] == "verified" for q in data["questions"])

    assert quiz_call["n"] == 2  # the main batch + exactly one top-up call
    assert classifier_call["n"] == 2  # the main check + checking the topped-up question
    assert "write exactly 1 question(s) at this level" in captured_topup_prompt["text"]
    assert "Analyze" in captured_topup_prompt["text"]
    assert "Remember" not in captured_topup_prompt["text"]  # only the missing level was asked for

    client.delete(f"/books/{book_id}", headers=headers)


def test_generate_quiz_top_up_still_short_adds_a_warning_without_retrying(client, phase4b_headers, monkeypatch):
    import llm as llm_module
    import main as main_module

    headers = phase4b_headers
    main.limiter.reset()
    book_id = _upload_water_cycle_doc(client, headers, "bloom_topup_short.txt")

    initial = [
        _bloom_fix_question(1, "true_false", "remember", "True or False: heat causes evaporation.", "A"),
        _bloom_fix_question(2, "short_answer", "understand", "Explain condensation in your own words.", "Water vapor cools into droplets."),
        _bloom_fix_question(3, "scenario", "apply", "A hiker sees clouds. Apply the water cycle to explain why.", "Condensation is occurring."),
    ]

    quiz_call = {"n": 0}

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": [{"id": i, "level": q["bloom_level"]} for i, q in enumerate(initial, start=1)]}

        quiz_call["n"] += 1
        if quiz_call["n"] == 1:
            return {"questions": initial}
        return {"questions": []}  # the one top-up attempt also comes up empty

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    res = client.get(
        "/generate-quiz", headers=headers,
        params={"topic": "water cycle", "levels": "remember,understand,apply,analyze", "count": 4}
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["questions"]) == 3  # still short the "analyze" question
    assert data["warnings"] == ["Could not create enough Analyze questions -- try again or pick other levels"]
    assert quiz_call["n"] == 2  # main batch + exactly one top-up attempt, no further retry

    client.delete(f"/books/{book_id}", headers=headers)


def test_generate_quiz_no_top_up_call_when_nothing_is_missing(client, phase4b_headers, monkeypatch):
    import llm as llm_module
    import main as main_module

    headers = phase4b_headers
    main.limiter.reset()
    book_id = _upload_water_cycle_doc(client, headers, "bloom_topup_not_needed.txt")

    full_batch = [
        _bloom_fix_question(1, "true_false", "remember", "True or False: heat causes evaporation.", "A"),
        _bloom_fix_question(2, "short_answer", "understand", "Explain condensation in your own words.", "Water vapor cools into droplets."),
        _bloom_fix_question(3, "scenario", "apply", "A hiker sees clouds. Apply the water cycle to explain why.", "Condensation is occurring."),
    ]

    quiz_call = {"n": 0}

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": [{"id": i, "level": q["bloom_level"]} for i, q in enumerate(full_batch, start=1)]}
        quiz_call["n"] += 1
        return {"questions": full_batch}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    res = client.get(
        "/generate-quiz", headers=headers,
        params={"topic": "water cycle", "levels": "remember,understand,apply", "count": 3}
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["questions"]) == 3
    assert data["warnings"] == []
    assert quiz_call["n"] == 1  # no top-up call -- every requested level was already filled

    client.delete(f"/books/{book_id}", headers=headers)


def test_generate_quiz_saves_questions_and_question_crud_lifecycle(
    client, phase4b_headers, phase4b_other_headers, monkeypatch
):
    import llm as llm_module
    import main as main_module

    headers = phase4b_headers
    other_headers = phase4b_other_headers
    main.limiter.reset()
    filename = "bloom_crud.txt"
    book_id = _upload_water_cycle_doc(client, headers, filename)

    questions = [
        {"id": 1, "type": "true_false", "bloom_level": "remember",
         "question": "True or False: evaporation is caused by heat from the sun.",
         "options": ["True", "False"], "correctAnswer": "A",
         "explanation": "Explained in source 1.", "source_index": 1},
        {"id": 2, "type": "short_answer", "bloom_level": "understand",
         "question": "Explain condensation in your own words.",
         "correctAnswer": "Water vapor cools into droplets.",
         "explanation": "Explained in source 1.", "source_index": 1},
        {"id": 3, "type": "scenario", "bloom_level": "apply",
         "question": "A hiker sees clouds forming overhead. Apply the water cycle to explain why.",
         "correctAnswer": "Condensation is occurring.",
         "explanation": "Explained in source 1.", "source_index": 1},
    ]

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": [
                {"id": i, "level": q["bloom_level"]} for i, q in enumerate(questions, start=1)
            ]}
        return {"questions": questions}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    res = client.get(
        "/generate-quiz", headers=headers,
        params={"topic": "water cycle", "levels": "remember,understand,apply", "count": 3}
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["questions"]) == 3
    assert all(q["bloom_check"] == "verified" for q in data["questions"])
    assert all(q["source"]["book_name"] == filename for q in data["questions"])

    question_ids = [q["question_id"] for q in data["questions"]]
    editable_id = next(q["question_id"] for q in data["questions"] if q["type"] == "short_answer")

    # GET /questions: the owner sees everything just generated.
    list_res = client.get("/questions", headers=headers, params={"topic": "water cycle"})
    assert list_res.status_code == 200
    list_data = list_res.json()
    listed_ids = {item["question_id"] for item in list_data["items"]}
    assert set(question_ids) <= listed_ids
    sample = next(item for item in list_data["items"] if item["question_id"] == editable_id)
    assert sample["source"]["book_name"] == filename
    assert sample["bloom_check"] == "verified"

    # Cross-user isolation: another user can't see, edit, or delete them.
    assert client.get("/questions", headers=other_headers).json()["total"] == 0
    assert client.patch(
        f"/questions/{editable_id}", headers=other_headers, json={"explanation": "hijack"}
    ).status_code == 404
    assert client.delete(f"/questions/{editable_id}", headers=other_headers).status_code == 404

    # PATCH bloom_level as the owner marks it teacher_set (short_answer is
    # also a valid type at "apply", so this stays a legal level for it).
    patch_res = client.patch(
        f"/questions/{editable_id}", headers=headers, json={"bloom_level": "apply"}
    )
    assert patch_res.status_code == 200
    patched = patch_res.json()
    assert patched["bloom_level"] == "apply"
    assert patched["bloom_check"] == "teacher_set"
    assert patched["detected_level"] is None

    # Deleting the book deletes its questions.
    assert client.delete(f"/books/{book_id}", headers=headers).status_code == 200
    after_delete = client.get("/questions", headers=headers, params={"topic": "water cycle"}).json()
    assert all(item["question_id"] not in question_ids for item in after_delete["items"])


def test_patch_question_mcq_validation_rules(client, phase4b_headers, monkeypatch):
    import llm as llm_module

    headers = phase4b_headers
    main.limiter.reset()
    book_id = _upload_water_cycle_doc(client, headers, "bloom_patch_mcq.txt")

    monkeypatch.setattr(llm_module, "generate_json", lambda prompt, schema, system=None: {"questions": []})

    res = client.get("/generate-quiz", headers=headers, params={"topic": "water cycle"})
    assert res.status_code == 200
    mcq_question = next(q for q in res.json()["questions"] if q["type"] == "mcq")
    question_id = mcq_question["question_id"]

    bad = client.patch(
        f"/questions/{question_id}", headers=headers,
        json={"options": ["A", "A", "B", "C"]}
    )
    assert bad.status_code == 400

    good = client.patch(
        f"/questions/{question_id}", headers=headers,
        json={"options": ["Alpha", "Beta", "Gamma", "Delta"], "correct_answer": "B"}
    )
    assert good.status_code == 200
    assert good.json()["options"] == ["Alpha", "Beta", "Gamma", "Delta"]
    assert good.json()["correct_answer"] == "B"

    client.delete(f"/books/{book_id}", headers=headers)


def test_generate_quiz_logs_drop_reasons_without_leaking_question_text(client, phase4b_headers, monkeypatch, caplog):
    import logging
    import llm as llm_module

    headers = phase4b_headers
    main.limiter.reset()
    book_id = _upload_water_cycle_doc(client, headers, "bloom_drop_log.txt")

    secret_marker = "zzz_super_secret_marker_zzz"
    valid_questions = [
        _bloom_fix_question(1, "scenario", "analyze", "Compare the processes of evaporation and condensation.", "They are reverse phase changes of water."),
        _bloom_fix_question(2, "scenario", "analyze", "Examine how temperature differences drive the water cycle.", "Temperature differences drive evaporation and condensation."),
        _bloom_fix_question(3, "scenario", "analyze", "Analyze why groundwater levels change seasonally.", "Seasonal infiltration and evaporation change groundwater levels."),
    ]
    # Dropped for a known reason (true_false isn't an allowed type at
    # "analyze") -- its question text carries a marker that must never
    # reach the log, only the drop reason category should.
    dropped_question = {
        "id": 4, "type": "true_false", "bloom_level": "analyze",
        "question": f"True or False: the {secret_marker} affects groundwater.",
        "options": ["True", "False"], "correctAnswer": "A",
        "explanation": "Explained in source 1.", "source_index": 1
    }

    def fake_generate_json(prompt, schema, system=None):
        if schema is main.CLASSIFIER_SCHEMA:
            return {"items": [{"id": i, "level": "analyze"} for i in range(1, 4)]}
        return {"questions": valid_questions + [dropped_question]}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    with caplog.at_level(logging.INFO, logger="main"):
        res = client.get(
            "/generate-quiz", headers=headers,
            params={"topic": "water cycle", "levels": "analyze", "count": 3}
        )
    assert res.status_code == 200
    assert len(res.json()["questions"]) == 3

    drop_logs = [r.getMessage() for r in caplog.records if "dropped" in r.getMessage()]
    assert any("dropped 1: type 'true_false' not allowed for 'analyze'" in msg for msg in drop_logs)
    assert all(secret_marker not in r.getMessage() for r in caplog.records)

    client.delete(f"/books/{book_id}", headers=headers)


def test_list_questions_rejects_invalid_filters(client, phase4b_headers):
    headers = phase4b_headers
    assert client.get("/questions", headers=headers, params={"level": "bogus"}).status_code == 422
    assert client.get("/questions", headers=headers, params={"level": "apply,bogus"}).status_code == 422
    assert client.get("/questions", headers=headers, params={"check": "bogus"}).status_code == 422
    assert client.get("/questions", headers=headers, params={"limit": 0}).status_code == 422
    assert client.get("/questions", headers=headers, params={"offset": -1}).status_code == 422


def test_list_questions_filters_by_multiple_levels(client, phase4b_headers, monkeypatch):
    import llm as llm_module
    import main as main_module

    headers = phase4b_headers
    main.limiter.reset()
    book_id = _upload_water_cycle_doc(client, headers, "bloom_multilevel_filter.txt")

    seed = [
        _bloom_fix_question(1, "true_false", "remember", "Multilevel filter remember question.", "A"),
        _bloom_fix_question(2, "short_answer", "understand", "Multilevel filter understand question.", "Answer."),
        _bloom_fix_question(3, "scenario", "apply", "Multilevel filter apply question.", "Answer."),
    ]

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": [{"id": i, "level": q["bloom_level"]} for i, q in enumerate(seed, start=1)]}
        return {"questions": seed}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    res = client.get(
        "/generate-quiz", headers=headers,
        params={"topic": "multilevel filter topic", "levels": "remember,understand,apply", "count": 3}
    )
    assert res.status_code == 200

    list_res = client.get(
        "/questions", headers=headers,
        params={"topic": "multilevel filter topic", "level": "remember,apply"}
    )
    assert list_res.status_code == 200
    items = list_res.json()["items"]
    levels_seen = {item["bloom_level"] for item in items}
    assert levels_seen == {"remember", "apply"}
    assert all(item["bloom_level"] != "understand" for item in items)

    client.delete(f"/books/{book_id}", headers=headers)


# -----------------------------
# PHASE 4B EXTRA: VARIETY -- RECENT QUESTIONS STAY USER-SCOPED
# -----------------------------
def test_generate_quiz_recent_questions_appear_in_prompt_and_stay_user_scoped(
    client, phase4b_headers, phase4b_other_headers, monkeypatch
):
    import llm as llm_module
    import main as main_module

    headers_a = phase4b_headers
    headers_b = phase4b_other_headers
    main.limiter.reset()

    book_a = _upload_water_cycle_doc(client, headers_a, "bloom_recent_a.txt")

    marker = "zzz_recent_marker_zzz"
    seed_questions = [
        _bloom_fix_question(1, "scenario", "apply", f"Apply the water cycle to explain {marker} in a city.", "Because of evaporation and condensation."),
        _bloom_fix_question(2, "scenario", "apply", f"A farmer asks about {marker} and irrigation timing.", "Irrigation should follow the water cycle stages."),
        _bloom_fix_question(3, "scenario", "apply", f"Explain {marker} for a coastal town's water supply.", "Coastal towns rely on evaporation-driven rainfall."),
    ]
    captured_prompts_a = []

    def fake_generate_json_seed(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": [{"id": i, "level": "apply"} for i in range(1, 4)]}
        captured_prompts_a.append(prompt)
        return {"questions": seed_questions}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json_seed)

    # First quiz seeds the Question table with the marker-bearing texts.
    res1 = client.get(
        "/generate-quiz", headers=headers_a,
        params={"topic": "water cycle", "levels": "apply", "count": 3}
    )
    assert res1.status_code == 200

    # Second quiz, same topic: this prompt must list those seeded
    # questions as "do not repeat".
    main.limiter.reset()
    second_batch = [
        _bloom_fix_question(10, "scenario", "apply", "A brand new distinct question about the water cycle.", "A fresh distinct answer."),
        _bloom_fix_question(11, "scenario", "apply", "Another brand new distinct question about clouds.", "Another fresh distinct answer."),
        _bloom_fix_question(12, "scenario", "apply", "A third brand new distinct question about rainfall.", "A third fresh distinct answer."),
    ]

    def fake_generate_json_second(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": [{"id": i, "level": "apply"} for i in range(1, 4)]}
        captured_prompts_a.append(prompt)
        return {"questions": second_batch}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json_second)

    res2 = client.get(
        "/generate-quiz", headers=headers_a,
        params={"topic": "water cycle", "levels": "apply", "count": 3}
    )
    assert res2.status_code == 200

    last_prompt_a = captured_prompts_a[-1]
    assert marker in last_prompt_a
    assert "Do not repeat or closely paraphrase" in last_prompt_a

    # A second user, same topic string, must never see user A's questions
    # (nor even the "do not repeat" block at all, since they have no
    # history of their own yet).
    main.limiter.reset()
    book_b = _upload_water_cycle_doc(client, headers_b, "bloom_recent_b.txt")
    captured_prompts_b = []

    def fake_generate_json_b(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": [{"id": i, "level": "apply"} for i in range(1, 4)]}
        captured_prompts_b.append(prompt)
        return {"questions": [
            _bloom_fix_question(1, "scenario", "apply", "User B's own distinct water cycle question.", "User B's own answer."),
            _bloom_fix_question(2, "scenario", "apply", "User B's second distinct question.", "User B's second answer."),
            _bloom_fix_question(3, "scenario", "apply", "User B's third distinct question.", "User B's third answer."),
        ]}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json_b)

    res3 = client.get(
        "/generate-quiz", headers=headers_b,
        params={"topic": "water cycle", "levels": "apply", "count": 3}
    )
    assert res3.status_code == 200
    assert marker not in captured_prompts_b[0]
    assert "Do not repeat" not in captured_prompts_b[0]

    client.delete(f"/books/{book_a}", headers=headers_a)
    client.delete(f"/books/{book_b}", headers=headers_b)


# -----------------------------
# PHASE 4D: SINGLE-QUESTION REGENERATE + QUESTION BANK SUMMARY
# -----------------------------
@pytest.fixture(scope="session")
def phase4d_headers(client):
    main.limiter.reset()
    _register(client, "phase4d_test_user", "phase4dpass1")
    return _auth_headers(client, "phase4d_test_user", "phase4dpass1")


@pytest.fixture(scope="session")
def phase4d_other_headers(client):
    main.limiter.reset()
    _register(client, "phase4d_other_user", "phase4dopass1")
    return _auth_headers(client, "phase4d_other_user", "phase4dopass1")


def _seed_three_questions_and_get_apply_target(client, headers, monkeypatch, filename, topic="water cycle"):
    """
    Generates a 3-question quiz (remember/understand/apply, all agreed by
    the mocked checker) via the real /generate-quiz route, and returns
    (book_id, the_apply_question) -- the question dict the regenerate
    tests below act on.
    """
    import llm as llm_module
    import main as main_module

    main.limiter.reset()
    book_id = _upload_water_cycle_doc(client, headers, filename)

    seed = [
        _bloom_fix_question(1, "true_false", "remember", "Original remember question text.", "A"),
        _bloom_fix_question(2, "short_answer", "understand", "Original understand question text.", "Original understand answer."),
        _bloom_fix_question(3, "scenario", "apply", "Original apply question text.", "Original apply answer."),
    ]

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": [{"id": i, "level": q["bloom_level"]} for i, q in enumerate(seed, start=1)]}
        return {"questions": seed}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    res = client.get(
        "/generate-quiz", headers=headers,
        params={"topic": topic, "levels": "remember,understand,apply", "count": 3}
    )
    assert res.status_code == 200, res.text
    target = next(q for q in res.json()["questions"] if q["bloom_level"] == "apply")
    return book_id, target


def test_regenerate_question_keeps_id_and_level_updates_text(client, phase4d_headers, monkeypatch):
    import llm as llm_module
    import main as main_module

    headers = phase4d_headers
    book_id, target = _seed_three_questions_and_get_apply_target(client, headers, monkeypatch, "regen_basic.txt")
    question_id = target["question_id"]

    main.limiter.reset()
    replacement = _bloom_fix_question(1, "scenario", "apply", "A brand new distinct apply-level question.", "A brand new answer.")

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": [{"id": 1, "level": "apply"}]}
        return {"questions": [replacement]}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    res = client.post(f"/questions/{question_id}/regenerate", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["question_id"] == question_id
    assert data["bloom_level"] == "apply"
    assert data["bloom_check"] == "verified"
    assert data["question"] == "A brand new distinct apply-level question."
    assert data["question"] != target["question"]

    # Confirm it's the SAME row, updated in place.
    list_res = client.get("/questions", headers=headers, params={"topic": "water cycle"})
    matching = [item for item in list_res.json()["items"] if item["question_id"] == question_id]
    assert len(matching) == 1
    assert matching[0]["question"] == "A brand new distinct apply-level question."

    client.delete(f"/books/{book_id}", headers=headers)


def test_regenerate_question_with_different_allowed_type(client, phase4d_headers, monkeypatch):
    import llm as llm_module
    import main as main_module

    headers = phase4d_headers
    book_id, target = _seed_three_questions_and_get_apply_target(client, headers, monkeypatch, "regen_type.txt")
    question_id = target["question_id"]
    assert target["type"] == "scenario"

    main.limiter.reset()
    replacement = _bloom_fix_question(1, "short_answer", "apply", "A short-answer apply-level replacement.", "A replacement answer.")

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": [{"id": 1, "level": "apply"}]}
        return {"questions": [replacement]}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    res = client.post(f"/questions/{question_id}/regenerate", headers=headers, json={"type": "short_answer"})
    assert res.status_code == 200
    data = res.json()
    assert data["type"] == "short_answer"
    assert data["bloom_level"] == "apply"
    assert data["question"] == "A short-answer apply-level replacement."

    client.delete(f"/books/{book_id}", headers=headers)


def test_regenerate_question_rejects_type_not_allowed_for_level(client, phase4d_headers, monkeypatch):
    headers = phase4d_headers
    book_id, target = _seed_three_questions_and_get_apply_target(client, headers, monkeypatch, "regen_badtype.txt")
    question_id = target["question_id"]

    main.limiter.reset()
    # "interview" is not one of apply's allowed types (mcq/scenario/short_answer).
    res = client.post(f"/questions/{question_id}/regenerate", headers=headers, json={"type": "interview"})
    assert res.status_code == 400

    client.delete(f"/books/{book_id}", headers=headers)


def test_regenerate_question_other_user_gets_404(client, phase4d_headers, phase4d_other_headers, monkeypatch):
    headers = phase4d_headers
    other_headers = phase4d_other_headers
    book_id, target = _seed_three_questions_and_get_apply_target(client, headers, monkeypatch, "regen_404.txt")
    question_id = target["question_id"]

    main.limiter.reset()
    res = client.post(f"/questions/{question_id}/regenerate", headers=other_headers)
    assert res.status_code == 404

    client.delete(f"/books/{book_id}", headers=headers)


def test_regenerate_question_returns_503_on_llm_unavailable(client, phase4d_headers, monkeypatch):
    import llm as llm_module

    headers = phase4d_headers
    book_id, target = _seed_three_questions_and_get_apply_target(client, headers, monkeypatch, "regen_503.txt")
    question_id = target["question_id"]

    main.limiter.reset()

    def raise_unavailable(*a, **kw):
        raise llm_module.LLMUnavailableError("boom")

    monkeypatch.setattr(llm_module, "generate_json", raise_unavailable)

    res = client.post(f"/questions/{question_id}/regenerate", headers=headers)
    assert res.status_code == 503
    assert res.json()["detail"] == main.AI_BUSY_DETAIL

    client.delete(f"/books/{book_id}", headers=headers)


def test_regenerate_question_retries_with_a_different_type_when_first_fails(client, phase4d_headers, monkeypatch):
    import llm as llm_module
    import main as main_module

    headers = phase4d_headers
    book_id, target = _seed_three_questions_and_get_apply_target(client, headers, monkeypatch, "regen_retry_type.txt")
    question_id = target["question_id"]
    assert target["type"] == "scenario"

    main.limiter.reset()

    quiz_call = {"n": 0}
    mcq_replacement = _bloom_fix_question(
        1, "mcq", "apply", "A brand new mcq apply-level question.", "A",
        options=["Alpha", "Beta", "Gamma", "Delta"]
    )

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": [{"id": 1, "level": "apply"}]}
        quiz_call["n"] += 1
        if quiz_call["n"] == 1:
            return {"questions": []}  # first attempt (type=scenario): nothing usable
        return {"questions": [mcq_replacement]}  # second attempt (a different allowed type): succeeds

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    res = client.post(f"/questions/{question_id}/regenerate", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["type"] == "mcq"
    assert data["bloom_level"] == "apply"
    assert quiz_call["n"] == 2

    client.delete(f"/books/{book_id}", headers=headers)


def test_regenerate_question_both_attempts_fail_returns_422(client, phase4d_headers, monkeypatch):
    import llm as llm_module
    import main as main_module

    headers = phase4d_headers
    book_id, target = _seed_three_questions_and_get_apply_target(client, headers, monkeypatch, "regen_both_fail.txt")
    question_id = target["question_id"]

    main.limiter.reset()

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": [{"id": 1, "level": "apply"}]}
        return {"questions": []}  # every attempt comes back empty

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    res = client.post(f"/questions/{question_id}/regenerate", headers=headers)
    assert res.status_code == 422
    assert res.json()["detail"] == (
        "Couldn't write a different question on this topic. Try another question type, or edit this one yourself."
    )

    client.delete(f"/books/{book_id}", headers=headers)


def test_regenerate_question_logs_drop_reasons_without_leaking_text(client, phase4d_headers, monkeypatch, caplog):
    import logging
    import llm as llm_module
    import main as main_module

    headers = phase4d_headers
    book_id, target = _seed_three_questions_and_get_apply_target(client, headers, monkeypatch, "regen_drop_log.txt")
    question_id = target["question_id"]

    main.limiter.reset()

    secret_marker = "zzz_regen_secret_zzz"
    # true_false isn't an allowed type at "apply" -- dropped for that reason
    # on every attempt, regardless of which type was being tried.
    bad_question = {
        "id": 1, "type": "true_false", "bloom_level": "apply",
        "question": f"True or False: {secret_marker} is real.",
        "options": ["True", "False"], "correctAnswer": "A",
        "explanation": "Explained in source 1.", "source_index": 1
    }

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": [{"id": 1, "level": "apply"}]}
        return {"questions": [bad_question]}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    with caplog.at_level(logging.INFO, logger="main"):
        res = client.post(f"/questions/{question_id}/regenerate", headers=headers)
    assert res.status_code == 422

    drop_logs = [r.getMessage() for r in caplog.records if "dropped" in r.getMessage()]
    assert any("not allowed for 'apply'" in msg for msg in drop_logs)
    assert all(secret_marker not in r.getMessage() for r in caplog.records)

    client.delete(f"/books/{book_id}", headers=headers)


def test_questions_summary_counts(client, phase4d_headers, monkeypatch):
    import llm as llm_module
    import main as main_module

    headers = phase4d_headers
    main.limiter.reset()
    book_id = _upload_water_cycle_doc(client, headers, "regen_summary.txt")

    seed = [
        _bloom_fix_question(1, "true_false", "remember", "Summary remember question.", "A"),
        _bloom_fix_question(2, "short_answer", "understand", "Summary understand question.", "Summary understand answer."),
        _bloom_fix_question(3, "scenario", "apply", "Summary apply question.", "Summary apply answer."),
    ]

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            # Disagrees on the "apply" one -- after the regen-on-mismatch
            # attempt (which gets the same fixed seed back) it stays flagged.
            return {"items": [
                {"id": 1, "level": "remember"},
                {"id": 2, "level": "understand"},
                {"id": 3, "level": "create"},
            ]}
        return {"questions": seed}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    res = client.get(
        "/generate-quiz", headers=headers,
        params={"topic": "summary counts topic", "levels": "remember,understand,apply", "count": 3}
    )
    assert res.status_code == 200
    questions = res.json()["questions"]
    flagged_count = sum(1 for q in questions if q["bloom_check"] == "flagged")
    assert flagged_count >= 1

    summary = client.get("/questions/summary", headers=headers).json()
    assert summary["total"] >= 3
    assert summary["flagged"] >= flagged_count
    assert sum(summary["by_level"].values()) == summary["total"]

    client.delete(f"/books/{book_id}", headers=headers)


# -----------------------------
# PHASE 4 FIXES: NEAR-DUPLICATE QUESTIONS GET DROPPED + TOPPED UP
# -----------------------------
def test_generate_quiz_drops_near_duplicate_and_tops_up(client, phase4b_headers, monkeypatch, caplog):
    import logging
    import llm as llm_module
    import main as main_module

    headers = phase4b_headers
    main.limiter.reset()
    book_id = _upload_water_cycle_doc(client, headers, "bloom_near_dup.txt")

    # Q2 and Q3 are near-identical rewordings of the same "understand"
    # question ("rivers" vs "lakes" is the only real difference) -- high
    # enough word overlap to trip the Jaccard near-duplicate check.
    initial = [
        _bloom_fix_question(1, "true_false", "remember", "True or False: heat causes evaporation.", "A"),
        _bloom_fix_question(
            2, "short_answer", "understand",
            "Explain why water evaporates from oceans and rivers due to heat from the sun.",
            "Heat from the sun causes evaporation."
        ),
        _bloom_fix_question(
            3, "short_answer", "understand",
            "Explain why water evaporates from oceans and lakes due to heat from the sun.",
            "Heat from the sun causes evaporation."
        ),
    ]
    topped_up_question = _bloom_fix_question(
        4, "scenario", "apply", "A hiker sees clouds forming overhead. Apply the water cycle to explain why.",
        "Condensation is occurring."
    )

    quiz_call = {"n": 0}
    classifier_call = {"n": 0}

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            classifier_call["n"] += 1
            if classifier_call["n"] == 1:
                return {"items": [{"id": i, "level": q["bloom_level"]} for i, q in enumerate(initial, start=1)]}
            return {"items": [{"id": 1, "level": "apply"}]}  # recheck of the topped-up question

        quiz_call["n"] += 1
        if quiz_call["n"] == 1:
            return {"questions": initial}
        return {"questions": [topped_up_question]}

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    with caplog.at_level(logging.INFO, logger="main"):
        res = client.get(
            "/generate-quiz", headers=headers,
            params={"topic": "water cycle", "levels": "remember,understand,apply", "count": 3}
        )
    assert res.status_code == 200
    data = res.json()
    questions = data["questions"]
    assert len(questions) == 3

    levels_present = [q["bloom_level"] for q in questions]
    assert levels_present.count("understand") == 1  # the near-duplicate was dropped
    assert "apply" in levels_present  # backfilled by the existing top-up mechanism
    assert quiz_call["n"] == 2  # initial generation + exactly one top-up call

    drop_logs = [r.getMessage() for r in caplog.records if "dropped" in r.getMessage()]
    assert any("too similar to another question in this quiz" in msg for msg in drop_logs)

    client.delete(f"/books/{book_id}", headers=headers)


# -----------------------------
# PHASE 5B: EXAM PAPER GENERATION
# -----------------------------
@pytest.fixture(scope="session")
def phase5b_headers(client):
    main.limiter.reset()
    _register(client, "phase5b_test_user", "phase5bpass1")
    return _auth_headers(client, "phase5b_test_user", "phase5bpass1")


@pytest.fixture(scope="session")
def phase5b_other_headers(client):
    main.limiter.reset()
    _register(client, "phase5b_other_user", "phase5bopass1")
    return _auth_headers(client, "phase5b_other_user", "phase5bopass1")


PAPER_BOOK_CONTENT = "\n\n".join(
    f"Paragraph {i}: sample study material about computer networking concept number {i}, "
    "covering routing tables, switching hardware, and protocol layering in enough depth "
    "to fill a distinct chunk of text for exam-paper generation testing."
    for i in range(1, 41)
).encode()

# A small, fast pattern for most tests: 2 questions worth 2 marks (Section
# A, "low" marks band) + 2 questions worth 5 marks (Section B, "mid" marks
# band) -- 4 slots total, no either_or pairs to keep assertions simple.
SMALL_PATTERN = {
    "key": "small_test", "name": "Small Test Pattern", "duration_minutes": 30,
    "paper_kind": "written",
    "sections": [
        {
            "key": "A", "title": "Section A", "instructions": "Answer all.",
            "marks_each": 2, "num_questions": 2, "choice": "none"
        },
        {
            "key": "B", "title": "Section B", "instructions": "Answer all.",
            "marks_each": 5, "num_questions": 2, "choice": "none"
        },
    ],
}
SMALL_BLOOM_TARGETS = {
    "remember": 25, "understand": 25, "apply": 25, "analyze": 25, "evaluate": 0, "create": 0
}


def _upload_paper_doc(client, headers, filename="paper_source.txt"):
    res = client.post(
        "/upload-book", headers=headers,
        files={"file": (filename, PAPER_BOOK_CONTENT, "text/plain")}
    )
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "ready"  # JOBS_SYNC=true: already processed
    return res.json()["book_id"]


# Deliberately much shorter than PAPER_BOOK_CONTENT -- chunks into far
# fewer pages, for unit-weighting tests that need two books of visibly
# different size ("by_size" weighting should treat them differently;
# "equal" should not).
SHORT_PAPER_BOOK_CONTENT = "\n\n".join(
    f"Short paragraph {i} with a little bit of sample text for a small test document."
    for i in range(1, 4)
).encode()


def _upload_short_paper_doc(client, headers, filename="short_paper_source.txt"):
    res = client.post(
        "/upload-book", headers=headers,
        files={"file": (filename, SHORT_PAPER_BOOK_CONTENT, "text/plain")}
    )
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "ready"
    return res.json()["book_id"]


def _create_paper(client, headers, book_ids, pattern=None, bloom_targets=None, title="Test Paper"):
    res = client.post(
        "/papers", headers=headers,
        json={
            "title": title,
            "pattern": pattern or SMALL_PATTERN,
            "bloom_targets": bloom_targets or SMALL_BLOOM_TARGETS,
            "book_ids": book_ids,
        }
    )
    assert res.status_code == 200, res.text
    return res.json()


_PAPER_LABEL_TO_LEVEL = {}


def _paper_label_to_level_map():
    if not _PAPER_LABEL_TO_LEVEL:
        import bloom as bloom_module
        for key in bloom_module.level_keys():
            _PAPER_LABEL_TO_LEVEL[bloom_module.get_level(key).label] = key
    return _PAPER_LABEL_TO_LEVEL


def _make_paper_llm_fake():
    """
    A generate_json fake good for the whole Phase 5B flow: for any
    non-classifier schema, it invents exactly as many questions as the
    prompt's "write exactly N question(s) at this level" bullets ask for,
    each tagged with the requested bloom_level (and marks/marking_scheme
    when the bullet is marks-aware), with source_index assigned 1..N in
    call order so main.generate_paper's source_index-based slot matching
    has something real to match against. For CLASSIFIER_SCHEMA calls it
    agrees with whatever level each question text was invented at, so no
    mismatch/regeneration branch fires unless a test overrides this fake.
    """
    import main as main_module
    import paper as paper_module
    label_to_level = _paper_label_to_level_map()
    counter = {"n": 0}
    level_by_text = {}

    def fake(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            items = []
            tail = prompt.split("Questions:\n", 1)[1] if "Questions:\n" in prompt else ""
            for line in tail.strip().split("\n"):
                m = re.match(r"(\d+)\.\s*(.*)", line.strip())
                if not m:
                    continue
                items.append({"id": int(m.group(1)), "level": level_by_text.get(m.group(2).strip(), "remember")})
            return {"items": items}

        # Parse each numbered source's real text out of the prompt, so the
        # fake's generated questions can quote a snippet of it -- Phase 5B's
        # grounding check (main._is_grounded) requires real term overlap
        # with the slot's own source window, so a generic placeholder
        # question text would get dropped as "not grounded".
        sources_text = prompt.split("Sources:\n", 1)[1] if "Sources:\n" in prompt else ""
        source_content_by_index = {
            int(m.group(1)): m.group(2).strip()
            for m in re.finditer(r"\[(\d+)\]\s*(.*?)(?=\n\n\[\d+\]|\Z)", sources_text, re.DOTALL)
        }

        questions = []
        source_index = 0
        for match in re.finditer(
            r"- ([A-Za-z]+) -- write exactly (\d+) question\(s\) at this level"
            r"(?:, each worth EXACTLY (\d+) marks)?",
            prompt
        ):
            label, count, marks = match.group(1), int(match.group(2)), match.group(3)
            level_key = label_to_level[label]
            marks_int = int(marks) if marks is not None else 2
            # Pick a type this (level, marks, "written") slot actually
            # allows -- bloom.py's per-level types and paper.py's marks-band
            # types don't always overlap the way a naive "short vs long"
            # guess would assume (e.g. remember never allows short_answer).
            allowed_types = paper_module.allowed_types_for_slot(level_key, marks_int, "written") or ["short_answer"]
            chosen_type = allowed_types[0]

            for _ in range(count):
                counter["n"] += 1
                source_index += 1
                snippet = " ".join(source_content_by_index.get(source_index, "").split()[:15])
                text = f"Generated paper question #{counter['n']} about {level_key}: {snippet}".strip()
                level_by_text[text] = level_key
                q = {
                    "id": counter["n"], "type": chosen_type, "bloom_level": level_key,
                    "question": text, "explanation": "A sample explanation.", "source_index": source_index,
                }
                if chosen_type == "mcq":
                    q["options"] = ["Option A", "Option B", "Option C", "Option D"]
                    q["correctAnswer"] = "A"
                elif chosen_type == "true_false":
                    q["options"] = ["True", "False"]
                    q["correctAnswer"] = "A"
                else:
                    q["correctAnswer"] = "A sample correct answer."
                if marks is not None:
                    q["marks"] = marks_int
                    q["marking_scheme"] = [{"point": "Correctness", "marks": marks_int}]
                questions.append(q)

        return {"questions": questions}

    return fake


def test_verb_guard_regenerates_a_question_opened_with_the_wrong_levels_verb(monkeypatch):
    # Phase 5 fixes from browser testing, item 5: "Propose a..." is a
    # Create verb (bloom.py), so writing it for an Apply slot should trip
    # the cheap lexical guard and trigger exactly one regeneration call
    # that explicitly asks the model to avoid "propose" this time.
    from types import SimpleNamespace
    import bloom as bloom_module

    window = SimpleNamespace(content="Congestion control adjusts the sender's rate based on feedback from the network.")
    top_pages = [(1.0, window)]
    drop_log = []
    questions = [{
        "id": 1, "type": "short_answer", "bloom_level": "apply",
        "question": "Propose a new congestion-control scheme for this network.",
        "correctAnswer": "A sample answer.", "explanation": "A sample explanation.",
        "marks": 5, "marking_scheme": [{"point": "Correctness", "marks": 5}], "source_index": 1,
    }]

    verb_hit = bloom_module.classify_by_verbs(questions[0]["question"])
    assert verb_hit["level"] == "create" and verb_hit["matched"] == "propose"

    captured_prompts = []

    def fake_generate_json(prompt, schema=None, system=None):
        captured_prompts.append(prompt)
        return {"questions": [{
            "id": 1, "type": "short_answer", "bloom_level": "apply",
            "question": "How would you use the sliding window technique to manage flow on this link?",
            "correctAnswer": "A sample answer.", "explanation": "A sample explanation.",
            "marks": 5, "marking_scheme": [{"point": "Correctness", "marks": 5}], "source_index": 1,
        }]}

    monkeypatch.setattr(main.llm, "generate_json", fake_generate_json)

    result = main._apply_verb_guard(questions, "Networking", top_pages, drop_log, [], {"apply": 5}, "written")

    assert len(captured_prompts) == 1
    assert "propose" in captured_prompts[0].lower()
    assert "avoid" in captured_prompts[0].lower()
    assert result[0]["question"] != "Propose a new congestion-control scheme for this network."
    assert bloom_module.classify_by_verbs(result[0]["question"])["level"] in (None, "apply")


def test_verb_guard_leaves_questions_alone_when_verbs_match_their_level(monkeypatch):
    from types import SimpleNamespace

    window = SimpleNamespace(content="Some source text.")
    top_pages = [(1.0, window)]
    questions = [{
        "id": 1, "type": "short_answer", "bloom_level": "remember",
        "question": "Define the term 'checksum' as used in this chapter.",
        "correctAnswer": "A.", "explanation": "E.", "marks": 2,
        "marking_scheme": [{"point": "x", "marks": 2}], "source_index": 1,
    }]

    def fail_if_called(*args, **kwargs):
        raise AssertionError("verb guard should not regenerate a correctly-leveled question")

    monkeypatch.setattr(main.llm, "generate_json", fail_if_called)

    result = main._apply_verb_guard(questions, "Networking", top_pages, [], [], {"remember": 2}, "written")
    assert result[0]["question"] == "Define the term 'checksum' as used in this chapter."


def test_create_paper_builds_plan_without_any_llm_call(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_create.txt")

    def fail_generate_json(*a, **kw):
        raise AssertionError("generate_json should not be called during paper creation")
    monkeypatch.setattr(llm_module, "generate_json", fail_generate_json)

    data = _create_paper(client, headers, [book_id], title="Unit Test Paper")

    assert data["status"] == "draft"
    assert len(data["plan"]) == 4
    assert len(data["slots"]) == 4
    assert all(slot["question"] is None for slot in data["slots"])
    assert data["warnings"] == []
    assert set(data["blueprint_target"].keys()) == set(data["blueprint_planned"]["by_level"].keys())


def test_create_paper_rejects_invalid_pattern(client, phase5b_headers):
    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_bad_pattern.txt")
    bad_pattern = dict(SMALL_PATTERN, sections=[dict(SMALL_PATTERN["sections"][0], num_questions=0)])

    res = client.post(
        "/papers", headers=headers,
        json={
            "title": "Bad Pattern", "pattern": bad_pattern,
            "bloom_targets": SMALL_BLOOM_TARGETS, "book_ids": [book_id]
        }
    )
    assert res.status_code == 422
    assert isinstance(res.json()["detail"], list) and res.json()["detail"]


def test_create_paper_rejects_attempt_count_exceeding_num_questions(client, phase5b_headers):
    # Phase 5 fixes from browser testing, item 4: "attempt any 6 of 5" is
    # structurally impossible (there aren't 6 questions to pick from) and
    # must 422, matching the frontend's live "attempt > num_questions"
    # validation that disables "Preview plan" for the same input.
    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_bad_attempt.txt")
    bad_pattern = dict(
        SMALL_PATTERN,
        sections=[dict(SMALL_PATTERN["sections"][0], choice="any_n", attempt=6, num_questions=5)]
    )

    res = client.post(
        "/papers", headers=headers,
        json={
            "title": "Attempt Exceeds Count", "pattern": bad_pattern,
            "bloom_targets": SMALL_BLOOM_TARGETS, "book_ids": [book_id]
        }
    )
    assert res.status_code == 422
    assert any("exceeds" in detail for detail in res.json()["detail"])


def test_create_paper_rejects_invalid_bloom_targets(client, phase5b_headers):
    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_bad_targets.txt")
    bad_targets = {"remember": 50, "understand": 10}  # sums to 60, not 100

    res = client.post(
        "/papers", headers=headers,
        json={
            "title": "Bad Targets", "pattern": SMALL_PATTERN,
            "bloom_targets": bad_targets, "book_ids": [book_id]
        }
    )
    assert res.status_code == 422
    assert isinstance(res.json()["detail"], list) and res.json()["detail"]


def test_create_paper_rejects_other_users_book(client, phase5b_headers, phase5b_other_headers):
    other_book_id = _upload_paper_doc(client, phase5b_other_headers, "paper_other_owner.txt")

    res = client.post(
        "/papers", headers=phase5b_headers,
        json={
            "title": "Not Mine", "pattern": SMALL_PATTERN,
            "bloom_targets": SMALL_BLOOM_TARGETS, "book_ids": [other_book_id]
        }
    )
    assert res.status_code == 404


def test_create_paper_default_unit_weighting_is_equal_regardless_of_size(client, phase5b_headers):
    headers = phase5b_headers
    big_book = _upload_paper_doc(client, headers, "weights_equal_big.txt")
    small_book = _upload_short_paper_doc(client, headers, "weights_equal_small.txt")

    data = _create_paper(client, headers, [big_book, small_book], title="Equal Weights Paper")
    assert data["unit_weights"] == {str(big_book): 1, str(small_book): 1}


def test_create_paper_by_size_unit_weighting_uses_chunk_counts(client, phase5b_headers):
    headers = phase5b_headers
    big_book = _upload_paper_doc(client, headers, "weights_by_size_big.txt")
    small_book = _upload_short_paper_doc(client, headers, "weights_by_size_small.txt")

    res = client.post(
        "/papers", headers=headers,
        json={
            "title": "By Size Weights Paper", "pattern": SMALL_PATTERN,
            "bloom_targets": SMALL_BLOOM_TARGETS, "book_ids": [big_book, small_book],
            "unit_weighting": "by_size",
        }
    )
    assert res.status_code == 200, res.text
    weights = res.json()["unit_weights"]
    assert weights[str(big_book)] > weights[str(small_book)] > 0


def test_create_paper_custom_unit_weighting_uses_given_weights(client, phase5b_headers):
    headers = phase5b_headers
    book_a = _upload_paper_doc(client, headers, "weights_custom_a.txt")
    book_b = _upload_short_paper_doc(client, headers, "weights_custom_b.txt")

    res = client.post(
        "/papers", headers=headers,
        json={
            "title": "Custom Weights Paper", "pattern": SMALL_PATTERN,
            "bloom_targets": SMALL_BLOOM_TARGETS, "book_ids": [book_a, book_b],
            "unit_weighting": "custom", "unit_weights": {str(book_a): 5, str(book_b): 1},
        }
    )
    assert res.status_code == 200, res.text
    assert res.json()["unit_weights"] == {str(book_a): 5.0, str(book_b): 1.0}


def test_create_paper_custom_unit_weighting_requires_weights_for_every_book(client, phase5b_headers):
    headers = phase5b_headers
    book_a = _upload_paper_doc(client, headers, "weights_missing_a.txt")
    book_b = _upload_short_paper_doc(client, headers, "weights_missing_b.txt")

    res = client.post(
        "/papers", headers=headers,
        json={
            "title": "Missing Weight Paper", "pattern": SMALL_PATTERN,
            "bloom_targets": SMALL_BLOOM_TARGETS, "book_ids": [book_a, book_b],
            "unit_weighting": "custom", "unit_weights": {str(book_a): 5},
        }
    )
    assert res.status_code == 422

    res2 = client.post(
        "/papers", headers=headers,
        json={
            "title": "No Weights At All Paper", "pattern": SMALL_PATTERN,
            "bloom_targets": SMALL_BLOOM_TARGETS, "book_ids": [book_a, book_b],
            "unit_weighting": "custom",
        }
    )
    assert res2.status_code == 422


def test_create_paper_rejects_unknown_unit_weighting(client, phase5b_headers):
    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "weights_unknown_mode.txt")
    res = client.post(
        "/papers", headers=headers,
        json={
            "title": "Bad Weighting Paper", "pattern": SMALL_PATTERN,
            "bloom_targets": SMALL_BLOOM_TARGETS, "book_ids": [book_id],
            "unit_weighting": "bogus",
        }
    )
    assert res.status_code == 422


def test_generate_fills_all_slots_and_blueprint_actual_matches_plan(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_generate_full.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Full Generate Paper")

    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())

    main.limiter.reset()
    res = client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "ready"  # JOBS_SYNC=true: already generated

    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    assert detail["status"] == "ready"
    assert all(slot["question"] is not None for slot in detail["slots"])
    assert detail["warnings"] == []

    for slot in detail["slots"]:
        q = slot["question"]
        assert q["marks"] == slot["marks"]
        assert sum(p["marks"] for p in q["marking_scheme"]) == slot["marks"]

    assert detail["blueprint_actual"]["by_level"] == detail["blueprint_planned"]["by_level"]


def test_blueprint_actual_counts_flagged_question_at_detected_level(client, phase5b_headers, monkeypatch):
    # Phase 5 fixes from browser testing, item 3: blueprint_actual must
    # reflect the REAL mix on the paper, so a flagged question's marks
    # move to its detected level, not the intended one it was planned at
    # (blueprint_planned keeps using the intended level, unchanged).
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_blueprint_actual.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Blueprint Actual Paper")

    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    slot = next(s for s in detail["slots"] if s["marks"] == 2 and s["bloom_level"] == "remember")

    db = TestSessionLocal()
    try:
        question = db.query(main.Question).filter(main.Question.id == slot["question"]["question_id"]).first()
        question.bloom_check = "flagged"
        question.detected_level = "apply"
        db.commit()
    finally:
        db.close()

    detail2 = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    # Planned (the plan's intended level) is untouched by the flag...
    assert detail2["blueprint_planned"]["by_level"]["remember"] == detail["blueprint_planned"]["by_level"]["remember"]
    # ...but actual moves this slot's 2 marks from remember to apply.
    assert detail2["blueprint_actual"]["by_level"]["remember"] == detail["blueprint_actual"]["by_level"]["remember"] - 2
    assert detail2["blueprint_actual"]["by_level"]["apply"] == detail["blueprint_actual"]["by_level"]["apply"] + 2


def test_generate_with_no_usable_questions_leaves_warnings_but_stays_ready(client, phase5b_headers, monkeypatch):
    import llm as llm_module
    import main as main_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_empty_slots.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Empty Slots Paper")

    def fake_generate_json(prompt, schema, system=None):
        if schema is main_module.CLASSIFIER_SCHEMA:
            return {"items": []}
        return {"questions": []}
    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    main.limiter.reset()
    res = client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    assert res.status_code == 202
    assert res.json()["status"] == "ready"

    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    assert detail["status"] == "ready"
    assert all(slot["question"] is None for slot in detail["slots"])
    assert len(detail["warnings"]) >= 1
    assert "couldn't generate a question for this slot" in detail["warnings"][0]


def test_generate_only_fills_slots_still_empty(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_partial_fill.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Partial Fill Paper")

    db = TestSessionLocal()
    try:
        user_id = db.query(main.User).filter(main.User.username == "phase5b_test_user").first().id
        slot0_marks = paper_data["slots"][0]["marks"]
        existing = main.Question(
            user_id=user_id, book_id=book_id, topic="pre-existing", type="short_answer",
            bloom_level="remember", bloom_check="unchecked",
            question="A question already sitting in this slot.",
            correct_answer="Pre-existing answer.", explanation="Pre-existing explanation.",
            marks=slot0_marks, marking_scheme=json.dumps([{"point": "x", "marks": slot0_marks}]),
        )
        db.add(existing)
        db.flush()
        slot0 = db.query(main.PaperQuestion).filter(main.PaperQuestion.id == paper_data["slots"][0]["id"]).first()
        slot0.question_id = existing.id
        db.commit()
        existing_id = existing.id
    finally:
        db.close()

    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    res = client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    assert res.status_code == 202
    assert res.json()["status"] == "ready"

    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    assert detail["slots"][0]["question"]["question_id"] == existing_id  # untouched
    assert all(slot["question"] is not None for slot in detail["slots"])


def test_generate_never_mixes_two_books_sources_in_one_call(client, phase5b_headers, monkeypatch):
    # Phase 5B quality fix: grouping slots purely by (level, marks) let one
    # generate_json call's numbered sources span two different books --
    # the real bug this reproduces is a CN-labelled slot (CRC/Hamming
    # content) coming back answered from the NS document's encryption
    # content instead. Grouping now includes book_id, so this must never
    # happen: no single call's "Sources:" block may contain both books'
    # distinctive marker text.
    import llm as llm_module

    headers = phase5b_headers
    book_a = _upload_paper_doc(client, headers, "grounding_book_a.txt")  # contains "networking"
    book_b = _upload_short_paper_doc(client, headers, "grounding_book_b.txt")  # contains "small test document"
    paper_data = _create_paper(client, headers, [book_a, book_b], title="Grounding Paper")

    # Force two slots (one per book) into the exact same (level, marks)
    # group, regardless of what plan_paper happened to assign naturally.
    db = TestSessionLocal()
    try:
        slots = (
            db.query(main.PaperQuestion)
            .filter(main.PaperQuestion.paper_id == paper_data["id"])
            .order_by(main.PaperQuestion.position)
            .all()
        )
        slot_a, slot_b = slots[0], slots[1]
        slot_a.bloom_level = "remember"
        slot_a.book_id = book_a
        slot_b.bloom_level = "remember"
        slot_b.book_id = book_b
        db.commit()
    finally:
        db.close()

    captured_sources = []

    def fake(prompt, schema, system=None):
        if schema is main.CLASSIFIER_SCHEMA:
            return {"items": []}
        sources_text = prompt.split("Sources:\n", 1)[1] if "Sources:\n" in prompt else ""
        captured_sources.append(sources_text)
        return {"questions": []}
    monkeypatch.setattr(llm_module, "generate_json", fake)

    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)

    assert len(captured_sources) >= 2
    for sources_text in captured_sources:
        has_a = "networking" in sources_text
        has_b = "small test document" in sources_text
        assert not (has_a and has_b), "a single call's sources must never span two different books"
    assert any("networking" in s for s in captured_sources)
    assert any("small test document" in s for s in captured_sources)


def test_generate_exhausts_wait_budget_then_fails_and_retry_fills_empty_slots(
    client, phase5b_headers, monkeypatch
):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_busy.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Busy Paper")

    # Keep the retry/backoff fast and deterministic: no real waiting, and a
    # budget small enough that even the first (60s) wait exceeds it, so the
    # job gives up on its very first busy response.
    monkeypatch.setattr(main.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(main, "PAPER_JOB_MAX_WAIT_SECONDS", 30.0)

    def raise_unavailable(*a, **kw):
        raise llm_module.LLMUnavailableError("boom")
    monkeypatch.setattr(llm_module, "generate_json", raise_unavailable)

    main.limiter.reset()
    res = client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    assert res.status_code == 202
    assert res.json()["status"] == "failed"  # JOBS_SYNC=true: already ran and hit the busy error

    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    assert detail["status"] == "failed"
    assert detail["error"] == "AI service is busy, click Generate to continue"
    assert detail["waiting_for_ai"] is False  # cleared, not left stuck mid-wait
    assert all(slot["question"] is None for slot in detail["slots"])

    # Retry with a working mock -- nothing was saved from the failed
    # attempt, so this fills every slot.
    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    res2 = client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    assert res2.status_code == 202
    assert res2.json()["status"] == "ready"

    detail2 = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    assert all(slot["question"] is not None for slot in detail2["slots"])


def test_generate_waits_out_two_busy_responses_then_succeeds_with_no_manual_retry(
    client, phase5b_headers, monkeypatch
):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_busy_then_ok.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Busy Then OK Paper")

    paper_id = paper_data["id"]
    waiting_flags_seen = []

    def fake_sleep(seconds):
        # Captures whether the DB shows waiting_for_ai=True *while* the
        # job is paused -- a concurrent GET /papers/{id}/status would see
        # exactly this, mid-wait, from a different request/thread.
        db = TestSessionLocal()
        try:
            row = db.query(main.Paper).filter(main.Paper.id == paper_id).first()
            waiting_flags_seen.append(row.waiting_for_ai)
        finally:
            db.close()
    monkeypatch.setattr(main.time, "sleep", fake_sleep)

    real_fake = _make_paper_llm_fake()
    busy_calls_left = [2]  # the very first two generate_json calls are "busy"

    def flaky_then_fake(prompt, schema, system=None):
        if schema is not main.CLASSIFIER_SCHEMA and busy_calls_left[0] > 0:
            busy_calls_left[0] -= 1
            raise llm_module.LLMUnavailableError("boom")
        return real_fake(prompt, schema, system=system)
    monkeypatch.setattr(llm_module, "generate_json", flaky_then_fake)

    main.limiter.reset()
    res = client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    assert res.status_code == 202
    assert res.json()["status"] == "ready"  # no manual retry needed

    assert waiting_flags_seen == [True, True]  # both busy waits were visible mid-wait

    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    assert detail["status"] == "ready"
    assert detail["waiting_for_ai"] is False
    assert all(slot["question"] is not None for slot in detail["slots"])


def test_paper_stuck_generating_fails_on_startup_cleanup(client, phase5b_headers):
    db = TestSessionLocal()
    paper_id = None
    try:
        user = db.query(main.User).filter(main.User.username == "phase5b_test_user").first()
        p = main.Paper(
            user_id=user.id, title="Interrupted", pattern="{}", bloom_targets="{}",
            book_ids="[]", plan="[]", status="generating",
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        paper_id = p.id

        failed_count = main._fail_interrupted_papers(db)
        assert failed_count >= 1

        db.refresh(p)
        assert p.status == "failed"
        assert p.error == "Generation was interrupted, click Generate to continue."
    finally:
        if paper_id is not None:
            db.query(main.Paper).filter(main.Paper.id == paper_id).delete()
            db.commit()
        db.close()


def test_swap_slot_accepts_matching_bank_question_and_flags_level_change(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_swap.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Swap Paper")

    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()

    slot = next(s for s in detail["slots"] if s["marks"] == 2)
    other_level = next(lvl for lvl in ("remember", "understand", "apply") if lvl != slot["bloom_level"])

    db = TestSessionLocal()
    try:
        user_id = db.query(main.User).filter(main.User.username == "phase5b_test_user").first().id
        bank_q = main.Question(
            user_id=user_id, book_id=book_id, topic="bank",
            type="short_answer", bloom_level=other_level, bloom_check="unchecked",
            question="A freshly written bank question for swapping in.",
            correct_answer="Some answer.", explanation="Some explanation.",
            marks=2, marking_scheme=json.dumps([{"point": "x", "marks": 2}]),
        )
        db.add(bank_q)
        db.commit()
        db.refresh(bank_q)
        bank_question_id = bank_q.id
    finally:
        db.close()

    res = client.patch(
        f"/papers/{paper_data['id']}/slots/{slot['id']}", headers=headers,
        json={"question_id": bank_question_id}
    )
    assert res.status_code == 200, res.text
    data = res.json()
    swapped_slot = next(s for s in data["slots"] if s["id"] == slot["id"])
    assert swapped_slot["question"]["question_id"] == bank_question_id
    assert any("swapped in a" in w for w in data["warnings"])


def test_swap_slot_rejects_wrong_marks_and_enforces_ownership(
    client, phase5b_headers, phase5b_other_headers, monkeypatch
):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_swap_reject.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Swap Reject Paper")

    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    slot_2marks = next(s for s in detail["slots"] if s["marks"] == 2)
    slot_5marks = next(s for s in detail["slots"] if s["marks"] == 5)

    # Wrong marks: a 5-mark slot's question doesn't fit a 2-mark slot.
    res = client.patch(
        f"/papers/{paper_data['id']}/slots/{slot_2marks['id']}", headers=headers,
        json={"question_id": slot_5marks["question"]["question_id"]}
    )
    assert res.status_code == 400

    # Another user's question is 404 -- not theirs to use.
    other_book_id = _upload_paper_doc(client, phase5b_other_headers, "paper_swap_other.txt")
    other_paper = _create_paper(client, phase5b_other_headers, [other_book_id], title="Other User Paper")
    main.limiter.reset()
    client.post(f"/papers/{other_paper['id']}/generate", headers=phase5b_other_headers)
    other_detail = client.get(f"/papers/{other_paper['id']}", headers=phase5b_other_headers).json()
    foreign_question_id = next(s["question"]["question_id"] for s in other_detail["slots"] if s["question"])

    res2 = client.patch(
        f"/papers/{paper_data['id']}/slots/{slot_2marks['id']}", headers=headers,
        json={"question_id": foreign_question_id}
    )
    assert res2.status_code == 404

    # Owner-only on the paper/slot itself too.
    res3 = client.patch(
        f"/papers/{paper_data['id']}/slots/{slot_2marks['id']}", headers=phase5b_other_headers,
        json={"question_id": slot_5marks["question"]["question_id"]}
    )
    assert res3.status_code == 404


def test_regenerate_slot_writes_new_question_and_is_owner_only(
    client, phase5b_headers, phase5b_other_headers, monkeypatch
):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_regen_slot.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Regen Slot Paper")

    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    slot = detail["slots"][0]
    old_question_id = slot["question"]["question_id"]

    res_other = client.post(
        f"/papers/{paper_data['id']}/slots/{slot['id']}/regenerate", headers=phase5b_other_headers
    )
    assert res_other.status_code == 404

    main.limiter.reset()
    res = client.post(f"/papers/{paper_data['id']}/slots/{slot['id']}/regenerate", headers=headers)
    assert res.status_code == 200, res.text
    new_detail = res.json()
    new_slot = next(s for s in new_detail["slots"] if s["id"] == slot["id"])
    assert new_slot["question"]["question_id"] != old_question_id
    assert new_slot["question"]["marks"] == slot["marks"]


def test_slot_candidates_filters_by_marks_and_level_and_is_owner_only(
    client, phase5b_headers, phase5b_other_headers
):
    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_candidates.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Candidates Paper")
    slot = paper_data["slots"][0]  # a still-empty 2-mark slot (draft paper)

    db = TestSessionLocal()
    try:
        user_id = db.query(main.User).filter(main.User.username == "phase5b_test_user").first().id
        # "remember" at 2 marks ("low" band) only allows mcq/true_false/
        # fill_blank -- short_answer isn't in the intersection, so these use
        # true_false to actually fit the slot.
        matching = main.Question(
            user_id=user_id, book_id=book_id, topic="bank", type="true_false",
            bloom_level="remember", bloom_check="unchecked",
            question="A matching candidate question.", correct_answer="A",
            explanation="Explanation.", marks=slot["marks"],
            marking_scheme=json.dumps([{"point": "x", "marks": slot["marks"]}]),
        )
        no_marks_yet = main.Question(
            user_id=user_id, book_id=book_id, topic="bank", type="true_false",
            bloom_level="remember", bloom_check="unchecked",
            question="A plain quiz question with no marks assigned yet.",
            correct_answer="A", explanation="Explanation.",
        )
        wrong_marks = main.Question(
            user_id=user_id, book_id=book_id, topic="bank", type="long_answer",
            bloom_level="analyze", bloom_check="unchecked",
            question="A question worth the wrong number of marks.",
            correct_answer="Answer.", explanation="Explanation.",
            marks=10, marking_scheme=json.dumps([{"point": "x", "marks": 10}]),
        )
        db.add_all([matching, no_marks_yet, wrong_marks])
        db.commit()
    finally:
        db.close()

    res = client.get(f"/papers/{paper_data['id']}/slots/{slot['id']}/candidates", headers=headers)
    assert res.status_code == 200
    candidate_texts = [c["question"] for c in res.json()]
    assert "A matching candidate question." in candidate_texts
    assert "A plain quiz question with no marks assigned yet." in candidate_texts
    assert "A question worth the wrong number of marks." not in candidate_texts

    res_other = client.get(
        f"/papers/{paper_data['id']}/slots/{slot['id']}/candidates", headers=phase5b_other_headers
    )
    assert res_other.status_code == 404


# -----------------------------
# PHASE 5C EXTRA: widened-window retry, warning sync on regenerate/swap
# -----------------------------
def _make_single_question_fake(slot, classifier_level, question_suffix=""):
    """
    A generate_json fake for slot-level (regenerate) tests: returns
    exactly ONE question for `slot`'s own level/marks, grounded via a
    snippet of the real source text, with the classifier reporting
    `classifier_level` for it -- lets a test force either a flagged
    (classifier_level != slot's level) or verified result.
    """
    import paper as paper_module

    def fake(prompt, schema, system=None):
        if schema is main.CLASSIFIER_SCHEMA:
            return {"items": [{"id": 1, "level": classifier_level}]}
        sources_text = prompt.split("Sources:\n", 1)[1] if "Sources:\n" in prompt else ""
        snippet = " ".join(sources_text.split()[:10])
        allowed_types = paper_module.allowed_types_for_slot(
            slot["bloom_level"], slot["marks"], "written"
        ) or ["short_answer"]
        chosen_type = allowed_types[0]
        q = {
            "id": 1, "type": chosen_type, "bloom_level": slot["bloom_level"],
            "question": f"Explain {snippet}{question_suffix}",
            "explanation": "Explanation.", "source_index": 1,
            "marks": slot["marks"], "marking_scheme": [{"point": "x", "marks": slot["marks"]}],
        }
        if chosen_type == "mcq":
            q["options"] = ["Option A", "Option B", "Option C", "Option D"]
            q["correctAnswer"] = "A"
        elif chosen_type == "true_false":
            q["options"] = ["True", "False"]
            q["correctAnswer"] = "A"
        else:
            q["correctAnswer"] = "An answer."
        return {"questions": [q]}

    return fake


def test_generate_retries_still_empty_slot_with_wider_window(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "widened_retry.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Widened Retry Paper")

    db = TestSessionLocal()
    try:
        narrow = main._assign_source_windows(db, book_id, 1, window_size=3)[0]
        wide = main._assign_source_windows(db, book_id, 1, window_size=5)[0]
    finally:
        db.close()
    assert len(wide.content) > len(narrow.content)
    threshold = (len(narrow.content) + len(wide.content)) // 2

    label_to_level = _paper_label_to_level_map()
    level_by_text = {}

    def fake(prompt, schema, system=None):
        if schema is main.CLASSIFIER_SCHEMA:
            items = []
            tail = prompt.split("Questions:\n", 1)[1] if "Questions:\n" in prompt else ""
            for line in tail.strip().split("\n"):
                m = re.match(r"(\d+)\.\s*(.*)", line.strip())
                if m:
                    items.append({"id": int(m.group(1)), "level": level_by_text.get(m.group(2).strip(), "remember")})
            return {"items": items}

        sources_text = prompt.split("Sources:\n", 1)[1] if "Sources:\n" in prompt else ""
        words = sources_text.split()
        questions = []
        source_index = 0
        for m in re.finditer(
            r"- ([A-Za-z]+) -- write exactly (\d+) question\(s\) at this level"
            r"(?:, each worth EXACTLY (\d+) marks)?",
            prompt
        ):
            label, count, marks = m.group(1), int(m.group(2)), m.group(3)
            level_key = label_to_level[label]
            marks_int = int(marks) if marks is not None else 2
            allowed_types = main.paper.allowed_types_for_slot(level_key, marks_int, "written") or ["short_answer"]
            chosen_type = allowed_types[0]
            # Only the widened (5-chunk) window is long enough to clear this
            # threshold -- the narrow (<=3-chunk) window always gets a
            # deliberately unrelated, ungrounded question instead, so every
            # slot can ONLY be filled by the widened-window retry pass.
            grounded = len(sources_text) > threshold
            for _ in range(count):
                source_index += 1
                if grounded:
                    # Two DIFFERENT word spans of the real source -- both
                    # share source terms (grounded), but the answer is
                    # never a literal substring of the question (which
                    # would otherwise trip the "leaks its own answer" check).
                    text = f"Explain {' '.join(words[:10])}"
                    answer = ' '.join(words[10:20]) or ' '.join(words[:10])
                else:
                    text = "Completely unrelated question about something else entirely."
                    answer = "Something not in any source."
                text = f"{text} (#{source_index})"
                level_by_text[text] = level_key
                q = {
                    "id": source_index, "type": chosen_type, "bloom_level": level_key,
                    "question": text, "explanation": "Explanation.", "source_index": source_index,
                }
                if chosen_type == "mcq":
                    q["options"] = ["Option A", "Option B", "Option C", "Option D"]
                    q["correctAnswer"] = "A"
                elif chosen_type == "true_false":
                    q["options"] = ["True", "False"]
                    q["correctAnswer"] = "A"
                else:
                    q["correctAnswer"] = answer
                if marks is not None:
                    q["marks"] = marks_int
                    q["marking_scheme"] = [{"point": "Correctness", "marks": marks_int}]
                questions.append(q)
        return {"questions": questions}

    monkeypatch.setattr(llm_module, "generate_json", fake)
    main.limiter.reset()
    res = client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "ready"

    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    assert all(slot["question"] is not None for slot in detail["slots"])


def test_regenerate_slot_syncs_flagged_warning_then_clears_it(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "regen_warning.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Regen Warning Paper")

    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    slot = detail["slots"][0]
    slot_label = f"Section '{slot['section']}' question {slot['number']}"
    disagree_level = "create" if slot["bloom_level"] != "create" else "remember"

    # First regenerate: the classifier disagrees -- a "classified as X, not
    # Y" warning for this slot must appear.
    monkeypatch.setattr(llm_module, "generate_json", _make_single_question_fake(slot, disagree_level, " (v1)"))
    main.limiter.reset()
    res = client.post(f"/papers/{paper_data['id']}/slots/{slot['id']}/regenerate", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert any(slot_label in w and "classified as" in w for w in data["warnings"])

    # Second regenerate: the classifier now agrees -- the old warning for
    # this exact slot must be gone (Phase 5C EXTRA B).
    monkeypatch.setattr(
        llm_module, "generate_json", _make_single_question_fake(slot, slot["bloom_level"], " (v2)")
    )
    main.limiter.reset()
    res2 = client.post(f"/papers/{paper_data['id']}/slots/{slot['id']}/regenerate", headers=headers)
    assert res2.status_code == 200, res2.text
    data2 = res2.json()
    assert not any(slot_label in w for w in data2["warnings"])


def test_swap_slot_syncs_flagged_warning_from_bank_question(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "swap_warning.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Swap Warning Paper")

    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    slot = next(s for s in detail["slots"] if s["marks"] == 2)
    slot_label = f"Section '{slot['section']}' question {slot['number']}"

    db = TestSessionLocal()
    try:
        user_id = db.query(main.User).filter(main.User.username == "phase5b_test_user").first().id
        allowed_types = main.paper.allowed_types_for_slot(slot["bloom_level"], 2, "written") or ["short_answer"]
        flagged_q = main.Question(
            user_id=user_id, book_id=book_id, topic="bank", type=allowed_types[0],
            bloom_level=slot["bloom_level"], bloom_check="flagged", detected_level="create",
            question="A flagged bank question for swapping in.",
            correct_answer="A", explanation="Explanation.",
            marks=2, marking_scheme=json.dumps([{"point": "x", "marks": 2}]),
        )
        db.add(flagged_q)
        db.commit()
        db.refresh(flagged_q)
        flagged_q_id = flagged_q.id
    finally:
        db.close()

    res = client.patch(
        f"/papers/{paper_data['id']}/slots/{slot['id']}", headers=headers,
        json={"question_id": flagged_q_id}
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert any(slot_label in w and "classified as" in w for w in data["warnings"])


def test_delete_paper_keeps_bank_questions(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_delete.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Delete Paper")

    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    question_ids = [s["question"]["question_id"] for s in detail["slots"]]

    res = client.delete(f"/papers/{paper_data['id']}", headers=headers)
    assert res.status_code == 200
    assert client.get(f"/papers/{paper_data['id']}", headers=headers).status_code == 404

    db = TestSessionLocal()
    try:
        remaining = db.query(main.Question).filter(main.Question.id.in_(question_ids)).count()
        assert remaining == len(question_ids)
        assert db.query(main.PaperQuestion).filter(main.PaperQuestion.paper_id == paper_data["id"]).count() == 0
    finally:
        db.close()


def test_question_bank_origin_usage_filter_and_delete_unused(client, phase5b_headers, monkeypatch):
    # Phase 5 fixes from browser testing, item 9: origin ("quiz" vs
    # "paper"), used_in_papers, the source filter, and the "delete unused
    # questions" cleanup endpoint.
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "qbank_origin.txt")
    paper_data = _create_paper(client, headers, [book_id], title="QBank Origin Paper")

    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)
    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    slot = next(s for s in detail["slots"] if s["marks"] == 2)
    used_paper_question_id = slot["question"]["question_id"]

    db = TestSessionLocal()
    try:
        user_id = db.query(main.User).filter(main.User.username == "phase5b_test_user").first().id
        # A plain quiz-origin question for the same user (never part of any paper).
        quiz_q = main.Question(
            user_id=user_id, topic="quiz bank", type="short_answer", bloom_level="remember",
            bloom_check="unchecked", question="A plain quiz question.",
            correct_answer="A.", explanation="E.",
        )
        db.add(quiz_q)
        db.commit()
        db.refresh(quiz_q)
        quiz_question_id = quiz_q.id
    finally:
        db.close()

    def _list(params=None):
        return client.get("/questions", headers=headers, params={"limit": 200, **(params or {})}).json()

    items_by_id = {item["question_id"]: item for item in _list()["items"]}
    assert items_by_id[used_paper_question_id]["origin"] == "paper"
    assert items_by_id[used_paper_question_id]["used_in_papers"] == [
        {"paper_id": paper_data["id"], "title": "QBank Origin Paper"}
    ]
    assert items_by_id[quiz_question_id]["origin"] == "quiz"
    assert items_by_id[quiz_question_id]["used_in_papers"] == []

    paper_ids = {i["question_id"] for i in _list({"source": "paper"})["items"]}
    assert used_paper_question_id in paper_ids and quiz_question_id not in paper_ids

    quiz_ids = {i["question_id"] for i in _list({"source": "quiz"})["items"]}
    assert quiz_question_id in quiz_ids and used_paper_question_id not in quiz_ids

    unused_before = {i["question_id"] for i in _list({"source": "unused"})["items"]}
    assert quiz_question_id in unused_before
    assert used_paper_question_id not in unused_before  # currently filling a slot

    assert client.get("/questions", headers=headers, params={"source": "bogus"}).status_code == 422

    # Swap in a fresh bank question built to fit the slot -- the old one
    # should stay in the bank but drop out of "used" (item 9b).
    allowed_types = main.paper.allowed_types_for_slot(slot["bloom_level"], 2, "written") or ["short_answer"]
    db = TestSessionLocal()
    try:
        replacement = main.Question(
            user_id=user_id, book_id=book_id, topic="bank", type=allowed_types[0],
            bloom_level=slot["bloom_level"], bloom_check="verified",
            question="A replacement bank question for swapping in.",
            correct_answer="A", explanation="Explanation.",
            marks=2, marking_scheme=json.dumps([{"point": "x", "marks": 2}]),
        )
        db.add(replacement)
        db.commit()
        db.refresh(replacement)
        replacement_id = replacement.id
    finally:
        db.close()

    res = client.patch(
        f"/papers/{paper_data['id']}/slots/{slot['id']}", headers=headers,
        json={"question_id": replacement_id}
    )
    assert res.status_code == 200, res.text

    unused_after = {i["question_id"] for i in _list({"source": "unused"})["items"]}
    assert used_paper_question_id in unused_after  # swapped out -- unused, but still in the bank
    assert used_paper_question_id in {i["question_id"] for i in _list()["items"]}

    # Delete-unused removes the quiz question and the swapped-out paper
    # question, but never the replacement that's now actually in the slot.
    res = client.delete("/questions/unused", headers=headers)
    assert res.status_code == 200
    assert res.json()["deleted"] >= 2

    remaining_ids = {i["question_id"] for i in _list()["items"]}
    assert replacement_id in remaining_ids
    assert quiz_question_id not in remaining_ids
    assert used_paper_question_id not in remaining_ids


def test_delete_book_empties_paper_slots(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "paper_book_delete.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Book Delete Paper")

    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)

    res = client.delete(f"/books/{book_id}", headers=headers)
    assert res.status_code == 200

    detail = client.get(f"/papers/{paper_data['id']}", headers=headers).json()
    assert detail["status"] == "ready"  # untouched
    assert all(slot["question"] is None for slot in detail["slots"])  # all emptied


# -----------------------------
# PHASE 5D: WORD EXPORT
# -----------------------------
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_export_requires_ready_status(client, phase5b_headers):
    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "export_not_ready.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Export Not Ready Paper")

    res = client.get(f"/papers/{paper_data['id']}/export", headers=headers, params={"kind": "teacher"})
    assert res.status_code == 409


def test_export_rejects_unknown_kind(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "export_bad_kind.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Export Bad Kind Paper")
    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)

    res = client.get(f"/papers/{paper_data['id']}/export", headers=headers, params={"kind": "bogus"})
    assert res.status_code == 422


def test_export_owner_only(client, phase5b_headers, phase5b_other_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "export_owner.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Export Owner Paper")
    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)

    res = client.get(
        f"/papers/{paper_data['id']}/export", headers=phase5b_other_headers, params={"kind": "teacher"}
    )
    assert res.status_code == 404


def test_export_student_blocked_with_empty_slots_but_teacher_and_key_still_work(
    client, phase5b_headers, monkeypatch
):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "export_blocked.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Export Blocked Paper")

    def fake(prompt, schema, system=None):
        if schema is main.CLASSIFIER_SCHEMA:
            return {"items": []}
        return {"questions": []}  # nothing generated -- every slot stays empty
    monkeypatch.setattr(llm_module, "generate_json", fake)
    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)

    res = client.get(f"/papers/{paper_data['id']}/export", headers=headers, params={"kind": "student"})
    assert res.status_code == 409
    assert res.json()["detail"] == "Fill all questions first."

    for kind in ("teacher", "answer_key"):
        res2 = client.get(f"/papers/{paper_data['id']}/export", headers=headers, params={"kind": kind})
        assert res2.status_code == 200, res2.text
        assert res2.headers["content-type"] == DOCX_CONTENT_TYPE
        assert len(res2.content) > 0


def test_export_student_succeeds_with_correct_content_type_and_filename(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "export_ok.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Export OK Paper")
    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)

    res = client.get(f"/papers/{paper_data['id']}/export", headers=headers, params={"kind": "student"})
    assert res.status_code == 200
    assert res.headers["content-type"] == DOCX_CONTENT_TYPE
    assert 'filename="Export OK Paper-student.docx"' in res.headers["content-disposition"]
    assert len(res.content) > 0


def test_export_filename_strips_unsafe_characters(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "export_unsafe_name.txt")
    paper_data = _create_paper(client, headers, [book_id], title="Mid/Sem: Exam? 2026*")
    monkeypatch.setattr(llm_module, "generate_json", _make_paper_llm_fake())
    main.limiter.reset()
    client.post(f"/papers/{paper_data['id']}/generate", headers=headers)

    res = client.get(f"/papers/{paper_data['id']}/export", headers=headers, params={"kind": "answer_key"})
    assert res.status_code == 200
    disposition = res.headers["content-disposition"]
    assert "/" not in disposition.split("filename=")[1]
    assert ":" not in disposition.split("filename=")[1]
    assert 'filename="MidSem Exam 2026-answer_key.docx"' in disposition


# -----------------------------
# PHASE 5E EXTRA: total_marks excludes unattempted any_n marks
# -----------------------------
def test_paper_total_marks_excludes_unattempted_any_n_marks(client, phase5b_headers):
    # mid_sem_50: Section A (5x2=10) + Section B ("any 4 of 5" x5m -- a
    # student only ever answers 4*5=20, not the 25 printed) + Section C
    # (2 either_or pairs x10m, marks_on_paper counts each pair once = 20).
    # total_marks = 10+20+20 = 50; marks_on_paper = 10+25+20 = 55.
    headers = phase5b_headers
    book_id = _upload_paper_doc(client, headers, "total_marks_check.txt")

    res = client.post(
        "/papers", headers=headers,
        json={
            "title": "Total Marks Check Paper", "pattern_key": "mid_sem_50",
            "bloom_preset": "balanced", "book_ids": [book_id]
        }
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["total_marks"] == 50

    list_res = client.get("/papers", headers=headers)
    assert list_res.status_code == 200
    summary = next(p for p in list_res.json() if p["id"] == data["id"])
    assert summary["total_marks"] == 50


# -----------------------------
# PHASE 5E: IMPORT LAYOUT FROM AN OLD PAPER
# -----------------------------
def _fake_pattern_import_response():
    return {
        "duration_minutes": 120,
        "total_marks": 50,
        "sections": [
            {
                "title": "Section A", "instructions": "Answer all questions.",
                "marks_each": 2, "num_questions": 5, "choice": "none", "attempt": None
            },
            {
                "title": "Section B", "instructions": "Attempt any 4 of the following 5 questions.",
                "marks_each": 5, "num_questions": 5, "choice": "any_n", "attempt": 4
            },
            {
                "title": "Section C", "instructions": "Answer EITHER (a) OR (b) for each question.",
                "marks_each": 10, "num_questions": 2, "choice": "either_or", "attempt": None
            },
        ],
        "questions": [
            {"section": "Section A", "number": "1", "text": "Define a firewall.", "marks": 2},
            {"section": "Section A", "number": "2", "text": "What is a VPN?", "marks": 2},
            {"section": "Section A", "number": "3", "text": "State the CIA triad.", "marks": 2},
            {"section": "Section A", "number": "4", "text": "List two symmetric ciphers.", "marks": 2},
            {"section": "Section A", "number": "5", "text": "Define a digital signature.", "marks": 2},
            {"section": "Section B", "number": "6", "text": "Explain how TLS establishes a secure session.", "marks": 5},
        ],
    }


def test_import_paper_pattern_returns_pattern_and_bloom_mix(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers

    def fake(prompt, schema, system=None):
        if schema is main.CLASSIFIER_SCHEMA:
            return {"items": [{"id": i, "level": "remember"} for i in range(1, 7)]}
        return _fake_pattern_import_response()
    monkeypatch.setattr(llm_module, "generate_json", fake)

    main.limiter.reset()
    res = client.post(
        "/papers/import-pattern", headers=headers,
        files={"file": ("old_paper.txt", b"Some old paper text content.", "text/plain")}
    )
    assert res.status_code == 200, res.text
    data = res.json()

    sections = data["pattern"]["sections"]
    assert len(sections) == 3
    assert sections[0]["key"] == "A"
    assert sections[0]["choice"] == "none"
    assert sections[1]["choice"] == "any_n"
    assert sections[1]["attempt"] == 4
    assert sections[2]["choice"] == "either_or"
    assert data["pattern"]["duration_minutes"] == 120

    assert len(data["questions"]) == 6

    # All 6 questions' marks (2+2+2+2+2+5=15) classified "remember" --
    # clearly labelled an estimate.
    assert data["bloom_mix"]["estimated"] is True
    assert data["bloom_mix"]["marks"]["remember"] == 15
    assert data["bloom_mix"]["percent"]["remember"] == 100.0


def test_import_paper_pattern_invalid_layout_returns_422(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers

    def fake(prompt, schema, system=None):
        if schema is main.CLASSIFIER_SCHEMA:
            return {"items": []}
        return {"sections": [], "questions": []}
    monkeypatch.setattr(llm_module, "generate_json", fake)

    main.limiter.reset()
    res = client.post(
        "/papers/import-pattern", headers=headers,
        files={"file": ("bad_old_paper.txt", b"Not recognizable as a paper.", "text/plain")}
    )
    assert res.status_code == 422
    assert "layout" in res.json()["detail"].lower()


def test_import_paper_pattern_structurally_invalid_section_returns_422(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers

    def fake(prompt, schema, system=None):
        if schema is main.CLASSIFIER_SCHEMA:
            return {"items": []}
        return {
            "duration_minutes": 60, "total_marks": 0,
            "sections": [{
                "title": "Section A", "instructions": "x",
                "marks_each": 0, "num_questions": 0, "choice": "none", "attempt": None
            }],
            "questions": [],
        }
    monkeypatch.setattr(llm_module, "generate_json", fake)

    main.limiter.reset()
    res = client.post(
        "/papers/import-pattern", headers=headers,
        files={"file": ("invalid_section.txt", b"text", "text/plain")}
    )
    assert res.status_code == 422


def test_import_paper_pattern_file_not_added_to_documents_and_deleted(client, phase5b_headers, monkeypatch):
    import llm as llm_module

    headers = phase5b_headers
    captured = {}
    real_extract = main._extract_text_for_pattern_import

    def spy_extract(file_path, filename):
        captured["path"] = file_path
        assert os.path.exists(file_path)  # still there while the request is in flight
        return real_extract(file_path, filename)
    monkeypatch.setattr(main, "_extract_text_for_pattern_import", spy_extract)

    def fake(prompt, schema, system=None):
        if schema is main.CLASSIFIER_SCHEMA:
            return {"items": []}
        return _fake_pattern_import_response()
    monkeypatch.setattr(llm_module, "generate_json", fake)

    books_before = client.get("/books", headers=headers).json()

    main.limiter.reset()
    res = client.post(
        "/papers/import-pattern", headers=headers,
        files={"file": ("old_paper_cleanup.txt", b"Some old paper text content.", "text/plain")}
    )
    assert res.status_code == 200, res.text

    books_after = client.get("/books", headers=headers).json()
    assert len(books_after) == len(books_before)  # never added as a document

    assert "path" in captured
    assert not os.path.exists(captured["path"])  # deleted once the request finished

"""
End-to-end API tests against a real FastAPI app instance (TestClient),
using an isolated on-disk SQLite database so nothing here touches the real
dev database (teacher_ai.db). Exercises the full auth + upload + per-user
isolation flow described in the deployment checklist.
"""
import os
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

    headers = llm_mock_headers
    book_id = _upload_water_cycle_doc(client, headers, "quiz_retry.txt")

    call_count = {"n": 0}

    def fake_generate_json(prompt, schema, system=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"questions": []}  # first attempt: nothing usable
        return {
            "questions": [
                {
                    "id": 1, "type": "short_answer",
                    "question": "What causes water to evaporate?",
                    "correctAnswer": "Heat from the sun.",
                    "explanation": "Explained in source 1.", "source_index": 1
                },
                {
                    "id": 2, "type": "long_answer",
                    "question": "Describe condensation.",
                    "correctAnswer": "Water vapor cools into droplets.",
                    "explanation": "Explained in source 1.", "source_index": 1
                },
                {
                    "id": 3, "type": "scenario",
                    "question": "A hiker sees clouds forming overhead. What process is at work?",
                    "correctAnswer": "Condensation.",
                    "explanation": "Explained in source 1.", "source_index": 1
                },
            ]
        }

    monkeypatch.setattr(llm_module, "generate_json", fake_generate_json)

    res = client.get("/generate-quiz", headers=headers, params={"topic": "water cycle"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["questions"]) == 3
    assert call_count["n"] == 2  # exactly one retry happened

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

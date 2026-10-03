# AI Teacher Assistant

A RAG-based teaching assistant. Upload your own course material (PDF, Word, PowerPoint) and get grounded AI chat answers, semantic search, and auto-generated quizzes — all sourced from what you actually uploaded, not the open internet.

Gemini (`GEMINI_API_KEY`, free tier) is the main AI provider for both answer generation and embeddings. Voyage AI is available as an optional alternative embedding provider, and everything can also run fully offline via local models (`flan-t5-small` for generation, `all-MiniLM-L6-v2` for embeddings) with no API key at all. See [AI providers](#ai-providers) below.

## Features

- **Document upload** — PDF / DOCX / PPTX / TXT / MD, or one or more photos/scans (JPG / PNG / WEBP / HEIC), parsed and chunked into a semantic index; processing runs in the background with a live status (`processing` → `needs_review`/`ready`/`failed`, with a progress count and a one-click retry on failure) so an upload never blocks the request
- **OCR for scanned PDFs and handwritten photos** — a PDF page with no usable text layer, or any uploaded photo, is transcribed with Gemini vision; optionally held for review before it's indexed (see [Document processing](#document-processing))
- **AI Chat** — ask questions, get answers grounded in your uploaded material with cited sources
- **Semantic search** — find relevant passages by meaning, not just keyword match
- **Quiz Builder** — generates MCQ / true-false / fill-in-the-blank / short & long answer / scenario / viva / interview questions from your material, with grading
- **Notes Summarizer** and **AI Tutor** — bullet-point summaries and free-form Q&A
- **Analytics** — real usage stats (active study time, quiz performance, question activity, document library breakdown), computed from what you've actually done, not placeholder numbers

## Tech stack

**Backend** — FastAPI, SQLAlchemy + SQLite, Gemini (`google-genai`) + Voyage (`voyageai`) + local (`sentence-transformers` / `transformers` Flan-T5) AI providers, `pdfplumber` / `python-docx` / `python-pptx`, OCR via Gemini vision + `pypdfium2` / `Pillow` / `pillow-heif`, JWT auth (`python-jose` + `passlib`), rate limiting (`slowapi`)

**Frontend** — React 19, React Router, Vite, Tailwind CSS v4

## Project structure

```
backend/
  main.py               FastAPI app & routes
  auth.py                JWT auth, password hashing
  jobs.py                 Background document-processing worker (ThreadPoolExecutor), incl. OCR orchestration
  ocr.py                   PDF-page rendering (pypdfium2) + image preparation (Pillow) for OCR
  models.py               SQLAlchemy models (User, Book, Page, DocumentPage)
  database.py            SQLite engine/session setup
  document_parsers.py    PDF/DOCX/PPTX parsing + chunking
  embeddings.py           Swappable embeddings (Gemini / Voyage / local MiniLM)
  llm.py                   Swappable answer generation (Gemini / local Flan-T5) + Gemini-vision OCR (read_image)
  rag.py                   Local Flan-T5 generation, quiz generation, relevance checking
  scripts/reembed.py      Re-embeds stored pages after switching EMBEDDING_PROVIDER
  uploads/                Saved original files, one folder per <user_id>/<book_id> (gitignored)
frontend/
  src/pages/               Home, Chat, Documents, AITools, Analytics, Login
  src/components/          Sidebar, PageLayout, ProtectedRoute, DocumentStatusBadge
  src/hooks/                Auth, active-time tracking, quiz/query history
  src/services/api.js      Backend API client
```

## Setup

### Backend

```
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # macOS/Linux
pip install -r requirements.txt
```

For running the test suite too, install the dev extras instead (installs `requirements.txt` plus `pytest`):

```
pip install -r requirements-dev.txt
```

`requirements.txt` alone is enough for the default Gemini-based setup. `torch`, `transformers`, and `sentence-transformers` live in a separate `requirements-local.txt` instead, since they're only needed for `LLM_PROVIDER=local` and/or `EMBEDDING_PROVIDER=local` (see [AI providers](#ai-providers)) — install it only if you're using one of those:

```
pip install -r requirements-local.txt
```

Create `backend/.env` — see `backend/.env.example` for the full list with descriptions, copy it as a starting point (`cp .env.example .env`). Every variable is optional except `SECRET_KEY` and `GEMINI_API_KEY` (required for the default AI providers — see [AI providers](#ai-providers) below); the rest fall back to sane local-dev defaults:

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | *(required)* | Signs JWT access tokens. Generate a long random string — the app refuses to start without it. |
| `HF_TOKEN` | unset | Hugging Face token. Reserved for future use; not currently read by any module, only raises Hub rate limits for the one-time model download if you hit them. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Minutes before an issued JWT expires. |
| `CORS_ORIGINS` | the local Vite dev ports | Comma-separated list of allowed frontend origins. **Must** be set in production to your real frontend URL(s) — see [Production deployment](#production-deployment). |
| `MAX_UPLOAD_MB` | `60` | Maximum accepted size for the single-document `file` upload (phone scans of a multi-page PDF are often 30-60 MB). Per-image uploads (`files`) are separately capped at 15 MB each, regardless of this setting. |
| `LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`). |
| `DATABASE_URL` | an absolute `sqlite:///.../backend/teacher_ai.db` path next to `database.py` | SQLAlchemy database URL. Only set this to point at a different file or a different database engine for production. |
| `UPLOAD_DIR` | an absolute `uploads/` folder next to `main.py` | Where uploaded original files are saved (`<UPLOAD_DIR>/<user_id>/<book_id>/original<ext>`) for background processing and retry. |
| `JOB_WORKERS` | `2` | Thread pool size for background document processing (`jobs.py`). |
| `EMBED_JOB_MAX_WAIT_SECONDS` | `1800` | How long a background upload job waits out Gemini embedding rate limits before marking the document `failed` — see [AI providers](#ai-providers). |
| `OCR_MIN_CHARS` | `40` | Minimum real characters a PDF page's extracted text needs before it's treated as "text" instead of being OCR'd — see [Document processing](#document-processing). |
| `MAX_OCR_PAGES` | `50` | Per-document cap on low-text PDF pages sent to OCR, and on images in one multi-image/photo upload. |

See [AI providers](#ai-providers) for `LLM_PROVIDER`, `EMBEDDING_PROVIDER`, and the Gemini/Voyage-specific variables.

Run it:

```
uvicorn main:app --reload
```

Serves on `http://127.0.0.1:8000`. AI clients/models are prepared once at startup (FastAPI lifespan) rather than per-request — this fails fast with a clear error if a required API key is missing. Local models (used only when a provider is set to `local`) are downloaded on first use (~100MB total, one-time). Check `GET /health` for `{"status", "models_ready", "database_ready", "llm": {"provider", "model", "configured", "ready"}}`.

> **Windows + antivirus HTTPS scanning (e.g. Avast):** if the model download fails with `SSL: CERTIFICATE_VERIFY_FAILED`, your antivirus is intercepting HTTPS and Python doesn't trust its certificate. Fix: `pip install pip-system-certs` inside the venv.

### Frontend

```
cd frontend
npm install
npm run dev
```

Serves on `http://localhost:5173`. By default it talks to the backend at `http://127.0.0.1:8000`; to point it elsewhere, copy `frontend/.env.example` to `frontend/.env` and set `VITE_API_URL`.

**`VITE_API_URL` is baked in at build time, not read at runtime.** Vite statically replaces `import.meta.env.VITE_API_URL` when it builds the bundle, so the variable must be set in the environment (or in `frontend/.env.production`) *before* running `npm run build` — setting it later, or on the server that hosts `dist/`, has no effect. To confirm which URL a given build was compiled with, search the built bundle: `grep -o "http[^\"]*" frontend/dist/assets/*.js | head`.

```
# frontend/.env.production
VITE_API_URL=https://api.your-domain.com
```

```
cd frontend
npm run build      # reads VITE_API_URL at this point
npm run preview    # optional: serve dist/ locally to sanity-check the build
```

`dist/` is a static bundle — serve it with any static file host (nginx, Vercel, Netlify, `serve dist/`, etc.); it doesn't need Node running in production.

## AI providers

**Gemini is the main provider for both** of the things that are swappable via env vars — which AI generates chat/quiz answers, and which AI turns text into search vectors — and both default to Gemini's free tier, reusing the same `GEMINI_API_KEY`. Voyage is available as an optional alternative for embeddings only, and both can be switched to fully-local models with no API key and no internet access at all:

```
# .env — run everything offline, no API key needed
LLM_PROVIDER=local
EMBEDDING_PROVIDER=local
```

Using either `local` value also needs `pip install -r requirements-local.txt` (see [Setup](#setup)) — `torch`/`transformers`/`sentence-transformers` aren't installed by `requirements.txt` alone.

**Answer generation** — `LLM_PROVIDER`:
| Value | Notes |
|---|---|
| `gemini` (default, main) | Hosted, via `google-genai`. Model from `GEMINI_MODEL` (default `gemini-3.8-flash`). Requires `GEMINI_API_KEY` — the app fails to start without it while this is active. |
| `local` (optional) | The in-process Flan-T5 model (`rag.py`), no API key, works fully offline. See [Known limitations](#known-limitations) for its quality tradeoffs. |

**Embeddings** — `EMBEDDING_PROVIDER`:
| Value | Notes |
|---|---|
| `gemini` (default, main) | Hosted, via `google-genai`, reusing `GEMINI_API_KEY`. Model `GEMINI_EMBED_MODEL` (default `gemini-embedding-001`), vector size `GEMINI_EMBED_DIM` (default `768`), batched at `GEMINI_EMBED_BATCH_SIZE` (default `50`). |
| `voyage` (optional) | Hosted, via `voyageai`. Requires its own `VOYAGE_API_KEY`. Model `VOYAGE_MODEL` (default `voyage-4`), 1024-dimensional, batched at 64. **Without a payment method on the Voyage account, it's capped at ~10K tokens/minute** — noticeably slower for large uploads than the other providers. |
| `local` (optional) | The in-process `all-MiniLM-L6-v2` model, no API key, works fully offline, 384-dimensional. |

Vectors from different providers (or different dimensions of the same provider) aren't comparable, so every page records which `embedding_model` produced its vector (`Page.embedding_model`, e.g. `"gemini:gemini-embedding-001:768"`). Retrieval only scores pages matching the currently active model — **whenever you change `EMBEDDING_PROVIDER` (or switch back), existing documents stop showing up in chat/search** (with a clear "Your documents need re-indexing" message) until you re-embed them with the newly active provider:

```
cd backend
python scripts/reembed.py                    # every page, all users
python scripts/reembed.py --username alice    # only one user's pages
```

This re-embeds in batches with progress output and invalidates the retrieval cache when done; running it again when nothing needs re-embedding is a harmless no-op (each page keeps its existing vector if `embedding_model` already matches the active one — no need to re-run it after every restart, only after an actual provider/model change).

Rate limits: both Gemini paths retry transient failures automatically, and a Gemini **embedding** call specifically paces itself around the free tier's per-minute quota (waiting out a `429` and continuing) rather than failing outright — see `EMBED_MAX_WAIT_SECONDS` / `EMBED_QUERY_MAX_WAIT_SECONDS` / `EMBED_JOB_MAX_WAIT_SECONDS` in `.env.example`. On the interactive request path (chat/search/tutor/flashcards/quiz), if the service is still unavailable after that, routes return a `503`: `/chat`, `/tools/tutor`, `/tools/summarize`, `/tools/flashcards`, `/generate-quiz`, and `/semantic-search` all reply `"AI service is busy. Please try again in a minute."` — just retry shortly after. The background upload job (see [Document processing](#document-processing)) uses a much longer wait budget instead, since no request is blocked on it, and marks the document `failed` with `"Search service is busy, please retry."` only if that's exhausted too.

## Document processing

`POST /upload-book` validates the file(s), saves them to `UPLOAD_DIR`, creates the `Book` row with `status="processing"`, and returns `202 {"book_id", "status": "processing"}` immediately — parsing/OCR, chunking, embedding, and saving pages all happen afterward in a background worker (`jobs.py`, a small `ThreadPoolExecutor` sized by `JOB_WORKERS`), each job with its own database session.

**Upload shapes** — a single traditional document goes in the `file` field (`.pdf`/`.docx`/`.pptx`/`.doc`/`.ppt`/`.txt`/`.md`); one or more page photos/scans go in the `files` field instead (`.jpg`/`.jpeg`/`.png`/`.webp`/`.heic`, up to `MAX_OCR_PAGES` of them), treated as one document with pages in the order sent. A form field `review_ocr` (default `true`) controls what happens to OCR'd text — see below.

**Per-page extraction** (`DocumentPage`, one row per page regardless of source) — a PDF page keeps its real `pdfplumber` text when there are at least `OCR_MIN_CHARS` characters of it (`method="text"`); otherwise that page is rendered to an image (`pypdfium2`, ~200 DPI) and OCR'd. Every page of an image upload is always OCR'd. DOCX/PPTX/TXT/MD are unchanged (always `method="text"`). OCR (`llm.read_image`, Gemini vision) transcribes everything verbatim, including handwriting, marking truly unreadable words `[illegible]` — images are normalized first (`ocr.py`: EXIF rotation fixed, converted to RGB, downscaled to at most 2000px on the longest side, light auto-contrast; no OpenCV). `Book.source_type` (`text`/`scanned`/`mixed`) is set from what was actually extracted; an all-OCR document with every page still empty has nothing to index and ends up `failed`, same as before OCR existed.

**Review gate** — an OCR'd page's `review_status` is `"needs_review"` when `review_ocr=true`, otherwise `"auto_approved"`. If **any** page needs review, the whole book stops at `status="needs_review"` and nothing is chunked or embedded yet (no review/approve endpoint exists yet — a later phase adds one). Only once every page is approved does chunking (from `DocumentPage.extracted_text`, keeping each chunk's original page number) + embedding + saving `Page` rows proceed to `status="ready"`.

- `GET /books` now also returns each book's `status`, `error`, `source_type`, `pages_total`, `pages_done`, and `created_at`.
- `GET /books/{id}/status` — poll this while `status == "processing"` for live progress: `pages_done`/`pages_total` count OCR pages while `source_type` is still null, then switch to counting embedding chunks once extraction finishes.
- `POST /books/{id}/retry` — re-runs processing for a `failed` book from its saved original file(s) (same `review_ocr` choice as the original upload); `400` if the book isn't `failed` or the file(s) are missing. Re-extracts (and re-OCRs) everything from scratch rather than resuming partway through.
- `DELETE /books/{id}` also removes the book's `UPLOAD_DIR` folder (original file(s) + any rendered/saved OCR page images).
- Chat, search, semantic search, flashcards, and quiz generation only ever see pages from `ready` books (a `needs_review` book behaves like one with no documents yet, same as `processing`/`failed`).
- If the server restarts while a book is still `processing`, it's marked `failed` on the next startup (`"Processing was interrupted. Please re-upload."`) — jobs aren't resumed across a restart.
- `LLM_PROVIDER=local` has no vision model, so an OCR'd page on that provider fails the whole book with `"Reading scanned or handwritten pages needs the Gemini provider."`

The frontend (`Documents.jsx`/`Home.jsx`) shows the new book immediately with a status badge (now including `needs_review`, amber) and polls `GET /books/{id}/status` every 2 seconds until it reaches a stable state, then stops. Documents.jsx's upload also has a "Let me check the text before it's used" checkbox (the `review_ocr` choice), checked by default.

## Migrating an existing database

Books are scoped to the account that uploaded them (`Book.user_id`), and pages record which embedding model produced their vector (`Page.embedding_model`). A `teacher_ai.db` created before either of these existed needs both backfilled:

```
cd backend
python migrate.py <username>
```

This adds the `books.user_id` column if it's missing and assigns every currently-unowned book to `<username>` (which must already be a registered account), adds `pages.embedding_model` if missing, backfilling it to `"local:all-MiniLM-L6-v2"` on existing rows (all pages embedded before this column existed used local MiniLM), adds the Phase 3A processing columns (`books.status`/`error`/`source_type`/`pages_total`/`pages_done`/`created_at`) if missing, backfilling `status="ready"` on existing rows (they were fully processed synchronously before the background pipeline existed), and adds the Phase 3B OCR columns (`books.review_ocr`, and the `document_pages` table) if missing. Safe to run more than once — every column/table is only added once, and every backfill only ever touches rows that still need it.

## Running tests

```
cd backend
pip install -r requirements-dev.txt   # installs requirements.txt + pytest
python -m pytest tests/ -v
```

The whole suite runs with **no internet connection and no real `backend/.env`** — `tests/conftest.py` sets a dummy `SECRET_KEY`/`GEMINI_API_KEY` (so the app's startup lifespan succeeds without a real key or a config file), sets `JOBS_SYNC=true` (so `jobs.py`'s background document processing runs inline instead of racing a real worker thread), and replaces `embeddings.embed_documents()`/`embed_query()` with deterministic fake vectors everywhere except `tests/test_embeddings.py` itself and the handful of tests that deliberately exercise the real embeddings pipeline against a mocked low-level client (marked `@pytest.mark.real_embeddings_path`) to test rate-limit handling; `tests/test_llm.py` and the LLM-dependent `tests/test_api.py` cases mock `llm.generate()`/`generate_json()` directly. No test makes a real API call.

`tests/test_quiz.py` exercises quiz fairness rules (no scenario-question answer leaks, True/False isn't always the same value, no repeated concepts) directly against `rag.py` — no server or database needed. `tests/test_api.py` drives the real FastAPI app through `TestClient` against an isolated on-disk SQLite database and upload folder (`tests/test_api.db` / `tests/test_uploads/`, created and deleted automatically — neither ever touches the real `teacher_ai.db` or `uploads/`): register, login, the generic bad-login message, uploading a small `.txt`, listing books, per-user isolation (a second account can neither see nor delete another account's book), delete, the Gemini-backed chat/summarize/flashcards/quiz routes and their 503/429 edge cases (all against mocked LLM/embedding clients), and the background-processing flow — a background failure (no extractable text, or an embedding service that stays rate-limited) leaves the book `status="failed"` with a friendly error instead of erroring the upload itself, `/books/{id}/status` and `/books/{id}/retry` 404 for a non-owner, retry re-processes a failed book back to `ready` from its saved file, delete removes the book's upload folder, and a book stuck `processing` from an interrupted run is marked `failed` on the next startup.

The OCR tests in the same file mock `llm.read_image()` directly (never a real Gemini vision call) and, for the PDF per-page decision, `document_parsers.parse_pdf()` and `ocr.render_pdf_page_to_png()` -- so a mixed scanned/typed PDF doesn't need a real multi-page PDF fixture, just canned per-page text. They cover: a low-text PDF page gets OCR'd while a text-rich one doesn't (and `source_type` comes out `"mixed"`); several images become one book with pages in submission order; `.heic` is accepted; `review_ocr=true` stops at `status="needs_review"` with no `Page` rows yet, `review_ocr=false` goes straight to `ready`; an `[illegible]` marker survives into the indexed text; `LLM_PROVIDER=local` fails OCR with the documented friendly message; and `MAX_OCR_PAGES` is enforced at upload time.

## Production deployment

Start the backend with a single worker — the embedding and answer-generation models are loaded into that worker's memory at startup, so extra workers would each load their own separate copy and none of them would share the in-memory retrieval cache:

```
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

Before deploying, set:

- **`SECRET_KEY`** — required; generate a long random string (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`). Tokens signed with a different key than the one validating them will all be rejected as invalid.
- **`GEMINI_API_KEY`** — required with the default `LLM_PROVIDER=gemini` / `EMBEDDING_PROVIDER=gemini`; the app fails to start without it. See [AI providers](#ai-providers) for switching to Voyage or fully-local models instead.
- **`CORS_ORIGINS`** — set to your real frontend origin(s) (comma-separated for more than one), e.g. `CORS_ORIGINS=https://app.your-domain.com`. Left unset, the backend only allows the local Vite dev ports and will reject the deployed frontend's requests.
- **`VITE_API_URL`** (frontend) — the backend's public URL, set before running `npm run build` (see [Frontend](#frontend) above — it's compiled into the bundle, not read at runtime).

Also worth setting for a real deployment: `ACCESS_TOKEN_EXPIRE_MINUTES`, `MAX_UPLOAD_MB`, `LOG_LEVEL`, and `UPLOAD_DIR` (see the env var table in [Setup](#setup)) — point `UPLOAD_DIR` at persistent storage, since a failed book can only be retried while its original file is still there, and a restart marks any book still `processing` as `failed` (jobs aren't resumed).

`POST /login` and `POST /register` are rate-limited to 10 requests/minute per IP (`slowapi`) to slow down credential-stuffing and account-creation abuse; a client over the limit gets `429` with `{"error": "Rate limit exceeded: ..."}`. The AI routes are separately rate-limited **per logged-in user** (not per IP, so one user can't exhaust another's budget): `/chat` and `/tools/tutor` at 20/minute, `/tools/summarize` and `/tools/flashcards` at 10/minute, `/generate-quiz` at 5/minute — a client over one of those limits gets `429` with `{"detail": "Too many requests. Please wait a minute."}`. The limiter's counters are in-memory and per-process, so they reset on restart and aren't shared across multiple worker processes or machines — fine for the single-worker setup above, but wouldn't rate-limit correctly if scaled to multiple workers without switching to a shared backing store (e.g. Redis).

Unexpected server errors (anything not raised deliberately as an `HTTPException`) are caught by a global handler: the client always gets a plain `{"detail": "Internal server error"}` with a `500`, never the exception text or a stack trace; the real exception and stack trace go to the log instead.

## Known limitations

- **OCR needs the Gemini provider.** `LLM_PROVIDER=local` has no vision model, so a scanned PDF page or any photo upload fails the whole document with a clear error instead of silently producing an empty or garbled page.
- **No review/approve endpoint yet.** A book with any OCR'd page and `review_ocr=true` stops at `status="needs_review"` with its transcriptions saved (`DocumentPage.extracted_text`) and nothing chunked/embedded — there's no API yet to view/edit/approve that text and move it to `ready`; upload with `review_ocr=false` to skip the gate entirely.
- **Retry re-extracts (and re-OCRs) everything.** `/books/{id}/retry` doesn't resume partway through or reuse a previous attempt's already-transcribed pages, so retrying a document that failed late (e.g. during embedding, after OCR already succeeded) re-runs OCR on every low-text/image page again.
- **`flan-t5-small` answer quality (LLM_PROVIDER=local only).** The default `LLM_PROVIDER=gemini` doesn't have this limitation. The local answer-generation model is intentionally small (so it runs on a CPU with no external API key), which means answers can be shallow, occasionally repetitive, or misphrase a nuance from the source text. For sharper answers on `local`, uploading more specific/well-structured source material tends to help more than rephrasing the question.
- **Free-tier rate limits.** Gemini's free tier enforces per-minute quotas on both embeddings and generation; a large upload or a burst of chat/quiz/summarize/flashcard requests can hit them. Embedding requests pace themselves and wait out the quota automatically instead of failing (see [AI providers](#ai-providers)); generation requests retry briefly and then return a `503` ("AI service is busy...") if the service is still unavailable — just retry shortly after. Voyage's free tier (no payment method on the account) is similarly capped, at ~10K tokens/minute.
- **Single SQLite file, single worker.** Fine for individual or small-team use; not built for high-concurrency or multi-instance deployment (see the `--workers 1` note above).
- **English-oriented.** The embedding and generation models (Gemini, Voyage, and the local fallbacks) are primarily English-trained; other languages will work less reliably for retrieval and generation.

## Notes

- The SQLite database (`backend/teacher_ai.db`) is created automatically on first run and is gitignored — it's local dev data, not meant to be shared.

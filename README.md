# AI Teacher Assistant

A RAG-based teaching assistant. Upload your own course material (PDF, Word, PowerPoint) and get grounded AI chat answers, semantic search, and auto-generated quizzes — all sourced from what you actually uploaded, not the open internet.

Gemini (`GEMINI_API_KEY`, free tier) is the main AI provider for both answer generation and embeddings. Voyage AI is available as an optional alternative embedding provider, and everything can also run fully offline via local models (`flan-t5-small` for generation, `all-MiniLM-L6-v2` for embeddings) with no API key at all. See [AI providers](#ai-providers) below.

## Features

- **Document upload** — PDF / DOCX / PPTX, parsed and chunked into a semantic index
- **AI Chat** — ask questions, get answers grounded in your uploaded material with cited sources
- **Semantic search** — find relevant passages by meaning, not just keyword match
- **Quiz Builder** — generates MCQ / true-false / fill-in-the-blank / short & long answer / scenario / viva / interview questions from your material, with grading
- **Notes Summarizer** and **AI Tutor** — bullet-point summaries and free-form Q&A
- **Analytics** — real usage stats (active study time, quiz performance, question activity, document library breakdown), computed from what you've actually done, not placeholder numbers

## Tech stack

**Backend** — FastAPI, SQLAlchemy + SQLite, Gemini (`google-genai`) + Voyage (`voyageai`) + local (`sentence-transformers` / `transformers` Flan-T5) AI providers, `pdfplumber` / `python-docx` / `python-pptx`, JWT auth (`python-jose` + `passlib`), rate limiting (`slowapi`)

**Frontend** — React 19, React Router, Vite, Tailwind CSS v4

## Project structure

```
backend/
  main.py               FastAPI app & routes
  auth.py                JWT auth, password hashing
  models.py               SQLAlchemy models (User, Book, Page)
  database.py            SQLite engine/session setup
  document_parsers.py    PDF/DOCX/PPTX parsing + chunking
  embeddings.py           Swappable embeddings (Gemini / Voyage / local MiniLM)
  llm.py                   Swappable answer generation (Gemini / local Flan-T5)
  rag.py                   Local Flan-T5 generation, quiz generation, relevance checking
  scripts/reembed.py      Re-embeds stored pages after switching EMBEDDING_PROVIDER
frontend/
  src/pages/               Home, Chat, Documents, AITools, Analytics, Login
  src/components/          Sidebar, PageLayout, ProtectedRoute
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

Create `backend/.env` — see `backend/.env.example` for the full list with descriptions, copy it as a starting point (`cp .env.example .env`). Every variable is optional except `SECRET_KEY` and `GEMINI_API_KEY` (required for the default AI providers — see [AI providers](#ai-providers) below); the rest fall back to sane local-dev defaults:

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | *(required)* | Signs JWT access tokens. Generate a long random string — the app refuses to start without it. |
| `HF_TOKEN` | unset | Hugging Face token. Reserved for future use; not currently read by any module, only raises Hub rate limits for the one-time model download if you hit them. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Minutes before an issued JWT expires. |
| `CORS_ORIGINS` | the local Vite dev ports | Comma-separated list of allowed frontend origins. **Must** be set in production to your real frontend URL(s) — see [Production deployment](#production-deployment). |
| `MAX_UPLOAD_MB` | `25` | Maximum accepted document upload size. |
| `LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`). |

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

Rate limits: both Gemini paths retry transient failures automatically, and a Gemini **embedding** call specifically paces itself around the free tier's per-minute quota (waiting out a `429` and continuing) rather than failing outright — see `EMBED_MAX_WAIT_SECONDS` / `EMBED_QUERY_MAX_WAIT_SECONDS` in `.env.example`. If the service is still unavailable after that, routes return a `503` instead of a generic error or a stack trace: `/upload-book` replies `"Search service is busy. Please try uploading again in a minute."` (nothing is saved), and `/chat`, `/tools/tutor`, `/tools/summarize`, `/tools/flashcards`, `/generate-quiz`, and `/semantic-search` all reply `"AI service is busy. Please try again in a minute."` — just retry shortly after.

## Migrating an existing database

Books are scoped to the account that uploaded them (`Book.user_id`), and pages record which embedding model produced their vector (`Page.embedding_model`). A `teacher_ai.db` created before either of these existed needs both backfilled:

```
cd backend
python migrate.py <username>
```

This adds the `books.user_id` column if it's missing and assigns every currently-unowned book to `<username>` (which must already be a registered account), and adds `pages.embedding_model` if missing, backfilling it to `"local:all-MiniLM-L6-v2"` on existing rows (all pages embedded before this column existed used local MiniLM). Safe to run more than once — every column is only added once, and every backfill only ever touches rows that still need it.

## Running tests

```
cd backend
pip install -r requirements-dev.txt   # installs requirements.txt + pytest
python -m pytest tests/ -v
```

`tests/test_quiz.py` exercises quiz fairness rules (no scenario-question answer leaks, True/False isn't always the same value, no repeated concepts) directly against `rag.py` — no server or database needed. `tests/test_api.py` drives the real FastAPI app through `TestClient` against an isolated on-disk SQLite database (`tests/test_api.db`, created and deleted automatically — it never touches `teacher_ai.db`): register, login, the generic bad-login message, uploading a small `.txt`, listing books, per-user isolation (a second account can neither see nor delete another account's book), and delete. The first run loads the real AI models via the app's startup lifespan, so it takes a similar amount of time to boot as the live server.

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

Also worth setting for a real deployment: `ACCESS_TOKEN_EXPIRE_MINUTES`, `MAX_UPLOAD_MB`, and `LOG_LEVEL` (see the env var table in [Setup](#setup)).

`POST /login` and `POST /register` are rate-limited to 10 requests/minute per IP (`slowapi`) to slow down credential-stuffing and account-creation abuse; a client over the limit gets `429` with `{"error": "Rate limit exceeded: ..."}`. The limiter's counters are in-memory and per-process, so they reset on restart and aren't shared across multiple worker processes or machines — fine for the single-worker setup above, but wouldn't rate-limit correctly if scaled to multiple workers without switching to a shared backing store (e.g. Redis).

Unexpected server errors (anything not raised deliberately as an `HTTPException`) are caught by a global handler: the client always gets a plain `{"detail": "Internal server error"}` with a `500`, never the exception text or a stack trace; the real exception and stack trace go to the log instead.

## Known limitations

- **No OCR or handwriting recognition yet.** A scanned PDF with no text layer (an image of a page, not extracted text) or a handwritten document returns a `422` with a clear message rather than silently producing an empty or garbled document — reading scanned/handwritten content is planned for Phase 3, not implemented yet.
- **`flan-t5-small` answer quality (LLM_PROVIDER=local only).** The default `LLM_PROVIDER=gemini` doesn't have this limitation. The local answer-generation model is intentionally small (so it runs on a CPU with no external API key), which means answers can be shallow, occasionally repetitive, or misphrase a nuance from the source text. For sharper answers on `local`, uploading more specific/well-structured source material tends to help more than rephrasing the question.
- **Free-tier rate limits.** Gemini's free tier enforces per-minute quotas on both embeddings and generation; a large upload or a burst of chat/quiz/summarize/flashcard requests can hit them. Embedding requests pace themselves and wait out the quota automatically instead of failing (see [AI providers](#ai-providers)); generation requests retry briefly and then return a `503` ("AI service is busy...") if the service is still unavailable — just retry shortly after. Voyage's free tier (no payment method on the account) is similarly capped, at ~10K tokens/minute.
- **Single SQLite file, single worker.** Fine for individual or small-team use; not built for high-concurrency or multi-instance deployment (see the `--workers 1` note above).
- **English-oriented.** The embedding and generation models (Gemini, Voyage, and the local fallbacks) are primarily English-trained; other languages will work less reliably for retrieval and generation.

## Notes

- The SQLite database (`backend/teacher_ai.db`) is created automatically on first run and is gitignored — it's local dev data, not meant to be shared.

# AI Teacher Assistant

A RAG-based teaching assistant. Upload your own course material (PDF, Word, PowerPoint) and get grounded AI chat answers, semantic search, and auto-generated quizzes — all sourced from what you actually uploaded, not the open internet.

Runs fully local: embeddings ([`all-MiniLM-L6-v2`](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)) and answer generation ([`flan-t5-small`](https://huggingface.co/google/flan-t5-small)) both run on your own machine via `sentence-transformers` / `transformers` — no external LLM API key required.

## Features

- **Document upload** — PDF / DOCX / PPTX, parsed and chunked into a semantic index
- **AI Chat** — ask questions, get answers grounded in your uploaded material with cited sources
- **Semantic search** — find relevant passages by meaning, not just keyword match
- **Quiz Builder** — generates MCQ / true-false / fill-in-the-blank / short & long answer / scenario / viva / interview questions from your material, with grading
- **Notes Summarizer** and **AI Tutor** — bullet-point summaries and free-form Q&A
- **Analytics** — real usage stats (active study time, quiz performance, question activity, document library breakdown), computed from what you've actually done, not placeholder numbers

## Tech stack

**Backend** — FastAPI, SQLAlchemy + SQLite, `sentence-transformers`, `transformers` (Flan-T5), `pdfplumber` / `python-docx` / `python-pptx`, JWT auth (`python-jose` + `passlib`), rate limiting (`slowapi`)

**Frontend** — React 19, React Router, Vite, Tailwind CSS v4

## Project structure

```
backend/
  main.py               FastAPI app & routes
  auth.py                JWT auth, password hashing
  models.py               SQLAlchemy models (User, Book, Page)
  database.py            SQLite engine/session setup
  document_parsers.py    PDF/DOCX/PPTX parsing + chunking
  embeddings.py           Sentence-transformer embedding model
  rag.py                   Answer generation, quiz generation, relevance checking
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

Create `backend/.env` — see `backend/.env.example` for the full list with descriptions, copy it as a starting point (`cp .env.example .env`). Every variable is optional except `SECRET_KEY`; the rest fall back to sane local-dev defaults:

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | *(required)* | Signs JWT access tokens. Generate a long random string — the app refuses to start without it. |
| `HF_TOKEN` | unset | Hugging Face token. Reserved for future use; not currently read by any module, only raises Hub rate limits for the one-time model download if you hit them. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Minutes before an issued JWT expires. |
| `CORS_ORIGINS` | the local Vite dev ports | Comma-separated list of allowed frontend origins. **Must** be set in production to your real frontend URL(s) — see [Production deployment](#production-deployment). |
| `MAX_UPLOAD_MB` | `25` | Maximum accepted document upload size. |
| `LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`). |

Run it:

```
uvicorn main:app --reload
```

Serves on `http://127.0.0.1:8000`. First run downloads the two AI models (~100MB total, one-time); they're loaded once at startup (FastAPI lifespan) rather than per-request. Check `GET /health` for `{"status", "models_ready", "database_ready"}`.

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

## Migrating an existing database

Books are scoped to the account that uploaded them (`Book.user_id`). A `teacher_ai.db` created before this existed has books with no owner — everyone's book list will look empty until you assign them:

```
cd backend
python migrate.py <username>
```

This adds the `books.user_id` column if it's missing, then assigns every currently-unowned book to `<username>` (which must already be a registered account). Safe to run more than once — the column is only added once, and the assignment only ever touches books that still have no owner, so re-running it with the same or a different username won't reassign books that already belong to someone.

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
- **`CORS_ORIGINS`** — set to your real frontend origin(s) (comma-separated for more than one), e.g. `CORS_ORIGINS=https://app.your-domain.com`. Left unset, the backend only allows the local Vite dev ports and will reject the deployed frontend's requests.
- **`VITE_API_URL`** (frontend) — the backend's public URL, set before running `npm run build` (see [Frontend](#frontend) above — it's compiled into the bundle, not read at runtime).

Also worth setting for a real deployment: `ACCESS_TOKEN_EXPIRE_MINUTES`, `MAX_UPLOAD_MB`, and `LOG_LEVEL` (see the env var table in [Setup](#setup)).

`POST /login` and `POST /register` are rate-limited to 10 requests/minute per IP (`slowapi`) to slow down credential-stuffing and account-creation abuse; a client over the limit gets `429` with `{"error": "Rate limit exceeded: ..."}`. The limiter's counters are in-memory and per-process, so they reset on restart and aren't shared across multiple worker processes or machines — fine for the single-worker setup above, but wouldn't rate-limit correctly if scaled to multiple workers without switching to a shared backing store (e.g. Redis).

Unexpected server errors (anything not raised deliberately as an `HTTPException`) are caught by a global handler: the client always gets a plain `{"detail": "Internal server error"}` with a `500`, never the exception text or a stack trace; the real exception and stack trace go to the log instead.

## Known limitations

- **No OCR.** A scanned PDF with no text layer (an image of a page, not extracted text) returns a `422` with a clear message rather than silently producing an empty or garbled document — OCR support (to actually read the scanned text) is not implemented yet.
- **`flan-t5-small` answer quality.** The local answer-generation model is intentionally small (so it runs on a CPU with no external API key), which means answers can be shallow, occasionally repetitive, or misphrase a nuance from the source text — it's meant to stay grounded in your uploaded documents rather than to reason deeply. For sharper answers, uploading more specific/well-structured source material tends to help more than rephrasing the question.
- **Single SQLite file, single worker.** Fine for individual or small-team use; not built for high-concurrency or multi-instance deployment (see the `--workers 1` note above).
- **English-oriented.** Both the embedding model and Flan-T5 are primarily English-trained; other languages will work less reliably for retrieval and generation.

## Notes

- The SQLite database (`backend/teacher_ai.db`) is created automatically on first run and is gitignored — it's local dev data, not meant to be shared.

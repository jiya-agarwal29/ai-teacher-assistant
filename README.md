# AI Teacher Assistant

A RAG-based teaching assistant. Upload your own course material (PDF, Word, PowerPoint) and get grounded AI chat answers, semantic search, and auto-generated quizzes — all sourced from what you actually uploaded, not the open internet.

Gemini (`GEMINI_API_KEY`, free tier) is the main AI provider for both answer generation and embeddings. Voyage AI is available as an optional alternative embedding provider, and everything can also run fully offline via local models (`flan-t5-small` for generation, `all-MiniLM-L6-v2` for embeddings) with no API key at all. See [AI providers](#ai-providers) below.

## Features

- **Document upload** — PDF / DOCX / PPTX / TXT / MD, or one or more photos/scans (JPG / PNG / WEBP / HEIC), parsed and chunked into a semantic index; processing runs entirely in the background with a live status (`processing` → `needs_review`/`ready`/`failed`, with a progress count and a one-click retry on failure) so an upload never blocks the request
- **OCR for scanned PDFs and handwritten photos** — a PDF page with no usable text layer, or any uploaded photo, is transcribed with Gemini vision; optionally held for review before it's indexed (see [Document processing](#document-processing))
- **OCR review page** — a page-by-page editor (image + zoom beside the editable transcription, `[illegible]` markers highlighted) to fix up OCR'd text, re-run OCR on a single page, and approve one page or a whole document before it's searchable (see [OCR review](#ocr-review))
- **AI Chat** — ask questions, get answers grounded in your uploaded material with cited sources; every conversation is saved to your account, not just your browser, so it follows you anywhere you log in (see [Analytics and history](#analytics-and-history))
- **Semantic search** — find relevant passages by meaning, not just keyword match
- **Quiz Builder** — generates MCQ / true-false / fill-in-the-blank / short & long answer / scenario / viva / interview questions from your material, with grading
- **Notes Summarizer** and **AI Tutor** — bullet-point summaries and free-form Q&A
- **Analytics and history** — real usage stats (active study time, quiz performance, question activity, document library breakdown) and your chat history, both saved server-side per account — computed from what you've actually done, not a placeholder, and not stuck in one browser's storage (see [Analytics and history](#analytics-and-history))
- **Bloom's taxonomy & Question Bank** — every quiz question is tagged with a Bloom's cognitive level, independently checked (not just self-assigned), and saved to a browsable, editable, regenerate-one-at-a-time Question Bank (see [Bloom's taxonomy](#blooms-taxonomy))
- **Question papers** — plan a full exam paper (a preset or custom section layout, a Bloom's-mix preset or a custom split) and see the blueprint *before* any AI call; generate every slot in the background, grounded per-document with live progress; edit, regenerate, or swap in a bank question for any slot afterward; export student / teacher / answer-key copies as Word documents; optionally start the layout from an old paper (see [Question papers](#question-papers))
- **Session-expiry warning** — a "your session is about to expire" popup with a live countdown and a one-click "Stay logged in", so a lesson-prep session isn't cut off mid-work without warning (see [Session expiry](#session-expiry))

## Tech stack

**Backend** — FastAPI, SQLAlchemy + SQLite (local dev default) or PostgreSQL (`psycopg` 3, e.g. Neon — see [Database migrations](#database-migrations)) with Alembic migrations, Gemini (`google-genai`) + Voyage (`voyageai`) + local (`sentence-transformers` / `transformers` Flan-T5) AI providers, `pdfplumber` / `python-docx` / `python-pptx`, OCR via Gemini vision + `pypdfium2` / `Pillow` / `pillow-heif`, JWT auth (`python-jose` + `passlib`), rate limiting (`slowapi`)

**Frontend** — React 19, React Router, Vite, Tailwind CSS v4

## Project structure

```
backend/
  main.py               FastAPI app & routes
  auth.py                JWT auth, password hashing
  jobs.py                 Background document-processing worker (ThreadPoolExecutor), incl. OCR orchestration
  ocr.py                   PDF-page rendering (pypdfium2) + image preparation (Pillow) for OCR
  models.py               SQLAlchemy models (User, Book, Page, DocumentPage, Question, Paper, PaperQuestion, ActivityEvent, QuizAttempt, QuizAnswer, Conversation, ChatMessage)
  bloom.py                 Bloom's taxonomy: the six levels, verbs, prompt rules, verb classifier (pure Python, no imports from the rest of the app)
  paper.py                 Exam paper patterns + the blueprint planner (pure Python, no imports from the rest of the app) — see Question papers
  paper_export.py          Builds the student/teacher/answer-key .docx exports (python-docx) — see Question papers
  database.py            SQLite/Postgres engine+session setup (DATABASE_URL)
  alembic/                 Alembic migrations -- see Database migrations
  document_parsers.py    PDF/DOCX/PPTX parsing + chunking
  embeddings.py           Swappable embeddings (Gemini / Voyage / local MiniLM)
  llm.py                   Swappable answer generation (Gemini / local Flan-T5) + Gemini-vision OCR (read_image)
  rag.py                   Local Flan-T5 generation, quiz generation, relevance checking
  scripts/reembed.py      Re-embeds stored pages after switching EMBEDDING_PROVIDER
  uploads/                Saved original files, one folder per <user_id>/<book_id> (gitignored)
frontend/
  src/pages/               Home, Chat, Documents, DocumentReview, AITools, QuestionBank, PaperList, PaperNew, PaperView, Analytics, Login
  src/components/          Sidebar, PageLayout, ProtectedRoute, DocumentStatusBadge, BloomBadge, PaperStatusBadge, BlueprintPanel, PaperPlanPreview, ImportedDataToast
  src/hooks/                Auth (incl. the one-time analytics/chat import, see Analytics and history), active-time tracking (a server heartbeat, not localStorage), draft persistence, useBloomLevels
  src/utils/bloomColors.js  The six levels' colours (cool → warm), shared by BloomBadge/the quiz builder/Analytics/the paper blueprint panel
  src/utils/analyticsImport.js, chatImport.js  The one-time legacy-localStorage imports — see Analytics and history
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

**Already have a `backend/venv` from before Phase 6?** Re-run `pip install -r requirements.txt` after every pull, not just the first time — it now includes `tzdata` (Windows ships no system IANA timezone database at all, which `zoneinfo`, used by [Analytics and history](#analytics-and-history), needs), and an existing venv won't pick up a newly-added dependency on its own.

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
| `MAX_UPLOAD_MB` | `100` | Maximum accepted size for the single-document `file` upload (phone scans of a multi-page PDF can run large). Per-image uploads (`files`) are separately capped at 15 MB each, regardless of this setting. |
| `LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`). |
| `DATABASE_URL` | an absolute `sqlite:///.../backend/teacher_ai.db` path next to `database.py` | SQLAlchemy database URL. Accepts a Postgres URL too (`postgres://` or `postgresql://`, as most hosts — Neon, Render, Heroku — hand out); both are normalized to the `postgresql+psycopg://` driver URL automatically. See [Database migrations](#database-migrations). |
| `ENV` | `development` | Set to `production` to skip the dev-only `create_all()` on startup — production schema changes go through Alembic migrations instead (see [Database migrations](#database-migrations)). Any other value keeps the local-dev convenience of creating missing tables automatically. |
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

Serves on `http://127.0.0.1:8000`. AI clients/models are prepared once at startup (FastAPI lifespan) rather than per-request — this fails fast with a clear error if a required API key is missing. Local models (used only when a provider is set to `local`) are downloaded on first use (~100MB total, one-time). Startup also logs the active `MAX_UPLOAD_MB`/`MAX_OCR_PAGES` limits, so a stale/misconfigured value is visible without having to go digging — a running server never picks up a changed `.env` value until it's restarted. Check `GET /health` for `{"status", "models_ready", "database_ready", "llm": {"provider", "model", "configured", "ready"}}`.

> **Windows + antivirus HTTPS scanning (e.g. Avast):** if the model download fails with `SSL: CERTIFICATE_VERIFY_FAILED`, your antivirus is intercepting HTTPS and Python doesn't trust its certificate. Fix: `pip install pip-system-certs` inside the venv.

### Frontend

```
cd frontend
npm install
npm run dev
```

Serves on `http://localhost:5173`. By default it talks to the backend at `http://127.0.0.1:8000`; to point it elsewhere, copy `frontend/.env.example` to `frontend/.env` and set `VITE_API_URL`. The same file also has `VITE_SESSION_WARNING_MINUTES` (default `5`) — see [Session expiry](#session-expiry).

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

**Review gate** — an OCR'd page's `review_status` is `"needs_review"` when `review_ocr=true`, otherwise `"auto_approved"`. If **any** page needs review, the whole book stops at `status="needs_review"` and nothing is chunked or embedded yet. Only once every page is approved does chunking (from `DocumentPage.extracted_text`, keeping each chunk's original page number) + embedding + saving `Page` rows proceed to `status="ready"` — see [OCR review](#ocr-review).

- `GET /books` now also returns each book's `status`, `error`, `source_type`, `pages_total`, `pages_done`, and `created_at`.
- `GET /books/{id}/status` — poll this while `status == "processing"` for live progress: `pages_done`/`pages_total` count OCR pages while `source_type` is still null, then switch to counting embedding chunks once extraction finishes.
- `POST /books/{id}/retry` — re-runs processing for a `failed` book from its saved original file(s) (same `review_ocr` choice as the original upload); `400` if the book isn't `failed` or the file(s) are missing. Re-extracts (and re-OCRs) everything from scratch rather than resuming partway through.
- `DELETE /books/{id}` also removes the book's `UPLOAD_DIR` folder (original file(s) + any rendered/saved OCR page images).
- Chat, search, semantic search, flashcards, and quiz generation only ever see pages from `ready` books (a `needs_review` book behaves like one with no documents yet, same as `processing`/`failed`).
- If the server restarts while a book is still `processing`, it's marked `failed` on the next startup (`"Processing was interrupted. Please re-upload."`) — jobs aren't resumed across a restart.
- `LLM_PROVIDER=local` has no vision model, so an OCR'd page on that provider fails the whole book with `"Reading scanned or handwritten pages needs the Gemini provider."`

The frontend (`Documents.jsx`/`Home.jsx`) shows the new book immediately with a status badge (now including `needs_review`, amber) and polls `GET /books/{id}/status` every 2 seconds until it reaches a stable state, then stops. Documents.jsx's upload also has a "Let me check the text before it's used" checkbox (the `review_ocr` choice), checked by default.

## OCR review

A `needs_review` book's amber badge (and a "Review" button in Documents.jsx) link to `/documents/{id}/review` — a page-by-page editor for the transcribed text before it's trusted enough to index:

- `GET /books/{id}/pages` — every page's `method`, `review_status`, `extracted_text`, and whether it has a saved image.
- `GET /books/{id}/pages/{n}/image` — the saved page image (the frontend fetches this with the JWT as a `fetch`-to-blob, since a plain `<img src>` can't carry an `Authorization` header); only ever serves a file actually inside that book's own `UPLOAD_DIR` folder.
- `PUT /books/{id}/pages/{n}` `{extracted_text}` — saves edited text (max 20,000 characters) without otherwise touching `review_status`, **unless** the page was already `approved`/`auto_approved`, in which case editing reopens it to `needs_review` (and reopens the book to `needs_review` too, if it was `ready`).
- `POST /books/{id}/pages/{n}/approve` and `POST /books/{id}/approve-all` — approve one page or every remaining `needs_review` page. Once none are left, this kicks off the background chunk+embed job (`status` → `processing` → `ready`), replacing any previous `Page` rows rather than appending to them — so re-approving after a post-`ready` edit correctly rebuilds the index from the edited text.
- `POST /books/{id}/pages/{n}/reread` — re-runs OCR against that page's already-saved image (no re-render/re-upload) in the background, setting `review_status="needs_review"` (and reopening the book the same way an edit does) immediately so the frontend can show that it's in flight. The frontend asks for confirmation first ("Re-reading will replace your edits...") if the page's text was hand-edited, since the fresh transcription overwrites it.

All five endpoints are owner-only (`404` otherwise) and only ever operate on files inside the book's own upload folder.

## Bloom's taxonomy

Every quiz question — AI-generated or the local hybrid fallback — is tagged with one of Bloom's six cognitive levels, defined once in `backend/bloom.py` (deliberately pure Python, no imports from the rest of the app) as the single source of truth for each level's label, description, prompt rules, and which quiz question types suit it:

| Level | Checks |
|---|---|
| **Remember** | Recall facts, terms, or basic ideas exactly as learned. |
| **Understand** | Explain ideas or concepts in your own words. |
| **Apply** | Use what was learned in a new situation. |
| **Analyze** | Break information into parts and see how they relate. |
| **Evaluate** | Make a judgement about something and justify it. |
| **Create** | Design, propose, or build something new using the ideas. |

**How a level is chosen, and checked** — a level is *chosen* when a question is written and only ever *checked* afterward; it's never derived from the question's type (`mcq`/`scenario`/etc. are a separate axis, the answer format). `GET /generate-quiz?topic=&levels=&count=` (`levels` a comma-separated subset, default all six; `count` 1–12, default 6) uses `bloom.distribute()` to spread `count` questions across the requested levels (lower levels get any remainder first) and asks Gemini for exactly that many per level in one call, each one self-labelled with the `bloom_level` it was written for. That self-label then goes through two independent checks before it's trusted:

1. **`bloom.classify_by_verbs()`** — a free, instant, keyword/phrase match (no API call) against each level's common verbs. Used only to log a disagreement at `DEBUG`, never to override anything — it's a sanity check, not a judge.
2. **A second Gemini call** classifies every question's level from its text alone — it is never told which level each one was *written* for, so it can't just agree by default.
3. A question the checker disagrees with is regenerated **once** for that specific slot and rechecked. Still wrong afterward, it's kept but marked `bloom_check="flagged"` with `detected_level` set to what the checker thinks it actually is instead.
4. Separately, if a level ends up short after validation (a disallowed type, a leaked answer, a duplicate, etc. — all silently dropped and logged in aggregate at `INFO`, e.g. `"dropped 1: type 'true_false' not allowed for 'analyze'"`, never with the question's own text), **one** extra call tops up just the missing slots. Still short after that, the response's top-level `warnings` array gets one plain-English line for it (`"Could not create enough Analyze questions -- try again or pick other levels"`) instead of silently shipping fewer questions than asked.
5. Every write (initial generation, the mismatch retry, and the top-up) also gets a reminder that every question must test a different idea/concept/scenario from the others, plus up to 15 of the user's own most recent saved question texts on that exact topic (never another user's, and never the answers) as "don't repeat these."

Every saved question ends up with a `bloom_check` of `"verified"` (the checker agreed), `"flagged"` (it disagreed, even after the retry), `"unchecked"` (the checker was unavailable, or the local hybrid fallback was used — it has no concept of Bloom levels at all, so its questions are labelled after the fact by the verb classifier alone, defaulting to `"remember"` on no match), or `"teacher_set"` (a teacher changed the level by hand).

**The Question Bank** (`/question-bank`, `src/pages/QuestionBank.jsx`) — every question ever generated is saved (the `Question` table) and browsable from here: a summary strip (total, flagged, a bar per level), level-chip (multi-select) and "flagged only" filters, a debounced topic search, 50-at-a-time pagination, and per-question **Edit** (inline, including the Bloom level — restricted to levels valid for that question's type — with backend validation errors shown inline), **Regenerate** (confirm first; one new question for the same topic/level, replacing it in place), and **Delete** (confirm first).

**Endpoints** (all owner-only, `404` for another user's question):

- `GET /bloom/levels` — the six levels (`key`, `label`, `order`, `description`, `question_types`); the frontend (`useBloomLevels` hook) fetches this once and never hard-codes a label or description.
- `GET /generate-quiz?topic=&levels=&count=` — the existing response shape plus, per question, `question_id`/`bloom_level`/`bloom_label`/`bloom_check`/`detected_level`/`source`, and top-level `levels`/`level_counts`/`warnings`.
- `GET /questions?level=&topic=&check=&limit=&offset=` — `{total, items}`, newest first. `level` accepts one key or a comma-separated list; `topic` is a case-insensitive contains match; `check` is one of `verified`/`flagged`/`unchecked`/`teacher_set`; `limit`/`offset` default `50`/`0`.
- `GET /questions/summary` — `{total, by_level: {key: n}, flagged}`, backing the Question Bank's summary strip.
- `PATCH /questions/{id}` `{question?, options?, correct_answer?, explanation?, bloom_level?}` — changing `bloom_level` sets `bloom_check="teacher_set"`; validated the same way generation is (e.g. an `mcq` needs exactly 4 distinct options).
- `POST /questions/{id}/regenerate` `{type?}` (10/minute per user) — writes one new question for the same topic and `bloom_level` (optionally a different type still valid for that level), reusing the exact same generation-and-check machinery as `/generate-quiz`. `503` if the AI is unavailable.
- `DELETE /questions/{id}`, and `DELETE /books/{id}` also deletes that book's questions.

## Question papers

A full exam paper — not a single quiz — built from a chosen section layout and Bloom's mix, planned deterministically first and only then filled in by AI slot by slot. `backend/paper.py` is the pure-Python pattern/blueprint model (no DB, no AI, no imports from the rest of the app); `backend/paper_export.py` turns a finished paper into a `.docx`; everything else lives alongside the quiz/Question Bank code in `main.py`.

**Patterns** — three presets (`GET /paper-patterns`) plus a custom layout:

| Preset | Sections |
|---|---|
| `mid_sem_50` | A: 5×2 marks (answer all) · B: 5×5 marks (attempt any 4 of 5) · C: 2×10-mark either/or pairs |
| `end_sem_100` | A: 10×2 marks · B: 8×5 marks (attempt any 6 of 8) · C: 5×10-mark either/or pairs |
| `mcq_30` | A: 30×1 mark, MCQ only |

A custom pattern is any list of sections, each with a `title`, `instructions`, `marks_each`, `num_questions`, and a `choice` — `"none"` (answer every question), `"any_n"` (attempt only `attempt` of `num_questions`), or `"either_or"` (each numbered question offers two alternative options worth the same marks, only one answered). `paper.validate_pattern()` checks the structure (positive counts/marks, a valid `attempt` for `any_n`, MCQ sections capped at 2 marks each) before anything is planned.

**Bloom's mix** — four presets (`balanced`, `basics` mostly-recall-heavy, `higher_order`, `mcq_friendly` which excludes Evaluate/Create since neither level's question types include `mcq`) or a custom `{level: percent}` map, validated to sum to 100 (`paper.validate_bloom_targets()`).

**The planner** (`paper.plan_paper()`) — runs entirely before any AI call, so the teacher sees the plan first. Each selected document is one "unit"; `POST /papers` accepts `unit_weighting`: `"equal"` (default — every document weighted the same regardless of size), `"by_size"` (weighted by chunk count), or `"custom"` (`unit_weights: {book_id: number}`). A slot's Bloom level is chosen from whichever levels a question of that many marks can actually test (1–2 marks: Remember/Understand/Apply; 3–6: Understand through Evaluate; 7+: Analyze/Evaluate/Create) — every targeted level that has *any* eligible slot gets one even if that overshoots its percentage target, then the rest are balanced to minimize the gap; a level with no eligible slot at all (e.g. Create on an MCQ paper) gets a plain-English warning instead of silently vanishing. Within each section, slots are ordered low-to-high Bloom level (an either/or pair's two options always move together); units are spread across each section proportionally to their weight (Hamilton's largest-remainder apportionment), not just round-robin, so a 3:2:1-weighted set of documents gets roughly a 3:2:1 split of each section's questions too. The response includes `blueprint_target` (marks per level the percentages imply) and `blueprint_planned` (marks per level/document the plan actually produced) alongside the plan itself — nothing is generated yet; the paper sits at `status="draft"` until `POST /papers/{id}/generate` is called.

**Background generation** (`POST /papers/{id}/generate`, 3/minute, `main.generate_paper` run from `jobs.py`) — fills every still-`None` slot, grouped by `(bloom_level, marks, document)` so a single Gemini call's source material is always from one document only (batching across documents let one real run answer a CN-labelled slot from the NS document's content instead — grounding only holds by construction when every source in a prompt is from the slot's own book). Each slot is grounded in its own non-overlapping window of 2–3 chunks (`_assign_source_windows`, spread evenly through the whole document so one paper covers the whole document and no chunk repeats), reusing the exact same Phase-4B pipeline `/generate-quiz` uses (generate → independent level check → near-duplicate drop → top-up) via shared helper functions taking new optional `marks_by_level`/`paper_kind` parameters — never a copy. A generated question is also lexically checked against its own window's content (`main._is_grounded`, ≥2 shared non-generic terms) and dropped if it isn't actually about that material; a slot still empty after the normal pass gets one more attempt with a wider (5-chunk) window before being left empty with a warning (the paper still finishes `"ready"`). A busy/rate-limited Gemini response doesn't fail the job outright: it's waited out (`Retry-After` if given, else 60s doubling to a 5-minute cap, bounded by `PAPER_JOB_MAX_WAIT_SECONDS`, default 1800) and the job resumes — `GET /papers/{id}/status` exposes `waiting_for_ai` so the UI can show it, and progress (`progress_done`/`progress_total`) updates after every slot. Create-level questions get an extra writer/checker rule against copying a ready-made plan straight out of the source (`bloom.NO_COPIED_PLAN_CONSTRAINT` / an extension of `main.CHECKER_DIRECT_ANSWER_RULE`) — a "design a hardening plan" question scores at most Understand if the source already lists that exact plan. Written papers never put MCQ/true-false on a 3+ mark slot (`paper.allowed_types_for_slot`) — those only ever fit 1–2 marks; MCQ papers are unaffected (already capped at 2 marks/section). Only a `LLMUnavailableError` after the whole wait budget is exhausted fails the paper (`"AI service is busy, click Generate to continue"`) — a server restart mid-generation does the same on the next startup (`"Generation was interrupted, click Generate to continue"`); either way, calling `/generate` again only ever fills slots still empty.

**Editing a generated paper** — per slot: `PATCH /papers/{id}/slots/{slot_id}` swaps in a bank question (same marks, an allowed level for that many marks, an allowed type — same-document candidates sorted first via `GET /papers/{id}/slots/{slot_id}/candidates`); `POST /papers/{id}/slots/{slot_id}/regenerate` (10/minute) writes a fresh one through the same generate-check-ground pipeline; `PATCH /questions/{id}` now also accepts `marking_scheme` edits, which must still sum to the question's own `marks`. Both regenerate and swap keep the paper's `warnings` in sync exactly like bulk generation does — a flagged result adds a `"classified as X, not the intended Y"` line, and fixing it (or swapping to a verified question) clears the old line rather than leaving it stale. `blueprint_actual` (marks per level/document from the questions actually saved) is always computed fresh from current state, never cached.

**Export** (`GET /papers/{id}/export?kind=student|teacher|answer_key`, owner-only, `409` if the paper isn't `ready`) — a clean `.docx` via `paper_export.py`: **student** (header, general instructions, each section's own instructions, numbered questions with either/or as "(a) ... OR (b) ...", MCQ options lettered A–D, marks right-aligned — no Bloom level, no document name, no answers); **teacher** (the same, plus a small grey tag under each question — level, document *without* its file extension — and a final page with marks-per-level target-vs-actual and marks-per-document tables, after a page break, with page numbers in the footer); **answer_key** (every question's correct answer — MCQ as letter + option text — and its marking scheme points). "Max Marks" on all three, and everywhere the frontend shows it, is `paper.total_marks()` — what a student actually answers (e.g. 50 for `mid_sem_50`'s "any 4 of 5" section), not `marks_on_paper()` (55, every question printed); the teacher copy's blueprint page shows the 55-mark figure too, explicitly labelled "marks on paper". An empty slot prints `"[Question missing]"` in the teacher/answer-key copies and blocks the student export (`409 "Fill all questions first"`) until it's filled. Duration is worded ("2 hours", "1 hour 30 minutes") rather than raw minutes.

**Importing an old paper's layout** (`POST /papers/import-pattern`, 5/minute, optional) — upload a document or a single photo/scan of an existing paper (same type/size rules as a document upload, the same Gemini-vision OCR fallback for a low-text page or a bare photo, run synchronously rather than as a background job) and Gemini reads back its section layout and every individual question. The layout is validated the same way (`paper.validate_pattern`) and handed to the "New paper" builder's custom pattern editor to start from — nothing is saved until the teacher actually creates a paper with it, and the uploaded file is deleted again once its text has been read, whichever way the request ends (never added to the user's documents or the search index). The old paper's own questions are also classified with the existing level checker in one batched call into an **estimated** marks-per-level `bloom_mix`, shown as a small bars card next to whichever new Bloom's mix the teacher picks.

**Endpoints** (all owner-only, `404` otherwise, matching the Question Bank's convention): `GET /paper-patterns`, `POST /papers`, `GET /papers`, `GET /papers/{id}`, `GET /papers/{id}/status`, `PATCH /papers/{id}` (`title`/`subject`/`duration_minutes`), `DELETE /papers/{id}` (keeps the underlying bank questions; `DELETE /books/{id}` empties that book's slots instead of deleting the paper), `POST /papers/{id}/generate`, `PATCH /papers/{id}/slots/{slot_id}`, `POST /papers/{id}/slots/{slot_id}/regenerate`, `GET /papers/{id}/slots/{slot_id}/candidates`, `GET /papers/{id}/export`, `POST /papers/import-pattern`.

**The frontend** (`/papers`, `/papers/new`, `/papers/:id`) — a list with a live-polling status badge; a one-page builder (title/subject → pattern cards or a custom section editor, with "Use an old paper's layout" → documents checklist → Bloom's mix chips or a custom split → a plan preview with the target-vs-planned blueprint before generating); and the paper view, which renders `draft`/`generating`/`failed` appropriately and, once `ready`, lays the paper out like a printed exam with a teacher/student toggle (teacher view adds the level/document/check tags, marking scheme, and Edit/Regenerate/Swap per question), a target-vs-actual blueprint side panel, a print stylesheet, a "Download" menu for the three export kinds, and a "Fix all problems" button that regenerates every empty or flagged slot one by one.

## Analytics and history

Usage stats and chat history both moved from browser-only `localStorage` (easy to lose, impossible to fake-proof, and stuck on one device) to a server-side store, per user — logging in from any browser shows the same numbers and the same conversations.

**What's recorded, and where.** `ActivityEvent` rows (`backend/models.py`) are written only by the server itself, right after the real work of a request already succeeded — never accepted from a client request body, so a user can't inflate their own stats:
- `"query"` — a `/chat` or `/tools/tutor` answer (including the "not enough information" case, since a real retrieval attempt happened; *not* the "no documents uploaded"/"needs re-indexing" bounce-backs, since no attempt was made at all).
- `"tool"` — `/tools/summarize`, `/tools/flashcards`, or `/semantic-search`, with `meta={"tool": "..."}`.
- `"quiz_generated"` — a successful `/generate-quiz` call.
- `"paper_generated"` — a paper's background generation job reaching `"ready"` (not `"failed"`).
- `"active_seconds"` — a heartbeat (below) or an imported legacy day-bucket; `value` is the seconds, not 1.

A logging failure never breaks the request it's attached to (`main._log_activity` catches and warns instead of raising). `QuizAttempt`/`QuizAnswer` hold graded quiz results (`POST /analytics/quiz-attempts`, called once per quiz submitted — the server computes `correct`/`total` from the per-question answers itself, and ignores a `question_id` that doesn't belong to the caller rather than 404ing the whole attempt). `Conversation`/`ChatMessage` hold chat history (see below).

**Active study time** is a heartbeat, not a click-counter: `hooks/useActiveTime.js`'s tracker only counts time while the tab is visible *and* the user has interacted (mouse/keyboard/touch/scroll) within the last 60 seconds — an idle tab left open, or a backgrounded one, accrues nothing. Seconds accumulate client-side and are sent as one `POST /analytics/active-time` call every 60 active seconds, plus a final flush of whatever's still pending when the page is hidden or closed (`visibilitychange`/`pagehide`); a dropped heartbeat (offline, the 4/minute server-side limit) is just discarded, never retried in a loop.

**`GET /analytics/summary?days=7|30|90&tz=<IANA timezone>`** (`tz` defaults to the browser's own `Intl.DateTimeFormat().resolvedOptions().timeZone`; an unrecognized zone name is `422`, not silently treated as UTC) returns, all computed server-side:
- `totals` — `queries`, `tools_used`, `quizzes_generated`, `quizzes_taken`, `average_score_pct` (mean of each attempt's own percentage, not a totals-weighted average), `active_seconds` — all within the `days` window; `documents_ready`/`questions_in_bank`/`papers_ready` are current snapshots, not windowed.
- `queries_by_day` / `active_seconds_by_day` — every day in the window present, oldest first, zeros included, bucketed by the *user's* local calendar day (an event at 20:00 UTC lands on the *next* day in `Asia/Kolkata`) — SQLite returns `created_at` as naive UTC, so `main._as_utc`/`_local_date_key` reattach the UTC tzinfo before converting.
- `accuracy_by_level` / `topics` / `weak_topics` (≥2 attempts, <60% average, lowest first, max 5) / `recent_attempts` (last 10, not windowed) — all from `QuizAnswer`/`QuizAttempt`.
- `paper_coverage` — marks per Bloom level across every *ready* paper (not windowed — a paper's coverage is structural, not a recent-activity metric), counting a flagged question at its **detected** level rather than its intended one, the same "show the real mix" rule the paper blueprint itself uses (see [Question papers](#question-papers)).

**Chat history.** `GET /conversations` (newest-first, `id`/`title`/`updated_at`/`message_count`), `POST /conversations` (`title?`, defaults `"New chat"`), `GET/PATCH/DELETE /conversations/{id}` — all owner-only, `404` otherwise, deleting one cascades its messages. The frontend creates a conversation lazily, on the first message actually sent while none is selected, never on a page visit — so "New Chat" never litters the account with empty ones. `POST /chat {question, conversation_id?}` (prefer this one — `GET /chat?question=...` is kept only for backward compatibility and is otherwise identical, but puts the question in the URL, where server access logs/browser history/any intermediate proxy can see it) saves the question and answer (with sources) into `conversation_id` when one is given and owned, auto-titling a still-`"New chat"` conversation from that first question (~40 characters) and bumping `updated_at` — the exact same success/failure boundary as `ActivityEvent` logging, so a `503` or the "no documents" bounce-back saves nothing, and omitting `conversation_id` behaves exactly as before conversations existed.

**The one-time import.** A teacher who used the app before this phase has real history sitting in `localStorage` alone. Once per account (not per browser), logging in checks `GET /analytics/import-status` and `GET /conversations/import-status`; if either is `false` *and this specific browser* still has the corresponding old key (`queries_by_day`/`active_time_by_day`/`quiz_history` for analytics, `chat_conversations` for chat), it's uploaded (`POST /analytics/import` / `POST /conversations/import`) and the local key deleted — `utils/analyticsImport.js`/`utils/chatImport.js`, both fired from `AuthProvider.login()`, fire-and-forget. A browser that was never used before this phase makes no API call at all for that import: calling it with nothing to import would still mark the *account* as imported, permanently blocking a **different** browser that actually has real history from ever getting its turn. A day-bucketed count (no real time-of-day) is stored as one event at local noon on that day, so it always buckets back onto the same local day; a `409` (another browser already imported this account) is treated the same as success — the now-redundant local copy is deleted either way. Either outcome shows a small one-time "moved to your account" toast (`components/ImportedDataToast.jsx`); a real failure is logged to the console and simply retried on the next login, with the local data left in place.

**Clearing data.** Analytics has its own button (`DELETE /analytics`, with a confirmation dialog) that wipes `ActivityEvent`/`QuizAttempt`/`QuizAnswer` for the caller — documents, the Question Bank, and papers are untouched, and `analytics_imported_at` is deliberately *not* reset, so the same old browser data can't be re-imported and double-counted after a clear. Chat conversations are deleted individually (the trash icon per conversation) — there's no bulk "clear all chats" button.

**Endpoints:** `POST /analytics/active-time`, `POST /analytics/quiz-attempts`, `GET /analytics/summary`, `GET /analytics/import-status`, `POST /analytics/import`, `DELETE /analytics`, `GET /conversations`, `POST /conversations`, `GET /conversations/{id}`, `PATCH /conversations/{id}`, `DELETE /conversations/{id}`, `GET /conversations/import-status`, `POST /conversations/import`, `POST /chat` (preferred) and `GET /chat` (deprecated, kept for backward compatibility).

## Session expiry

`POST /refresh-token` (requires a still-valid token; an expired or invalid one gets the usual `401`) exchanges it for a new one with a fresh `ACCESS_TOKEN_EXPIRE_MINUTES` expiry, same response shape as `/login`. Rate-limited to 10/minute per user, same as the other authenticated routes.

The frontend (`hooks/AuthProvider.jsx`) decodes the token's own `exp` claim to schedule two client-side timers — no polling — rescheduled every time the token changes (login, a page load picking up a stored token, or a refresh):

- A warning popup (`components/SessionExpiryModal.jsx`, `VITE_SESSION_WARNING_MINUTES` before expiry, default `5`) with a live countdown, offering **"Stay logged in"** (calls `/refresh-token`) or **"Log out"**. Focus-trapped and accessible (Esc acts as "Stay logged in").
- A hard logout exactly at expiry if the warning is ignored.

Either path redirects to `/login` with "Your session has expired. Please log in again." and, after re-authenticating, returns the user to the page they were on (`ProtectedRoute`'s saved `location.state.from`). An unsent Chat question or an unsaved edit on the OCR review page is saved to a per-user `localStorage` slot right before any logout (manual, the expiry timer, or a 401 from any request) *and* right before a page refresh/close (`beforeunload`/`pagehide`), then restored once on the next visit (`hooks/useDraftPersistence.js`). On the review page specifically, the restored edit's page number also wins over whatever `?page=` is in the URL, and a plain refresh/close shows the browser's own "leave page?" prompt whenever there's an unsaved edit (never otherwise).

Leaving the review page for another page in the app — the sidebar's nav links or its own Back button — asks the same "discard unsaved changes?" question first if there's an edit in progress; the current page is also kept in the URL as `?page=N` (history is *replaced*, not pushed, so Previous/Next don't pile up back-button entries).

## Database migrations

**Local dev (SQLite, default):** nothing to do — `main.py`'s startup lifespan calls `create_all()` automatically whenever `ENV` isn't `production` (the default), so a fresh `backend/teacher_ai.db` gets every table the first time you run the server. Schema changes to an *existing* local SQLite database still apply automatically the same way, as long as they're only new tables/columns `create_all()` can add non-destructively; anything structural goes through Alembic like everywhere else (below).

**Pointing at Postgres (e.g. Neon):** set `DATABASE_URL` in `backend/.env` to the connection string your host gives you (`postgres://...` or `postgresql://...` both work — see the env var table in [Setup](#setup)), and `psycopg[binary]`/`alembic` (already in `requirements.txt`) will be there. Then run the migrations once against it:

```
cd backend
alembic upgrade head
```

**Production:** set `ENV=production` so the startup lifespan's `create_all()` is skipped entirely — a production database's schema should only ever change via a migration applied as its own deploy step, never implicitly on app startup:

```
ENV=production DATABASE_URL=postgresql://... alembic upgrade head   # one-time, per deploy with new migrations
ENV=production DATABASE_URL=postgresql://... uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

**Making a schema change going forward:** edit `models.py`, then generate and apply a migration for it:

```
cd backend
alembic revision --autogenerate -m "describe the change"
# review the generated file in alembic/versions/ -- autogenerate is a first draft, not ground truth
alembic upgrade head
```

**A local SQLite database from before Alembic existed** (anything created by `main.py`'s own `create_all()` before this section existed) already has every table Alembic's baseline migration creates — running the baseline against it would try to `CREATE TABLE` something that's already there. Tell Alembic it's already up to date instead of trying to recreate anything:

```
cd backend
python migrate.py <username>   # if you haven't already -- see below; brings old columns current first
alembic stamp head
```

From here on, every schema change is a new Alembic revision, not a hand-edited column.

### Migrating a database from before `Book.user_id`/`Page.embedding_model`

`migrate.py` is **deprecated** (superseded by Alembic above) and kept only for a local SQLite database old enough to need this one-time backfill. Books are scoped to the account that uploaded them (`Book.user_id`), and pages record which embedding model produced their vector (`Page.embedding_model`). A `teacher_ai.db` created before either of these existed needs both backfilled:

```
cd backend
python migrate.py <username>
```

This adds the `books.user_id` column if it's missing and assigns every currently-unowned book to `<username>` (which must already be a registered account), adds `pages.embedding_model` if missing, backfilling it to `"local:all-MiniLM-L6-v2"` on existing rows (all pages embedded before this column existed used local MiniLM), adds the Phase 3A processing columns (`books.status`/`error`/`source_type`/`pages_total`/`pages_done`/`created_at`) if missing, backfilling `status="ready"` on existing rows (they were fully processed synchronously before the background pipeline existed), adds the Phase 3B OCR columns (`books.review_ocr`, and the `document_pages` table) if missing, and creates the `questions` table (see [Bloom's taxonomy](#blooms-taxonomy)) if missing. Safe to run more than once — every column/table is only added once, and every backfill only ever touches rows that still need it. Do not add anything new to this script — a new schema change is a new Alembic revision instead.

## Running tests

```
cd backend
pip install -r requirements-dev.txt   # installs requirements.txt + pytest
python -m pytest tests/ -v
```

The whole suite runs with **no internet connection and no real `backend/.env`** — `tests/conftest.py` sets a dummy `SECRET_KEY`/`GEMINI_API_KEY` (so the app's startup lifespan succeeds without a real key or a config file), sets `JOBS_SYNC=true` (so `jobs.py`'s background document processing runs inline instead of racing a real worker thread), and replaces `embeddings.embed_documents()`/`embed_query()` with deterministic fake vectors everywhere except `tests/test_embeddings.py` itself and the handful of tests that deliberately exercise the real embeddings pipeline against a mocked low-level client (marked `@pytest.mark.real_embeddings_path`) to test rate-limit handling; `tests/test_llm.py` and the LLM-dependent `tests/test_api.py` cases mock `llm.generate()`/`generate_json()` directly. No test makes a real API call.

`tests/test_quiz.py` exercises quiz fairness rules (no scenario-question answer leaks, True/False isn't always the same value, no repeated concepts) directly against `rag.py` — no server or database needed. `tests/test_api.py` drives the real FastAPI app through `TestClient` against an isolated on-disk SQLite database and upload folder (`tests/test_api.db` / `tests/test_uploads/`, created and deleted automatically — neither ever touches the real `teacher_ai.db` or `uploads/`): register, login, the generic bad-login message, uploading a small `.txt`, listing books, per-user isolation (a second account can neither see nor delete another account's book), delete, the Gemini-backed chat/summarize/flashcards/quiz routes and their 503/429 edge cases (all against mocked LLM/embedding clients), and the background-processing flow — a background failure (no extractable text, or an embedding service that stays rate-limited) leaves the book `status="failed"` with a friendly error instead of erroring the upload itself, `/books/{id}/status` and `/books/{id}/retry` 404 for a non-owner, retry re-processes a failed book back to `ready` from its saved file, delete removes the book's upload folder, and a book stuck `processing` from an interrupted run is marked `failed` on the next startup.

**Running the exact same suite against Postgres:** set `TEST_DATABASE_URL` (separately from `DATABASE_URL`, which the app itself uses) in `backend/.env` to a real Postgres connection string — `tests/test_api.py` picks it up automatically, drops/recreates the schema fresh at the start of the run instead of deleting a SQLite file, and everything else behaves identically. Leave it unset to keep using the default on-disk SQLite file. `tests/test_alembic_migrations.py` separately guards against drift between `models.py` and the Alembic migrations themselves: it runs `alembic upgrade head` against a brand-new, empty SQLite database and asserts the result has exactly the same tables/columns `Base.metadata.create_all()` would build directly from the current models.

The OCR tests in the same file mock `llm.read_image()` directly (never a real Gemini vision call) and, for the PDF per-page decision, `document_parsers.parse_pdf()` and `ocr.render_pdf_page_to_png()` -- so a mixed scanned/typed PDF doesn't need a real multi-page PDF fixture, just canned per-page text. They cover: a low-text PDF page gets OCR'd while a text-rich one doesn't (and `source_type` comes out `"mixed"`); several images become one book with pages in submission order; `.heic` is accepted; `review_ocr=true` stops at `status="needs_review"` with no `Page` rows yet, `review_ocr=false` goes straight to `ready`; an `[illegible]` marker survives into the indexed text; `LLM_PROVIDER=local` fails OCR with the documented friendly message; and `MAX_OCR_PAGES` is enforced at upload time.

The OCR review tests cover: every `/books/{id}/pages...` endpoint 404s for a non-owner; a corrupted/malicious `image_path` outside the book's own upload folder is refused rather than served; editing a page's text then approving it ends `status="ready"` with the *edited* text in the indexed `Page` rows (not the original OCR output); `approve-all` clears every remaining `needs_review` page in one call; re-reading a page re-runs OCR and reopens an already-`ready` book to `needs_review`; and editing a `ready` book's page reopens and (once re-approved) re-indexes only that book, leaving an unrelated book's own `Page` rows completely untouched.

`tests/test_paper.py` exercises `paper.py`'s pattern validation and `plan_paper()` directly — no server, database, or AI call needed, same as `test_quiz.py`: preset/custom pattern shapes, Bloom-target validation, which levels/types a given mark value allows, every targeted level with an eligible slot getting covered even at the cost of overshooting, a level with no eligible slot anywhere producing a warning instead of silently vanishing, low-to-high level ordering within a section, and proportional (not flat round-robin) unit spread across a section. `tests/test_paper_export.py` does the same for `paper_export.py`'s `build_docx()` against a hand-built mock paper dict, read back with `python-docx`: all three kinds build without error; the student copy leaks no level name, document name, marking scheme, or free-text answer; MCQ options and either/or pairs format correctly; an empty slot prints `"[Question missing]"`; the answer key has every question's scheme; `total_marks` (not `marks_on_paper`) drives "Max Marks" for an `any_n` section; duration is worded; document names drop their extension. The paper sections of `tests/test_api.py` (all with `llm.generate_json`/`_classify_questions_with_gemini` mocked, `JOBS_SYNC=true` running the background job inline) cover the full `/papers...` surface: planning with no AI call at all, invalid pattern/Bloom-target/owner rejections, generation filling every slot with a matching `blueprint_actual`, grounding actually preventing one call's sources from spanning two different documents, the widened-window retry filling a slot the normal pass couldn't, a busy AI pausing (and a test spying on `waiting_for_ai` mid-wait) rather than failing the job, a server-restart-interrupted paper failing cleanly on the next startup, swap/regenerate/candidates enforcing their rules and owner-only, both syncing the paper's warnings the same way bulk generation does, deleting a paper keeping its bank questions while deleting a book empties that book's slots, every export kind's content-type/filename/409 rules, and `POST /papers/import-pattern` returning a pattern + estimated `bloom_mix` for a valid old paper, a friendly `422` for one the model couldn't make sense of, and confirming the uploaded file is never added as a document and is deleted again afterward.

The analytics and chat-history sections of `tests/test_api.py` (see [Analytics and history](#analytics-and-history)) cover: each tool route logging exactly one event on success and none on failure (a mocked `LLMUnavailableError`), a logging exception inside `_log_activity` itself never propagating; the active-time heartbeat's 1–120s bound and 4/minute limit; a quiz attempt's `correct`/`total` always computed server-side (never trusted from the client) and a foreign `question_id` ignored rather than 404ing the whole attempt; `GET /analytics/summary`'s day buckets being correct across a real timezone boundary (an event at 20:00 UTC landing on the next day in `Asia/Kolkata`), every day present with zeros for a brand-new user, `accuracy_by_level`/`weak_topics` math (including that a topic's average is the *mean of each attempt's own percentage*, not a totals-weighted figure), and `paper_coverage` counting a flagged question at its detected level; the one-time import succeeding once then `409`ing, and rejecting an oversized/future-dated/out-of-bounds payload without importing anything; and `DELETE /analytics` clearing only that user's analytics while leaving documents/papers/another user's data untouched. The conversation tests cover: a `POST /chat` (and the deprecated `GET /chat`) with `conversation_id` saving the real question+answer round trip and auto-titling a still-`"New chat"` conversation from the first question only (a second message never re-titles it), that the question genuinely never ends up in the POST request's URL, owner isolation (`404` for another user on every verb), delete cascading its messages, a foreign `conversation_id` still answering normally without saving into someone else's chat, an LLM error saving nothing, and `POST /conversations/import` enforcing its size/role/content-length limits and its own once-then-`409` guard.

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

`POST /login` and `POST /register` are rate-limited to 10 requests/minute per IP (`slowapi`) to slow down credential-stuffing and account-creation abuse; a client over the limit gets `429` with `{"error": "Rate limit exceeded: ..."}`. The AI routes are separately rate-limited **per logged-in user** (not per IP, so one user can't exhaust another's budget): `/chat` and `/tools/tutor` at 20/minute, `/tools/summarize` and `/tools/flashcards` at 10/minute, `/generate-quiz` at 5/minute, `/refresh-token` at 10/minute — a client over one of those limits gets `429` with `{"detail": "Too many requests. Please wait a minute."}`. The limiter's counters are in-memory and per-process, so they reset on restart and aren't shared across multiple worker processes or machines — fine for the single-worker setup above, but wouldn't rate-limit correctly if scaled to multiple workers without switching to a shared backing store (e.g. Redis).

See [Session expiry](#session-expiry) for `/refresh-token`'s own rate limit and the client-side warning/logout timers.

Unexpected server errors (anything not raised deliberately as an `HTTPException`) are caught by a global handler: the client always gets a plain `{"detail": "Internal server error"}` with a `500`, never the exception text or a stack trace; the real exception and stack trace go to the log instead.

## Known limitations

- **Background jobs don't resume after a server restart.** A book still `processing` (initial upload/retry, or re-indexing after approval) when the server stops is marked `failed` on the next startup ("Processing was interrupted. Please re-upload.") rather than picked back up — there's no persistent job queue behind `jobs.py`'s in-memory `ThreadPoolExecutor`.
- **OCR quality depends on photo quality.** Gemini vision transcribes handwriting well overall, but a blurry photo, heavy glare, extreme skew, or very small/cramped handwriting produces more `[illegible]` markers or outright wrong text — review the transcription (see [OCR review](#ocr-review)) rather than trusting it blindly for a photo taken in poor conditions.
- **Free-tier rate limits make large scans slow.** Each OCR'd page is its own Gemini vision call, and every chunk still needs its own embedding call afterward — a large multi-page scan on the free tier can take several minutes if it runs into the per-minute quota partway through (it paces itself and keeps going rather than failing, see [AI providers](#ai-providers) and `EMBED_JOB_MAX_WAIT_SECONDS`), and a burst of chat/quiz/summarize/flashcard requests can separately hit the interactive-path limits and return a `503` — just retry shortly after. Voyage's free tier (no payment method on the account) is similarly capped, at ~10K tokens/minute.
- **OCR needs the Gemini provider.** `LLM_PROVIDER=local` has no vision model, so a scanned PDF page or any photo upload fails the whole document with a clear error instead of silently producing an empty or garbled page.
- **Retry re-extracts (and re-OCRs) everything.** `/books/{id}/retry` (for a `failed` book) doesn't resume partway through or reuse a previous attempt's already-transcribed pages, so retrying a document that failed late (e.g. during embedding, after OCR already succeeded) re-runs OCR on every low-text/image page again. The review endpoints (edit/approve/re-read) are unaffected — they never discard an already-successful OCR pass.
- **`flan-t5-small` answer quality (LLM_PROVIDER=local only).** The default `LLM_PROVIDER=gemini` doesn't have this limitation. The local answer-generation model is intentionally small (so it runs on a CPU with no external API key), which means answers can be shallow, occasionally repetitive, or misphrase a nuance from the source text. For sharper answers on `local`, uploading more specific/well-structured source material tends to help more than rephrasing the question.
- **Single worker, regardless of database.** Postgres (see [Database migrations](#database-migrations)) removes the single-SQLite-file constraint for a real deployment, but the backend is still one `uvicorn` worker (the AI models are loaded into that worker's own memory at startup — see the `--workers 1` note above) — fine for individual or small-team use, not built for high-concurrency.
- **English-oriented.** The embedding and generation models (Gemini, Voyage, and the local fallbacks) are primarily English-trained; other languages will work less reliably for retrieval and generation.
- **Bloom's level checking is AI-based and can be wrong — for quizzes and papers alike.** The question writer and the independent checker are both the same kind of model making a judgment call — they can agree with each other on a label a human teacher would dispute (e.g. a question that mostly restates an already-stated fact can pass as "Analyze" when it's really closer to "Understand"), or disagree with each other on a question that's genuinely borderline. A `bloom_check="verified"` means two automated passes agreed, not that the level is pedagogically correct, and a `"flagged"` one isn't necessarily *wrong* either — spot-check real output (a paper's teacher copy shows every level/check tag for exactly this reason); don't take either as ground truth.
- **Each quiz costs several Gemini calls, not one.** Writing N levels in one quiz costs at least one call to generate it and one to check it, plus up to two more if any level needs the mismatch-retry or the missing-level top-up (see [Bloom's taxonomy](#blooms-taxonomy)) — on a free-tier quota, a handful of quizzes in quick succession can exhaust the per-minute budget and return a `503`, on top of the OCR/embedding calls already noted above.
- **A full exam paper costs a lot more calls, and free-tier quotas make it visibly slow.** A 14-slot `mid_sem_50` paper is 8–10 generation groups (each with its own generate + check-level + possible retry/top-up calls) plus the old-paper-import classifier call if used — on the free tier this routinely runs into the per-minute quota partway through and pauses (`waiting_for_ai`, see [Question papers](#question-papers)) before resuming on its own, so a paper that would take seconds against an unmetered API can take a few minutes in practice. This is expected, not a bug — just check back on `/papers/{id}` (or let the page's own polling do it) rather than assuming a stalled progress count means something broke.
- **Each selected document is one planning "unit", regardless of how much of it ends up used.** The Bloom/document blueprint balances marks across documents (equally by default, or by chunk count / a custom weight — see [Question papers](#question-papers)), not across topics within a document — a 200-page textbook and a 2-page handout selected together are each still just "one unit" to the planner, so the shorter one can end up asked about more intensively, chunk-for-chunk, than the longer one.
- **Study time only counts active, visible-tab time.** The heartbeat (see [Analytics and history](#analytics-and-history)) needs a real mouse/keyboard/touch/scroll event within the last 60 seconds *and* the tab actually visible — reading a long answer without touching anything for over a minute, or leaving the tab open in the background while working elsewhere, stops counting exactly as intended; it's a floor on genuine engagement, not a stopwatch you can leave running.
- **Analytics are per user, not per class yet.** Every number — study time, quiz scores, Bloom's-level accuracy, chat history — is scoped to the individual account that generated it; there's no teacher-facing rollup across a whole class/section's accounts yet, so comparing students today means looking at each of their accounts in turn.

## Notes

- The SQLite database (`backend/teacher_ai.db`) is created automatically on first run and is gitignored — it's local dev data, not meant to be shared.

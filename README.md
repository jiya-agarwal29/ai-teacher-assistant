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

**Backend** — FastAPI, SQLAlchemy + SQLite, `sentence-transformers`, `transformers` (Flan-T5), `pdfplumber` / `python-docx` / `python-pptx`, JWT auth (`python-jose` + `passlib`)

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

Create `backend/.env`:

```
SECRET_KEY=change-me-to-a-random-string
HUGGINGFACE_API_TOKEN=optional-hf-token
```

`SECRET_KEY` signs JWTs — set it to any random string. `HUGGINGFACE_API_TOKEN` is optional; it only raises Hugging Face Hub rate limits for the one-time model download, it isn't required for the app to run.

Run it:

```
uvicorn main:app --reload
```

Serves on `http://127.0.0.1:8000`. First run downloads the two AI models (~100MB total, one-time).

> **Windows + antivirus HTTPS scanning (e.g. Avast):** if the model download fails with `SSL: CERTIFICATE_VERIFY_FAILED`, your antivirus is intercepting HTTPS and Python doesn't trust its certificate. Fix: `pip install pip-system-certs` inside the venv.

### Frontend

```
cd frontend
npm install
npm run dev
```

Serves on `http://localhost:5173`.

## Notes

- The SQLite database (`backend/teacher_ai.db`) is created automatically on first run and is gitignored — it's local dev data, not meant to be shared.
- Documents are **not** scoped per user — all uploaded books are visible to every account. Fine for solo/local use; would need a `user_id` column on `Book` before this is safe for multiple untrusted users.

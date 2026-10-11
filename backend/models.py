from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Index, Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from database import Base

# Lifecycle of Book.status: a new upload starts "processing" (background
# job running). If any page needed OCR and review_ocr was set, it stops at
# "needs_review" (DocumentPage rows exist, but nothing is chunked/embedded
# yet -- no review/approve endpoint exists yet, a later phase adds one).
# Otherwise it proceeds straight to "ready" (searchable) or "failed" (error
# set, original file(s) kept on disk for /books/{id}/retry).
class Book(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)

    status = Column(String, default="processing", nullable=False)
    error = Column(Text, nullable=True)
    source_type = Column(String, nullable=True)
    pages_total = Column(Integer, default=0, nullable=False)
    pages_done = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Whether OCR'd pages need manual review before being indexed (the
    # upload-time checkbox choice) -- stored so /books/{id}/retry can
    # re-run processing with the same choice instead of re-asking for it.
    review_ocr = Column(Boolean, default=True, nullable=False)

    pages = relationship("Page", cascade="all, delete-orphan")
    document_pages = relationship("DocumentPage", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"

    id = Column(Integer, primary_key=True, index=True)

    book_id = Column(
        Integer,
        ForeignKey("books.id"),
        index=True
    )

    page_number = Column(Integer)

    chunk_number = Column(Integer)

    content = Column(Text)

    embedding = Column(Text)

    # Which provider+model produced `embedding` (e.g. "voyage:voyage-4" or
    # "local:all-MiniLM-L6-v2") -- vectors from different models have
    # different dimensions and can't be compared, so retrieval only scores
    # pages whose embedding_model matches the currently active one.
    embedding_model = Column(String, nullable=True)


class DocumentPage(Base):
    """
    One page of a book's source material, however it was produced: real
    extracted text (method="text") or a Gemini-vision transcription of a
    rendered/photographed page image (method="ocr"). This is the canonical
    per-page record jobs.py chunks from -- Page rows (the searchable index)
    are only ever built from here, once every page's review_status is
    "auto_approved"/"approved" (see Book.status="needs_review").
    """
    __tablename__ = "document_pages"

    id = Column(Integer, primary_key=True, index=True)
    book_id = Column(Integer, ForeignKey("books.id"), index=True, nullable=False)
    page_number = Column(Integer, nullable=False)

    # Path to the saved page image on disk -- only set for method="ocr"
    # (a rendered PDF page or an uploaded photo); null for method="text".
    image_path = Column(String, nullable=True)

    extracted_text = Column(Text, nullable=True)

    # "text" (pdfplumber/docx/pptx/txt extraction) or "ocr" (Gemini vision).
    method = Column(String, nullable=False)

    # "auto_approved" (used as-is), "needs_review" (OCR'd, awaiting a human
    # check -- no review/approve endpoint exists yet), or "approved" (will
    # be set by that future endpoint).
    review_status = Column(String, nullable=False)

    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True)
    password = Column(String)

    # Phase 6A: set the one time this user's old browser-only (localStorage)
    # analytics history was imported via POST /analytics/import -- null
    # means it never has been, and that endpoint is still available; a
    # second import attempt 409s instead of double-counting old history.
    analytics_imported_at = Column(DateTime, nullable=True)


class Question(Base):
    """
    A single quiz question generated for a user (Phase 4B), tagged with the
    Bloom's taxonomy level it was written for. book_id/page_id point at the
    source chunk it was grounded in when known (the AI quiz path); both are
    null for a question from the local hybrid fallback generator, which
    doesn't track a single source chunk.

    bloom_check records how that level was arrived at:
      - "verified": an independent classifier agreed with the intended level.
      - "flagged": the classifier disagreed, even after one regeneration
        attempt -- detected_level holds what the classifier thought instead.
      - "unchecked": no classifier check ran (local hybrid fallback, or the
        checker was unavailable).
      - "teacher_set": a teacher edited bloom_level directly via PATCH.

    Deleted explicitly by DELETE /books/{id} (not a cascade relationship)
    since a book's questions are a separate concern from its pages/chunks.
    """
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    book_id = Column(Integer, ForeignKey("books.id"), index=True, nullable=True)
    page_id = Column(Integer, ForeignKey("pages.id"), nullable=True)

    topic = Column(String, nullable=False)
    type = Column(String, nullable=False)

    bloom_level = Column(String, nullable=False)
    bloom_check = Column(String, default="unchecked", nullable=False)
    detected_level = Column(String, nullable=True)

    question = Column(Text, nullable=False)
    options = Column(Text, nullable=True)
    correct_answer = Column(Text, nullable=False)
    explanation = Column(Text, nullable=False)

    # Phase 5B: set when this question was written for an exam paper slot
    # (null for a plain AI Tools quiz question). marking_scheme is a JSON
    # list of {point, marks}, whose marks always sum to exactly `marks`.
    marks = Column(Integer, nullable=True)
    marking_scheme = Column(Text, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class Paper(Base):
    """
    An exam paper (Phase 5B): a chosen Section/marks pattern plus Bloom
    targets, planned once (paper.plan_paper) against the selected books
    (each book is one "unit", weighted by its chunk count) before any AI
    call -- `plan` is that plan, saved as-is so the teacher can see and
    adjust it before generating actual question text.

    status: "draft" (planned, nothing generated yet) -> "generating" ->
    "ready" (every slot that could be filled, was) or "failed" (see
    `error`). A "failed" paper can be retried -- POST /papers/{id}/generate
    only ever fills still-empty slots, never regenerates a filled one.
    progress_done/progress_total track slots filled during "generating"
    for a status-poll progress bar, the same idea as Book's pages_done/
    pages_total. `warnings` (JSON list of strings) collects anything the
    plan or generation step couldn't fully satisfy (an unreachable Bloom
    target, a slot that came up empty, a book that got removed).
    """
    __tablename__ = "papers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)

    title = Column(String, nullable=False)
    subject = Column(String, nullable=True)

    pattern = Column(Text, nullable=False)        # JSON: the Pattern used (preset or custom)
    bloom_targets = Column(Text, nullable=False)   # JSON: {level: percent}
    book_ids = Column(Text, nullable=False)        # JSON: [book_id, ...] -- the units, in order
    # JSON: {book_id (str): weight} actually used for this paper's units --
    # "equal" (default, every selected book weighted 1), "by_size" (chunk
    # count), or "custom" (teacher-supplied), per POST /papers's
    # unit_weighting. Kept alongside book_ids so a saved paper still shows
    # which weights it was planned with.
    unit_weights = Column(Text, nullable=True)
    plan = Column(Text, nullable=False)            # JSON: paper.plan_paper()'s "plan" list

    status = Column(String, default="draft", nullable=False)
    error = Column(Text, nullable=True)
    progress_done = Column(Integer, default=0, nullable=False)
    progress_total = Column(Integer, default=0, nullable=False)
    warnings = Column(Text, nullable=True)  # JSON list of strings
    # True only while the background generation job is paused, waiting out
    # a busy/rate-limited AI response (see main.generate_paper) -- status
    # stays "generating" throughout, this is just an extra flag the UI can
    # show ("waiting for AI service...") while progress isn't moving.
    waiting_for_ai = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )


class PaperQuestion(Base):
    """
    One slot of a Paper's plan, linking it to the actual Question written
    for it (question_id is null until generation fills the slot -- an
    empty slot after generation just stays null, with a warning on the
    Paper explaining why). Deleting a Paper deletes its PaperQuestions
    (cascade) but never the underlying Questions -- those stay in the
    user's bank regardless. Deleting a Book deletes its Questions as
    today (DELETE /books/{id}), which sets this row's question_id back to
    null (not delete the row itself) -- the slot just becomes empty again.
    """
    __tablename__ = "paper_questions"

    id = Column(Integer, primary_key=True, index=True)
    paper_id = Column(Integer, ForeignKey("papers.id"), index=True, nullable=False)

    section = Column(String, nullable=False)
    number = Column(String, nullable=False)
    option = Column(String, nullable=True)  # "a" | "b" | None
    marks = Column(Integer, nullable=False)
    bloom_level = Column(String, nullable=False)
    book_id = Column(Integer, ForeignKey("books.id"), nullable=True)

    question_id = Column(Integer, ForeignKey("questions.id"), nullable=True)
    position = Column(Integer, nullable=False)  # the slot's order in the plan, for display


class ActivityEvent(Base):
    """
    One recorded unit of real usage (Phase 6A), logged server-side only
    (see main._log_activity) -- never accepted directly from a client
    request body -- so a user can't inflate their own analytics. `kind`:
      - "query": a /chat or /tools/tutor answer.
      - "tool": /tools/summarize, /tools/flashcards, or /semantic-search;
        `meta` is {"tool": "summarize" | "flashcards" | "semantic_search"}.
      - "quiz_generated": a successful /generate-quiz call.
      - "paper_generated": a paper's background generation job reaching
        status "ready" (not "failed").
      - "active_seconds": a POST /analytics/active-time heartbeat, or an
        imported day-bucket from POST /analytics/import -- `value` is the
        number of seconds, not 1.
    `value` defaults to 1 so a plain SUM (not COUNT) works uniformly across
    every kind -- "how many" for a count-like kind, "how many seconds" for
    active_seconds.
    """
    __tablename__ = "activity_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    kind = Column(String, nullable=False)
    value = Column(Integer, default=1, nullable=False)
    meta = Column(Text, nullable=True)  # small JSON object, e.g. {"tool": "summarize"}
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    __table_args__ = (
        Index("ix_activity_events_user_created", "user_id", "created_at"),
    )


class QuizAttempt(Base):
    """
    One graded quiz result (Phase 6A): either a real submission (POST
    /analytics/quiz-attempts, called once per quiz taken) or reconstructed
    from a one-time import of old localStorage quiz history (POST
    /analytics/import). correct/total are always computed server-side from
    the submitted per-question answers, never trusted directly from the
    client.
    """
    __tablename__ = "quiz_attempts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    topic = Column(String, nullable=False)
    correct = Column(Integer, nullable=False)
    total = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    answers = relationship("QuizAnswer", cascade="all, delete-orphan")


class QuizAnswer(Base):
    """
    One answered question within a QuizAttempt. question_id points back at
    the Question bank row when the quiz was a real AI-generated one (null
    for an imported/legacy attempt, or any quiz with no tracked question
    id); bloom_level is stored redundantly even when question_id is set,
    since an imported attempt has a level (from the old byLevel breakdown)
    but no question row to look it up from at all.
    """
    __tablename__ = "quiz_answers"

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(Integer, ForeignKey("quiz_attempts.id"), index=True, nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=True)
    bloom_level = Column(String, nullable=True)
    question_type = Column(String, nullable=False)
    is_correct = Column(Boolean, nullable=False)
    self_graded = Column(Boolean, default=False, nullable=False)
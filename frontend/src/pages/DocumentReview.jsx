import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { api } from '../services/api';
import { useDraftPersistence } from '../hooks/useDraftPersistence';
import {
  ArrowLeft,
  ZoomIn,
  ZoomOut,
  Maximize2,
  Save,
  CheckCircle2,
  RotateCw,
  RotateCcw,
  ChevronLeft,
  ChevronRight,
  AlertCircle,
  Loader2,
  MessageSquare,
  FileText
} from 'lucide-react';

const STATUS_POLL_INTERVAL_MS = 2000;
const REREAD_POLL_INTERVAL_MS = 2000;
const REREAD_POLL_MAX_ATTEMPTS = 20; // ~40s before giving up waiting for a re-read to land

const DOT_CLASSES = {
  needs_review: 'bg-amber-500',
  approved: 'bg-emerald-500',
  auto_approved: 'bg-emerald-500'
};

/** Splits text on the literal "[illegible]" marker and highlights every occurrence. */
function renderHighlightedText(text) {
  if (!text) return <span className="italic text-slate-400 dark:text-slate-600">No text extracted for this page.</span>;
  const parts = text.split('[illegible]');
  return parts.map((part, idx) => (
    <span key={idx}>
      {part}
      {idx < parts.length - 1 && (
        <mark className="bg-amber-300/60 dark:bg-amber-500/40 text-amber-900 dark:text-amber-200 rounded px-1 font-semibold not-italic">
          [illegible]
        </mark>
      )}
    </span>
  ));
}

export default function DocumentReview() {
  const { id: bookId } = useParams();
  const navigate = useNavigate();

  const [pages, setPages] = useState([]);
  const [bookName, setBookName] = useState('');
  const [bookStatus, setBookStatus] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  const [currentPageNumber, setCurrentPageNumber] = useState(null);
  const [textDraft, setTextDraft] = useState('');
  const [isDirty, setIsDirty] = useState(false);
  const [showPreview, setShowPreview] = useState(true);

  const [imageUrl, setImageUrl] = useState(null);
  const [imageError, setImageError] = useState('');
  const [zoomPercent, setZoomPercent] = useState(100);

  const [actionError, setActionError] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [isApprovingPage, setIsApprovingPage] = useState(false);
  const [isApprovingAll, setIsApprovingAll] = useState(false);
  const [rereadingPage, setRereadingPage] = useState(null);
  const [isRotating, setIsRotating] = useState(false);

  const [indexingPhase, setIndexingPhase] = useState(false);
  const [finalOutcome, setFinalOutcome] = useState(null); // null | 'ready' | 'failed'

  // An unsaved edit a session-expiry (or manual) logout would otherwise
  // silently discard -- saved per-book (the page number travels inside the
  // value) right before the token is cleared, restored once on return.
  const [hasPendingRestore, setHasPendingRestore] = useState(false);
  const restoredDraftRef = useRef(null);
  useDraftPersistence(
    `review_draft_${bookId}`,
    isDirty ? { pageNumber: currentPageNumber, text: textDraft } : null,
    (restored) => {
      restoredDraftRef.current = restored;
      setHasPendingRestore(true);
    },
    { serialize: JSON.stringify, deserialize: JSON.parse }
  );

  const pagesRef = useRef(pages);
  useEffect(() => { pagesRef.current = pages; }, [pages]);

  // Live current-page-number snapshot for closures (e.g. the re-read poll
  // below) that must never act on a page the user has since navigated away
  // from, even though they captured the page-being-re-read at start time.
  const currentPageNumberRef = useRef(currentPageNumber);
  useEffect(() => { currentPageNumberRef.current = currentPageNumber; }, [currentPageNumber]);

  const currentPage = useMemo(
    () => pages.find((p) => p.page_number === currentPageNumber) || null,
    [pages, currentPageNumber]
  );

  // Initial load: the book's name/status plus every page's review state.
  useEffect(() => {
    let ignore = false;
    (async () => {
      setIsLoading(true);
      try {
        const [status, pageList] = await Promise.all([
          api.books.status(bookId),
          api.books.pages.list(bookId)
        ]);
        if (ignore) return;
        setBookName(status.name);
        setBookStatus(status.status);
        setPages(pageList);
        const firstUnapproved = pageList.find((p) => p.review_status === 'needs_review');
        setCurrentPageNumber((firstUnapproved || pageList[0])?.page_number ?? null);
        setLoadError('');
      } catch (err) {
        if (!ignore) setLoadError(err.message || 'Could not load this document for review.');
      } finally {
        if (!ignore) setIsLoading(false);
      }
    })();
    return () => { ignore = true; };
  }, [bookId]);

  // Once pages have loaded and a draft was restored (see useDraftPersistence
  // above), jump straight to the page it belongs to instead of whatever the
  // initial load picked.
  useEffect(() => {
    if (!hasPendingRestore) return;
    const targetPage = restoredDraftRef.current?.pageNumber;
    if (pages.some((p) => p.page_number === targetPage)) {
      setCurrentPageNumber(targetPage);
    }
  }, [hasPendingRestore, pages]);

  // Reset the editable draft when the selected page changes -- normally to
  // that page's saved server text, but to the just-restored draft instead
  // if one is pending for this exact page. Not run on every background
  // `pages` refresh, so an in-progress edit is never silently clobbered by,
  // say, a re-read landing for another page.
  useEffect(() => {
    if (currentPageNumber == null) return;

    if (hasPendingRestore && restoredDraftRef.current?.pageNumber === currentPageNumber) {
      setTextDraft(restoredDraftRef.current.text);
      setIsDirty(true);
      setZoomPercent(100);
      restoredDraftRef.current = null;
      setHasPendingRestore(false);
      return;
    }

    const page = pagesRef.current.find((p) => p.page_number === currentPageNumber);
    setTextDraft(page?.extracted_text || '');
    setIsDirty(false);
    setZoomPercent(100);
  }, [currentPageNumber, hasPendingRestore]);

  // Load (and clean up) the current page's image as an object URL -- a
  // plain <img src> can't carry the Authorization header this needs.
  useEffect(() => {
    if (currentPageNumber == null) return;
    const page = pagesRef.current.find((p) => p.page_number === currentPageNumber);
    if (!page?.has_image) {
      setImageUrl(null);
      return;
    }

    let cancelled = false;
    let objectUrl = null;
    setImageError('');
    api.books.pages.getImageUrl(bookId, currentPageNumber)
      .then((url) => {
        if (cancelled) { URL.revokeObjectURL(url); return; }
        objectUrl = url;
        setImageUrl(url);
      })
      .catch((err) => {
        if (!cancelled) setImageError(err.message || 'Failed to load page image.');
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [bookId, currentPageNumber]);

  // Warn on tab close/refresh while there's an unsaved edit.
  useEffect(() => {
    const handler = (e) => {
      if (!isDirty) return;
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [isDirty]);

  // Once every page is approved (indexingPhase flips on), poll the book's
  // own status until the background chunk+embed job finishes.
  useEffect(() => {
    if (!indexingPhase) return;
    const interval = setInterval(async () => {
      try {
        const status = await api.books.status(bookId);
        setBookStatus(status.status);
        if (status.status === 'ready') {
          setFinalOutcome('ready');
          setIndexingPhase(false);
        } else if (status.status === 'failed') {
          setFinalOutcome('failed');
          setActionError(status.error || 'Indexing failed.');
          setIndexingPhase(false);
        }
      } catch {
        // Transient failure polling status -- just try again next tick.
      }
    }, STATUS_POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [indexingPhase, bookId]);

  const confirmDiscardIfDirty = () => {
    if (!isDirty) return true;
    return window.confirm('You have unsaved changes on this page. Discard them?');
  };

  const goToPage = useCallback((pageNumber) => {
    if (pageNumber === currentPageNumber) return;
    if (!confirmDiscardIfDirty()) return;
    setCurrentPageNumber(pageNumber);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPageNumber, isDirty]);

  const sortedPages = useMemo(() => [...pages].sort((a, b) => a.page_number - b.page_number), [pages]);
  const currentIndex = sortedPages.findIndex((p) => p.page_number === currentPageNumber);

  const handlePrevious = () => {
    if (currentIndex > 0) goToPage(sortedPages[currentIndex - 1].page_number);
  };
  const handleNext = () => {
    if (currentIndex >= 0 && currentIndex < sortedPages.length - 1) goToPage(sortedPages[currentIndex + 1].page_number);
  };

  // Pages whose text has ever been hand-edited (vs. untouched OCR output) --
  // used only to decide whether "Re-read page" needs to warn that it'll
  // throw that edit away.
  const editedPageNumbersRef = useRef(new Set());

  const handleSave = async () => {
    if (currentPageNumber == null) return;
    setIsSaving(true);
    setActionError('');
    try {
      const result = await api.books.pages.updateText(bookId, currentPageNumber, textDraft);
      setPages((prev) => prev.map((p) => (
        p.page_number === currentPageNumber
          ? { ...p, extracted_text: textDraft, review_status: result.review_status }
          : p
      )));
      setBookStatus(result.status);
      setIsDirty(false);
      editedPageNumbersRef.current.add(currentPageNumber);
    } catch (err) {
      setActionError(err.message || 'Failed to save this page.');
    } finally {
      setIsSaving(false);
    }
  };

  const applyApprovalResult = (result) => {
    setBookStatus(result.status);
    if (result.pages_remaining === 0) {
      setIndexingPhase(true);
    }
  };

  const handleApprovePage = async () => {
    if (currentPageNumber == null) return;
    setIsApprovingPage(true);
    setActionError('');
    try {
      // Save any pending edit first so approval reflects the latest text.
      if (isDirty) {
        await api.books.pages.updateText(bookId, currentPageNumber, textDraft);
        editedPageNumbersRef.current.add(currentPageNumber);
      }
      const result = await api.books.pages.approve(bookId, currentPageNumber);
      setPages((prev) => prev.map((p) => (
        p.page_number === currentPageNumber
          ? { ...p, extracted_text: textDraft, review_status: 'approved' }
          : p
      )));
      setIsDirty(false);
      applyApprovalResult(result);
    } catch (err) {
      setActionError(err.message || 'Failed to approve this page.');
    } finally {
      setIsApprovingPage(false);
    }
  };

  const handleApproveAll = async () => {
    const remaining = pages.filter((p) => p.review_status === 'needs_review').length;
    if (remaining === 0) return;
    if (!window.confirm(`Approve all ${remaining} remaining page(s) and build the index?`)) return;

    setIsApprovingAll(true);
    setActionError('');
    try {
      const result = await api.books.pages.approveAll(bookId);
      setPages((prev) => prev.map((p) => (p.review_status === 'needs_review' ? { ...p, review_status: 'approved' } : p)));
      applyApprovalResult(result);
    } catch (err) {
      setActionError(err.message || 'Failed to approve all pages.');
    } finally {
      setIsApprovingAll(false);
    }
  };

  // Core re-read flow, shared by the "Re-read page" button (which confirms
  // first if the page was edited) and the post-rotate "Re-read this page
  // now?" prompt (which is already its own explicit confirmation).
  const performReread = async (pageNumber) => {
    setIsDirty(false); // a re-read is about to overwrite this page's text either way
    editedPageNumbersRef.current.delete(pageNumber); // starting fresh from the new transcription
    setRereadingPage(pageNumber);
    setActionError('');
    try {
      await api.books.pages.reread(bookId, pageNumber);
      setBookStatus('needs_review');

      let attempts = 0;
      const poll = async () => {
        attempts += 1;
        try {
          const freshPages = await api.books.pages.list(bookId);
          setPages(freshPages);
          const freshPage = freshPages.find((p) => p.page_number === pageNumber);
          // Only touch the textarea if the user is still looking at this
          // exact page -- checked against a live ref, not the pageNumber
          // this closure captured, so navigating away mid-re-read can't
          // clobber whatever page they've since switched to.
          if (currentPageNumberRef.current === pageNumber) {
            setTextDraft(freshPage?.extracted_text || '');
          }
        } catch {
          // Keep polling despite a transient failure.
        }
        if (attempts < REREAD_POLL_MAX_ATTEMPTS) {
          setTimeout(poll, REREAD_POLL_INTERVAL_MS);
        } else {
          setRereadingPage(null);
        }
      };
      setTimeout(poll, REREAD_POLL_INTERVAL_MS);
      // Stop the "re-reading" indicator a little before the hard timeout so
      // it doesn't look stuck even if this particular page took longer.
      setTimeout(() => setRereadingPage((current) => (current === pageNumber ? null : current)), REREAD_POLL_INTERVAL_MS * REREAD_POLL_MAX_ATTEMPTS);
    } catch (err) {
      setActionError(err.message || 'Failed to re-read this page.');
      setRereadingPage(null);
    }
  };

  const handleReread = async () => {
    if (currentPageNumber == null) return;

    const wasEdited = isDirty || editedPageNumbersRef.current.has(currentPageNumber);
    if (wasEdited && !window.confirm(
      'Re-reading will replace your edits on this page with a fresh transcription. Continue?'
    )) {
      return;
    }

    await performReread(currentPageNumber);
  };

  const handleRotate = async (direction) => {
    if (currentPageNumber == null) return;
    const pageNumber = currentPageNumber;

    setIsRotating(true);
    setActionError('');
    try {
      await api.books.pages.rotate(bookId, pageNumber, direction);

      // The saved image file was overwritten in place -- the object URL
      // we're already holding still points at the old (cached) bytes, so
      // it has to be replaced, not just left alone.
      setImageUrl((previousUrl) => {
        if (previousUrl) URL.revokeObjectURL(previousUrl);
        return null;
      });
      const freshUrl = await api.books.pages.getImageUrl(bookId, pageNumber);
      if (currentPageNumberRef.current === pageNumber) {
        setImageUrl(freshUrl);
      } else {
        URL.revokeObjectURL(freshUrl); // the user navigated away while this was in flight
      }

      if (window.confirm('Re-read this page now?')) {
        await performReread(pageNumber);
      }
    } catch (err) {
      setActionError(err.message || 'Failed to rotate this page.');
    } finally {
      setIsRotating(false);
    }
  };

  const handleBack = () => {
    if (!confirmDiscardIfDirty()) return;
    navigate('/documents');
  };

  const remainingCount = pages.filter((p) => p.review_status === 'needs_review').length;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={28} className="animate-spin text-violet-500" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="text-center py-20 border-2 border-dashed border-rose-200 dark:border-rose-900/40 rounded-[32px] bg-rose-50/30 dark:bg-rose-950/10">
        <AlertCircle size={44} className="mx-auto text-rose-400 dark:text-rose-500 mb-4" />
        <h3 className="text-sm font-bold text-rose-600 dark:text-rose-400">Could not load this document</h3>
        <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-2 max-w-xs mx-auto">{loadError}</p>
        <Link to="/documents" className="inline-block mt-4 text-xs font-bold text-violet-600 dark:text-violet-400 hover:underline">
          Back to Documents
        </Link>
      </div>
    );
  }

  if (finalOutcome === 'ready') {
    return (
      <div className="flex flex-col items-center justify-center text-center py-24 space-y-5 animate-fade-in">
        <div className="w-16 h-16 rounded-2xl bg-emerald-500/10 text-emerald-500 flex items-center justify-center">
          <CheckCircle2 size={32} />
        </div>
        <h2 className="text-lg font-bold text-slate-900 dark:text-white">
          Ready — you can now chat and make quizzes from this document
        </h2>
        <div className="flex items-center gap-3">
          <Link
            to="/chat"
            className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-violet-600 to-indigo-500 hover:from-violet-500 hover:to-indigo-400 text-white font-semibold text-xs tracking-tight shadow-md hover:scale-[1.02] active:scale-[0.98] transition-all flex items-center gap-2"
          >
            <MessageSquare size={14} />
            Chat about "{bookName}"
          </Link>
          <Link to="/documents" className="px-5 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-semibold text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-all">
            Back to Documents
          </Link>
        </div>
      </div>
    );
  }

  if (indexingPhase) {
    return (
      <div className="flex flex-col items-center justify-center text-center py-24 space-y-4 animate-fade-in">
        <Loader2 size={32} className="animate-spin text-violet-500" />
        <h2 className="text-sm font-bold text-slate-700 dark:text-slate-200">Indexing…</h2>
        <p className="text-[11px] text-slate-400 dark:text-slate-500">Building the search index from your approved pages.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-fade-in text-slate-800 dark:text-slate-100">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={handleBack}
            className="p-2 rounded-xl text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800/60 transition-all shrink-0"
            title="Back to Documents"
          >
            <ArrowLeft size={18} />
          </button>
          <div className="min-w-0">
            <h1 className="text-lg font-extrabold tracking-tight text-slate-900 dark:text-white truncate">
              Reviewing: {bookName}
            </h1>
            <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-0.5">
              {remainingCount > 0 ? `${remainingCount} page(s) still need review` : 'All pages approved'}
              {bookStatus && ` · book status: ${bookStatus}`}
            </p>
          </div>
        </div>

        <button
          onClick={handleApproveAll}
          disabled={remainingCount === 0 || isApprovingAll}
          className="px-4 py-2 rounded-xl bg-gradient-to-r from-violet-600 to-indigo-500 hover:from-violet-500 hover:to-indigo-400 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold text-xs tracking-tight shadow-md transition-all flex items-center gap-2 shrink-0"
        >
          {isApprovingAll ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
          Approve all ({remainingCount})
        </button>
      </div>

      {actionError && (
        <div className="flex items-start gap-3 p-4 rounded-2xl bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-xs font-medium">
          <AlertCircle size={16} className="shrink-0 mt-0.5" />
          <span>{actionError}</span>
        </div>
      )}

      <div className="flex flex-col lg:flex-row gap-6">
        {/* Page list / thumbnails */}
        <div className="lg:w-56 shrink-0 bg-white/40 dark:bg-slate-900/30 border border-slate-200 dark:border-slate-800/80 rounded-2xl p-3 backdrop-blur-md lg:max-h-[70vh] lg:overflow-y-auto no-scrollbar">
          <div className="flex lg:flex-col gap-2 overflow-x-auto lg:overflow-visible no-scrollbar">
            {sortedPages.map((p) => (
              <button
                key={p.page_number}
                onClick={() => goToPage(p.page_number)}
                className={`flex items-center gap-2 px-3 py-2.5 rounded-xl text-xs font-semibold text-left shrink-0 transition-all
                  ${p.page_number === currentPageNumber
                    ? 'bg-violet-500/10 text-violet-700 dark:text-violet-300 border border-violet-500/30'
                    : 'text-slate-600 dark:text-slate-300 border border-transparent hover:bg-slate-50 dark:hover:bg-slate-800/40'}`}
              >
                <span className={`w-2 h-2 rounded-full shrink-0 ${DOT_CLASSES[p.review_status] || 'bg-slate-300'}`} />
                Page {p.page_number}
              </button>
            ))}
          </div>
        </div>

        {/* Main review area */}
        {currentPage && (
          <div className="flex-1 min-w-0 space-y-4">
            <div className="flex flex-col md:flex-row gap-4">
              {/* Image viewer */}
              <div className="md:w-1/2 bg-slate-900/5 dark:bg-black/30 border border-slate-200 dark:border-slate-800/80 rounded-2xl overflow-hidden flex flex-col">
                <div className="flex items-center justify-between px-3 py-2 border-b border-slate-200 dark:border-slate-800/80 bg-white/60 dark:bg-slate-900/40">
                  <span className="text-[10px] font-bold text-slate-500 dark:text-slate-400">Page image</span>
                  <div className="flex items-center gap-1">
                    <button onClick={() => setZoomPercent((z) => Math.max(50, z - 25))} className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800/60" title="Zoom out">
                      <ZoomOut size={14} />
                    </button>
                    <button onClick={() => setZoomPercent((z) => Math.min(300, z + 25))} className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800/60" title="Zoom in">
                      <ZoomIn size={14} />
                    </button>
                    <button onClick={() => setZoomPercent(100)} className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800/60" title="Fit to width">
                      <Maximize2 size={14} />
                    </button>
                    {currentPage.has_image && (
                      <>
                        <span className="w-px h-4 bg-slate-200 dark:bg-slate-700 mx-1" />
                        <button
                          onClick={() => handleRotate('left')}
                          disabled={isRotating}
                          className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800/60 disabled:opacity-40 disabled:cursor-not-allowed"
                          title="Rotate left"
                        >
                          <RotateCcw size={14} />
                        </button>
                        <button
                          onClick={() => handleRotate('right')}
                          disabled={isRotating}
                          className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800/60 disabled:opacity-40 disabled:cursor-not-allowed"
                          title="Rotate right"
                        >
                          <RotateCw size={14} />
                        </button>
                        {isRotating && <Loader2 size={14} className="animate-spin text-violet-500 ml-1" />}
                      </>
                    )}
                  </div>
                </div>
                <div className="overflow-auto p-3 flex-1 min-h-[260px] max-h-[55vh]">
                  {currentPage.has_image ? (
                    imageError ? (
                      <p className="text-[10px] text-rose-500 dark:text-rose-400">{imageError}</p>
                    ) : imageUrl ? (
                      <img
                        src={imageUrl}
                        alt={`Page ${currentPage.page_number}`}
                        style={{ width: `${zoomPercent}%`, maxWidth: zoomPercent <= 100 ? '100%' : 'none' }}
                      />
                    ) : (
                      <div className="flex items-center justify-center h-full">
                        <Loader2 size={20} className="animate-spin text-slate-400" />
                      </div>
                    )
                  ) : (
                    <div className="flex flex-col items-center justify-center h-full gap-2 text-slate-400 dark:text-slate-600">
                      <FileText size={28} />
                      <span className="text-[10px]">This page's text came directly from the document (no image).</span>
                    </div>
                  )}
                </div>
              </div>

              {/* Editable text + highlighted preview */}
              <div className="md:w-1/2 flex flex-col gap-3">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-bold text-slate-500 dark:text-slate-400">Extracted text</span>
                  <button
                    onClick={() => setShowPreview((v) => !v)}
                    className="text-[10px] font-semibold text-violet-600 dark:text-violet-400 hover:underline"
                  >
                    {showPreview ? 'Hide highlighted preview' : 'Show highlighted preview'}
                  </button>
                </div>
                <textarea
                  value={textDraft}
                  onChange={(e) => { setTextDraft(e.target.value); setIsDirty(true); }}
                  rows={10}
                  maxLength={20000}
                  className="w-full p-3 bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 rounded-xl text-xs font-mono text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-violet-500/20 focus:border-violet-500 resize-y"
                  placeholder="No text extracted for this page."
                />
                {showPreview && (
                  <div className="p-3 bg-slate-50/70 dark:bg-slate-800/30 border border-slate-100 dark:border-slate-800/50 rounded-xl text-xs leading-relaxed whitespace-pre-wrap max-h-40 overflow-y-auto text-slate-600 dark:text-slate-300">
                    {renderHighlightedText(textDraft)}
                  </div>
                )}
              </div>
            </div>

            {/* Action buttons */}
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={handleSave}
                disabled={!isDirty || isSaving}
                className="px-4 py-2 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-xs font-semibold text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700/60 disabled:opacity-40 disabled:cursor-not-allowed transition-all flex items-center gap-2"
              >
                {isSaving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                Save
              </button>
              <button
                onClick={handleApprovePage}
                disabled={isApprovingPage}
                className="px-4 py-2 rounded-xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 text-xs font-semibold hover:bg-emerald-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition-all flex items-center gap-2"
              >
                {isApprovingPage ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle2 size={13} />}
                Approve page
              </button>
              {currentPage.has_image && (
                <button
                  onClick={handleReread}
                  disabled={rereadingPage === currentPage.page_number}
                  className="px-4 py-2 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-xs font-semibold text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700/60 disabled:opacity-40 disabled:cursor-not-allowed transition-all flex items-center gap-2"
                >
                  {rereadingPage === currentPage.page_number ? <Loader2 size={13} className="animate-spin" /> : <RotateCw size={13} />}
                  {rereadingPage === currentPage.page_number ? 'Re-reading…' : 'Re-read page'}
                </button>
              )}

              <div className="flex-1" />

              <button
                onClick={handlePrevious}
                disabled={currentIndex <= 0}
                className="p-2 rounded-xl text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800/60 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                title="Previous page"
              >
                <ChevronLeft size={18} />
              </button>
              <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500">
                Page {currentIndex + 1} of {sortedPages.length}
              </span>
              <button
                onClick={handleNext}
                disabled={currentIndex < 0 || currentIndex >= sortedPages.length - 1}
                className="p-2 rounded-xl text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800/60 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                title="Next page"
              >
                <ChevronRight size={18} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

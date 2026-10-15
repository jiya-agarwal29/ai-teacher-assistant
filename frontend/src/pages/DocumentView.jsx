import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { api } from '../services/api';
import DocumentStatusBadge from '../components/DocumentStatusBadge';
import {
  ArrowLeft,
  Download,
  Search,
  AlertCircle,
  Loader2,
  RotateCw,
  ClipboardCheck,
  FileText,
  Calendar,
  Layers
} from 'lucide-react';

const STATUS_POLL_INTERVAL_MS = 2000;
const VIEWABLE_STATUSES = ['ready', 'needs_review'];
const TABS = { FILE: 'file', TEXT: 'text' };

export default function DocumentView() {
  const { id: bookId } = useParams();
  const navigate = useNavigate();

  const [book, setBook] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [activeTab, setActiveTab] = useState(TABS.FILE);

  const [fileInfo, setFileInfo] = useState(null);
  const [fileError, setFileError] = useState('');
  const [fileLoading, setFileLoading] = useState(false);

  const [pages, setPages] = useState([]);
  const [pagesError, setPagesError] = useState('');
  const [pagesLoading, setPagesLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  const [retryError, setRetryError] = useState('');
  const [isRetrying, setIsRetrying] = useState(false);

  // Read inside the effects below instead of closing over `book` directly,
  // same pattern as Documents.jsx's booksRef -- keeps each effect's guard
  // check reading the latest value without re-running on every render.
  const bookRef = useRef(book);
  useEffect(() => {
    bookRef.current = book;
  }, [book]);

  // GET /books/{id}/status alone doesn't carry created_at, so metadata
  // comes from the full list -- the same source Documents.jsx uses.
  const loadBook = useCallback(async () => {
    try {
      const all = await api.books.getAll();
      const found = (all || []).find((b) => String(b.id) === String(bookId));
      if (!found) {
        setLoadError('Document not found.');
      } else {
        setBook(found);
        setLoadError('');
      }
    } catch (err) {
      setLoadError(err.message || 'Could not load this document.');
    } finally {
      setIsLoading(false);
    }
  }, [bookId]);

  useEffect(() => {
    let ignore = false;
    (async () => {
      if (!ignore) await loadBook();
    })();
    return () => { ignore = true; };
  }, [loadBook]);

  // Poll for progress while processing, same pattern as Documents.jsx.
  useEffect(() => {
    if (!book || book.status !== 'processing') return;
    const interval = setInterval(async () => {
      try {
        const status = await api.books.status(bookId);
        setBook((prev) => (prev ? { ...prev, ...status } : prev));
      } catch {
        // Transient network hiccup -- the next tick will try again.
      }
    }, STATUS_POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [book, bookId]);

  // Load (and free) the original file as a blob URL -- a plain
  // <iframe>/<img> src can't carry the Authorization header this needs.
  // Only fetched once the document is actually viewable.
  useEffect(() => {
    if (!bookRef.current || !VIEWABLE_STATUSES.includes(bookRef.current.status)) {
      setFileInfo(null);
      return;
    }

    let cancelled = false;
    let objectUrl = null;
    setFileLoading(true);
    setFileError('');
    api.books.getFileUrl(bookId)
      .then((info) => {
        if (cancelled) { URL.revokeObjectURL(info.url); return; }
        objectUrl = info.url;
        setFileInfo(info);
      })
      .catch((err) => {
        if (!cancelled) setFileError(err.message || 'Could not load the original file.');
      })
      .finally(() => {
        if (!cancelled) setFileLoading(false);
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [bookId, book]);

  // Extracted text tab.
  useEffect(() => {
    if (!bookRef.current || !VIEWABLE_STATUSES.includes(bookRef.current.status)) return;
    let ignore = false;
    setPagesLoading(true);
    api.books.pages.list(bookId)
      .then((data) => {
        if (!ignore) { setPages(data || []); setPagesError(''); }
      })
      .catch((err) => {
        if (!ignore) setPagesError(err.message || 'Could not load extracted text.');
      })
      .finally(() => {
        if (!ignore) setPagesLoading(false);
      });
    return () => { ignore = true; };
  }, [bookId, book]);

  const handleRetry = async () => {
    setRetryError('');
    setIsRetrying(true);
    try {
      await api.books.retry(bookId);
      await loadBook();
    } catch (err) {
      setRetryError(err.message || 'Failed to retry this document.');
    } finally {
      setIsRetrying(false);
    }
  };

  const filteredPages = pages.filter((p) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (p.extracted_text || '').toLowerCase().includes(q) || String(p.page_number).includes(q);
  });

  if (isLoading) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div className="h-8 w-48 bg-slate-100 dark:bg-slate-800/60 rounded-xl animate-pulse"></div>
        <div className="h-96 bg-slate-100 dark:bg-slate-800/60 rounded-[28px] animate-pulse"></div>
      </div>
    );
  }

  if (loadError || !book) {
    return (
      <div className="text-center py-20 border-2 border-dashed border-rose-200 dark:border-rose-900/40 rounded-[32px] bg-rose-50/30 dark:bg-rose-950/10">
        <AlertCircle size={44} className="mx-auto text-rose-400 dark:text-rose-500 mb-4" />
        <h3 className="text-sm font-bold text-rose-600 dark:text-rose-400">{loadError || 'Document not found.'}</h3>
        <Link to="/documents" className="inline-block mt-4 text-xs font-bold text-violet-600 dark:text-violet-400 hover:text-violet-500">
          Back to Documents
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-fade-in text-slate-800 dark:text-slate-100">
      <div className="flex items-center gap-3">
        <button
          onClick={() => navigate('/documents')}
          className="p-2 rounded-xl text-slate-400 hover:text-violet-500 hover:bg-violet-500/10 transition-colors shrink-0"
          title="Back to Documents"
        >
          <ArrowLeft size={18} />
        </button>
        <div className="min-w-0">
          <h1 className="text-xl font-extrabold tracking-tight text-slate-900 dark:text-white truncate">
            {book.name}
          </h1>
          <div className="flex items-center gap-3 text-[10px] text-slate-400 dark:text-slate-500 font-semibold mt-1 flex-wrap">
            <span className="flex items-center gap-1">
              <Calendar size={11} />
              {book.created_at ? new Date(book.created_at).toLocaleString() : 'Unknown date'}
            </span>
            {book.source_type && (
              <span className="flex items-center gap-1">
                <Layers size={11} />
                {book.source_type}
              </span>
            )}
            {book.pages_total > 0 && (
              <span>{book.pages_total} page{book.pages_total === 1 ? '' : 's'}</span>
            )}
          </div>
        </div>
        <div className="ml-auto shrink-0">
          <DocumentStatusBadge status={book.status} pagesTotal={book.pages_total} pagesDone={book.pages_done} sourceType={book.source_type} bookId={book.id} />
        </div>
      </div>

      {book.status === 'processing' && (
        <div className="p-10 rounded-[28px] border border-slate-200 dark:border-slate-800/80 bg-white/40 dark:bg-slate-900/30 text-center">
          <Loader2 size={28} className="mx-auto text-violet-500 animate-spin mb-3" />
          <p className="text-xs font-bold text-slate-700 dark:text-slate-200">
            {book.pages_total > 0
              ? (book.source_type ? `Processing ${book.pages_done}/${book.pages_total}` : `Reading page ${book.pages_done} of ${book.pages_total}`)
              : 'Processing this document…'}
          </p>
        </div>
      )}

      {book.status === 'failed' && (
        <div className="p-6 rounded-[28px] border border-rose-200 dark:border-rose-900/40 bg-rose-50/30 dark:bg-rose-950/10 space-y-3">
          <div className="flex items-start gap-3 text-rose-600 dark:text-rose-400">
            <AlertCircle size={18} className="shrink-0 mt-0.5" />
            <p className="text-xs font-medium">{book.error || 'This document failed to process.'}</p>
          </div>
          {retryError && <p className="text-[10px] text-rose-500 dark:text-rose-400">{retryError}</p>}
          <button
            onClick={handleRetry}
            disabled={isRetrying}
            className="flex items-center gap-1.5 px-4 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-60 text-white font-bold text-xs rounded-xl transition-colors"
          >
            <RotateCw size={13} className={isRetrying ? 'animate-spin' : ''} />
            Retry
          </button>
        </div>
      )}

      {book.status === 'needs_review' && (
        <Link
          to={`/documents/${bookId}/review`}
          className="flex items-center gap-2 p-4 rounded-2xl border border-amber-200 dark:border-amber-900/40 bg-amber-50/40 dark:bg-amber-950/10 text-amber-700 dark:text-amber-400 font-bold text-xs hover:border-amber-400/60 transition-colors"
        >
          <ClipboardCheck size={15} />
          This document has pages waiting for review — Review pages
        </Link>
      )}

      {VIEWABLE_STATUSES.includes(book.status) && (
        <>
          <div className="flex gap-2 border-b border-slate-200 dark:border-slate-800/80">
            <button
              onClick={() => setActiveTab(TABS.FILE)}
              className={`px-4 py-2.5 text-xs font-bold border-b-2 transition-colors ${activeTab === TABS.FILE ? 'border-violet-500 text-violet-600 dark:text-violet-400' : 'border-transparent text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300'}`}
            >
              Original
            </button>
            <button
              onClick={() => setActiveTab(TABS.TEXT)}
              className={`px-4 py-2.5 text-xs font-bold border-b-2 transition-colors ${activeTab === TABS.TEXT ? 'border-violet-500 text-violet-600 dark:text-violet-400' : 'border-transparent text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300'}`}
            >
              Extracted text
            </button>
          </div>

          {activeTab === TABS.FILE && (
            <div className="rounded-[28px] border border-slate-200 dark:border-slate-800/80 bg-white/40 dark:bg-slate-900/30 overflow-hidden min-h-[480px] flex items-center justify-center p-4">
              {fileLoading ? (
                <Loader2 size={28} className="text-violet-500 animate-spin" />
              ) : fileError ? (
                <div className="text-center py-10">
                  <AlertCircle size={32} className="mx-auto text-slate-400 dark:text-slate-600 mb-3" />
                  <p className="text-xs font-medium text-slate-500 dark:text-slate-400 max-w-xs">{fileError}</p>
                </div>
              ) : fileInfo?.contentType?.startsWith('application/pdf') ? (
                <object data={fileInfo.url} type="application/pdf" className="w-full h-[75vh] rounded-2xl">
                  <iframe src={fileInfo.url} title={book.name} className="w-full h-[75vh] rounded-2xl border-0" />
                </object>
              ) : fileInfo?.contentType?.startsWith('image/') ? (
                <img src={fileInfo.url} alt={book.name} className="max-w-full max-h-[75vh] rounded-2xl object-contain" />
              ) : fileInfo ? (
                <div className="text-center py-10 space-y-3">
                  <FileText size={36} className="mx-auto text-slate-400 dark:text-slate-600" />
                  <p className="text-xs font-medium text-slate-500 dark:text-slate-400">
                    This file type can't be previewed in the browser.
                  </p>
                  <a
                    href={fileInfo.url}
                    download={fileInfo.filename}
                    className="inline-flex items-center gap-1.5 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white font-bold text-xs rounded-xl transition-colors"
                  >
                    <Download size={13} />
                    Download original
                  </a>
                </div>
              ) : null}
            </div>
          )}

          {activeTab === TABS.TEXT && (
            <div className="space-y-4">
              <div className="relative">
                <span className="absolute inset-y-0 left-0 pl-3.5 flex items-center text-slate-400 dark:text-slate-500">
                  <Search size={15} />
                </span>
                <input
                  type="text"
                  placeholder="Search extracted text..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-10 pr-4 py-2.5 bg-slate-50 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-800 text-xs font-medium text-slate-900 dark:text-white rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-500/10 focus:border-violet-500 transition-all placeholder-slate-400"
                />
              </div>

              {pagesLoading ? (
                <div className="h-40 bg-slate-100 dark:bg-slate-800/60 rounded-2xl animate-pulse"></div>
              ) : pagesError ? (
                <p className="text-xs text-rose-500 dark:text-rose-400">{pagesError}</p>
              ) : filteredPages.length === 0 ? (
                <p className="text-xs text-slate-400 dark:text-slate-500 text-center py-10">
                  {searchQuery ? 'No pages match your search.' : 'No extracted text for this document yet.'}
                </p>
              ) : (
                <div className="space-y-3">
                  {filteredPages.map((p) => (
                    <div key={p.page_number} className="p-4 rounded-2xl border border-slate-200 dark:border-slate-800/80 bg-white/40 dark:bg-slate-900/30">
                      <span className="text-[10px] font-extrabold uppercase tracking-wider text-violet-600 dark:text-violet-400">
                        Page {p.page_number}
                      </span>
                      <p className="text-xs text-slate-700 dark:text-slate-300 mt-2 whitespace-pre-wrap leading-relaxed">
                        {p.extracted_text || <span className="italic text-slate-400 dark:text-slate-600">No text extracted for this page.</span>}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

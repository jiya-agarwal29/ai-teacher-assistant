import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../services/api';
import DocumentStatusBadge from '../components/DocumentStatusBadge';
import {
  Upload,
  Search,
  Trash2,
  FileText,
  AlertCircle,
  Sparkles,
  CheckCircle2,
  FolderOpen,
  RotateCw
} from 'lucide-react';

const STATUS_POLL_INTERVAL_MS = 2000;
const DOCUMENT_EXTENSIONS = ['.pdf', '.docx', '.doc', '.pptx', '.ppt', '.txt', '.md'];
const IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp', '.heic'];
const getExtension = (name) => name.slice(name.lastIndexOf('.')).toLowerCase();

export default function Documents() {
  const [books, setBooks] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [searchQuery, setSearchQuery] = useState('');

  // Upload States
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadError, setUploadError] = useState('');
  const [uploadSuccess, setUploadSuccess] = useState('');
  const [reviewOcr, setReviewOcr] = useState(true);

  // Load books. Wrapped in useCallback with a stable (empty) dependency
  // list so it can be safely listed as an effect dependency below.
  const loadBooks = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await api.books.getAll();
      setBooks(data || []);
      setLoadError('');
    } catch (error) {
      console.error('Failed to fetch books:', error);
      setLoadError(error.message || 'Could not reach the backend. Please make sure the server is running.');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    // Guard against setting state after this component has unmounted (e.g.
    // navigating away before the request resolves).
    let ignore = false;
    (async () => {
      if (ignore) return;
      await loadBooks();
    })();
    return () => {
      ignore = true;
    };
  }, [loadBooks]);

  // Polls GET /books/{id}/status for every book still "processing", every
  // STATUS_POLL_INTERVAL_MS, until it reaches "ready"/"failed". Runs once
  // for the life of the component (not re-created on every `books` update)
  // by reading the latest list through a ref; stops on unmount/navigation.
  const booksRef = useRef(books);
  useEffect(() => {
    booksRef.current = books;
  }, [books]);

  useEffect(() => {
    const interval = setInterval(async () => {
      const processing = booksRef.current.filter((b) => b.status === 'processing');
      if (processing.length === 0) return;

      const results = await Promise.allSettled(processing.map((b) => api.books.status(b.id)));

      setBooks((prev) => prev.map((b) => {
        const idx = processing.findIndex((p) => p.id === b.id);
        if (idx === -1) return b;
        const result = results[idx];
        return result.status === 'fulfilled' ? { ...b, ...result.value } : b;
      }));
    }, STATUS_POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, []);

  // Upload book API call. Accepts either a single document or one or more
  // page photos/scans (uploaded together as one document, OCR'd in order).
  const handleUploadFiles = async (fileList) => {
    const selectedFiles = Array.from(fileList || []);
    if (selectedFiles.length === 0) return;

    const allImages = selectedFiles.every((f) => IMAGE_EXTENSIONS.includes(getExtension(f.name)));
    const allDocuments = selectedFiles.every((f) => DOCUMENT_EXTENSIONS.includes(getExtension(f.name)));

    if (!allImages && !allDocuments) {
      setUploadError('Unsupported file format. Please upload PDF, Word (.docx, .doc), PowerPoint (.pptx, .ppt), or plain text (.txt, .md) documents, or one or more photos/scans (.jpg, .png, .webp, .heic).');
      return;
    }
    if (allDocuments && selectedFiles.length > 1) {
      setUploadError('Please upload one document at a time. To combine multiple photos/scans into one document, select image files instead.');
      return;
    }

    const uploadPayload = allImages ? selectedFiles : selectedFiles[0];
    const label = allImages
      ? `${selectedFiles.length} image${selectedFiles.length === 1 ? '' : 's'}`
      : `"${selectedFiles[0].name}"`;

    setIsUploading(true);
    setUploadProgress(0);
    setUploadError('');
    setUploadSuccess('');

    try {
      await api.books.upload(uploadPayload, (progress) => {
        setUploadProgress(progress);
      }, { reviewOcr });
      setUploadSuccess(`${label} uploaded and now processing — track progress below.`);
      // Refresh list so the new "processing" book shows up immediately;
      // the poller above then follows it to ready/needs_review/failed.
      await loadBooks();
    } catch (err) {
      setUploadError(err.message || 'Failed to upload document. Check your backend status.');
    } finally {
      setIsUploading(false);
    }
  };

  const handleRetry = async (id) => {
    setUploadError('');
    try {
      await api.books.retry(id);
      await loadBooks();
    } catch (err) {
      setUploadError(err.message || 'Failed to retry this document.');
    }
  };

  const handleFileChange = (e) => {
    handleUploadFiles(e.target.files);
    e.target.value = ''; // allow re-selecting the same file(s) later
  };

  // Drag and drop handlers
  const handleDragOver = (e) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    handleUploadFiles(e.dataTransfer.files);
  };

  const handleDelete = async (id, name) => {
    if (!window.confirm(`Are you sure you want to delete "${name}"? This removes all matching chunks from the semantic index.`)) {
      return;
    }
    
    try {
      await api.books.delete(id);
      setUploadSuccess('Document deleted successfully.');
      loadBooks();
    } catch (err) {
      setUploadError(err.message || 'Failed to delete book.');
    }
  };

  // Live filter list by search query
  const filteredBooks = books.filter(book => 
    book.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="space-y-8 animate-fade-in text-slate-800 dark:text-slate-100">
      
      {/* Top Header */}
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white">
          Knowledge Base Documents
        </h1>
        <p className="text-sm text-slate-500 dark:text-slate-400 mt-2 font-medium">
          Upload and manage the study materials (PDF, Word, PPT) fed to your AI Study Assistant RAG context.
        </p>
      </div>

      {/* Upload Drag and Drop Section */}
      <div 
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`
          relative border-2 border-dashed rounded-[32px] p-10 text-center transition-all duration-300 theme-transition
          ${isDragging 
            ? 'border-violet-500 bg-violet-500/5 dark:bg-violet-500/10 scale-[1.01]' 
            : 'border-slate-200 dark:border-slate-800/80 bg-white/40 dark:bg-slate-900/30 backdrop-blur-md hover:border-slate-300 dark:hover:border-slate-700/80'}
        `}
      >
        <div className="flex flex-col items-center justify-center max-w-md mx-auto space-y-4">
          <div className="w-14 h-14 rounded-2xl bg-violet-500/10 text-violet-600 dark:text-violet-400 flex items-center justify-center">
            <Upload size={26} className={isDragging ? 'animate-bounce' : ''} />
          </div>
          <div>
            <h3 className="text-sm font-bold text-slate-800 dark:text-white">
              Drag & Drop Study Documents
            </h3>
            <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-2 leading-relaxed">
              Drag any textbook, chapter syllabus, Word document, or PowerPoint presentation slides here, or one or more photos/scans of handwritten or printed pages. The assistant will partition the text and build semantic embeddings.
            </p>
          </div>

          <label className="px-5 py-2.5 bg-gradient-to-tr from-violet-600 to-indigo-500 hover:from-violet-500 hover:to-indigo-400 text-white font-semibold text-xs tracking-tight rounded-xl shadow-md cursor-pointer hover:scale-[1.02] active:scale-[0.98] transition-all">
            Browse files
            <input
              type="file"
              accept=".pdf,.pptx,.ppt,.docx,.doc,.txt,.md,.jpg,.jpeg,.png,.webp,.heic"
              multiple
              disabled={isUploading}
              onChange={handleFileChange}
              className="hidden"
            />
          </label>

          <label className="flex items-center gap-2 text-[10px] font-semibold text-slate-500 dark:text-slate-400 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={reviewOcr}
              onChange={(e) => setReviewOcr(e.target.checked)}
              disabled={isUploading}
              className="rounded border-slate-300 dark:border-slate-700 text-violet-600 focus:ring-violet-500 focus:ring-offset-0"
            />
            Let me check the text before it's used
          </label>
        </div>

        {/* Upload Overlay indicator */}
        {isUploading && (
          <div className="absolute inset-0 bg-white/95 dark:bg-slate-900/95 backdrop-blur-md rounded-[32px] flex flex-col items-center justify-center p-8 z-20">
            <div className="w-12 h-12 border-4 border-violet-500 border-t-transparent rounded-full animate-spin mb-4"></div>
            <h4 className="text-sm font-bold text-slate-800 dark:text-white mb-2">Analyzing Knowledge Base Document</h4>
            <p className="text-[10px] text-slate-400 dark:text-slate-500 mb-4 max-w-xs leading-normal">
              Parsing document pages, segmenting structures, and creating vector weights...
            </p>
            <div className="w-56 bg-slate-100 dark:bg-slate-800 h-2 rounded-full overflow-hidden mb-2">
              <div className="bg-violet-500 h-full transition-all duration-300" style={{ width: `${uploadProgress}%` }}></div>
            </div>
            <span className="text-[10px] font-bold text-violet-500">{uploadProgress}% Complete</span>
          </div>
        )}
      </div>

      {/* Alert Banners */}
      {uploadError && (
        <div className="flex items-start gap-3 p-4 rounded-2xl bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-xs font-medium">
          <AlertCircle size={16} className="shrink-0 mt-0.5" />
          <span>{uploadError}</span>
        </div>
      )}

      {uploadSuccess && (
        <div className="flex items-start gap-3 p-4 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 dark:text-emerald-400 text-xs font-medium">
          <CheckCircle2 size={16} className="shrink-0 mt-0.5" />
          <span>{uploadSuccess}</span>
        </div>
      )}

      {/* Search and Filters */}
      <div className="flex flex-col sm:flex-row justify-between items-stretch sm:items-center gap-4 bg-white/40 dark:bg-slate-900/30 border border-slate-200 dark:border-slate-800/80 p-4 rounded-2xl">
        <div className="relative flex-1">
          <span className="absolute inset-y-0 left-0 pl-3.5 flex items-center text-slate-400 dark:text-slate-500">
            <Search size={16} />
          </span>
          <input 
            type="text"
            placeholder="Search matching document titles..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2.5 bg-slate-50 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-800 text-xs font-medium text-slate-900 dark:text-white rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-500/10 focus:border-violet-500 transition-all placeholder-slate-400"
          />
        </div>
        <div className="flex items-center justify-between text-slate-400 dark:text-slate-500 text-[10px] font-semibold tracking-wider uppercase px-2">
          <span>Records: {filteredBooks.length} Total</span>
        </div>
      </div>

      {/* Book List Content */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          <div className="h-44 bg-slate-100 dark:bg-slate-800/60 rounded-[28px] animate-pulse"></div>
          <div className="h-44 bg-slate-100 dark:bg-slate-800/60 rounded-[28px] animate-pulse"></div>
          <div className="h-44 bg-slate-100 dark:bg-slate-800/60 rounded-[28px] animate-pulse"></div>
        </div>
      ) : loadError ? (
        <div className="text-center py-20 border-2 border-dashed border-rose-200 dark:border-rose-900/40 rounded-[32px] bg-rose-50/30 dark:bg-rose-950/10">
          <AlertCircle size={44} className="mx-auto text-rose-400 dark:text-rose-500 mb-4" />
          <h3 className="text-sm font-bold text-rose-600 dark:text-rose-400">Could not load your documents</h3>
          <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-2 max-w-xs mx-auto">{loadError}</p>
        </div>
      ) : filteredBooks.length === 0 ? (
        <div className="text-center py-20 border-2 border-dashed border-slate-200 dark:border-slate-800 rounded-[32px] bg-white/20 dark:bg-slate-900/10 backdrop-blur-md">
          <FolderOpen size={44} className="mx-auto text-slate-400 dark:text-slate-600 mb-4" />
          <h3 className="text-sm font-bold text-slate-700 dark:text-slate-300">No matching documents found</h3>
          <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-2 max-w-xs mx-auto">
            {searchQuery ? 'Try refinement keywords or upload a new study document.' : 'Start by dragging in study documents above to populate your database.'}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredBooks.map((book) => (
            <div
              key={book.id}
              className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm hover:scale-[1.02] hover:shadow-md hover:border-violet-500/30 dark:hover:border-violet-500/20 transition-all duration-300 flex flex-col h-48 group"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-4 min-w-0">
                  <div className="w-11 h-11 rounded-2xl bg-gradient-to-tr from-violet-500/10 to-indigo-500/10 text-violet-600 dark:text-violet-400 flex items-center justify-center shrink-0">
                    <FileText size={20} />
                  </div>
                  <div className="min-w-0">
                    <h3 className="text-xs font-bold text-slate-800 dark:text-white truncate group-hover:text-violet-600 dark:group-hover:text-violet-400 transition-colors">
                      {book.name}
                    </h3>
                    <span className="text-[9px] font-semibold text-slate-400 dark:text-slate-500 block mt-1">
                      Index Reference: #{book.id}
                    </span>
                  </div>
                </div>
                <DocumentStatusBadge status={book.status} pagesTotal={book.pages_total} pagesDone={book.pages_done} sourceType={book.source_type} />
              </div>

              {book.status === 'failed' && book.error && (
                <p className="text-[10px] text-rose-500 dark:text-rose-400 mt-2 line-clamp-2">
                  {book.error}
                </p>
              )}

              <div className="mt-auto pt-4 border-t border-slate-100 dark:border-slate-800/60 flex justify-between items-center text-[10px]">
                <div className="flex items-center gap-1.5 text-slate-400 dark:text-slate-500 min-w-0">
                  {book.status === 'ready' ? (
                    <>
                      <Sparkles size={11} className="text-violet-500 shrink-0" />
                      <span>{book.pages_total ? `${book.pages_total} chunks indexed` : 'RAG Context Active'}</span>
                    </>
                  ) : book.status === 'failed' ? (
                    <button
                      onClick={() => handleRetry(book.id)}
                      className="flex items-center gap-1 text-violet-600 dark:text-violet-400 font-bold hover:text-violet-500 transition-colors"
                    >
                      <RotateCw size={11} />
                      Retry
                    </button>
                  ) : (
                    <span>Indexing your document…</span>
                  )}
                </div>
                <button
                  onClick={() => handleDelete(book.id, book.name)}
                  className="p-2 rounded-xl text-slate-400 hover:text-rose-500 hover:bg-rose-500/10 transition-all shrink-0"
                  title="Purge Document Data"
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

    </div>
  );
}

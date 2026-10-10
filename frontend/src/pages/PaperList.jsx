import { useState, useEffect, useRef, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Plus, Trash2, AlertCircle, ClipboardList, Loader2 } from 'lucide-react';
import { api } from '../services/api';
import PaperStatusBadge from '../components/PaperStatusBadge';

const STATUS_POLL_INTERVAL_MS = 3000;

export default function PaperList() {
  const navigate = useNavigate();
  const [papers, setPapers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.papers.list();
      setPapers(data || []);
      setError('');
    } catch (err) {
      setError(err.message || 'Could not load your question papers.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let ignore = false;
    (async () => {
      if (ignore) return;
      await load();
    })();
    return () => {
      ignore = true;
    };
  }, [load]);

  // Polls GET /papers/{id}/status for every paper still "generating", so
  // the list's status badge shows live progress -- same pattern as
  // Documents.jsx's background-processing poller.
  const papersRef = useRef(papers);
  useEffect(() => {
    papersRef.current = papers;
  }, [papers]);

  useEffect(() => {
    const interval = setInterval(async () => {
      const generating = papersRef.current.filter((p) => p.status === 'generating');
      if (generating.length === 0) return;

      const results = await Promise.allSettled(generating.map((p) => api.papers.status(p.id)));

      setPapers((prev) => prev.map((p) => {
        const idx = generating.findIndex((g) => g.id === p.id);
        if (idx === -1) return p;
        const result = results[idx];
        return result.status === 'fulfilled' ? { ...p, ...result.value } : p;
      }));
    }, STATUS_POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, []);

  const handleDelete = async (id, title) => {
    if (!window.confirm(`Delete "${title}"? This cannot be undone (its questions stay in your Question Bank).`)) {
      return;
    }
    try {
      await api.papers.remove(id);
      setPapers((prev) => prev.filter((p) => p.id !== id));
    } catch (err) {
      setError(err.message || 'Could not delete this paper.');
    }
  };

  return (
    <div className="space-y-8 animate-fade-in text-slate-800 dark:text-slate-100">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white">Question Papers</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-2 font-medium">
            Plan, generate, and print exam papers built from your documents.
          </p>
        </div>
        <button
          type="button"
          onClick={() => navigate('/papers/new')}
          className="flex items-center gap-2 px-5 py-3 rounded-2xl bg-gradient-to-tr from-violet-600 to-indigo-500 text-white font-semibold text-xs tracking-tight hover:scale-[1.01] transition-all shrink-0"
        >
          <Plus size={14} /> New paper
        </button>
      </div>

      {error && (
        <div className="flex items-start gap-3 p-4 rounded-2xl bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-xs font-medium">
          <AlertCircle size={16} className="shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <div className="flex flex-col items-center justify-center py-20">
          <Loader2 size={28} className="animate-spin text-violet-500 mb-3" />
          <span className="text-xs font-bold text-slate-500 dark:text-slate-400">Loading your papers...</span>
        </div>
      ) : papers.length === 0 ? (
        <div className="p-8 rounded-[28px] border-2 border-dashed border-slate-200 dark:border-slate-800/80 text-center max-w-md mx-auto">
          <ClipboardList size={36} className="text-slate-400 dark:text-slate-600 mx-auto mb-3" />
          <p className="text-[11px] text-slate-500 dark:text-slate-400 leading-relaxed">
            No question papers yet — click "New paper" to build one from your ready documents.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {papers.map((p) => (
            <div
              key={p.id}
              className="p-5 rounded-[24px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm flex items-center justify-between gap-4 hover:border-violet-500/30 dark:hover:border-violet-500/20 transition-colors"
            >
              <Link to={`/papers/${p.id}`} className="min-w-0 flex-1 flex items-center gap-4">
                <div className="w-11 h-11 rounded-2xl bg-gradient-to-tr from-violet-500/10 to-indigo-500/10 text-violet-600 dark:text-violet-400 flex items-center justify-center shrink-0">
                  <ClipboardList size={20} />
                </div>
                <div className="min-w-0">
                  <h3 className="text-xs font-bold text-slate-800 dark:text-white truncate group-hover:text-violet-600 transition-colors">
                    {p.title}
                  </h3>
                  <span className="text-[9px] font-semibold text-slate-400 dark:text-slate-500">
                    {p.total_marks} marks · {p.created_at ? new Date(p.created_at).toLocaleDateString() : ''}
                  </span>
                </div>
              </Link>
              <div className="flex items-center gap-3 shrink-0">
                <PaperStatusBadge
                  status={p.status}
                  progressDone={p.progress_done}
                  progressTotal={p.progress_total}
                  waitingForAi={p.waiting_for_ai}
                />
                <button
                  type="button"
                  onClick={() => handleDelete(p.id, p.title)}
                  className="p-2 rounded-xl text-slate-400 hover:text-rose-500 hover:bg-rose-500/10 transition-all"
                  title="Delete paper"
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

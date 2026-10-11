import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  ArrowLeft,
  Printer,
  AlertCircle,
  AlertTriangle,
  Loader2,
  RefreshCw,
  Pencil,
  Shuffle,
  Plus,
  Trash2,
  Wand2,
  Download,
  ChevronDown,
  X
} from 'lucide-react';
import { api } from '../services/api';
import useBloomLevels from '../hooks/useBloomLevels';
import BloomBadge from '../components/BloomBadge';
import BlueprintPanel from '../components/BlueprintPanel';
import PaperPlanPreview from '../components/PaperPlanPreview';

const STATUS_POLL_INTERVAL_MS = 3000;

const TYPE_LABELS = {
  mcq: 'MCQ',
  true_false: 'True / False',
  fill_blank: 'Fill in the Blank',
  short_answer: 'Short Answer',
  long_answer: 'Long Answer',
  scenario: 'Scenario-based',
  viva: 'Viva Question',
  interview: 'Interview Question'
};

const inputClass = 'w-full px-3 py-2 rounded-xl text-[11px] bg-slate-50 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-800 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-500/10 focus:border-violet-500 transition-all';

function isProblemSlot(slot) {
  return !slot.question || slot.question.bloom_check === 'flagged';
}

function groupSlotsBySection(slots) {
  const bySection = new Map();
  for (const slot of slots) {
    if (!bySection.has(slot.section)) bySection.set(slot.section, []);
    bySection.get(slot.section).push(slot);
  }
  return bySection;
}

function groupByNumber(slots) {
  const groups = [];
  const indexByNumber = new Map();
  for (const slot of slots) {
    if (!indexByNumber.has(slot.number)) {
      indexByNumber.set(slot.number, groups.length);
      groups.push([]);
    }
    groups[indexByNumber.get(slot.number)].push(slot);
  }
  return groups;
}

/**
 * Lists up to 20 bank questions that fit `slot` and lets the teacher swap
 * one in -- opened from either an empty or a filled slot.
 */
function SwapDialog({ paperId, slot, onClose, onSwapped }) {
  const [candidates, setCandidates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [swappingId, setSwappingId] = useState(null);

  useEffect(() => {
    let ignore = false;
    (async () => {
      try {
        const data = await api.papers.candidates(paperId, slot.id);
        if (!ignore) setCandidates(data || []);
      } catch (err) {
        if (!ignore) setError(err.message || 'Could not load candidate questions.');
      } finally {
        if (!ignore) setLoading(false);
      }
    })();
    return () => { ignore = true; };
  }, [paperId, slot.id]);

  const handlePick = async (questionId) => {
    setSwappingId(questionId);
    setError('');
    try {
      const updated = await api.papers.swapSlot(paperId, slot.id, questionId);
      onSwapped(updated);
      onClose();
    } catch (err) {
      setError(err.message || 'Could not swap in this question.');
      setSwappingId(null);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-900/50 backdrop-blur-sm p-4 print:hidden">
      <div className="w-full max-w-xl max-h-[80vh] overflow-y-auto bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-[24px] p-6 shadow-xl animate-fade-in">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-bold text-slate-900 dark:text-white">Swap in a question from your bank</h2>
          <button onClick={onClose} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800">
            <X size={16} />
          </button>
        </div>

        {error && (
          <div className="flex items-start gap-2 p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-[11px] font-medium mb-3">
            <AlertCircle size={13} className="shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 size={22} className="animate-spin text-violet-500" />
          </div>
        ) : candidates.length === 0 ? (
          <p className="text-[11px] text-slate-400 dark:text-slate-500 py-6 text-center">
            No matching questions in your bank yet for this slot.
          </p>
        ) : (
          <div className="space-y-2.5">
            {candidates.map((c) => (
              <div
                key={c.question_id}
                className="p-3.5 rounded-2xl border border-slate-200 dark:border-slate-800 flex items-start justify-between gap-3"
              >
                <div className="min-w-0 space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <BloomBadge levelKey={c.bloom_level} />
                    <span className="px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400">
                      {TYPE_LABELS[c.type] || c.type}
                    </span>
                    {c.marks != null && (
                      <span className="text-[9px] font-bold text-slate-400 dark:text-slate-500">{c.marks} marks</span>
                    )}
                  </div>
                  <p className="text-[11px] font-semibold text-slate-700 dark:text-slate-200">{c.question}</p>
                  {c.source && (
                    <p className="text-[9px] text-slate-400 dark:text-slate-500">
                      {c.source.book_name}{c.source.page_number != null ? `, p.${c.source.page_number}` : ''}
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => handlePick(c.question_id)}
                  disabled={swappingId != null}
                  className="shrink-0 px-3 py-1.5 rounded-lg text-[10px] font-bold bg-violet-600 hover:bg-violet-500 text-white transition-colors disabled:opacity-50"
                >
                  {swappingId === c.question_id ? 'Using...' : 'Use this'}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * One printed question slot: its question (or an empty-slot problem
 * card), and in teacher view the Bloom/document/check metadata, marking
 * scheme, and the Edit/Regenerate/Swap actions. Manages its own
 * edit-draft and busy state, like QuestionBank.jsx's QuestionRow.
 */
function QuestionSlot({ slot, option, paperId, teacherView, bloomLevelsByKey, onPaperUpdated, registerFixer }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [regenerating, setRegenerating] = useState(false);
  const [actionError, setActionError] = useState('');
  const [swapOpen, setSwapOpen] = useState(false);

  const q = slot.question;

  const handleRegenerate = useCallback(async () => {
    setRegenerating(true);
    setActionError('');
    try {
      const updated = await api.papers.regenerateSlot(paperId, slot.id);
      onPaperUpdated(updated);
      setEditing(false);
    } catch (err) {
      setActionError(err.message || 'Could not regenerate this question.');
      throw err;
    } finally {
      setRegenerating(false);
    }
  }, [paperId, slot.id, onPaperUpdated]);

  // Lets the page-level "Fix all problems" button drive this exact slot's
  // regenerate call (and surface its spinner) without duplicating the
  // request logic.
  useEffect(() => {
    if (!registerFixer) return undefined;
    registerFixer(slot.id, handleRegenerate);
    return () => registerFixer(slot.id, null);
  }, [registerFixer, slot.id, handleRegenerate]);

  const confirmRegenerate = () => {
    if (!window.confirm('Regenerate this question? Its current text will be replaced.')) return;
    handleRegenerate().catch(() => {});
  };

  const startEditing = () => {
    setDraft({
      question: q.question,
      options: q.options || [],
      correct_answer: q.correct_answer,
      explanation: q.explanation,
      marking_scheme: q.marking_scheme && q.marking_scheme.length > 0
        ? q.marking_scheme.map((p) => ({ ...p }))
        : [{ point: '', marks: slot.marks }]
    });
    setSaveError('');
    setEditing(true);
  };

  const schemeTotal = draft ? draft.marking_scheme.reduce((sum, p) => sum + (Number(p.marks) || 0), 0) : 0;

  const updateSchemePoint = (idx, field, value) => {
    setDraft((prev) => ({
      ...prev,
      marking_scheme: prev.marking_scheme.map((p, i) => (i === idx ? { ...p, [field]: value } : p))
    }));
  };
  const addSchemePoint = () => {
    setDraft((prev) => ({ ...prev, marking_scheme: [...prev.marking_scheme, { point: '', marks: 0 }] }));
  };
  const removeSchemePoint = (idx) => {
    setDraft((prev) => ({ ...prev, marking_scheme: prev.marking_scheme.filter((_, i) => i !== idx) }));
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveError('');
    try {
      const payload = {
        question: draft.question,
        correct_answer: draft.correct_answer,
        explanation: draft.explanation,
        marking_scheme: draft.marking_scheme
          .map((p) => ({ point: (p.point || '').trim(), marks: Number(p.marks) || 0 }))
          .filter((p) => p.point)
      };
      if (q.type === 'mcq') payload.options = draft.options;
      const updatedQuestion = await api.questions.update(q.question_id, payload);
      onPaperUpdated((prevPaper) => ({
        ...prevPaper,
        slots: prevPaper.slots.map((s) => (s.id === slot.id ? { ...s, question: updatedQuestion } : s))
      }));
      setEditing(false);
    } catch (err) {
      setSaveError(err.message || 'Could not save changes.');
    } finally {
      setSaving(false);
    }
  };

  const label = option ? `(${option})` : null;

  if (!q) {
    return (
      <div className="p-4 rounded-2xl border-2 border-rose-300 dark:border-rose-800/60 bg-rose-50/60 dark:bg-rose-950/20">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            {label && <span className="text-xs font-bold text-slate-500 dark:text-slate-400">{label}</span>}
            <span className="text-[10px] font-bold text-rose-600 dark:text-rose-400 uppercase tracking-wider">
              Needs a question
            </span>
            <span className="text-[9px] font-bold text-slate-400 dark:text-slate-500">{slot.marks} marks</span>
          </div>
          <div className="flex items-center gap-2 print:hidden">
            <button
              type="button"
              onClick={confirmRegenerate}
              disabled={regenerating}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[10px] font-bold bg-violet-600/10 text-violet-600 dark:text-violet-400 hover:bg-violet-600/20 transition-colors disabled:opacity-50"
            >
              {regenerating ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
              Regenerate
            </button>
            <button
              type="button"
              onClick={() => setSwapOpen(true)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[10px] font-bold bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"
            >
              <Shuffle size={11} /> Swap
            </button>
          </div>
        </div>
        {actionError && <p className="text-[10px] font-semibold text-rose-500 mt-2">{actionError}</p>}
        {swapOpen && (
          <SwapDialog paperId={paperId} slot={slot} onClose={() => setSwapOpen(false)} onSwapped={onPaperUpdated} />
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-sm text-slate-800 dark:text-slate-100 leading-relaxed">
            {label && <span className="font-bold">{label} </span>}
            {editing ? null : q.question}
          </p>

          {!editing && q.options && q.options.length > 0 && (
            <div className="mt-2 space-y-1 pl-1">
              {q.options.map((opt, idx) => (
                <p key={idx} className="text-[13px] text-slate-600 dark:text-slate-300">
                  {(['A', 'B', 'C', 'D'][idx] || '')}. {opt}
                </p>
              ))}
            </div>
          )}
        </div>
        <span className="text-xs font-bold text-slate-500 dark:text-slate-400 shrink-0 print:text-black">
          [{slot.marks}]
        </span>
      </div>

      {teacherView && !editing && (
        <div className="space-y-2 pl-1">
          <div className="flex items-center gap-3 flex-wrap text-[10px]">
            <BloomBadge levelKey={q.bloom_level} />
            <span className="px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400">
              {TYPE_LABELS[q.type] || q.type}
            </span>
            {q.source && (
              <span className="font-semibold text-slate-400 dark:text-slate-500">
                {q.source.book_name}{q.source.page_number != null ? `, p.${q.source.page_number}` : ''}
              </span>
            )}
          </div>

          {q.bloom_check === 'flagged' && (
            <p className="text-[10px] font-semibold text-amber-600 dark:text-amber-400 flex items-center gap-1.5">
              <AlertTriangle size={11} />
              Level unsure — looks like {bloomLevelsByKey[q.detected_level]?.label || q.detected_level || 'a different level'}
            </p>
          )}

          <p className="text-[11px] text-slate-500 dark:text-slate-400">
            <span className="font-bold text-slate-600 dark:text-slate-300">Answer:</span> {q.correct_answer}
          </p>

          {q.marking_scheme && q.marking_scheme.length > 0 && (
            <div className="text-[11px] text-slate-500 dark:text-slate-400">
              <span className="font-bold text-slate-600 dark:text-slate-300">Marking scheme:</span>
              <ul className="list-disc pl-5 mt-0.5 space-y-0.5">
                {q.marking_scheme.map((pt, idx) => (
                  <li key={idx}>{pt.point} <span className="font-bold">({pt.marks})</span></li>
                ))}
              </ul>
            </div>
          )}

          {actionError && <p className="text-[10px] font-semibold text-rose-500">{actionError}</p>}

          <div className="flex items-center gap-2 pt-1 print:hidden">
            <button
              type="button"
              onClick={startEditing}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[10px] font-bold bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"
            >
              <Pencil size={11} /> Edit
            </button>
            <button
              type="button"
              onClick={confirmRegenerate}
              disabled={regenerating}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[10px] font-bold bg-violet-600/10 text-violet-600 dark:text-violet-400 hover:bg-violet-600/20 transition-colors disabled:opacity-50"
            >
              {regenerating ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
              Regenerate
            </button>
            <button
              type="button"
              onClick={() => setSwapOpen(true)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[10px] font-bold bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"
            >
              <Shuffle size={11} /> Swap
            </button>
          </div>
        </div>
      )}

      {teacherView && editing && draft && (
        <div className="space-y-2.5 pl-1 pt-1">
          <textarea
            value={draft.question}
            onChange={(e) => setDraft((prev) => ({ ...prev, question: e.target.value }))}
            rows={2}
            className={inputClass}
          />

          {q.type === 'mcq' && draft.options.map((opt, idx) => (
            <input
              key={idx}
              value={opt}
              onChange={(e) => setDraft((prev) => ({
                ...prev,
                options: prev.options.map((o, i) => (i === idx ? e.target.value : o))
              }))}
              className={inputClass}
            />
          ))}

          <textarea
            value={draft.correct_answer}
            onChange={(e) => setDraft((prev) => ({ ...prev, correct_answer: e.target.value }))}
            rows={2}
            placeholder="Correct answer"
            className={inputClass}
          />
          <textarea
            value={draft.explanation}
            onChange={(e) => setDraft((prev) => ({ ...prev, explanation: e.target.value }))}
            rows={2}
            placeholder="Explanation"
            className={inputClass}
          />

          <div className="space-y-1.5">
            <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">Marking scheme</span>
            {draft.marking_scheme.map((pt, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <input
                  value={pt.point}
                  onChange={(e) => updateSchemePoint(idx, 'point', e.target.value)}
                  placeholder="Point"
                  className={`${inputClass} flex-1`}
                />
                <input
                  type="number"
                  value={pt.marks}
                  onChange={(e) => updateSchemePoint(idx, 'marks', e.target.value)}
                  className={`${inputClass} w-16`}
                />
                <button type="button" onClick={() => removeSchemePoint(idx)} className="p-1.5 text-slate-400 hover:text-rose-500">
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
            <div className="flex items-center justify-between">
              <button
                type="button"
                onClick={addSchemePoint}
                className="flex items-center gap-1 px-2 py-1 rounded-lg text-[9px] font-bold bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700"
              >
                <Plus size={10} /> Add point
              </button>
              <span className={`text-[10px] font-bold ${schemeTotal === slot.marks ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-500'}`}>
                Adds up to {schemeTotal} of {slot.marks}
              </span>
            </div>
          </div>

          {saveError && <p className="text-[10px] font-semibold text-rose-500">{saveError}</p>}

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || schemeTotal !== slot.marks}
              className="px-4 py-1.5 rounded-lg text-[10px] font-bold bg-violet-600 hover:bg-violet-500 text-white transition-colors disabled:opacity-50"
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="px-4 py-1.5 rounded-lg text-[10px] font-bold bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {swapOpen && (
        <SwapDialog paperId={paperId} slot={slot} onClose={() => setSwapOpen(false)} onSwapped={onPaperUpdated} />
      )}
    </div>
  );
}

const DOWNLOAD_OPTIONS = [
  { kind: 'student', label: 'Student copy' },
  { kind: 'teacher', label: 'Teacher copy' },
  { kind: 'answer_key', label: 'Answer key' }
];

/**
 * "Download" menu: a small dropdown offering the three .docx exports
 * (GET /papers/{id}/export?kind=...). Each download is fetched as a blob
 * (api.papers.download) rather than a plain <a href>, since the request
 * needs the Authorization header -- same reasoning as the OCR review
 * page's page images.
 */
function DownloadMenu({ paperId }) {
  const [open, setOpen] = useState(false);
  const [downloadingKind, setDownloadingKind] = useState(null);
  const [error, setError] = useState('');
  const containerRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const handleClickOutside = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  const handleDownload = async (kind) => {
    setDownloadingKind(kind);
    setError('');
    try {
      await api.papers.download(paperId, kind);
      setOpen(false);
    } catch (err) {
      setError(err.message || 'Could not download this file.');
    } finally {
      setDownloadingKind(null);
    }
  };

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-[11px] font-bold bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"
      >
        <Download size={12} /> Download <ChevronDown size={11} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute right-0 mt-1.5 w-48 rounded-xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 shadow-lg z-20 overflow-hidden">
          {DOWNLOAD_OPTIONS.map((opt) => (
            <button
              key={opt.kind}
              type="button"
              onClick={() => handleDownload(opt.kind)}
              disabled={downloadingKind != null}
              className="w-full flex items-center justify-between gap-2 px-3.5 py-2.5 text-[11px] font-semibold text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800/60 transition-colors disabled:opacity-50"
            >
              {opt.label}
              {downloadingKind === opt.kind && <Loader2 size={12} className="animate-spin" />}
            </button>
          ))}
        </div>
      )}

      {error && (
        <p className="absolute right-0 mt-1 w-56 text-[10px] font-semibold text-rose-500 text-right">{error}</p>
      )}
    </div>
  );
}

export default function PaperView() {
  const { id } = useParams();
  const { byKey: bloomLevelsByKey } = useBloomLevels();

  const [paper, setPaper] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [teacherView, setTeacherView] = useState(true);
  const [retrying, setRetrying] = useState(false);

  const [fixingAll, setFixingAll] = useState(false);
  const [fixAllProgress, setFixAllProgress] = useState({ done: 0, total: 0 });
  const [fixAllError, setFixAllError] = useState('');

  const load = useCallback(async () => {
    try {
      const data = await api.papers.get(id);
      setPaper(data);
      setError('');
    } catch (err) {
      setError(err.message || 'Could not load this paper.');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    let ignore = false;
    (async () => {
      if (ignore) return;
      await load();
    })();
    return () => { ignore = true; };
  }, [load]);

  // Polls GET /papers/{id}/status every 3s while "generating"; stops on
  // unmount or once the status changes.
  useEffect(() => {
    if (paper?.status !== 'generating') return undefined;
    let cancelled = false;

    const interval = setInterval(async () => {
      try {
        const statusData = await api.papers.status(id);
        if (cancelled) return;
        if (statusData.status !== 'generating') {
          const full = await api.papers.get(id);
          if (!cancelled) setPaper(full);
        } else {
          setPaper((prev) => (prev ? { ...prev, ...statusData } : prev));
        }
      } catch {
        // Transient poll failure -- try again next tick.
      }
    }, STATUS_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [paper?.status, id]);

  const handleGenerate = async () => {
    setError('');
    try {
      await api.papers.generate(id);
      await load();
    } catch (err) {
      setError(err.message || 'Could not start generation.');
    }
  };

  const handleRetry = async () => {
    setRetrying(true);
    setError('');
    try {
      await api.papers.generate(id);
      await load();
    } catch (err) {
      setError(err.message || 'Could not restart generation.');
    } finally {
      setRetrying(false);
    }
  };

  // Lets a child QuestionSlot's update (edit/regenerate/swap) either
  // replace the whole paper object, or a functional updater over the
  // previous one (used by the inline-edit save, which only changes one
  // slot's question).
  const handlePaperUpdated = (updatedOrUpdater) => {
    setPaper((prev) => (typeof updatedOrUpdater === 'function' ? updatedOrUpdater(prev) : updatedOrUpdater));
  };

  // Registry of each QuestionSlot's own regenerate function, keyed by
  // slot id -- "Fix all problems" below drives these one by one instead
  // of duplicating the regenerate request logic at the page level.
  const fixersRef = useRef({});
  const registerFixer = useCallback((slotId, fn) => {
    if (fn) fixersRef.current[slotId] = fn;
    else delete fixersRef.current[slotId];
  }, []);

  const handleFixAll = async () => {
    if (!paper) return;
    const problems = paper.slots.filter(isProblemSlot);
    if (problems.length === 0) return;

    setFixAllError('');
    setFixAllProgress({ done: 0, total: problems.length });
    setFixingAll(true);

    for (let i = 0; i < problems.length; i++) {
      const fixer = fixersRef.current[problems[i].id];
      try {
        if (fixer) await fixer();
        setFixAllProgress({ done: i + 1, total: problems.length });
      } catch (err) {
        setFixAllError(
          `Stopped after ${i} of ${problems.length} -- ${err.message || 'a slot failed to regenerate.'}`
        );
        break;
      }
    }

    setFixingAll(false);
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <Loader2 size={28} className="animate-spin text-violet-500 mb-3" />
        <span className="text-xs font-bold text-slate-500 dark:text-slate-400">Loading paper...</span>
      </div>
    );
  }

  if (error && !paper) {
    return (
      <div className="max-w-xl mx-auto space-y-4 animate-fade-in">
        <Link to="/papers" className="flex items-center gap-2 text-xs font-bold text-slate-500 dark:text-slate-400 hover:text-violet-600">
          <ArrowLeft size={14} /> Back to papers
        </Link>
        <div className="flex items-start gap-3 p-4 rounded-2xl bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-xs font-medium">
          <AlertCircle size={16} className="shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      </div>
    );
  }

  if (!paper) return null;

  const documentNames = Object.fromEntries((paper.book_ids || []).map((bid) => [String(bid), `Document #${bid}`]));
  // Slot/plan book_id is already carried on each slot; build a nicer name
  // map from the plan + slot metadata instead if a question's source names
  // the book directly.
  for (const slot of paper.slots || []) {
    if (slot.book_id != null && slot.question?.source?.book_name) {
      documentNames[String(slot.book_id)] = slot.question.source.book_name;
    }
  }

  if (paper.status === 'draft') {
    return (
      <div className="space-y-8 animate-fade-in text-slate-800 dark:text-slate-100">
        <div className="flex items-center justify-between gap-3">
          <Link to="/papers" className="flex items-center gap-2 text-xs font-bold text-slate-500 dark:text-slate-400 hover:text-violet-600">
            <ArrowLeft size={14} /> Back to papers
          </Link>
        </div>
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-slate-900 dark:text-white">{paper.title}</h1>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 font-medium">
            This paper hasn't been generated yet.
          </p>
        </div>
        {error && (
          <div className="flex items-start gap-3 p-4 rounded-2xl bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-xs font-medium">
            <AlertCircle size={16} className="shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}
        <PaperPlanPreview
          paperData={paper}
          documentNames={documentNames}
          actions={(
            <button
              type="button"
              onClick={handleGenerate}
              className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-xs font-bold bg-gradient-to-tr from-violet-600 to-indigo-500 text-white hover:scale-[1.01] transition-all"
            >
              <Wand2 size={13} /> Generate paper
            </button>
          )}
        />
      </div>
    );
  }

  if (paper.status === 'generating') {
    const pct = paper.progress_total > 0 ? Math.round((paper.progress_done / paper.progress_total) * 100) : 0;
    return (
      <div className="max-w-xl mx-auto space-y-6 animate-fade-in text-center py-10">
        <h1 className="text-xl font-extrabold tracking-tight text-slate-900 dark:text-white">{paper.title}</h1>
        <div className="w-14 h-14 border-4 border-violet-500 border-t-transparent rounded-full animate-spin mx-auto" />
        <p className="text-sm font-bold text-slate-700 dark:text-slate-200">
          {paper.waiting_for_ai
            ? 'Waiting for the AI service...'
            : `Writing question ${paper.progress_done} of ${paper.progress_total}`}
        </p>
        <div className="w-full max-w-sm mx-auto bg-slate-100 dark:bg-slate-800 h-2.5 rounded-full overflow-hidden">
          <div className="bg-violet-500 h-full transition-all duration-500" style={{ width: `${pct}%` }} />
        </div>
      </div>
    );
  }

  if (paper.status === 'failed') {
    return (
      <div className="max-w-xl mx-auto space-y-6 animate-fade-in">
        <Link to="/papers" className="flex items-center gap-2 text-xs font-bold text-slate-500 dark:text-slate-400 hover:text-violet-600">
          <ArrowLeft size={14} /> Back to papers
        </Link>
        <div className="p-8 rounded-[28px] border-2 border-dashed border-rose-200 dark:border-rose-900/40 bg-rose-50/30 dark:bg-rose-950/10 text-center space-y-4">
          <AlertCircle size={36} className="mx-auto text-rose-400 dark:text-rose-500" />
          <div>
            <h3 className="text-sm font-bold text-rose-600 dark:text-rose-400">Generation failed</h3>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-2">{paper.error}</p>
          </div>
          <button
            type="button"
            onClick={handleRetry}
            disabled={retrying}
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-xs font-bold bg-gradient-to-tr from-violet-600 to-indigo-500 text-white hover:scale-[1.01] transition-all disabled:opacity-50"
          >
            {retrying ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            {retrying ? 'Retrying...' : 'Retry'}
          </button>
        </div>
      </div>
    );
  }

  // status === "ready"
  const sections = groupSlotsBySection(paper.slots || []);
  // What a student actually answers (e.g. 50 for mid_sem_50's "any 4 of
  // 5" section), not marks_on_paper (55, every printed question) --
  // Phase 5E EXTRA fix.
  const maxMarks = paper.total_marks;
  const problemCount = (paper.slots || []).filter(isProblemSlot).length;

  return (
    <div className="space-y-6 animate-fade-in text-slate-800 dark:text-slate-100">
      <div className="flex items-center justify-between gap-4 flex-wrap print:hidden">
        <Link to="/papers" className="flex items-center gap-2 text-xs font-bold text-slate-500 dark:text-slate-400 hover:text-violet-600">
          <ArrowLeft size={14} /> Back to papers
        </Link>
        <div className="flex items-center gap-2 flex-wrap">
          {problemCount > 0 && (
            <button
              type="button"
              onClick={handleFixAll}
              disabled={fixingAll}
              className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-[11px] font-bold bg-amber-500/10 text-amber-700 dark:text-amber-400 hover:bg-amber-500/20 transition-colors disabled:opacity-50"
            >
              {fixingAll
                ? <Loader2 size={12} className="animate-spin" />
                : <Wand2 size={12} />}
              {fixingAll
                ? `Fixing ${fixAllProgress.done}/${fixAllProgress.total}...`
                : `Fix all problems (${problemCount})`}
            </button>
          )}
          <label className="flex items-center gap-2 text-[11px] font-bold text-slate-500 dark:text-slate-400 cursor-pointer select-none px-3 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">
            <input
              type="checkbox"
              checked={teacherView}
              onChange={(e) => setTeacherView(e.target.checked)}
              className="rounded border-slate-300 dark:border-slate-700 text-violet-600 focus:ring-violet-500 focus:ring-offset-0"
            />
            Teacher view
          </label>
          <DownloadMenu paperId={paper.id} />
          <button
            type="button"
            onClick={() => window.print()}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-[11px] font-bold bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"
          >
            <Printer size={12} /> Print
          </button>
        </div>
      </div>

      {fixAllError && (
        <div className="flex items-start gap-3 p-4 rounded-2xl bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-xs font-medium print:hidden">
          <AlertCircle size={16} className="shrink-0 mt-0.5" />
          <span>{fixAllError}</span>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6">
        <div className="p-8 md:p-10 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm print:rounded-none print:border-0 print:shadow-none print:p-0 print:bg-white">
          <div className="text-center border-b-2 border-slate-300 dark:border-slate-700 pb-5 mb-7 print:border-black">
            <h1 className="text-xl font-bold uppercase tracking-wide text-slate-900 dark:text-white print:text-black">{paper.title}</h1>
            {paper.subject && (
              <p className="text-sm text-slate-500 dark:text-slate-400 mt-1 print:text-black">{paper.subject}</p>
            )}
            <div className="flex justify-center gap-8 text-xs font-semibold text-slate-600 dark:text-slate-300 mt-3 print:text-black">
              <span>Max Marks: {maxMarks}</span>
              <span>Duration: {paper.pattern?.duration_minutes} minutes</span>
            </div>
          </div>

          <div className="space-y-8">
            {(paper.pattern?.sections || []).map((sectionMeta) => {
              const sectionSlots = sections.get(sectionMeta.key) || [];
              if (sectionSlots.length === 0) return null;
              const groups = groupByNumber(sectionSlots);
              return (
                <div key={sectionMeta.key}>
                  <h2 className="font-bold text-sm text-slate-900 dark:text-white print:text-black">{sectionMeta.title}</h2>
                  <p className="text-xs italic text-slate-500 dark:text-slate-400 mt-0.5 mb-4 print:text-black">
                    {sectionMeta.instructions}
                  </p>
                  <div className="space-y-5">
                    {groups.map((group) => (
                      <div key={group[0].id}>
                        {group.length === 1 ? (
                          <div className="flex gap-2">
                            <span className="text-sm font-bold text-slate-800 dark:text-white shrink-0 print:text-black">
                              {group[0].number}.
                            </span>
                            <div className="flex-1">
                              <QuestionSlot
                                slot={group[0]}
                                option={null}
                                paperId={paper.id}
                                teacherView={teacherView}
                                bloomLevelsByKey={bloomLevelsByKey}
                                onPaperUpdated={handlePaperUpdated}
                                registerFixer={registerFixer}
                              />
                            </div>
                          </div>
                        ) : (
                          <div>
                            <p className="text-sm font-bold text-slate-800 dark:text-white print:text-black">
                              {group[0].number}. Answer EITHER (a) OR (b):
                            </p>
                            <div className="pl-5 mt-2 space-y-4">
                              {group.map((slot) => (
                                <QuestionSlot
                                  key={slot.id}
                                  slot={slot}
                                  option={slot.option}
                                  paperId={paper.id}
                                  teacherView={teacherView}
                                  bloomLevelsByKey={bloomLevelsByKey}
                                  onPaperUpdated={handlePaperUpdated}
                                  registerFixer={registerFixer}
                                />
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="print:hidden">
          <BlueprintPanel
            target={paper.blueprint_target}
            comparison={paper.blueprint_actual?.by_level}
            comparisonLabel="Actual"
            byUnit={paper.blueprint_actual?.by_unit}
            documentNames={documentNames}
            warnings={paper.warnings}
          />
        </div>
      </div>
    </div>
  );
}

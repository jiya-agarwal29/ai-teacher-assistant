import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import {
  Search,
  Loader2,
  AlertCircle,
  ChevronRight,
  RefreshCw,
  Pencil,
  Trash2,
  Database
} from 'lucide-react';
import { api } from '../services/api';
import useBloomLevels from '../hooks/useBloomLevels';
import { getBloomColor } from '../utils/bloomColors';
import BloomBadge from '../components/BloomBadge';

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

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

function QuestionRow({ item, bloomLevels, onUpdated, onDeleted }) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [regenerating, setRegenerating] = useState(false);
  const [regenerateError, setRegenerateError] = useState('');
  const [deleting, setDeleting] = useState(false);

  const [draftQuestion, setDraftQuestion] = useState(item.question);
  const [draftOptions, setDraftOptions] = useState(item.options || []);
  const [draftCorrectAnswer, setDraftCorrectAnswer] = useState(item.correct_answer);
  const [draftExplanation, setDraftExplanation] = useState(item.explanation);
  const [draftLevel, setDraftLevel] = useState(item.bloom_level);

  const allowedLevelsForType = bloomLevels.filter((lvl) => lvl.question_types.includes(item.type));

  const startEditing = () => {
    setDraftQuestion(item.question);
    setDraftOptions(item.options || []);
    setDraftCorrectAnswer(item.correct_answer);
    setDraftExplanation(item.explanation);
    setDraftLevel(item.bloom_level);
    setSaveError('');
    setEditing(true);
    setExpanded(true);
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveError('');
    try {
      const payload = {
        question: draftQuestion,
        explanation: draftExplanation,
        correct_answer: draftCorrectAnswer
      };
      if (item.type === 'mcq') {
        payload.options = draftOptions;
      }
      if (draftLevel !== item.bloom_level) {
        payload.bloom_level = draftLevel;
      }
      const updated = await api.questions.update(item.question_id, payload);
      onUpdated(updated);
      setEditing(false);
    } catch (err) {
      setSaveError(err.message || 'Could not save changes.');
    } finally {
      setSaving(false);
    }
  };

  const handleRegenerate = async () => {
    if (!window.confirm('Regenerate this question? Its current text will be replaced.')) return;
    setRegenerating(true);
    setRegenerateError('');
    try {
      const updated = await api.questions.regenerate(item.question_id);
      onUpdated(updated);
    } catch (err) {
      setRegenerateError(err.message || 'Could not regenerate this question.');
    } finally {
      setRegenerating(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm('Delete this question? This cannot be undone.')) return;
    setDeleting(true);
    try {
      await api.questions.delete(item.question_id);
      onDeleted(item.question_id);
    } catch (err) {
      setRegenerateError(err.message || 'Could not delete this question.');
      setDeleting(false);
    }
  };

  return (
    <div className="p-5 rounded-[24px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-3">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left flex items-start gap-3"
      >
        <ChevronRight
          size={14}
          className={`mt-1 shrink-0 transition-transform text-slate-400 ${expanded ? 'rotate-90' : ''}`}
        />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <BloomBadge levelKey={item.bloom_level} />
            <span className="px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400">
              {TYPE_LABELS[item.type] || item.type}
            </span>
            {item.bloom_check === 'flagged' && (
              <span className="px-2 py-0.5 rounded-full text-[9px] font-bold bg-amber-500/10 text-amber-600 dark:text-amber-400">
                Flagged
              </span>
            )}
            <span className="text-[9px] font-semibold text-slate-400 dark:text-slate-500">{item.topic}</span>
          </div>
          <p className="text-xs font-semibold text-slate-800 dark:text-white">{item.question}</p>
          {item.source && (
            <p className="text-[9px] text-slate-400 dark:text-slate-500">
              Source: {item.source.book_name}{item.source.page_number != null ? `, p.${item.source.page_number}` : ''}
            </p>
          )}
        </div>
      </button>

      {expanded && !editing && (
        <div className="pl-6 space-y-2.5">
          <div className="text-[11px] space-y-1.5">
            {item.options && item.options.length > 0 && (
              <div className="space-y-1">
                {item.options.map((opt, idx) => (
                  <p key={idx} className="text-slate-500 dark:text-slate-400">
                    {(['A', 'B', 'C', 'D'][idx] || '')}. {opt}
                  </p>
                ))}
              </div>
            )}
            <p className="text-slate-700 dark:text-slate-300"><span className="font-bold">Answer:</span> {item.correct_answer}</p>
            <p className="text-slate-500 dark:text-slate-400"><span className="font-bold text-slate-600 dark:text-slate-300">Explanation:</span> {item.explanation}</p>
          </div>

          {regenerateError && (
            <p className="text-[10px] font-semibold text-rose-500">{regenerateError}</p>
          )}

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={startEditing}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"
            >
              <Pencil size={11} /> Edit
            </button>
            <button
              type="button"
              onClick={handleRegenerate}
              disabled={regenerating}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold bg-violet-600/10 text-violet-600 dark:text-violet-400 hover:bg-violet-600/20 transition-colors disabled:opacity-50"
            >
              {regenerating ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
              {regenerating ? 'Regenerating...' : 'Regenerate'}
            </button>
            <button
              type="button"
              onClick={handleDelete}
              disabled={deleting}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold bg-rose-500/10 text-rose-600 dark:text-rose-400 hover:bg-rose-500/20 transition-colors disabled:opacity-50"
            >
              <Trash2 size={11} /> {deleting ? 'Deleting...' : 'Delete'}
            </button>
          </div>
        </div>
      )}

      {expanded && editing && (
        <div className="pl-6 space-y-2.5">
          <textarea
            value={draftQuestion}
            onChange={(e) => setDraftQuestion(e.target.value)}
            rows={2}
            className={inputClass}
          />

          {item.type === 'mcq' && draftOptions.map((opt, idx) => (
            <input
              key={idx}
              value={opt}
              onChange={(e) => {
                const next = [...draftOptions];
                next[idx] = e.target.value;
                setDraftOptions(next);
              }}
              className={inputClass}
            />
          ))}

          {item.type === 'mcq' ? (
            <select value={draftCorrectAnswer} onChange={(e) => setDraftCorrectAnswer(e.target.value)} className={inputClass}>
              {['A', 'B', 'C', 'D'].map((letter) => <option key={letter} value={letter}>{letter}</option>)}
            </select>
          ) : item.type === 'true_false' ? (
            <select value={draftCorrectAnswer} onChange={(e) => setDraftCorrectAnswer(e.target.value)} className={inputClass}>
              <option value="A">True</option>
              <option value="B">False</option>
            </select>
          ) : (
            <textarea
              value={draftCorrectAnswer}
              onChange={(e) => setDraftCorrectAnswer(e.target.value)}
              rows={2}
              className={inputClass}
            />
          )}

          <textarea
            value={draftExplanation}
            onChange={(e) => setDraftExplanation(e.target.value)}
            rows={2}
            className={inputClass}
          />

          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">Level</span>
            <select value={draftLevel} onChange={(e) => setDraftLevel(e.target.value)} className={`${inputClass} w-auto`}>
              {allowedLevelsForType.map((lvl) => <option key={lvl.key} value={lvl.key}>{lvl.label}</option>)}
            </select>
          </div>

          {saveError && <p className="text-[10px] font-semibold text-rose-500">{saveError}</p>}

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
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
    </div>
  );
}

export default function QuestionBank() {
  const { levels: bloomLevels } = useBloomLevels();

  const [summary, setSummary] = useState(null);
  const [summaryError, setSummaryError] = useState('');

  const [selectedLevels, setSelectedLevels] = useState([]);
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [topicInput, setTopicInput] = useState('');
  const [debouncedTopic, setDebouncedTopic] = useState('');

  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');

  const refreshSummary = useCallback(() => {
    api.questions.summary()
      .then((data) => {
        setSummary(data);
        setSummaryError('');
      })
      .catch((err) => setSummaryError(err.message || 'Could not load summary.'));
  }, []);

  useEffect(() => {
    refreshSummary();
  }, [refreshSummary]);

  // Debounce the topic search box.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedTopic(topicInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [topicInput]);

  // Refetch page 1 whenever a filter changes. The loading/error reset for
  // this happens in the event handlers that change selectedLevels/
  // flaggedOnly/topicInput below (so it's immediate, and keeps this effect
  // body itself free of synchronous setState calls), not here.
  useEffect(() => {
    let cancelled = false;

    const params = { limit: PAGE_SIZE, offset: 0 };
    if (selectedLevels.length > 0) params.level = selectedLevels;
    if (flaggedOnly) params.check = 'flagged';
    if (debouncedTopic) params.topic = debouncedTopic;

    api.questions.list(params)
      .then((data) => {
        if (cancelled) return;
        setItems(data.items);
        setTotal(data.total);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || 'Could not load questions.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedLevels, flaggedOnly, debouncedTopic]);

  const handleLoadMore = async () => {
    setLoadingMore(true);
    setError('');
    try {
      const params = { limit: PAGE_SIZE, offset: items.length };
      if (selectedLevels.length > 0) params.level = selectedLevels;
      if (flaggedOnly) params.check = 'flagged';
      if (debouncedTopic) params.topic = debouncedTopic;
      const data = await api.questions.list(params);
      setItems((prev) => [...prev, ...data.items]);
      setTotal(data.total);
    } catch (err) {
      setError(err.message || 'Could not load more questions.');
    } finally {
      setLoadingMore(false);
    }
  };

  const toggleLevelFilter = (key) => {
    setSelectedLevels((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
    setLoading(true);
    setError('');
  };

  const handleFlaggedOnlyToggle = () => {
    setFlaggedOnly((v) => !v);
    setLoading(true);
    setError('');
  };

  const handleTopicInputChange = (e) => {
    setTopicInput(e.target.value);
    setLoading(true);
    setError('');
  };

  const handleItemUpdated = (updated) => {
    setItems((prev) => prev.map((it) => (it.question_id === updated.question_id ? updated : it)));
    refreshSummary();
  };

  const handleItemDeleted = (questionId) => {
    setItems((prev) => prev.filter((it) => it.question_id !== questionId));
    setTotal((prev) => Math.max(0, prev - 1));
    refreshSummary();
  };

  const levelCounts = summary?.by_level || {};
  const maxLevelCount = Math.max(1, ...Object.values(levelCounts));

  return (
    <div className="space-y-8 animate-fade-in text-slate-800 dark:text-slate-100">
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white">Question Bank</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400 mt-2 font-medium">
          Every question ever generated for you — filter, edit, regenerate, or remove any of them.
        </p>
      </div>

      {/* Summary strip */}
      <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-4">
        {summaryError ? (
          <p className="text-xs font-semibold text-rose-500">{summaryError}</p>
        ) : summary ? (
          <>
            <div className="flex flex-wrap gap-8">
              <div>
                <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">Total questions</span>
                <p className="text-2xl font-black text-slate-900 dark:text-white mt-0.5">{summary.total}</p>
              </div>
              <div>
                <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">Flagged</span>
                <p className="text-2xl font-black text-amber-600 dark:text-amber-400 mt-0.5">{summary.flagged}</p>
              </div>
            </div>

            <div className="space-y-2">
              {bloomLevels.map((lvl) => {
                const count = levelCounts[lvl.key] || 0;
                const colors = getBloomColor(lvl.key);
                return (
                  <div key={lvl.key} className="flex items-center gap-3">
                    <span className="w-24 text-[10px] font-bold text-slate-500 dark:text-slate-400 shrink-0">{lvl.label}</span>
                    <div className="flex-1 bg-slate-100 dark:bg-slate-800 h-2 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${colors.bar}`}
                        style={{ width: `${(count / maxLevelCount) * 100}%` }}
                      />
                    </div>
                    <span className="w-6 text-right text-[10px] font-bold text-slate-400 dark:text-slate-500">{count}</span>
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <p className="text-xs text-slate-400 dark:text-slate-500">Loading summary…</p>
        )}
      </div>

      {/* Filters */}
      <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-4">
        <div className="flex flex-wrap gap-2">
          {bloomLevels.map((lvl) => {
            const active = selectedLevels.includes(lvl.key);
            const colors = getBloomColor(lvl.key);
            return (
              <button
                key={lvl.key}
                type="button"
                onClick={() => toggleLevelFilter(lvl.key)}
                className={`px-3 py-1.5 rounded-full text-[10px] font-bold border transition-all ${
                  active
                    ? colors.solid
                    : 'border-slate-200 dark:border-slate-800 text-slate-400 dark:text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-800/30'
                }`}
              >
                {lvl.label}
              </button>
            );
          })}
          <button
            type="button"
            onClick={handleFlaggedOnlyToggle}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-bold border transition-all ${
              flaggedOnly
                ? 'bg-amber-600 text-white border-amber-600'
                : 'border-slate-200 dark:border-slate-800 text-slate-400 dark:text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-800/30'
            }`}
          >
            <AlertCircle size={12} />
            Flagged only
          </button>
        </div>

        <div className="relative max-w-sm">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            placeholder="Search by topic..."
            value={topicInput}
            onChange={handleTopicInputChange}
            className="w-full pl-9 pr-4 py-2.5 rounded-xl text-xs bg-slate-50 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-800 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-500/10 focus:border-violet-500 transition-all"
          />
        </div>
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
          <span className="text-xs font-bold text-slate-500 dark:text-slate-400">Loading your question bank...</span>
        </div>
      ) : summary && summary.total === 0 ? (
        <div className="p-8 rounded-[28px] border-2 border-dashed border-slate-200 dark:border-slate-800/80 text-center max-w-md mx-auto">
          <Database size={36} className="text-slate-400 dark:text-slate-600 mx-auto mb-3" />
          <p className="text-[11px] text-slate-500 dark:text-slate-400 leading-relaxed">
            No questions yet — generate a quiz on the{' '}
            <Link to="/tools?tab=quiz" className="font-bold text-violet-600 dark:text-violet-400 hover:underline">
              AI Tools
            </Link>{' '}
            page and every question is saved here.
          </p>
        </div>
      ) : items.length === 0 ? (
        <div className="p-8 rounded-[28px] border-2 border-dashed border-slate-200 dark:border-slate-800/80 text-center max-w-md mx-auto">
          <p className="text-[11px] text-slate-500 dark:text-slate-400">No questions match these filters.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {items.map((item) => (
            <QuestionRow
              key={item.question_id}
              item={item}
              bloomLevels={bloomLevels}
              onUpdated={handleItemUpdated}
              onDeleted={handleItemDeleted}
            />
          ))}

          {items.length < total && (
            <div className="flex justify-center pt-2">
              <button
                type="button"
                onClick={handleLoadMore}
                disabled={loadingMore}
                className="px-5 py-2.5 rounded-xl bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 text-xs font-bold hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors disabled:opacity-50"
              >
                {loadingMore ? 'Loading...' : `Load more (${total - items.length} remaining)`}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

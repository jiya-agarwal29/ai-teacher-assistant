import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertCircle, Plus, Trash2, Loader2, ArrowLeft, Sparkles } from 'lucide-react';
import { api } from '../services/api';
import useBloomLevels from '../hooks/useBloomLevels';
import { getBloomColor } from '../utils/bloomColors';
import PaperPlanPreview from '../components/PaperPlanPreview';

const PRESET_PATTERN_KEYS = ['mid_sem_50', 'end_sem_100', 'mcq_30'];

const BLOOM_PRESET_LABELS = {
  balanced: 'Balanced',
  basics: 'Mostly basics',
  higher_order: 'Higher-order',
  mcq_friendly: 'MCQ-friendly'
};

const CHOICE_LABELS = {
  none: 'Answer all',
  any_n: 'Attempt N of',
  either_or: 'Either / or'
};

const inputClass = 'w-full px-3 py-2 rounded-xl text-xs bg-slate-50 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-800 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-500/10 focus:border-violet-500 transition-all';

function patternTotalMarks(pattern) {
  return pattern.sections.reduce((sum, s) => sum + s.num_questions * s.marks_each, 0);
}

function customSectionsTotalMarks(sections) {
  return sections.reduce((sum, s) => sum + (Number(s.num_questions) || 0) * (Number(s.marks_each) || 0), 0);
}

function sectionKeyFor(idx) {
  return String.fromCharCode(65 + idx);
}

function instructionsFor(section) {
  if (section.choice === 'any_n') {
    return `Attempt any ${section.attempt || 1} of the following ${section.num_questions} questions.`;
  }
  if (section.choice === 'either_or') {
    return 'Answer EITHER (a) OR (b) for each question.';
  }
  return 'Answer all questions.';
}

function defaultCustomSection(idx) {
  return {
    title: `Section ${sectionKeyFor(idx)}`,
    num_questions: 5,
    marks_each: 2,
    choice: 'none',
    attempt: 4
  };
}

export default function PaperNew() {
  const navigate = useNavigate();
  const { levels: bloomLevels } = useBloomLevels();

  const [step, setStep] = useState('settings'); // 'settings' | 'preview'

  const [title, setTitle] = useState('');
  const [subject, setSubject] = useState('');

  const [patternChoice, setPatternChoice] = useState('mid_sem_50');
  const [customSections, setCustomSections] = useState([defaultCustomSection(0), defaultCustomSection(1)]);

  const [bloomChoice, setBloomChoice] = useState('balanced');
  const [customBloomTargets, setCustomBloomTargets] = useState({});

  const [books, setBooks] = useState([]);
  const [bookIds, setBookIds] = useState([]);
  const [booksLoading, setBooksLoading] = useState(true);

  const [patternsData, setPatternsData] = useState(null);
  const [patternsError, setPatternsError] = useState('');

  const [formError, setFormError] = useState('');
  const [creating, setCreating] = useState(false);
  const [paperData, setPaperData] = useState(null);

  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState('');

  useEffect(() => {
    let ignore = false;
    (async () => {
      try {
        const data = await api.papers.patterns();
        if (!ignore) setPatternsData(data);
      } catch (err) {
        if (!ignore) setPatternsError(err.message || 'Could not load paper patterns.');
      }
    })();
    return () => { ignore = true; };
  }, []);

  useEffect(() => {
    let ignore = false;
    (async () => {
      try {
        const data = await api.books.getAll();
        if (!ignore) setBooks((data || []).filter((b) => b.status === 'ready'));
      } catch {
        // Document list failing to load just leaves the checklist empty;
        // the "at least one ready document" validation below still applies.
      } finally {
        if (!ignore) setBooksLoading(false);
      }
    })();
    return () => { ignore = true; };
  }, []);

  const toggleBook = (id) => {
    setBookIds((prev) => (prev.includes(id) ? prev.filter((b) => b !== id) : [...prev, id]));
  };

  const addCustomSection = () => {
    setCustomSections((prev) => [...prev, defaultCustomSection(prev.length)]);
  };

  const removeCustomSection = (idx) => {
    setCustomSections((prev) => prev.filter((_, i) => i !== idx));
  };

  const updateCustomSection = (idx, field, value) => {
    setCustomSections((prev) => prev.map((s, i) => (i === idx ? { ...s, [field]: value } : s)));
  };

  const customBloomTotal = Object.values(customBloomTargets).reduce((sum, v) => sum + (Number(v) || 0), 0);

  const handlePreview = async (e) => {
    e.preventDefault();
    setFormError('');

    if (!title.trim()) {
      setFormError('Give this paper a title.');
      return;
    }
    if (bookIds.length === 0) {
      setFormError('Select at least one document.');
      return;
    }
    if (patternChoice === 'custom') {
      if (customSections.length === 0) {
        setFormError('Add at least one section to your custom pattern.');
        return;
      }
      for (const s of customSections) {
        if (!(Number(s.num_questions) > 0) || !(Number(s.marks_each) > 0)) {
          setFormError('Every section needs a positive number of questions and marks each.');
          return;
        }
        if (s.choice === 'any_n' && !(Number(s.attempt) > 0)) {
          setFormError('"Attempt N of" sections need a positive N.');
          return;
        }
      }
    }
    if (bloomChoice === 'custom' && Math.round(customBloomTotal) !== 100) {
      setFormError("Custom Bloom's mix must add up to exactly 100.");
      return;
    }

    const body = { title: title.trim(), book_ids: bookIds };
    if (subject.trim()) body.subject = subject.trim();

    if (patternChoice === 'custom') {
      body.pattern = {
        key: 'custom',
        name: title.trim() || 'Custom Pattern',
        duration_minutes: 60,
        paper_kind: 'written',
        sections: customSections.map((s, idx) => ({
          key: sectionKeyFor(idx),
          title: s.title || `Section ${sectionKeyFor(idx)}`,
          instructions: instructionsFor(s),
          marks_each: Number(s.marks_each),
          num_questions: Number(s.num_questions),
          choice: s.choice,
          attempt: s.choice === 'any_n' ? Number(s.attempt) : null
        }))
      };
    } else {
      body.pattern_key = patternChoice;
    }

    if (bloomChoice === 'custom') {
      body.bloom_targets = customBloomTargets;
    } else {
      body.bloom_preset = bloomChoice;
    }

    setCreating(true);
    try {
      const data = await api.papers.create(body);
      setPaperData(data);
      setStep('preview');
    } catch (err) {
      setFormError(err.message || 'Could not plan this paper.');
    } finally {
      setCreating(false);
    }
  };

  const handleBackToSettings = async () => {
    if (paperData?.id) {
      try {
        await api.papers.remove(paperData.id);
      } catch {
        // Best-effort -- the draft is harmless to leave behind if this fails.
      }
    }
    setPaperData(null);
    setGenerateError('');
    setStep('settings');
  };

  const handleGeneratePaper = async () => {
    if (!paperData?.id) return;
    setGenerateError('');
    setGenerating(true);
    try {
      await api.papers.generate(paperData.id);
      navigate(`/papers/${paperData.id}`);
    } catch (err) {
      setGenerateError(err.message || 'Could not start generation.');
      setGenerating(false);
    }
  };

  const documentNames = Object.fromEntries(books.map((b) => [String(b.id), b.name]));

  if (step === 'preview' && paperData) {
    return (
      <div className="space-y-8 animate-fade-in text-slate-800 dark:text-slate-100">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={handleBackToSettings}
            className="p-2 rounded-xl text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          >
            <ArrowLeft size={18} />
          </button>
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight text-slate-900 dark:text-white">{paperData.title}</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 font-medium">
              Review the plan before any question is actually written.
            </p>
          </div>
        </div>

        {generateError && (
          <div className="flex items-start gap-3 p-4 rounded-2xl bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-xs font-medium">
            <AlertCircle size={16} className="shrink-0 mt-0.5" />
            <span>{generateError}</span>
          </div>
        )}

        <PaperPlanPreview
          paperData={paperData}
          documentNames={documentNames}
          actions={(
            <>
              <button
                type="button"
                onClick={handleBackToSettings}
                className="px-4 py-2.5 rounded-xl text-xs font-bold bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"
              >
                Back to settings
              </button>
              <button
                type="button"
                onClick={handleGeneratePaper}
                disabled={generating}
                className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-xs font-bold bg-gradient-to-tr from-violet-600 to-indigo-500 text-white hover:scale-[1.01] transition-all disabled:opacity-50"
              >
                {generating ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                {generating ? 'Starting...' : 'Generate paper'}
              </button>
            </>
          )}
        />
      </div>
    );
  }

  return (
    <div className="space-y-8 animate-fade-in text-slate-800 dark:text-slate-100 max-w-4xl">
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white">New question paper</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400 mt-2 font-medium">
          Choose a pattern, pick documents, and set a Bloom's mix -- then preview the plan before anything is generated.
        </p>
      </div>

      <form onSubmit={handlePreview} className="space-y-8">
        {/* a. Title / subject */}
        <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-4">
          <div>
            <label className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider block mb-1.5">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Mid-Semester Examination"
              className={inputClass}
            />
          </div>
          <div>
            <label className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider block mb-1.5">Subject (optional)</label>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="e.g. Computer Networks"
              className={inputClass}
            />
          </div>
        </div>

        {/* b. Pattern */}
        <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-4">
          <h3 className="text-xs font-bold text-slate-900 dark:text-white">Pattern</h3>

          {patternsError && <p className="text-[10px] font-semibold text-rose-500">{patternsError}</p>}

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {patternsData && PRESET_PATTERN_KEYS.map((key) => {
              const preset = patternsData.patterns.find((p) => p.key === key);
              if (!preset) return null;
              const active = patternChoice === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setPatternChoice(key)}
                  className={`text-left p-4 rounded-2xl border-2 transition-all ${
                    active
                      ? 'border-violet-500 bg-violet-500/5 dark:bg-violet-500/10'
                      : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700'
                  }`}
                >
                  <p className="text-xs font-bold text-slate-800 dark:text-white">{preset.name}</p>
                  <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1">
                    {patternTotalMarks(preset)} marks · {preset.duration_minutes} min
                  </p>
                </button>
              );
            })}
            <button
              type="button"
              onClick={() => setPatternChoice('custom')}
              className={`text-left p-4 rounded-2xl border-2 transition-all ${
                patternChoice === 'custom'
                  ? 'border-violet-500 bg-violet-500/5 dark:bg-violet-500/10'
                  : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700'
              }`}
            >
              <p className="text-xs font-bold text-slate-800 dark:text-white">Custom</p>
              <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1">Build your own sections</p>
            </button>
          </div>

          {patternChoice === 'custom' && (
            <div className="space-y-3 pt-2">
              {customSections.map((s, idx) => (
                <div key={idx} className="p-4 rounded-2xl bg-slate-50 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-800 space-y-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500">Section {sectionKeyFor(idx)}</span>
                    <button
                      type="button"
                      onClick={() => removeCustomSection(idx)}
                      className="p-1.5 rounded-lg text-slate-400 hover:text-rose-500 hover:bg-rose-500/10 transition-colors"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5">
                    <input
                      type="text"
                      value={s.title}
                      onChange={(e) => updateCustomSection(idx, 'title', e.target.value)}
                      placeholder="Title"
                      className={`${inputClass} col-span-2`}
                    />
                    <input
                      type="number"
                      min={1}
                      value={s.num_questions}
                      onChange={(e) => updateCustomSection(idx, 'num_questions', e.target.value)}
                      placeholder="# questions"
                      className={inputClass}
                      title="Number of questions"
                    />
                    <input
                      type="number"
                      min={1}
                      value={s.marks_each}
                      onChange={(e) => updateCustomSection(idx, 'marks_each', e.target.value)}
                      placeholder="Marks each"
                      className={inputClass}
                      title="Marks each"
                    />
                    <select
                      value={s.choice}
                      onChange={(e) => updateCustomSection(idx, 'choice', e.target.value)}
                      className={inputClass}
                    >
                      {Object.entries(CHOICE_LABELS).map(([key, label]) => (
                        <option key={key} value={key}>{label}</option>
                      ))}
                    </select>
                  </div>
                  {s.choice === 'any_n' && (
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500">Attempt</span>
                      <input
                        type="number"
                        min={1}
                        max={s.num_questions}
                        value={s.attempt}
                        onChange={(e) => updateCustomSection(idx, 'attempt', e.target.value)}
                        className={`${inputClass} w-20`}
                      />
                      <span className="text-[10px] text-slate-400 dark:text-slate-500">of {s.num_questions}</span>
                    </div>
                  )}
                </div>
              ))}
              <div className="flex items-center justify-between">
                <button
                  type="button"
                  onClick={addCustomSection}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"
                >
                  <Plus size={12} /> Add section
                </button>
                <span className="text-[11px] font-bold text-slate-500 dark:text-slate-400">
                  Total: {customSectionsTotalMarks(customSections)} marks
                </span>
              </div>
            </div>
          )}
        </div>

        {/* c. Documents */}
        <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-3">
          <h3 className="text-xs font-bold text-slate-900 dark:text-white">Documents</h3>
          {booksLoading ? (
            <p className="text-[11px] text-slate-400 dark:text-slate-500">Loading your documents...</p>
          ) : books.length === 0 ? (
            <p className="text-[11px] text-slate-400 dark:text-slate-500">
              No ready documents yet -- upload and finish processing one on the Documents page first.
            </p>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {books.map((b) => (
                <label
                  key={b.id}
                  className="flex items-center gap-2.5 p-3 rounded-xl border border-slate-200 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/30 cursor-pointer transition-colors"
                >
                  <input
                    type="checkbox"
                    checked={bookIds.includes(b.id)}
                    onChange={() => toggleBook(b.id)}
                    className="rounded border-slate-300 dark:border-slate-700 text-violet-600 focus:ring-violet-500 focus:ring-offset-0"
                  />
                  <span className="text-[11px] font-semibold text-slate-700 dark:text-slate-200 truncate">{b.name}</span>
                </label>
              ))}
            </div>
          )}
        </div>

        {/* d. Bloom's mix */}
        <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-4">
          <h3 className="text-xs font-bold text-slate-900 dark:text-white">Bloom&apos;s mix</h3>
          <div className="flex flex-wrap gap-2">
            {patternsData && Object.keys(patternsData.bloom_presets).map((key) => {
              const active = bloomChoice === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setBloomChoice(key)}
                  className={`px-3.5 py-1.5 rounded-full text-[10px] font-bold border transition-all ${
                    active
                      ? 'bg-violet-600 text-white border-violet-600'
                      : 'border-slate-200 dark:border-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800/30'
                  }`}
                >
                  {BLOOM_PRESET_LABELS[key] || key}
                </button>
              );
            })}
            <button
              type="button"
              onClick={() => setBloomChoice('custom')}
              className={`px-3.5 py-1.5 rounded-full text-[10px] font-bold border transition-all ${
                bloomChoice === 'custom'
                  ? 'bg-violet-600 text-white border-violet-600'
                  : 'border-slate-200 dark:border-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800/30'
              }`}
            >
              Custom
            </button>
          </div>

          {bloomChoice === 'custom' && (
            <div className="space-y-3 pt-2">
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                {bloomLevels.map((lvl) => {
                  const colors = getBloomColor(lvl.key);
                  return (
                    <div key={lvl.key} className="space-y-1">
                      <span className={`inline-block px-2 py-0.5 rounded-full border text-[9px] font-bold ${colors.pill}`}>
                        {lvl.label}
                      </span>
                      <input
                        type="number"
                        min={0}
                        max={100}
                        value={customBloomTargets[lvl.key] ?? 0}
                        onChange={(e) => setCustomBloomTargets((prev) => ({ ...prev, [lvl.key]: e.target.value }))}
                        className={inputClass}
                      />
                    </div>
                  );
                })}
              </div>
              <p className={`text-[11px] font-bold ${Math.round(customBloomTotal) === 100 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-500'}`}>
                Total: {customBloomTotal}% {Math.round(customBloomTotal) !== 100 && '(must add up to 100)'}
              </p>
            </div>
          )}
        </div>

        {formError && (
          <div className="flex items-start gap-3 p-4 rounded-2xl bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-xs font-medium">
            <AlertCircle size={16} className="shrink-0 mt-0.5" />
            <span>{formError}</span>
          </div>
        )}

        <div className="flex justify-end">
          <button
            type="submit"
            disabled={creating}
            className="flex items-center gap-2 px-6 py-3 rounded-2xl bg-gradient-to-tr from-violet-600 to-indigo-500 text-white font-semibold text-xs tracking-tight hover:scale-[1.01] transition-all disabled:opacity-50"
          >
            {creating && <Loader2 size={14} className="animate-spin" />}
            {creating ? 'Planning...' : 'Preview plan'}
          </button>
        </div>
      </form>
    </div>
  );
}

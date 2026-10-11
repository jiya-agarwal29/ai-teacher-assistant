import { useState, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, userScopedKey, PENDING_DOCUMENTS_MESSAGE } from '../services/api';
import useBloomLevels from '../hooks/useBloomLevels';
import { getBloomColor } from '../utils/bloomColors';
import MessageWithDocsLink from '../components/MessageWithDocsLink';
import BloomBadge from '../components/BloomBadge';
import {
  Brain, 
  Search, 
  FileText, 
  HelpCircle, 
  Layers, 
  Sparkles, 
  BookOpen, 
  Check, 
  X, 
  AlertCircle,
  Copy,
  ChevronRight,
  BookMarked
} from 'lucide-react';

// Types the student grades themselves (no single correct letter/text to
// compare against) -- shared by isCorrectAnswer() and the analytics
// payload built on submit, so both agree on what "self_graded" means.
const SELF_GRADED_TYPES = ['short_answer', 'long_answer', 'scenario', 'viva', 'interview'];

// Shared by calculateScore() and the inline ✅/❌ display so both agree on
// what counts as a correct fill-in-the-blank answer. Ignores a leading
// article and trailing punctuation so "a process" / "process." / "The
// process" don't fail on formatting alone — still an exact match on the
// actual word(s), not fuzzy grading.
const normalizeFillBlankAnswer = (s) => (s || '')
  .trim()
  .toLowerCase()
  .replace(/^(a|an|the)\s+/i, '')
  .replace(/[.,!?;:'"]+$/, '');

// localStorage keys for the quiz builder's remembered Bloom-level/count choice.
const BLOOM_LEVELS_STORAGE_KEY = 'quiz_bloom_levels';
const BLOOM_COUNT_STORAGE_KEY = 'quiz_bloom_count';
const QUESTION_COUNT_OPTIONS = [6, 9, 12];

// sessionStorage key for the in-progress quiz itself (topic, questions,
// answers, submitted state) -- survives a refresh but not a tab close,
// and is cleared on logout/"New Quiz"/generating a different quiz.
const QUIZ_SESSION_STORAGE_KEY = 'ai_tools_quiz_state';

function readPersistedQuizState() {
  try {
    const raw = sessionStorage.getItem(userScopedKey(QUIZ_SESSION_STORAGE_KEY));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function clearPersistedQuizState() {
  try {
    sessionStorage.removeItem(userScopedKey(QUIZ_SESSION_STORAGE_KEY));
  } catch {
    // sessionStorage unavailable (private mode quota, etc.) -- not fatal.
  }
}

export default function AITools() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get('tab') || 'quiz';
  
  const handleTabChange = (tabName) => {
    setSearchParams({ tab: tabName });
    setQuizResult(null);
    setQuizQuestions([]);
    setQuizWarnings([]);
    setSemanticResult(null);
    setSummarizerResult(null);
    setFlashcardMessage('');
    setTutorResult('');
    setError('');
  };

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  // -----------------------------
  // TOOL 1: QUIZ GENERATOR STATE
  // -----------------------------
  // Read once, at mount, so a page refresh restores the quiz in progress
  // (topic, questions, answers, submitted state) instead of losing it.
  const [restoredQuiz] = useState(() => readPersistedQuizState());

  const [quizTopic, setQuizTopic] = useState(restoredQuiz?.topic || '');
  const [topicError, setTopicError] = useState('');
  const topicInputRef = useRef(null);
  const [quizResult, setQuizResult] = useState(restoredQuiz?.quizResult ?? null);
  const [quizQuestions, setQuizQuestions] = useState(restoredQuiz?.quizQuestions || []);
  const [quizWarnings, setQuizWarnings] = useState(restoredQuiz?.quizWarnings || []); // e.g. "Could not create enough Analyze questions..."
  const [selectedAnswers, setSelectedAnswers] = useState(restoredQuiz?.selectedAnswers || {}); // { questionId: selectedOptionLetter }
  const [fillBlankAnswers, setFillBlankAnswers] = useState(restoredQuiz?.fillBlankAnswers || {}); // { questionId: string }
  const [shortAnswers, setShortAnswers] = useState(restoredQuiz?.shortAnswers || {}); // { questionId: string }
  const [shortAnswerGrades, setShortAnswerGrades] = useState(restoredQuiz?.shortAnswerGrades || {}); // { questionId: 'correct' | 'incorrect' }
  const [submittedQuiz, setSubmittedQuiz] = useState(restoredQuiz?.submittedQuiz || false);

  // Persist the quiz-in-progress on every change, and clear it once there's
  // nothing worth restoring (no topic typed, no quiz generated).
  useEffect(() => {
    if (!quizTopic.trim() && quizQuestions.length === 0) {
      clearPersistedQuizState();
      return;
    }
    try {
      sessionStorage.setItem(
        userScopedKey(QUIZ_SESSION_STORAGE_KEY),
        JSON.stringify({
          topic: quizTopic,
          quizResult,
          quizQuestions,
          quizWarnings,
          selectedAnswers,
          fillBlankAnswers,
          shortAnswers,
          shortAnswerGrades,
          submittedQuiz
        })
      );
    } catch {
      // sessionStorage unavailable -- nothing to do about it here.
    }
  }, [quizTopic, quizResult, quizQuestions, quizWarnings, selectedAnswers, fillBlankAnswers, shortAnswers, shortAnswerGrades, submittedQuiz]);

  // Logout clears the in-progress quiz too -- 'before-logout' fires while
  // the current username (and so the right sessionStorage key) still
  // resolves, same contract useDraftPersistence.js relies on.
  useEffect(() => {
    const handleBeforeLogout = () => clearPersistedQuizState();
    window.addEventListener('before-logout', handleBeforeLogout);
    return () => window.removeEventListener('before-logout', handleBeforeLogout);
  }, []);

  const handleNewQuiz = () => {
    setQuizTopic('');
    setTopicError('');
    setQuizResult(null);
    setQuizQuestions([]);
    setQuizWarnings([]);
    setSelectedAnswers({});
    setFillBlankAnswers({});
    setShortAnswers({});
    setShortAnswerGrades({});
    setSubmittedQuiz(false);
    clearPersistedQuizState();
  };

  // Bloom's level picker + question count, remembered per user.
  const { levels: bloomLevels, byKey: bloomLevelsByKey } = useBloomLevels();
  const [selectedLevels, setSelectedLevels] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(userScopedKey(BLOOM_LEVELS_STORAGE_KEY)) || 'null');
      if (Array.isArray(saved) && saved.length > 0) return saved;
    } catch {
      // Malformed saved value -- fall through to the "all levels" default below.
    }
    return null; // null = not decided yet; defaults to all once bloomLevels loads
  });
  const [questionCount, setQuestionCount] = useState(() => {
    const saved = parseInt(localStorage.getItem(userScopedKey(BLOOM_COUNT_STORAGE_KEY)) || '', 10);
    return QUESTION_COUNT_OPTIONS.includes(saved) ? saved : 6;
  });

  // All six levels are on by default -- but the actual key list comes from
  // the backend (bloomLevels), never hard-coded here. Derived at render
  // time (no effect needed) so it's correct the instant bloomLevels loads,
  // whether or not the user has ever saved a preference of their own.
  const effectiveLevels = selectedLevels !== null ? selectedLevels : bloomLevels.map((lvl) => lvl.key);

  useEffect(() => {
    if (selectedLevels && selectedLevels.length > 0) {
      localStorage.setItem(userScopedKey(BLOOM_LEVELS_STORAGE_KEY), JSON.stringify(selectedLevels));
    }
  }, [selectedLevels]);

  useEffect(() => {
    localStorage.setItem(userScopedKey(BLOOM_COUNT_STORAGE_KEY), String(questionCount));
  }, [questionCount]);

  const toggleLevel = (key) => {
    setSelectedLevels((prev) => {
      const current = prev !== null ? prev : bloomLevels.map((lvl) => lvl.key);
      if (current.includes(key)) {
        if (current.length === 1) return current; // at least one must stay selected
        return current.filter((k) => k !== key);
      }
      return [...current, key];
    });
  };

  const selectAllLevels = () => setSelectedLevels(bloomLevels.map((lvl) => lvl.key));
  const selectHigherOrderLevels = () => {
    const higherOrder = bloomLevels.filter((lvl) => lvl.order >= 3).map((lvl) => lvl.key);
    if (higherOrder.length > 0) setSelectedLevels(higherOrder);
  };

  // Shared by calculateScore(), the per-question result display, and the
  // "Score by level" breakdown, so all three always agree on what counts
  // as correct for a given question type.
  const isCorrectAnswer = (q) => {
    if (q.type === 'mcq' || q.type === 'true_false' || !q.type) {
      return selectedAnswers[q.id] === q.correctAnswer;
    } else if (q.type === 'fill_blank') {
      return normalizeFillBlankAnswer(fillBlankAnswers[q.id]) === normalizeFillBlankAnswer(q.correctAnswer);
    } else if (SELF_GRADED_TYPES.includes(q.type)) {
      return shortAnswerGrades[q.id] === 'correct';
    }
    return false;
  };

  const calculateScore = () => {
    let correctCount = 0;
    const totalCount = quizQuestions.length;

    quizQuestions.forEach((q) => {
      if (isCorrectAnswer(q)) correctCount++;
    });

    const percentage = totalCount > 0 ? Math.round((correctCount / totalCount) * 100) : 0;
    return { correctCount, totalCount, percentage };
  };

  // Per Bloom-level correct/total for the levels actually present in this
  // quiz (an AI-generated quiz tags every question with bloom_level; a
  // quiz parsed from raw fallback text has none, so it returns {}).
  const getScoreByLevel = () => {
    const byLevel = {};
    quizQuestions.forEach((q) => {
      if (!q.bloom_level) return;
      const existing = byLevel[q.bloom_level] || { correct: 0, total: 0 };
      existing.total += 1;
      if (isCorrectAnswer(q)) existing.correct += 1;
      byLevel[q.bloom_level] = existing;
    });
    return byLevel;
  };


  // Quiz parsing helper
  const parseQuizText = (rawText) => {
    const parsedQuestions = [];
    try {
      const qBlocks = rawText.split(/Q\d+[:?]/gi);
      for (let i = 1; i < qBlocks.length; i++) {
        const block = qBlocks[i];
        const lines = block.split('\n').map(l => l.trim()).filter(Boolean);
        if (lines.length < 2) continue;

        const questionText = lines[0];
        const options = [];
        let correctAnswer = '';
        let explanation = '';

        for (const line of lines) {
          if (/^[A-D]\.\s/i.test(line)) {
            options.push(line.replace(/^[A-D]\.\s/i, '').trim());
          } else if (line.toLowerCase().startsWith('correct answer:') || line.toLowerCase().startsWith('correct:')) {
            correctAnswer = line.replace(/^(correct answer:|correct:)\s*/i, '').trim();
          } else if (line.toLowerCase().startsWith('explanation:')) {
            explanation = line.replace(/^explanation:\s*/i, '').trim();
          }
        }

        // Exact regex fallback if options empty
        if (options.length === 0) {
          const optA = block.match(/A\.\s*([^\n]+)/i);
          const optB = block.match(/B\.\s*([^\n]+)/i);
          const optC = block.match(/C\.\s*([^\n]+)/i);
          const optD = block.match(/D\.\s*([^\n]+)/i);
          if (optA) options.push(optA[1].trim());
          if (optB) options.push(optB[1].trim());
          if (optC) options.push(optC[1].trim());
          if (optD) options.push(optD[1].trim());
        }

        if (!correctAnswer) {
          const correctMatch = block.match(/correct answer:\s*([A-D])/i) || block.match(/correct:\s*([A-D])/i);
          if (correctMatch) correctAnswer = correctMatch[1].toUpperCase();
        }
        
        // Clean correct answer to be single letter
        if (correctAnswer && correctAnswer.length > 1) {
          const letterMatch = correctAnswer.match(/([A-D])/i);
          if (letterMatch) correctAnswer = letterMatch[1].toUpperCase();
        }

        if (!explanation) {
          const expMatch = block.match(/explanation:\s*([^\n]+)/i);
          if (expMatch) explanation = expMatch[1].trim();
        }

        if (questionText && options.length >= 2) {
          parsedQuestions.push({
            id: i,
            question: questionText,
            options,
            correctAnswer: correctAnswer || 'A',
            explanation: explanation || 'Context verified in vector database.'
          });
        }
      }
    } catch (err) {
      console.error(err);
    }
    return parsedQuestions;
  };

  const handleGenerateQuiz = async (e) => {
    e.preventDefault();
    if (!quizTopic.trim()) {
      setTopicError('Please enter a topic first');
      topicInputRef.current?.focus();
      return;
    }
    setTopicError('');

    setIsLoading(true);
    setQuizResult(null);
    setQuizQuestions([]);
    setQuizWarnings([]);
    setSelectedAnswers({});
    setFillBlankAnswers({});
    setShortAnswers({});
    setShortAnswerGrades({});
    setSubmittedQuiz(false);
    setError('');

    try {
      const res = await api.quiz.generate(quizTopic, { levels: effectiveLevels, count: questionCount });
      setQuizResult(res.quiz);
      setQuizWarnings(res.warnings || []);

      if (res.questions && res.questions.length > 0) {
        setQuizQuestions(res.questions);
      } else {
        const parsed = parseQuizText(res.quiz);
        setQuizQuestions(parsed);
      }
    } catch (err) {
      setError(err.message || 'Failed to generate quiz. Is the backend running?');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSelectOption = (qId, optionIdx) => {
    if (submittedQuiz) return;
    const optionLetter = ['A', 'B', 'C', 'D'][optionIdx];
    setSelectedAnswers({
      ...selectedAnswers,
      [qId]: optionLetter
    });
  };

  // -----------------------------
  // TOOL 2: SEMANTIC SEARCH STATE
  // -----------------------------
  const [semanticQuery, setSemanticQuery] = useState('');
  const [semanticResult, setSemanticResult] = useState(null);

  const handleSemanticSearch = async (e) => {
    e.preventDefault();
    if (!semanticQuery.trim()) return;

    setIsLoading(true);
    setSemanticResult(null);
    setError('');

    try {
      const res = await api.search.semantic(semanticQuery);
      setSemanticResult(res);
    } catch (err) {
      setError(err.message || 'Semantic search failed.');
    } finally {
      setIsLoading(false);
    }
  };

  // -----------------------------
  // TOOL 3: NOTES SUMMARIZER STATE
  // -----------------------------
  const [summarizerText, setSummarizerText] = useState('');
  const [summarizerResult, setSummarizerResult] = useState(null);

  const handleSummarize = async (e) => {
    e.preventDefault();
    if (!summarizerText.trim()) return;

    setIsLoading(true);
    setSummarizerResult(null);
    setError('');

    try {
      const res = await api.tools.summarize(summarizerText);
      setSummarizerResult(res.bullets || []);
    } catch (err) {
      setError(err.message || 'Summarization failed.');
    } finally {
      setIsLoading(false);
    }
  };

  // -----------------------------
  // TOOL 4: FLASHCARDS STATE
  // -----------------------------
  const [flashcardTopic, setFlashcardTopic] = useState('');
  const [flashcards, setFlashcards] = useState([
    { id: 1, front: "ACID: Atomicity", back: "All parts of the database transaction must succeed, or the entire transaction is rolled back." },
    { id: 2, front: "ACID: Consistency", back: "A transaction must move the database from one valid state to another valid state, preserving constraints." },
    { id: 3, front: "ACID: Isolation", back: "Transactions executing concurrently must not interfere with each other's execution." },
    { id: 4, front: "ACID: Durability", back: "Once a transaction is committed, its changes are permanently recorded in the database, even during a crash." },
  ]);
  const [flippedCards, setFlippedCards] = useState({}); // { cardId: boolean }

  const handleFlipCard = (id) => {
    setFlippedCards({
      ...flippedCards,
      [id]: !flippedCards[id]
    });
  };

  const [flashcardMessage, setFlashcardMessage] = useState('');

  const handleGenerateFlashcards = async (e) => {
    e.preventDefault();
    if (!flashcardTopic.trim()) return;

    setIsLoading(true);
    setError('');
    setFlashcardMessage('');

    try {
      const res = await api.tools.flashcards(flashcardTopic);
      const cards = (res.cards || []).map((c, idx) => ({
        id: Date.now() + idx,
        front: c.term,
        back: c.definition,
        source: c.source
      }));
      setFlashcards(cards);
      if (cards.length === 0) {
        setFlashcardMessage(res.message || 'No flashcards could be generated for this topic.');
      }
    } catch (err) {
      setError(err.message || 'Could not generate flashcards.');
    } finally {
      setIsLoading(false);
    }
  };

  // -----------------------------
  // TOOL 5: AI TUTOR STATE
  // -----------------------------
  const [tutorPrompt, setTutorPrompt] = useState('');
  const [tutorResult, setTutorResult] = useState('');

  const handleTutorSubmit = async (e) => {
    e.preventDefault();
    if (!tutorPrompt.trim()) return;

    setIsLoading(true);
    setTutorResult('');
    setError('');

    try {
      const res = await api.tools.tutor(tutorPrompt);
      setTutorResult(res.answer || res.message);
    } catch (err) {
      setError(err.message || 'Tutor session failed.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="space-y-8 animate-fade-in text-slate-800 dark:text-slate-100">
      
      {/* Header */}
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white">
          AI Educational Tools
        </h1>
        <p className="text-sm text-slate-500 dark:text-slate-400 mt-2 font-medium">
          Access high-fidelity tools connected to your semantic knowledge index.
        </p>
      </div>

      {/* Tabs Menu */}
      <div className="flex flex-wrap gap-2 border-b border-slate-200 dark:border-slate-800/80 pb-3">
        {[
          { id: 'quiz', name: 'Quiz Builder', icon: HelpCircle },
          { id: 'search', name: 'Semantic search', icon: Search },
          { id: 'summarize', name: 'Notes Summarizer', icon: FileText },
          { id: 'flashcards', name: 'Flashcards', icon: Layers },
          { id: 'tutor', name: 'AI Tutor', icon: Brain },
        ].map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => handleTabChange(tab.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold tracking-tight transition-all duration-200
                ${activeTab === tab.id 
                  ? 'bg-violet-600/10 text-violet-600 dark:bg-violet-500/20 dark:text-violet-400 border border-violet-600/25 dark:border-violet-400/30' 
                  : 'text-slate-500 hover:text-slate-800 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800/30'}`}
            >
              <Icon size={14} />
              {tab.name}
            </button>
          );
        })}
      </div>

      {error && (
        <div className="flex items-start gap-3 p-4 rounded-2xl bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-xs font-medium">
          <AlertCircle size={16} className="shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {/* Loading Overlay */}
      {isLoading && (
        <div className="flex flex-col items-center justify-center py-20">
          <div className="w-10 h-10 border-4 border-violet-500 border-t-transparent rounded-full animate-spin mb-4"></div>
          <span className="text-xs font-bold text-slate-700 dark:text-slate-300">Generating AI feedback...</span>
        </div>
      )}

      {!isLoading && (
        <div className="theme-transition">
          
          {/* TAB 1: QUIZ BUILDER */}
          {activeTab === 'quiz' && (
            <div className="space-y-6">
              <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm">
                <h3 className="text-xs font-bold text-slate-900 dark:text-white mb-3">Topic-based Quiz Generation</h3>
                <form onSubmit={handleGenerateQuiz} className="space-y-4">
                  <div>
                    <input
                      ref={topicInputRef}
                      type="text"
                      placeholder="e.g. Photosynthesis"
                      value={quizTopic}
                      onChange={(e) => {
                        setQuizTopic(e.target.value);
                        if (topicError) setTopicError('');
                      }}
                      className={`w-full px-4 py-3 rounded-2xl text-xs bg-slate-50 dark:bg-slate-800/40 border text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-500/10 transition-all ${
                        topicError
                          ? 'border-rose-400 dark:border-rose-500 focus:border-rose-500'
                          : 'border-slate-200 dark:border-slate-800 focus:border-violet-500'
                      }`}
                    />
                    {topicError && (
                      <p className="text-[10px] font-semibold text-rose-500 mt-1.5">{topicError}</p>
                    )}
                  </div>

                  {/* Bloom's level picker */}
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                        Bloom&apos;s levels
                      </span>
                      <div className="flex gap-1.5">
                        <button
                          type="button"
                          onClick={selectAllLevels}
                          className="px-2.5 py-1 rounded-lg text-[9px] font-bold bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"
                        >
                          All
                        </button>
                        <button
                          type="button"
                          onClick={selectHigherOrderLevels}
                          className="px-2.5 py-1 rounded-lg text-[9px] font-bold bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"
                        >
                          Higher-order only
                        </button>
                      </div>
                    </div>

                    <div className="flex flex-wrap gap-2">
                      {bloomLevels.map((lvl) => {
                        const active = effectiveLevels.includes(lvl.key);
                        const colors = getBloomColor(lvl.key);
                        return (
                          <button
                            key={lvl.key}
                            type="button"
                            title={lvl.description}
                            onClick={() => toggleLevel(lvl.key)}
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
                    </div>

                    <p className="text-[9px] text-slate-400 dark:text-slate-500">
                      Remember &amp; Understand check memory; Apply to Create check thinking.
                    </p>
                  </div>

                  {/* Number of questions */}
                  <div className="flex items-center gap-3">
                    <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                      Number of questions
                    </span>
                    <div className="flex gap-1.5">
                      {QUESTION_COUNT_OPTIONS.map((n) => (
                        <button
                          key={n}
                          type="button"
                          onClick={() => setQuestionCount(n)}
                          className={`w-9 py-1 rounded-lg text-[10px] font-bold border transition-all ${
                            questionCount === n
                              ? 'bg-violet-600 text-white border-violet-600'
                              : 'border-slate-200 dark:border-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800/30'
                          }`}
                        >
                          {n}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="flex justify-end">
                    <button
                      type="submit"
                      className="px-5 py-3 rounded-2xl bg-gradient-to-tr from-violet-600 to-indigo-500 text-white font-semibold text-xs tracking-tight hover:scale-[1.01] transition-all"
                    >
                      Build Test
                    </button>
                  </div>
                </form>
              </div>

              {/* Generation warnings, e.g. a requested level came up short */}
              {quizWarnings.length > 0 && (
                <div className="space-y-2 animate-fadeIn">
                  {quizWarnings.map((warning, idx) => (
                    <div
                      key={idx}
                      className="flex items-start gap-3 p-3.5 rounded-2xl bg-amber-500/10 border border-amber-500/20 text-amber-700 dark:text-amber-400 text-[11px] font-medium"
                    >
                      <AlertCircle size={14} className="shrink-0 mt-0.5" />
                      <span>{warning}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Quiz interactive display */}
              {quizQuestions.length > 0 ? (
                <div className="space-y-6 animate-fadeIn">
                  <div className="flex justify-between items-center bg-slate-100 dark:bg-slate-800/40 p-4 rounded-2xl">
                    <span className="text-xs font-bold">Generated Quiz for: "{quizTopic}"</span>
                    <div className="flex items-center gap-2">
                      {!submittedQuiz ? (
                        <button
                          onClick={() => {
                            const result = calculateScore();
                            if (result.totalCount > 0) {
                              // Fire-and-forget -- a failed analytics call must
                              // never block the student from seeing their score.
                              api.analytics.quizAttempt({
                                topic: quizTopic,
                                answers: quizQuestions.map((q) => ({
                                  question_id: q.question_id,
                                  bloom_level: q.bloom_level || undefined,
                                  question_type: q.type || 'unknown',
                                  is_correct: isCorrectAnswer(q),
                                  self_graded: SELF_GRADED_TYPES.includes(q.type)
                                }))
                              }).catch(() => {});
                            }
                            setSubmittedQuiz(true);
                          }}
                          className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-xl text-xs font-bold hover:scale-[1.01] transition-all"
                        >
                          Submit Answers
                        </button>
                      ) : (
                        <button
                          onClick={() => {
                            setSubmittedQuiz(false);
                            setSelectedAnswers({});
                            setFillBlankAnswers({});
                            setShortAnswers({});
                            setShortAnswerGrades({});
                          }}
                          className="px-4 py-2 bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-200 rounded-xl text-xs font-bold transition-colors"
                        >
                          Retry / Reset
                        </button>
                      )}
                      <button
                        onClick={handleNewQuiz}
                        className="px-4 py-2 border border-slate-300 dark:border-slate-700 text-slate-600 dark:text-slate-300 rounded-xl text-xs font-bold hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors"
                      >
                        New Quiz
                      </button>
                    </div>
                  </div>

                  {/* PREMIUM SCORE TRACKING CARD */}
                  {submittedQuiz && (
                    <div className="p-6 rounded-[28px] bg-gradient-to-tr from-violet-600/10 via-indigo-600/5 to-slate-900/5 dark:from-violet-500/10 dark:via-indigo-500/5 dark:to-slate-900/30 border border-violet-500/20 shadow-sm flex flex-col md:flex-row items-center gap-6 animate-fadeIn">
                      {/* Circular Progress Display */}
                      <div className="relative w-24 h-24 flex items-center justify-center bg-violet-600/10 dark:bg-violet-500/10 rounded-full border border-violet-500/20 shadow-inner">
                        <div className="text-center">
                          <span className="text-2xl font-black text-violet-600 dark:text-violet-400">
                            {calculateScore().percentage}%
                          </span>
                          <p className="text-[8px] text-slate-400 dark:text-slate-500 uppercase font-bold tracking-wider">Score</p>
                        </div>
                      </div>

                      {/* Details & Feedback */}
                      <div className="flex-grow space-y-2 text-center md:text-left w-full">
                        <h4 className="text-sm font-bold text-slate-900 dark:text-white flex items-center justify-center md:justify-start gap-1.5">
                          <Sparkles size={16} className="text-violet-500" />
                          <span>Quiz Performance Summary</span>
                        </h4>
                        <p className="text-xs text-slate-600 dark:text-slate-400">
                          You got <span className="font-bold text-violet-600 dark:text-violet-400">{calculateScore().correctCount}</span> out of <span className="font-bold">{calculateScore().totalCount}</span> questions correct.
                        </p>
                        
                        {/* Custom visual progress bar */}
                        <div className="w-full bg-slate-200 dark:bg-slate-800 h-2 rounded-full overflow-hidden">
                          <div 
                            className="bg-gradient-to-r from-violet-500 to-indigo-500 h-full rounded-full transition-all duration-500"
                            style={{ width: `${calculateScore().percentage}%` }}
                          />
                        </div>

                        {/* Motivational message */}
                        <p className="text-[11px] font-medium text-slate-500 dark:text-slate-400">
                          {calculateScore().percentage === 100 && "🏆 Master Class! You answered every question perfectly!"}
                          {calculateScore().percentage >= 80 && calculateScore().percentage < 100 && "🌟 Outstanding! You have a solid grasp of this material."}
                          {calculateScore().percentage >= 50 && calculateScore().percentage < 80 && "👍 Good effort! Review the explanations to reinforce your knowledge."}
                          {calculateScore().percentage < 50 && "📚 Keep studying! Re-read the document segments and try again."}
                        </p>
                      </div>
                    </div>
                  )}

                  {/* SCORE BY BLOOM'S LEVEL -- only the levels present in this quiz */}
                  {submittedQuiz && Object.keys(getScoreByLevel()).length > 0 && (
                    <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-3 animate-fadeIn">
                      <h4 className="text-xs font-bold text-slate-900 dark:text-white">Score by level</h4>
                      <div className="space-y-2">
                        {bloomLevels
                          .filter((lvl) => getScoreByLevel()[lvl.key])
                          .map((lvl) => {
                            const { correct, total } = getScoreByLevel()[lvl.key];
                            const pct = total > 0 ? Math.round((correct / total) * 100) : 0;
                            return (
                              <div key={lvl.key} className="flex items-center justify-between gap-3">
                                <BloomBadge levelKey={lvl.key} />
                                <span className="text-[11px] font-bold text-slate-500 dark:text-slate-400">
                                  {correct}/{total} ({pct}%)
                                </span>
                              </div>
                            );
                          })}
                      </div>
                    </div>
                  )}

                  <div className="space-y-4">
                    {quizQuestions.map((q) => {
                      const isCorrect = isCorrectAnswer(q);

                      return (
                        <div 
                          key={q.id}
                          className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-4 animate-fadeIn"
                        >
                          <div className="flex justify-between items-start gap-2">
                            <h4 className="text-xs font-bold text-slate-950 dark:text-white">
                              Question {q.id}: {q.question}
                            </h4>
                            <div className="flex items-center gap-1.5 shrink-0">
                              {q.bloom_level && <BloomBadge levelKey={q.bloom_level} />}
                              {/* Question Type Badge */}
                              <span className="px-2.5 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 whitespace-nowrap">
                                {q.type === 'mcq' && 'MCQ'}
                                {q.type === 'true_false' && 'True / False'}
                                {q.type === 'fill_blank' && 'Fill in the Blank'}
                                {q.type === 'short_answer' && 'Short Answer'}
                                {q.type === 'long_answer' && 'Long Answer'}
                                {q.type === 'scenario' && 'Scenario-based'}
                                {q.type === 'viva' && 'Viva Question'}
                                {q.type === 'interview' && 'Interview Question'}
                                {!q.type && 'MCQ'}
                              </span>
                            </div>
                          </div>

                          {(q.bloom_check === 'flagged' || q.source) && (
                            <div className="flex flex-wrap items-center gap-3 -mt-2">
                              {q.bloom_check === 'flagged' && (
                                <span className="text-[9px] font-semibold text-amber-600 dark:text-amber-400">
                                  Level unsure — looks more like {bloomLevelsByKey[q.detected_level]?.label || q.detected_level}
                                </span>
                              )}
                              {q.source && (
                                <span className="text-[9px] font-semibold text-slate-400 dark:text-slate-500">
                                  Source: {q.source.book_name}{q.source.page_number != null ? `, p.${q.source.page_number}` : ''}
                                </span>
                              )}
                            </div>
                          )}

                          {/* 1. MCQ and True/False Rendering */}
                          {(q.type === 'mcq' || q.type === 'true_false' || !q.type) && (
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                              {q.options.map((opt, idx) => {
                                const letter = ['A', 'B', 'C', 'D'][idx];
                                const isSelected = selectedAnswers[q.id] === letter;
                                const isActualCorrect = q.correctAnswer === letter;
                                
                                let buttonStyles = "border-slate-200 dark:border-slate-800/80 hover:bg-slate-50 dark:hover:bg-slate-800/20";
                                if (isSelected) {
                                  buttonStyles = "bg-violet-600/10 border-violet-500 text-violet-600 dark:text-violet-400";
                                }
                                
                                if (submittedQuiz) {
                                  if (isActualCorrect) {
                                    buttonStyles = "bg-emerald-500/10 border-emerald-500 text-emerald-600 dark:text-emerald-400";
                                  } else if (isSelected && !isCorrect) {
                                    buttonStyles = "bg-rose-500/10 border-rose-500 text-rose-600 dark:text-rose-400";
                                  } else {
                                    buttonStyles = "opacity-50 border-slate-200 dark:border-slate-800";
                                  }
                                }

                                return (
                                  <button
                                    key={idx}
                                    onClick={() => handleSelectOption(q.id, idx)}
                                    disabled={submittedQuiz}
                                    className={`p-3.5 text-left text-xs font-semibold border rounded-2xl transition-all flex items-center justify-between ${buttonStyles}`}
                                  >
                                    <span>{letter}. {opt}</span>
                                    {submittedQuiz && isActualCorrect && <Check size={14} className="text-emerald-500" />}
                                    {submittedQuiz && isSelected && !isCorrect && <X size={14} className="text-rose-500" />}
                                  </button>
                                );
                              })}
                            </div>
                          )}

                          {/* 2. Fill in the Blank Rendering */}
                          {q.type === 'fill_blank' && (
                            <div className="space-y-3">
                              <input 
                                type="text" 
                                placeholder={submittedQuiz ? "No answer submitted" : "Type your answer here..."}
                                value={fillBlankAnswers[q.id] || ''}
                                onChange={(e) => setFillBlankAnswers({ ...fillBlankAnswers, [q.id]: e.target.value })}
                                disabled={submittedQuiz}
                                className="w-full max-w-md px-4 py-3 rounded-2xl text-xs bg-slate-50 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-800 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-500/10 focus:border-violet-500 transition-all font-medium"
                              />
                            </div>
                          )}

                          {/* 3. Open-ended / Self-graded Rendering */}
                          {['short_answer', 'long_answer', 'scenario', 'viva', 'interview'].includes(q.type) && (
                            <div className="space-y-3">
                              <textarea 
                                placeholder={submittedQuiz ? "No answer submitted" : "Write your explanation here..."}
                                rows={q.type === 'long_answer' || q.type === 'scenario' ? 5 : 3}
                                value={shortAnswers[q.id] || ''}
                                onChange={(e) => setShortAnswers({ ...shortAnswers, [q.id]: e.target.value })}
                                disabled={submittedQuiz}
                                className="w-full px-4 py-3 rounded-2xl text-xs bg-slate-50 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-800 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-500/10 focus:border-violet-500 transition-all resize-none font-medium"
                              />
                            </div>
                          )}

                          {/* Interactive Result Details */}
                          {submittedQuiz && (
                            <>
                              {/* MCQ & True/False feedback */}
                              {(q.type === 'mcq' || q.type === 'true_false' || !q.type) && (
                                <div className="p-4 rounded-2xl bg-slate-50 dark:bg-slate-800/40 text-[11px] leading-relaxed border border-slate-100 dark:border-slate-800 space-y-1.5 animate-fadeIn">
                                  <span className="font-bold text-slate-800 dark:text-slate-200">
                                    {isCorrect ? "✅ Correct" : `❌ Incorrect (Correct: Option ${q.correctAnswer})`}
                                  </span>
                                  <p className="text-slate-500 dark:text-slate-400">{q.explanation}</p>
                                </div>
                              )}

                              {/* Fill in Blank feedback */}
                              {q.type === 'fill_blank' && (
                                <div className="p-4 rounded-2xl bg-slate-50 dark:bg-slate-800/40 text-[11px] leading-relaxed border border-slate-100 dark:border-slate-800 space-y-1.5 animate-fadeIn">
                                  <span className="font-bold text-slate-800 dark:text-slate-200">
                                    {isCorrect ? "✅ Correct" : `❌ Incorrect (Correct answer: "${q.correctAnswer}")`}
                                  </span>
                                  <p className="text-slate-500 dark:text-slate-400">
                                    Your response: <span className="font-mono text-slate-800 dark:text-slate-200 bg-slate-200/50 dark:bg-slate-800/80 px-2 py-0.5 rounded font-bold">{fillBlankAnswers[q.id] || '(Empty)'}</span>
                                  </p>
                                  <p className="text-slate-500 dark:text-slate-400">{q.explanation}</p>
                                </div>
                              )}

                              {/* Open-ended / Self-graded feedback & self grading */}
                              {['short_answer', 'long_answer', 'scenario', 'viva', 'interview'].includes(q.type) && (
                                <div className="space-y-3 animate-fadeIn">
                                  <div className="p-4 rounded-2xl bg-slate-50 dark:bg-slate-800/40 text-[11px] leading-relaxed border border-slate-100 dark:border-slate-800 space-y-2.5">
                                    <div>
                                      <span className="font-bold text-slate-800 dark:text-slate-200">📋 Reference Solution:</span>
                                      <p className="text-slate-600 dark:text-slate-400 mt-1 italic font-medium font-mono">"{q.correctAnswer}"</p>
                                    </div>
                                    <div className="pt-2 border-t border-slate-200/50 dark:border-slate-800/50">
                                      <span className="font-bold text-slate-800 dark:text-slate-200">🔍 Explanation:</span>
                                      <p className="text-slate-500 dark:text-slate-400 mt-1">{q.explanation}</p>
                                    </div>
                                  </div>

                                  <div className="flex items-center gap-3">
                                    <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">Self-Grading:</span>
                                    <button
                                      onClick={() => setShortAnswerGrades({ ...shortAnswerGrades, [q.id]: 'correct' })}
                                      className={`px-3 py-1.5 rounded-xl text-[10px] font-bold transition-all ${
                                        shortAnswerGrades[q.id] === 'correct'
                                          ? 'bg-emerald-500 text-white shadow-sm shadow-emerald-500/25'
                                          : 'bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 text-slate-700 dark:text-slate-300'
                                      }`}
                                    >
                                      Mark Correct
                                    </button>
                                    <button
                                      onClick={() => setShortAnswerGrades({ ...shortAnswerGrades, [q.id]: 'incorrect' })}
                                      className={`px-3 py-1.5 rounded-xl text-[10px] font-bold transition-all ${
                                        shortAnswerGrades[q.id] === 'incorrect'
                                          ? 'bg-rose-500 text-white shadow-sm shadow-rose-500/25'
                                          : 'bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 text-slate-700 dark:text-slate-300'
                                      }`}
                                    >
                                      Mark Incorrect
                                    </button>
                                  </div>
                                </div>
                              )}
                            </>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : quizResult && (quizResult === PENDING_DOCUMENTS_MESSAGE || quizResult.toLowerCase().includes("not enough information") || quizResult.toLowerCase().includes("no documents")) ? (
                <div className="p-6 rounded-[28px] bg-rose-500/5 border border-rose-500/10 text-center max-w-md mx-auto space-y-3 animate-fadeIn">
                  <AlertCircle size={36} className="text-rose-500 mx-auto" />
                  <h4 className="text-xs font-bold text-rose-600 dark:text-rose-400">Context Insufficient</h4>
                  <p className="text-[10px] text-slate-500 dark:text-slate-400 leading-relaxed">
                    <MessageWithDocsLink message={quizResult} />
                  </p>
                </div>
              ) : quizResult ? (
                // Parsing failed - show raw AI generation
                <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-4">
                  <div className="flex justify-between items-center">
                    <span className="text-xs font-bold">Generated Quiz output (Raw Output)</span>
                    <button 
                      onClick={() => navigator.clipboard.writeText(quizResult)}
                      className="p-2 rounded-xl border hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-500 dark:text-slate-400"
                    >
                      <Copy size={14} />
                    </button>
                  </div>
                  <pre className="p-4 rounded-2xl bg-slate-950 text-slate-200 text-xs font-mono overflow-x-auto whitespace-pre-wrap leading-relaxed">
                    {quizResult}
                  </pre>
                  
                </div>
              ) : (
                // First entry helper
                <div className="p-8 rounded-[28px] border-2 border-dashed border-slate-200 dark:border-slate-800/80 text-center max-w-md mx-auto">
                  <HelpCircle size={36} className="text-slate-400 dark:text-slate-600 mx-auto mb-3" />
                  <h3 className="text-xs font-bold text-slate-700 dark:text-slate-300">Generate an Interactive Quiz</h3>
                  <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1.5 leading-relaxed">
                    Enter any topic related to your documents. The RAG generator will isolate matching references and structure multiple choice questions.
                  </p>
                </div>
              )}
            </div>
          )}

          {/* TAB 2: SEMANTIC SEARCH */}
          {activeTab === 'search' && (
            <div className="space-y-6">
              <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm">
                <h3 className="text-xs font-bold text-slate-900 dark:text-white mb-3">Semantic Vector Search</h3>
                <form onSubmit={handleSemanticSearch} className="flex gap-3">
                  <input 
                    type="text"
                    placeholder="Enter a word or phrase to search for"
                    value={semanticQuery}
                    onChange={(e) => setSemanticQuery(e.target.value)}
                    className="flex-grow px-4 py-3 rounded-2xl text-xs bg-slate-50 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-800 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-500/10 focus:border-violet-500 transition-all"
                  />
                  <button 
                    type="submit"
                    className="px-5 py-3 rounded-2xl bg-gradient-to-tr from-violet-600 to-indigo-500 text-white font-semibold text-xs tracking-tight hover:scale-[1.01] transition-all"
                  >
                    Find
                  </button>
                </form>
              </div>

              {semanticResult ? (
                Array.isArray(semanticResult) ? (
                  <div className="space-y-4">
                    {semanticResult.map((res, idx) => (
                      <div 
                        key={idx}
                        className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-3"
                      >
                        <div className="flex justify-between items-center">
                          <div className="flex items-center gap-2">
                            <BookOpen size={14} className="text-violet-500" />
                            <span className="text-xs font-bold text-slate-800 dark:text-white">Document Match Reference</span>
                          </div>
                          <span className="px-2.5 py-0.5 rounded-lg bg-green-500/10 text-green-500 font-bold text-[9px]">
                            {Math.round(res.similarity_score * 100)}% Match
                          </span>
                        </div>

                        <p className="text-[11px] leading-relaxed text-slate-600 dark:text-slate-300 bg-slate-50/50 dark:bg-slate-950/20 p-4 rounded-xl border border-slate-200/20 whitespace-pre-wrap select-all font-sans">
                          {res.content}
                        </p>

                        <div className="flex gap-4 text-[9px] font-semibold text-slate-400 dark:text-slate-500">
                          <span>BOOK REFERENCE ID: #{res.book_id}</span>
                          <span>PAGE NUMBER: {res.page_number}</span>
                          <span>CHUNK: {res.chunk_number}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="p-6 text-center text-xs text-slate-400 dark:text-slate-500 bg-white/20 border border-slate-200 dark:border-slate-800 rounded-[28px]">
                    <MessageWithDocsLink message={semanticResult.message || 'No vector matches found.'} />
                  </div>
                )
              ) : (
                <div className="p-8 rounded-[28px] border-2 border-dashed border-slate-200 dark:border-slate-800/80 text-center max-w-md mx-auto">
                  <Search size={36} className="text-slate-400 dark:text-slate-600 mx-auto mb-3" />
                  <h3 className="text-xs font-bold text-slate-700 dark:text-slate-300">Semantic Matching Sandbox</h3>
                  <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1.5 leading-relaxed">
                    Unlike standard search which requires exact word spelling matches, semantic vector search reads mathematical embeddings to match the core concept.
                  </p>
                </div>
              )}
            </div>
          )}

          {/* TAB 3: NOTES SUMMARIZER */}
          {activeTab === 'summarize' && (
            <div className="space-y-6">
              <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-4">
                <div>
                  <h3 className="text-xs font-bold text-slate-900 dark:text-white">Bulletpoint Notes Summarizer</h3>
                  <p className="text-[9px] text-slate-400 dark:text-slate-500 mt-1">Paste study paragraphs, and the AI will condense them into bite-sized summaries.</p>
                </div>
                <form onSubmit={handleSummarize} className="space-y-3">
                  <textarea 
                    rows={6}
                    placeholder="Paste any notes or text here..."
                    value={summarizerText}
                    onChange={(e) => setSummarizerText(e.target.value)}
                    className="w-full px-4 py-3 rounded-2xl text-xs bg-slate-50 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-800 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-500/10 focus:border-violet-500 transition-all resize-none"
                  />
                  <div className="flex justify-end">
                    <button 
                      type="submit"
                      className="px-5 py-3 rounded-2xl bg-gradient-to-tr from-violet-600 to-indigo-500 text-white font-semibold text-xs tracking-tight hover:scale-[1.01] transition-all"
                    >
                      Summarize Text
                    </button>
                  </div>
                </form>
              </div>

              {summarizerResult !== null && (
                <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-3">
                  <h4 className="text-xs font-bold text-slate-950 dark:text-white flex items-center gap-2">
                    <Sparkles size={14} className="text-violet-500" />
                    AI Summary Results
                  </h4>
                  {summarizerResult.length > 0 ? (
                    <ul className="p-4 rounded-xl bg-slate-50/50 dark:bg-slate-950/20 text-xs text-slate-600 dark:text-slate-300 leading-relaxed border space-y-2 list-disc pl-8 font-sans">
                      {summarizerResult.map((bullet, idx) => (
                        <li key={idx} className="select-all">{bullet}</li>
                      ))}
                    </ul>
                  ) : (
                    <div className="p-4 rounded-xl bg-slate-50/50 dark:bg-slate-950/20 text-xs text-slate-500 dark:text-slate-400 border">
                      Could not generate a summary for this text.
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* TAB 4: FLASHCARDS */}
          {activeTab === 'flashcards' && (
            <div className="space-y-6">
              <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm">
                <h3 className="text-xs font-bold text-slate-900 dark:text-white mb-3">Topic-based Flashcard Deck</h3>
                <form onSubmit={handleGenerateFlashcards} className="flex gap-3">
                  <input 
                    type="text"
                    placeholder="e.g. CIA Triad"
                    value={flashcardTopic}
                    onChange={(e) => setFlashcardTopic(e.target.value)}
                    className="flex-grow px-4 py-3 rounded-2xl text-xs bg-slate-50 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-800 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-500/10 focus:border-violet-500 transition-all"
                  />
                  <button 
                    type="submit"
                    className="px-5 py-3 rounded-2xl bg-gradient-to-tr from-violet-600 to-indigo-500 text-white font-semibold text-xs tracking-tight hover:scale-[1.01] transition-all"
                  >
                    Build Deck
                  </button>
                </form>
              </div>

              {flashcardMessage && (
                <div className="p-6 text-center text-xs text-slate-400 dark:text-slate-500 bg-white/20 border border-slate-200 dark:border-slate-800 rounded-[28px]">
                  <MessageWithDocsLink message={flashcardMessage} />
                </div>
              )}

              {/* Flashcards Grid with Flip Effects */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 pt-4">
                {flashcards.map((card) => {
                  const isFlipped = !!flippedCards[card.id];
                  return (
                    <div 
                      key={card.id}
                      onClick={() => handleFlipCard(card.id)}
                      className="cursor-pointer h-44 perspective-1000 group w-full"
                    >
                      <div className={`relative w-full h-full text-center transition-all duration-500 transform-style-3d ${isFlipped ? 'rotate-y-180' : ''}`}>
                        
                        {/* Front Side */}
                        <div className="absolute inset-0 backface-hidden p-6 rounded-[28px] bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 shadow-sm flex flex-col justify-between items-center group-hover:border-violet-500/30 transition-all duration-300">
                          <div className="text-[10px] text-slate-400 dark:text-slate-500 font-bold uppercase tracking-wider">Concept Term</div>
                          <h3 className="text-sm font-bold text-slate-800 dark:text-white px-2 break-all">{card.front}</h3>
                          <div className="text-[9px] font-semibold text-violet-500 flex items-center gap-1">
                            <span>Click to Flip</span>
                            <ChevronRight size={10} className="rotate-90" />
                          </div>
                        </div>

                        {/* Back Side */}
                        <div className="absolute inset-0 backface-hidden rotate-y-180 p-6 rounded-[28px] bg-slate-950 text-white border border-slate-800 flex flex-col justify-between items-center">
                          <div className="text-[10px] text-slate-500 font-bold uppercase tracking-wider">Explanation</div>
                          <p className="text-[11px] leading-relaxed text-slate-300 px-2 font-medium">{card.back}</p>
                          <div className="text-[9px] font-semibold text-violet-400">Click to Flip Back</div>
                        </div>

                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* TAB 5: AI TUTOR */}
          {activeTab === 'tutor' && (
            <div className="space-y-6">
              <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-4">
                <div>
                  <h3 className="text-xs font-bold text-slate-900 dark:text-white">Specialized AI Homework Tutor</h3>
                  <p className="text-[9px] text-slate-400 dark:text-slate-500 mt-1">Prompt the AI professor to explain complex coding topics, proofs, or math formulas.</p>
                </div>
                <form onSubmit={handleTutorSubmit} className="space-y-3">
                  <input 
                    type="text"
                    placeholder="e.g. Explain photosynthesis in simple terms."
                    value={tutorPrompt}
                    onChange={(e) => setTutorPrompt(e.target.value)}
                    className="w-full px-4 py-3 rounded-2xl text-xs bg-slate-50 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-800 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-500/10 focus:border-violet-500 transition-all"
                  />
                  <div className="flex justify-end">
                    <button 
                      type="submit"
                      className="px-5 py-3 rounded-2xl bg-gradient-to-tr from-violet-600 to-indigo-500 text-white font-semibold text-xs tracking-tight hover:scale-[1.01] transition-all"
                    >
                      Consult Tutor
                    </button>
                  </div>
                </form>
              </div>

              {tutorResult && (
                <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-3">
                  <h4 className="text-xs font-bold text-slate-950 dark:text-white flex items-center gap-2">
                    <BookMarked size={14} className="text-violet-500 animate-pulse" />
                    AI Professor Explanation
                  </h4>
                  <div className="p-4 rounded-xl bg-slate-50/50 dark:bg-slate-950/20 text-xs text-slate-600 dark:text-slate-300 leading-relaxed border whitespace-pre-wrap select-all font-sans">
                    <MessageWithDocsLink message={tutorResult} />
                  </div>
                </div>
              )}
            </div>
          )}

        </div>
      )}

    </div>
  );
}

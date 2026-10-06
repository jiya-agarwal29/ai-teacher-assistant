import { userScopedKey } from '../services/api';

const HISTORY_KEY = 'quiz_history'; // array of { topic, correct, total, timestamp }
const MAX_HISTORY = 200;

function readHistory() {
  try {
    return JSON.parse(localStorage.getItem(userScopedKey(HISTORY_KEY)) || '[]');
  } catch {
    return [];
  }
}

/**
 * Call once per quiz submission, with the real graded result. `byLevel`
 * (optional) is { levelKey: { correct, total } } for whichever Bloom
 * levels appeared in this quiz -- omitted entirely for a quiz with no
 * level info (e.g. parsed from raw text), so old and new saved attempts
 * both load fine either way (getLevelStats() just skips attempts without it).
 */
export function recordQuizAttempt({ topic, correct, total, byLevel }) {
  if (!total || total <= 0) return;
  const history = readHistory();
  const entry = { topic: (topic || 'General').trim(), correct, total, timestamp: Date.now() };
  if (byLevel && Object.keys(byLevel).length > 0) {
    entry.byLevel = byLevel;
  }
  history.push(entry);
  const trimmed = history.length > MAX_HISTORY ? history.slice(history.length - MAX_HISTORY) : history;
  localStorage.setItem(userScopedKey(HISTORY_KEY), JSON.stringify(trimmed));
}

/**
 * Real quiz performance, computed from every submitted attempt.
 * - averagePercentage: mean of each quiz's own score (a 2-question quiz
 *   and a 20-question quiz count equally).
 * - weightedAccuracy: correct answers / total answers across every
 *   question you've ever answered (bigger quizzes weigh more).
 * Returns attempts: 0 and null percentages when nothing has been taken yet
 * — never a fabricated placeholder number.
 */
export function getQuizStats() {
  const history = readHistory();
  if (history.length === 0) {
    return { attempts: 0, averagePercentage: null, weightedAccuracy: null, totalQuestions: 0 };
  }

  const perQuizPercentages = history.map((h) => (h.correct / h.total) * 100);
  const averagePercentage = perQuizPercentages.reduce((a, b) => a + b, 0) / perQuizPercentages.length;

  const totalCorrect = history.reduce((sum, h) => sum + h.correct, 0);
  const totalQuestions = history.reduce((sum, h) => sum + h.total, 0);
  const weightedAccuracy = totalQuestions > 0 ? (totalCorrect / totalQuestions) * 100 : null;

  return { attempts: history.length, averagePercentage, weightedAccuracy, totalQuestions };
}

/**
 * Per-topic accuracy across all attempts, sorted weakest-first — the real
 * basis for a "this topic needs reinforcement" recommendation. Topics are
 * grouped case-insensitively since the same free-text topic may be typed
 * with different casing across attempts.
 */
export function getTopicBreakdown() {
  const history = readHistory();
  const byTopic = new Map();

  for (const h of history) {
    const key = h.topic.toLowerCase();
    const existing = byTopic.get(key) || { topic: h.topic, correct: 0, total: 0, attempts: 0 };
    existing.correct += h.correct;
    existing.total += h.total;
    existing.attempts += 1;
    byTopic.set(key, existing);
  }

  return Array.from(byTopic.values())
    .map((t) => ({ ...t, percentage: t.total > 0 ? (t.correct / t.total) * 100 : 0 }))
    .sort((a, b) => a.percentage - b.percentage);
}

/**
 * Accuracy per Bloom's level across every attempt that recorded one.
 * Attempts saved before this field existed are simply skipped for this
 * breakdown (they still count fully in getQuizStats()/getTopicBreakdown()).
 * Returns { levelKey: { correct, total, percentage } } for only the
 * levels that have ever appeared -- a level with no data at all is just
 * absent from the result, for the caller to render as "No data yet".
 */
export function getLevelStats() {
  const history = readHistory();
  const byLevel = {};

  for (const h of history) {
    if (!h.byLevel) continue;
    for (const [level, counts] of Object.entries(h.byLevel)) {
      const existing = byLevel[level] || { correct: 0, total: 0 };
      existing.correct += counts.correct || 0;
      existing.total += counts.total || 0;
      byLevel[level] = existing;
    }
  }

  return Object.fromEntries(
    Object.entries(byLevel).map(([level, { correct, total }]) => [
      level,
      { correct, total, percentage: total > 0 ? (correct / total) * 100 : 0 }
    ])
  );
}

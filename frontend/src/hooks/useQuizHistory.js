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

/** Call once per quiz submission, with the real graded result. */
export function recordQuizAttempt({ topic, correct, total }) {
  if (!total || total <= 0) return;
  const history = readHistory();
  history.push({ topic: (topic || 'General').trim(), correct, total, timestamp: Date.now() });
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

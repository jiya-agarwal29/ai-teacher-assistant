import { api, userScopedKey } from '../services/api';

// The old (pre-Phase-6A) per-user localStorage keys this one-time import
// reads from and, once imported, deletes -- see hooks/useQueryHistory.js
// (now removed), useActiveTime.js, and useQuizHistory.js for where these
// used to be written.
const LEGACY_QUERIES_BY_DAY_KEY = 'queries_by_day';
const LEGACY_ACTIVE_TIME_BY_DAY_KEY = 'active_time_by_day';
const LEGACY_QUIZ_HISTORY_KEY = 'quiz_history';
const LEGACY_QUERIES_TOTAL_KEY = 'queries_count';
const LEGACY_ACTIVE_TIME_TOTAL_KEY = 'active_time_total_seconds';

function readLegacyJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(userScopedKey(key));
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function clearLegacyKeys() {
  try {
    localStorage.removeItem(userScopedKey(LEGACY_QUERIES_BY_DAY_KEY));
    localStorage.removeItem(userScopedKey(LEGACY_ACTIVE_TIME_BY_DAY_KEY));
    localStorage.removeItem(userScopedKey(LEGACY_QUIZ_HISTORY_KEY));
    localStorage.removeItem(userScopedKey(LEGACY_QUERIES_TOTAL_KEY));
    localStorage.removeItem(userScopedKey(LEGACY_ACTIVE_TIME_TOTAL_KEY));
  } catch {
    // Best-effort cleanup -- leaving a stale key behind is harmless, it's
    // just never read again once analytics_imported_at is set server-side.
  }
}

/**
 * Runs once per login (see hooks/AuthProvider.jsx): if this account hasn't
 * imported its old browser-only analytics yet AND this specific browser
 * still has some of that old localStorage data, uploads it to POST
 * /analytics/import and deletes the local copy afterward.
 *
 * Deliberately does nothing (no API call at all) when this browser has no
 * legacy data of its own -- logging in from a browser that was never used
 * before Phase 6A must not burn the account's one-time import before a
 * DIFFERENT browser that actually has real history gets a chance to use it.
 *
 * Never throws: a failure is logged and simply tried again on the next
 * login (the local data is left in place so there's something to retry
 * with); `onImported` fires once real data was uploaded (200) or this
 * browser's now-redundant copy was cleared after discovering the account
 * was already imported elsewhere (409) -- never on any other error.
 */
export async function runOneTimeAnalyticsImport({ onImported } = {}) {
  try {
    const { imported } = await api.analytics.importStatus();
    if (imported) return;

    const queriesByDay = readLegacyJSON(LEGACY_QUERIES_BY_DAY_KEY, {});
    const activeSecondsByDay = readLegacyJSON(LEGACY_ACTIVE_TIME_BY_DAY_KEY, {});
    const quizHistory = readLegacyJSON(LEGACY_QUIZ_HISTORY_KEY, []);

    const hasLegacyData = (
      Object.keys(queriesByDay).length > 0
      || Object.keys(activeSecondsByDay).length > 0
      || quizHistory.length > 0
    );
    if (!hasLegacyData) return;

    await api.analytics.import({
      queries_by_day: queriesByDay,
      active_seconds_by_day: activeSecondsByDay,
      quiz_history: quizHistory,
    });

    clearLegacyKeys();
    onImported?.();
  } catch (err) {
    if (err?.status === 409) {
      // This account's history was already imported from another
      // browser -- this browser's copy is now redundant, not needed.
      clearLegacyKeys();
      onImported?.();
      return;
    }
    console.warn('One-time analytics import failed; will retry on next login.', err);
  }
}

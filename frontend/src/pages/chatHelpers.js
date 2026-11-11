// Lives in its own file (not inside Chat.jsx) so these two small decision
// points -- Fix round 2, items 11 and 13 -- can be unit tested directly,
// without mounting the whole chat page.

/**
 * Fix round 2, item 11: each chat's unsent draft is kept separately, keyed
 * by conversation id -- 'new' for the not-yet-created chat -- so chat A's
 * draft never shows up in chat B.
 */
export const draftKeyFor = (convId) => (convId == null ? 'new' : String(convId));

/**
 * Fix round 2, item 13: a chat request is retried (instead of surfacing a
 * failure right away) only when the backend reported itself specifically
 * as rate-limited (503 -- see AI_BUSY_DETAIL in main.py) and there's still
 * an attempt left -- any other error (a real failure, or the backend
 * being down) fails immediately rather than wasting the remaining
 * attempts on something that won't change.
 */
export function shouldRetryChatError(err, attempt, maxAttempts) {
  return err?.status === 503 && attempt < maxAttempts;
}

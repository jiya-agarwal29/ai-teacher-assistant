// Tracks "the user is doing something" globally, for AuthProvider's
// idle-aware session handling: silently refresh the token while the user
// is active, and only show the "session ending" popup once they've
// actually gone idle. A single module-level listener set (not one per
// component) so mounting AuthProvider more than once never double-counts.
const ACTIVITY_EVENTS = ['mousemove', 'mousedown', 'keydown', 'scroll', 'touchstart', 'wheel'];

let lastActivityAt = Date.now();
let listenersAttached = false;
const subscribers = new Set();

function notify() {
  lastActivityAt = Date.now();
  subscribers.forEach((callback) => callback(lastActivityAt));
}

/** Call once (AuthProvider does this) to start tracking mouse/keyboard/touch activity. */
export function startActivityTracking() {
  if (listenersAttached || typeof window === 'undefined') return;
  listenersAttached = true;
  ACTIVITY_EVENTS.forEach((evt) => window.addEventListener(evt, notify, { passive: true }));
}

/** Call whenever something else should also count as activity -- api.js's request() does this for every call. */
export function recordActivity() {
  notify();
}

export function getLastActivityAt() {
  return lastActivityAt;
}

/** Notified on every activity tick (mouse/keyboard/touch/API call) while subscribed. Returns an unsubscribe function. */
export function subscribeToActivity(callback) {
  subscribers.add(callback);
  return () => subscribers.delete(callback);
}

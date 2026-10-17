/**
 * Tracks in-flight API requests so a slow one (Render's free plan spins the
 * backend back up from sleep, which can take the better part of a minute)
 * can show a "waking up" banner -- Phase 7C item 8. This only times
 * requests the app is making anyway; it never sends an extra ping/probe
 * request of its own.
 *
 * Deliberately a plain module (not React state) so every call site in
 * api.js -- the shared request() helper and the handful of manual
 * fetch()/XHR calls for blobs/uploads -- can report in with no React
 * context available. ColdStartBanner subscribes to re-render on change.
 */
const SLOW_THRESHOLD_MS = 4000;

const listeners = new Set();
let activeRequestCount = 0;
let slowTimer = null;
let bannerVisible = false;

function setBannerVisible(next) {
  if (bannerVisible === next) return;
  bannerVisible = next;
  listeners.forEach((listener) => listener(bannerVisible));
}

/** Call at the start of any request; call the returned function when it settles (success or failure). */
export function trackRequest() {
  activeRequestCount += 1;
  if (!slowTimer) {
    slowTimer = setTimeout(() => {
      slowTimer = null;
      // Only show the banner if something is still in flight 4s later --
      // a request that already finished by then was never slow.
      if (activeRequestCount > 0) setBannerVisible(true);
    }, SLOW_THRESHOLD_MS);
  }

  let settled = false;
  return function untrackRequest() {
    if (settled) return;
    settled = true;
    activeRequestCount = Math.max(0, activeRequestCount - 1);
    if (activeRequestCount === 0) {
      if (slowTimer) {
        clearTimeout(slowTimer);
        slowTimer = null;
      }
      setBannerVisible(false);
    }
  };
}

/** For ColdStartBanner: subscribes to visibility changes, returns an unsubscribe function. Calls back immediately with the current value. */
export function subscribeColdStart(listener) {
  listeners.add(listener);
  listener(bannerVisible);
  return () => listeners.delete(listener);
}

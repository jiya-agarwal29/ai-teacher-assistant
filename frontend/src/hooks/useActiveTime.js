import { useEffect, useRef } from 'react';
import { api } from '../services/api';

// Only count time toward "study session" if the user interacted within
// this window — an idle tab with the screen just left on doesn't count.
const IDLE_LIMIT_MS = 60 * 1000;
const TICK_MS = 5000;
const HEARTBEAT_SECONDS = 60;
const ACTIVITY_EVENTS = ['mousemove', 'mousedown', 'keydown', 'scroll', 'touchstart', 'wheel'];

/**
 * Mount once (in the authenticated app shell) to report real "active"
 * usage time to the server (Phase 6A's POST /analytics/active-time) --
 * only while the tab is visible AND the user interacted within the last
 * IDLE_LIMIT_MS. A screen left open and untouched, or a backgrounded tab,
 * sends nothing. Active seconds accumulate locally and are sent as one
 * heartbeat every HEARTBEAT_SECONDS, plus a final flush of whatever's
 * still pending when the page is hidden/closed, so at most a few seconds
 * of real activity are ever lost. A dropped heartbeat (network error, the
 * 4/minute server-side limit) is simply discarded, never retried in a
 * loop -- a lost heartbeat is a few lost seconds, not worth piling up
 * retries over.
 */
export function useActiveTimeTracker() {
  const lastActivityRef = useRef(null);
  const pendingSecondsRef = useRef(0);

  useEffect(() => {
    // Set here rather than as useRef's initial value — calling Date.now()
    // during render is impure; effects are the right place for it.
    lastActivityRef.current = Date.now();

    const markActive = () => {
      lastActivityRef.current = Date.now();
    };
    ACTIVITY_EVENTS.forEach((evt) => window.addEventListener(evt, markActive, { passive: true }));

    const sendHeartbeat = (seconds) => {
      api.analytics.activeTime(Math.min(seconds, 120)).catch(() => {});
    };

    const flushPending = () => {
      if (pendingSecondsRef.current > 0) {
        sendHeartbeat(pendingSecondsRef.current);
        pendingSecondsRef.current = 0;
      }
    };

    const interval = setInterval(() => {
      const isVisible = document.visibilityState === 'visible';
      const idleFor = Date.now() - lastActivityRef.current;
      if (isVisible && idleFor <= IDLE_LIMIT_MS) {
        pendingSecondsRef.current += TICK_MS / 1000;
        if (pendingSecondsRef.current >= HEARTBEAT_SECONDS) {
          flushPending();
        }
      }
    }, TICK_MS);

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'hidden') flushPending();
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('pagehide', flushPending);

    return () => {
      ACTIVITY_EVENTS.forEach((evt) => window.removeEventListener(evt, markActive));
      clearInterval(interval);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('pagehide', flushPending);
    };
  }, []);
}

export function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.round(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);

  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m`;
  return `${seconds}s`;
}

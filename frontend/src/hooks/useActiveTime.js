import { useEffect, useRef } from 'react';
import { userScopedKey } from '../services/api';

// Only count time toward "study session" if the user interacted within
// this window — an idle tab with the screen just left on doesn't count.
const IDLE_LIMIT_MS = 60 * 1000;
const TICK_MS = 5000;
const ACTIVITY_EVENTS = ['mousemove', 'mousedown', 'keydown', 'scroll', 'touchstart', 'wheel'];

const TOTAL_KEY = 'active_time_total_seconds';
const DAILY_KEY = 'active_time_by_day';

function todayKey() {
  return new Date().toISOString().slice(0, 10); // YYYY-MM-DD, local calendar day
}

function addActiveSeconds(seconds) {
  const totalKey = userScopedKey(TOTAL_KEY);
  const total = parseInt(localStorage.getItem(totalKey) || '0', 10) + seconds;
  localStorage.setItem(totalKey, String(total));

  const dailyKey = userScopedKey(DAILY_KEY);
  let byDay = {};
  try {
    byDay = JSON.parse(localStorage.getItem(dailyKey) || '{}');
  } catch {
    byDay = {};
  }
  const key = todayKey();
  byDay[key] = (byDay[key] || 0) + seconds;
  localStorage.setItem(dailyKey, JSON.stringify(byDay));
}

/**
 * Mount once (in the authenticated app shell) to accumulate real "active"
 * usage time: only ticks while the tab is visible AND the user interacted
 * within the last IDLE_LIMIT_MS. A screen left open and untouched, or a
 * backgrounded tab, does not add to the total.
 */
export function useActiveTimeTracker() {
  const lastActivityRef = useRef(Date.now());

  useEffect(() => {
    const markActive = () => {
      lastActivityRef.current = Date.now();
    };
    ACTIVITY_EVENTS.forEach((evt) => window.addEventListener(evt, markActive, { passive: true }));

    const interval = setInterval(() => {
      const isVisible = document.visibilityState === 'visible';
      const idleFor = Date.now() - lastActivityRef.current;
      if (isVisible && idleFor <= IDLE_LIMIT_MS) {
        addActiveSeconds(TICK_MS / 1000);
      }
    }, TICK_MS);

    return () => {
      ACTIVITY_EVENTS.forEach((evt) => window.removeEventListener(evt, markActive));
      clearInterval(interval);
    };
  }, []);
}

export function getTotalActiveSeconds() {
  return parseInt(localStorage.getItem(userScopedKey(TOTAL_KEY)) || '0', 10);
}

export function getActiveSecondsInLastNDays(days) {
  let byDay = {};
  try {
    byDay = JSON.parse(localStorage.getItem(userScopedKey(DAILY_KEY)) || '{}');
  } catch {
    byDay = {};
  }
  let sum = 0;
  const now = new Date();
  for (let i = 0; i < days; i++) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    sum += byDay[key] || 0;
  }
  return sum;
}

export function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.round(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);

  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m`;
  return `${seconds}s`;
}

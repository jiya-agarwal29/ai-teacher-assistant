const TOTAL_KEY = 'queries_count';
const DAILY_KEY = 'queries_by_day'; // { "YYYY-MM-DD": count }

function todayKey(date = new Date()) {
  return date.toISOString().slice(0, 10);
}

function readDaily() {
  try {
    return JSON.parse(localStorage.getItem(DAILY_KEY) || '{}');
  } catch {
    return {};
  }
}

/** Call once per real question sent to the AI (e.g. on a successful Chat reply). */
export function recordQuery() {
  const total = parseInt(localStorage.getItem(TOTAL_KEY) || '0', 10) + 1;
  localStorage.setItem(TOTAL_KEY, String(total));

  const byDay = readDaily();
  const key = todayKey();
  byDay[key] = (byDay[key] || 0) + 1;
  localStorage.setItem(DAILY_KEY, JSON.stringify(byDay));
}

export function getTotalQueries() {
  return parseInt(localStorage.getItem(TOTAL_KEY) || '0', 10);
}

/** Real per-day counts for the last N days, oldest first, for charting. */
export function getQueriesByDay(days) {
  const byDay = readDaily();
  const result = [];
  const now = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    const key = todayKey(d);
    result.push({
      day: d.toLocaleDateString(undefined, { weekday: 'short' }),
      date: key,
      queries: byDay[key] || 0,
    });
  }
  return result;
}

/**
 * Local calendar-day key (YYYY-MM-DD). Deliberately not toISOString(),
 * which is UTC — activity before ~5:30 AM IST (or any positive UTC offset)
 * would otherwise land on the previous day's bucket.
 */
export function localDateKey(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

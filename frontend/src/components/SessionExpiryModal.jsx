import { useState, useEffect, useRef, useCallback } from 'react';
import { Clock, AlertCircle, Loader2 } from 'lucide-react';

const TICK_MS = 250;

function formatCountdown(remainingMs) {
  const totalSeconds = Math.max(0, Math.ceil(remainingMs / 1000));
  const mm = String(Math.floor(totalSeconds / 60)).padStart(2, '0');
  const ss = String(totalSeconds % 60).padStart(2, '0');
  return `${mm}:${ss}`;
}

/**
 * Shown by PageLayout while AuthProvider's showSessionWarning is true.
 * Forced choice (no backdrop-click-to-dismiss): "Stay logged in" refreshes
 * the token, "Log out" ends the session immediately. Esc acts as "Stay
 * logged in" (the least destructive action) and Tab is trapped inside the
 * dialog while it's open.
 */
export default function SessionExpiryModal({ expiresAt, onStayLoggedIn, onLogout }) {
  const [remainingMs, setRemainingMs] = useState(() => Math.max(0, (expiresAt ?? Date.now()) - Date.now()));
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState('');

  const dialogRef = useRef(null);
  const stayButtonRef = useRef(null);

  useEffect(() => {
    const interval = setInterval(() => {
      setRemainingMs(Math.max(0, (expiresAt ?? Date.now()) - Date.now()));
    }, TICK_MS);
    return () => clearInterval(interval);
  }, [expiresAt]);

  useEffect(() => {
    stayButtonRef.current?.focus();
  }, []);

  const handleStay = useCallback(async () => {
    setIsRefreshing(true);
    setRefreshError('');
    const success = await onStayLoggedIn();
    setIsRefreshing(false);
    if (!success) {
      setRefreshError("Couldn't extend your session");
    }
  }, [onStayLoggedIn]);

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        handleStay();
        return;
      }
      if (e.key !== 'Tab') return;

      const focusable = dialogRef.current?.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];

      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleStay]);

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-900/50 backdrop-blur-sm p-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="session-expiry-title"
        aria-describedby="session-expiry-countdown"
        className="w-full max-w-sm bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-[24px] p-6 shadow-xl animate-fade-in"
      >
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-xl bg-amber-500/10 text-amber-500 flex items-center justify-center shrink-0">
            <Clock size={18} />
          </div>
          <h2 id="session-expiry-title" className="text-sm font-bold text-slate-900 dark:text-white">
            Your session is about to expire
          </h2>
        </div>

        <p className="text-xs text-slate-500 dark:text-slate-400 mb-1">
          For your security, you'll be logged out in:
        </p>
        <p id="session-expiry-countdown" className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white mb-4 tabular-nums">
          {formatCountdown(remainingMs)}
        </p>

        {refreshError && (
          <div className="flex items-start gap-2 p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-[11px] font-medium mb-4">
            <AlertCircle size={14} className="shrink-0 mt-0.5" />
            <span>{refreshError}</span>
          </div>
        )}

        <div className="flex gap-2">
          <button
            ref={stayButtonRef}
            onClick={handleStay}
            disabled={isRefreshing}
            className="flex-1 py-2.5 rounded-xl bg-gradient-to-r from-violet-600 to-indigo-500 hover:from-violet-500 hover:to-indigo-400 text-white font-semibold text-xs tracking-tight shadow-md disabled:opacity-60 disabled:cursor-not-allowed transition-all flex items-center justify-center gap-2"
          >
            {isRefreshing && <Loader2 size={14} className="animate-spin" />}
            Stay logged in
          </button>
          <button
            onClick={onLogout}
            className="flex-1 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-semibold text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-all"
          >
            Log out
          </button>
        </div>
      </div>
    </div>
  );
}

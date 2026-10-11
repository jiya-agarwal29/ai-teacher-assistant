import { useEffect } from 'react';
import { CheckCircle2, X } from 'lucide-react';

const AUTO_DISMISS_MS = 6000;

/**
 * Shown once by PageLayout when AuthProvider's one-time analytics import
 * (see utils/analyticsImport.js) actually moved -- or found already
 * moved -- this browser's old localStorage activity onto the account.
 * Auto-dismisses; never reappears until the next login triggers it again.
 */
export default function ImportedActivityToast({ onDismiss }) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  return (
    <div
      role="status"
      className="fixed bottom-6 right-6 z-[100] max-w-sm bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-xl p-4 flex items-start gap-3 animate-fade-in print:hidden"
    >
      <div className="w-9 h-9 rounded-xl bg-emerald-500/10 text-emerald-500 flex items-center justify-center shrink-0">
        <CheckCircle2 size={16} />
      </div>
      <div className="flex-1 pt-0.5">
        <p className="text-xs font-bold text-slate-900 dark:text-white">Activity moved</p>
        <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5 leading-relaxed">
          Your earlier activity was moved to your account.
        </p>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors shrink-0"
        aria-label="Dismiss"
      >
        <X size={14} />
      </button>
    </div>
  );
}

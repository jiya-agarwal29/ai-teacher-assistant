import { useEffect } from 'react';
import { CheckCircle2, X } from 'lucide-react';

const AUTO_DISMISS_MS = 6000;

/**
 * Shown once by PageLayout for each one-time import AuthProvider's login()
 * kicked off (see utils/analyticsImport.js, utils/chatImport.js) that
 * actually moved -- or found already moved -- this browser's old
 * localStorage data onto the account. `offset` stacks more than one of
 * these vertically when both imports fire on the same login. Auto-
 * dismisses; never reappears until the next login triggers it again.
 */
export default function ImportedDataToast({ heading, message, onDismiss, offset = 0 }) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  return (
    <div
      role="status"
      style={{ bottom: `${1.5 + offset * 4.5}rem` }}
      className="fixed right-6 z-[100] max-w-sm bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-xl p-4 flex items-start gap-3 animate-fade-in print:hidden"
    >
      <div className="w-9 h-9 rounded-xl bg-emerald-500/10 text-emerald-500 flex items-center justify-center shrink-0">
        <CheckCircle2 size={16} />
      </div>
      <div className="flex-1 pt-0.5">
        <p className="text-xs font-bold text-slate-900 dark:text-white">{heading}</p>
        <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5 leading-relaxed">
          {message}
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

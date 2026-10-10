import { FileEdit, Loader2, CheckCircle2, XCircle } from 'lucide-react';

const STATUS_CONFIG = {
  draft: {
    label: 'Draft',
    icon: FileEdit,
    className: 'bg-slate-500/10 text-slate-600 dark:text-slate-400 border-slate-500/20'
  },
  generating: {
    label: 'Generating',
    icon: Loader2,
    spin: true,
    className: 'bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20'
  },
  ready: {
    label: 'Ready',
    icon: CheckCircle2,
    className: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20'
  },
  failed: {
    label: 'Failed',
    icon: XCircle,
    className: 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20'
  }
};

/**
 * Small pill showing a Paper's status. While "generating", shows live
 * progress once the caller has it ("Generating N/M") -- PaperList polls
 * GET /papers/{id}/status for every "generating" paper and merges
 * progress_done/progress_total in, the same way DocumentStatusBadge's
 * "Reading page N of M" works for books.
 */
export default function PaperStatusBadge({ status, progressDone = 0, progressTotal = 0, waitingForAi = false }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.draft;
  const Icon = config.icon;

  let label = config.label;
  if (status === 'generating') {
    label = progressTotal > 0 ? `Generating ${progressDone}/${progressTotal}` : 'Generating';
    if (waitingForAi) label = 'Waiting for AI service...';
  }

  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[10px] font-bold whitespace-nowrap ${config.className}`}>
      <Icon size={11} className={config.spin ? 'animate-spin' : ''} />
      {label}
    </span>
  );
}

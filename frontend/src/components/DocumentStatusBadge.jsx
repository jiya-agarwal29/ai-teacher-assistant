import { Link } from 'react-router-dom';
import { Loader2, CheckCircle2, XCircle, AlertTriangle } from 'lucide-react';

const STATUS_CONFIG = {
  processing: {
    label: 'Processing',
    icon: Loader2,
    spin: true,
    className: 'bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20'
  },
  needs_review: {
    label: 'Needs review',
    icon: AlertTriangle,
    spin: false,
    className: 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20'
  },
  ready: {
    label: 'Ready',
    icon: CheckCircle2,
    spin: false,
    className: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20'
  },
  failed: {
    label: 'Failed',
    icon: XCircle,
    spin: false,
    className: 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20'
  }
};

/**
 * Small pill showing a document's background-processing status. While
 * "processing", shows a live progress count once the backend has reported
 * one (pagesTotal > 0): "Reading page N of M" during OCR -- the backend
 * leaves sourceType null until every page's text is extracted -- then
 * "Processing N/M" once chunking/embedding takes over. Falls back to a
 * plain "Processing" label before either count exists yet.
 *
 * When status is "needs_review" and bookId is given, the pill links to
 * that document's OCR review page.
 */
export default function DocumentStatusBadge({ status, pagesTotal = 0, pagesDone = 0, sourceType = null, bookId = null }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.processing;
  const Icon = config.icon;

  let label = config.label;
  if (status === 'processing' && pagesTotal > 0) {
    label = sourceType
      ? `Processing ${pagesDone}/${pagesTotal}`
      : `Reading page ${pagesDone} of ${pagesTotal}`;
  }

  const pillClassName = `inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[10px] font-bold whitespace-nowrap ${config.className}`;
  const content = (
    <>
      <Icon size={11} className={config.spin ? 'animate-spin' : ''} />
      {label}
    </>
  );

  if (status === 'needs_review' && bookId != null) {
    return (
      <Link to={`/documents/${bookId}/review`} className={`${pillClassName} hover:scale-[1.04] transition-transform cursor-pointer`}>
        {content}
      </Link>
    );
  }

  return <span className={pillClassName}>{content}</span>;
}

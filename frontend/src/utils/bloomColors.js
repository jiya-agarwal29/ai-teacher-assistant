/**
 * Six distinct colours for Bloom's taxonomy levels, ordered cool (Remember)
 * to warm (Create) so the progression from recall to creation reads at a
 * glance. Shared by BloomBadge, the quiz builder's level chips, and the
 * Analytics "Accuracy by Bloom's level" bars, so a level always looks the
 * same colour everywhere -- only the level *labels/descriptions* come from
 * the backend (bloom.py); these colours are a pure frontend presentation
 * choice bloom.py has no opinion on.
 *
 * - pill: soft badge background (BloomBadge, question cards)
 * - solid: filled background for an active/selected chip
 * - bar: progress-bar fill (Analytics)
 */
const BLOOM_LEVEL_COLORS = {
  remember: {
    pill: 'bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20',
    solid: 'bg-sky-600 text-white border-sky-600',
    bar: 'bg-sky-500',
  },
  understand: {
    pill: 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border-cyan-500/20',
    solid: 'bg-cyan-600 text-white border-cyan-600',
    bar: 'bg-cyan-500',
  },
  apply: {
    pill: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20',
    solid: 'bg-emerald-600 text-white border-emerald-600',
    bar: 'bg-emerald-500',
  },
  analyze: {
    pill: 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20',
    solid: 'bg-amber-600 text-white border-amber-600',
    bar: 'bg-amber-500',
  },
  evaluate: {
    pill: 'bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20',
    solid: 'bg-orange-600 text-white border-orange-600',
    bar: 'bg-orange-500',
  },
  create: {
    pill: 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20',
    solid: 'bg-rose-600 text-white border-rose-600',
    bar: 'bg-rose-500',
  },
};

const FALLBACK_COLOR = {
  pill: 'bg-slate-500/10 text-slate-600 dark:text-slate-400 border-slate-500/20',
  solid: 'bg-slate-600 text-white border-slate-600',
  bar: 'bg-slate-400',
};

/** Returns the colour set for a level key, or a neutral grey for an unknown one. */
export function getBloomColor(levelKey) {
  return BLOOM_LEVEL_COLORS[levelKey] || FALLBACK_COLOR;
}

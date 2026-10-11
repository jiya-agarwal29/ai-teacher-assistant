import { AlertTriangle } from 'lucide-react';
import useBloomLevels from '../hooks/useBloomLevels';
import { getBloomColor } from '../utils/bloomColors';
import BloomBadge from './BloomBadge';

const fmt = (n) => {
  const num = Number(n) || 0;
  return Number.isInteger(num) ? String(num) : num.toFixed(1);
};

const pct = (marks, total) => {
  if (!total) return 0;
  return Math.round(((Number(marks) || 0) / total) * 100);
};

/**
 * Side panel shared by the "New paper" preview step (target vs the plan's
 * PLANNED marks, nothing generated yet -- no `actual` prop) and the ready
 * paper view (target vs planned vs the saved questions' ACTUAL mix, which
 * counts a flagged question at its detected level rather than the
 * intended one -- see main._compute_blueprint_actual). The main numbers
 * per level are percentages of the paper (Phase 5 fixes from browser
 * testing, items 2 and 3) since that's what a teacher is actually trying
 * to hit; the underlying marks are still shown, just as secondary detail.
 * Also lists marks per document and any paper-level warnings (amber).
 */
export default function BlueprintPanel({
  target = {},
  planned = {},
  actual = null,
  byUnit = {},
  documentNames = {},
  warnings = []
}) {
  const { levels: bloomLevels } = useBloomLevels();
  const paperTotal = Object.values(target).reduce((sum, v) => sum + (Number(v) || 0), 0) || 1;

  return (
    <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-5">
      <h3 className="text-xs font-bold text-slate-900 dark:text-white">Blueprint</h3>

      <div className="space-y-4">
        {bloomLevels.map((lvl) => {
          const targetMarks = target[lvl.key] || 0;
          const plannedMarks = planned[lvl.key] || 0;
          const actualMarks = actual ? (actual[lvl.key] || 0) : null;
          const barMarks = actual ? actualMarks : plannedMarks;
          const colors = getBloomColor(lvl.key);

          return (
            <div key={lvl.key} className="space-y-1">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <BloomBadge levelKey={lvl.key} />
                <span className="text-[9px] font-bold text-slate-500 dark:text-slate-400 whitespace-nowrap">
                  {actual !== null && `Actual ${pct(actualMarks, paperTotal)}% · `}
                  Planned {pct(plannedMarks, paperTotal)}% · Target {pct(targetMarks, paperTotal)}%
                </span>
              </div>
              <div className="relative h-3 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${colors.bar}`}
                  style={{ width: `${Math.min(100, pct(barMarks, paperTotal))}%` }}
                />
                <div
                  className="absolute inset-y-0 w-0.5 bg-slate-500 dark:bg-slate-300"
                  style={{ left: `${Math.min(100, pct(targetMarks, paperTotal))}%` }}
                  title={`Target: ${fmt(targetMarks)} marks`}
                />
              </div>
              <p className="text-[9px] font-medium text-slate-400 dark:text-slate-500">
                {fmt(barMarks)} of {fmt(paperTotal)} printed marks
              </p>
            </div>
          );
        })}
      </div>

      <p className="text-[9px] text-slate-400 dark:text-slate-500 italic">
        Exact match isn&apos;t always possible because each question is worth fixed marks (2, 5 or 10).
      </p>

      {Object.keys(byUnit).length > 0 && (
        <div className="space-y-2 pt-4 border-t border-slate-100 dark:border-slate-800/60">
          <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">By document</span>
          {Object.entries(byUnit).map(([unitId, marks]) => (
            <div key={unitId} className="flex items-center justify-between gap-3 text-[11px]">
              <span className="text-slate-600 dark:text-slate-300 truncate">{documentNames[unitId] || `Document #${unitId}`}</span>
              <span className="font-bold text-slate-500 dark:text-slate-400 shrink-0">{fmt(marks)} marks</span>
            </div>
          ))}
        </div>
      )}

      {warnings.length > 0 && (
        <div className="space-y-2 pt-4 border-t border-slate-100 dark:border-slate-800/60">
          {warnings.map((w, idx) => (
            <div
              key={idx}
              className="flex items-start gap-2 p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/20 text-[10px] font-medium text-amber-700 dark:text-amber-400"
            >
              <AlertTriangle size={12} className="shrink-0 mt-0.5" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

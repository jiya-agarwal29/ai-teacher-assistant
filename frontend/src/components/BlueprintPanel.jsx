import { AlertTriangle } from 'lucide-react';
import useBloomLevels from '../hooks/useBloomLevels';
import { getBloomColor } from '../utils/bloomColors';
import BloomBadge from './BloomBadge';

const fmt = (n) => {
  const num = Number(n) || 0;
  return Number.isInteger(num) ? String(num) : num.toFixed(1);
};

/**
 * Side panel shared by the "New paper" preview step (target vs the plan's
 * PLANNED marks, nothing generated yet) and the ready paper view (target
 * vs ACTUAL marks from the saved questions) -- which comparison it's
 * showing is just a prop, so both pages read identically. Also lists
 * marks per document and any paper-level warnings (amber).
 */
export default function BlueprintPanel({
  target = {},
  comparison = {},
  comparisonLabel = 'Planned',
  byUnit = {},
  documentNames = {},
  warnings = []
}) {
  const { levels: bloomLevels } = useBloomLevels();
  const targetValues = Object.values(target);
  const comparisonValues = Object.values(comparison);
  const maxMarks = Math.max(1, ...targetValues, ...comparisonValues);

  return (
    <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold text-slate-900 dark:text-white">Blueprint</h3>
        <div className="flex items-center gap-3 text-[9px] font-bold text-slate-400 dark:text-slate-500">
          <span className="flex items-center gap-1">
            <span className="w-2 h-0.5 bg-slate-400 dark:bg-slate-500 inline-block" /> Target
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-violet-500 inline-block" /> {comparisonLabel}
          </span>
        </div>
      </div>

      <div className="space-y-3">
        {bloomLevels.map((lvl) => {
          const targetMarks = target[lvl.key] || 0;
          const comparisonMarks = comparison[lvl.key] || 0;
          const colors = getBloomColor(lvl.key);
          return (
            <div key={lvl.key} className="space-y-1">
              <div className="flex items-center justify-between gap-2">
                <BloomBadge levelKey={lvl.key} />
                <span className="text-[9px] font-bold text-slate-400 dark:text-slate-500 whitespace-nowrap">
                  {fmt(comparisonMarks)} / {fmt(targetMarks)} marks
                </span>
              </div>
              <div className="relative h-3 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${colors.bar}`}
                  style={{ width: `${Math.min(100, (comparisonMarks / maxMarks) * 100)}%` }}
                />
                <div
                  className="absolute inset-y-0 w-0.5 bg-slate-500 dark:bg-slate-300"
                  style={{ left: `${Math.min(100, (targetMarks / maxMarks) * 100)}%` }}
                  title={`Target: ${fmt(targetMarks)} marks`}
                />
              </div>
            </div>
          );
        })}
      </div>

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

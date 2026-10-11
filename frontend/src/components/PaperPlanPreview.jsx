import BlueprintPanel from './BlueprintPanel';
import BloomBadge from './BloomBadge';

/**
 * The "plan" table + blueprint side panel shared by the New Paper
 * builder's preview step and a "draft" paper reopened on its /papers/:id
 * page -- both show the same plan_paper() output before any AI call, so
 * one component renders it for both. `actions` is whatever buttons the
 * caller wants under the table (different per page).
 */
export default function PaperPlanPreview({ paperData, documentNames, actions }) {
  const plan = paperData.plan || [];

  // Group by (section, number) so an either_or pair's two options render
  // as one row.
  const groups = [];
  const indexByKey = new Map();
  for (const slot of plan) {
    const key = `${slot.section}-${slot.number}`;
    if (!indexByKey.has(key)) {
      indexByKey.set(key, groups.length);
      groups.push([]);
    }
    groups[indexByKey.get(key)].push(slot);
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6">
      <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-4">
        <h3 className="text-xs font-bold text-slate-900 dark:text-white">Plan preview</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-left text-slate-400 dark:text-slate-500 text-[9px] font-bold uppercase tracking-wider border-b border-slate-100 dark:border-slate-800/60">
                <th className="py-2 pr-3">Section</th>
                <th className="py-2 pr-3">Q no.</th>
                <th className="py-2 pr-3">Marks</th>
                <th className="py-2 pr-3">Level</th>
                <th className="py-2 pr-3">Document</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((group, idx) => (
                <tr key={idx} className="border-b border-slate-50 dark:border-slate-800/30 last:border-0">
                  <td className="py-2 pr-3 font-bold text-slate-600 dark:text-slate-300">{group[0].section}</td>
                  <td className="py-2 pr-3 text-slate-500 dark:text-slate-400 whitespace-nowrap">
                    {group[0].number}{group.length > 1 ? ' (a / b)' : ''}
                  </td>
                  <td className="py-2 pr-3 text-slate-500 dark:text-slate-400">{group[0].marks}</td>
                  <td className="py-2 pr-3">
                    {group[0].bloom_level
                      ? <BloomBadge levelKey={group[0].bloom_level} />
                      : <span className="text-rose-500 dark:text-rose-400 text-[9px] font-bold">None possible</span>}
                  </td>
                  <td className="py-2 pr-3 text-slate-500 dark:text-slate-400">
                    {group.map((s) => documentNames[s.unit_id] || `Document #${s.unit_id}`).join(' / ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {actions && <div className="flex items-center gap-3 pt-2 flex-wrap">{actions}</div>}
      </div>

      <BlueprintPanel
        target={paperData.blueprint_target}
        planned={paperData.blueprint_planned?.by_level}
        byUnit={paperData.blueprint_planned?.by_unit}
        documentNames={documentNames}
        warnings={paperData.warnings}
      />
    </div>
  );
}

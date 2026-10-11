import { AlertTriangle } from 'lucide-react';

// Matches _slot_label() + _sync_slot_warning()/generate_paper()'s warning
// text in main.py, e.g. "Section 'A' question 4: classified as Remember,
// not the intended Understand." or "Section 'B' question 3 (b): couldn't
// generate a question for this slot." -- capturing the section, the
// question number (with its (a)/(b) option suffix if any), and the rest
// of the sentence so each can be turned into a clickable "Question N"
// link plus a plain-English tail.
const SLOT_WARNING_PATTERN = /^Section '([^']+)' question ([^:]+):\s*(.+)$/;

function findSlotId(slots, section, numberLabel) {
  const match = numberLabel.match(/^(\S+)(?:\s*\((\w)\))?$/);
  if (!match) return null;
  const [, number, option] = match;
  const slot = slots.find((s) => (
    s.section === section && s.number === number && (option ? s.option === option : !s.option)
  ));
  return slot ? slot.id : null;
}

/**
 * Replaces the generic amber warnings list, for the one place warnings are
 * actually about specific, already-generated questions a teacher can act
 * on (Phase 5 fixes from browser testing, item 6) -- the "New paper"
 * preview step's feasibility warnings (an unreachable Bloom target, a
 * unit getting too few marks) still go through BlueprintPanel's plain
 * list, since there's no question yet to jump to or fix.
 */
export default function QuestionsToReview({ warnings = [], slots = [] }) {
  if (warnings.length === 0) return null;

  const jumpToSlot = (section, numberLabel) => {
    const slotId = findSlotId(slots, section, numberLabel);
    if (slotId == null) return;
    document.getElementById(`paper-slot-${slotId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  return (
    <div className="p-6 rounded-[28px] bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 shadow-sm space-y-3 print:hidden">
      <h3 className="text-xs font-bold text-slate-900 dark:text-white">Questions to review</h3>
      <p className="text-[10px] text-slate-400 dark:text-slate-500 leading-relaxed">
        The level checker thinks these questions don&apos;t match their planned level. Click Regenerate, Swap,
        or Edit on the question itself -- or keep it if you agree with the question.
      </p>
      <div className="space-y-2">
        {warnings.map((w, idx) => {
          const match = w.match(SLOT_WARNING_PATTERN);
          if (!match) {
            return (
              <div
                key={idx}
                className="flex items-start gap-2 p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/20 text-[10px] font-medium text-amber-700 dark:text-amber-400"
              >
                <AlertTriangle size={12} className="shrink-0 mt-0.5" />
                <span>{w}</span>
              </div>
            );
          }
          const [, section, numberLabel, rest] = match;
          return (
            <div
              key={idx}
              className="flex items-start gap-2 p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/20 text-[10px] font-medium text-amber-700 dark:text-amber-400"
            >
              <AlertTriangle size={12} className="shrink-0 mt-0.5" />
              <span>
                <button
                  type="button"
                  onClick={() => jumpToSlot(section, numberLabel)}
                  className="font-bold underline hover:no-underline"
                >
                  Question {numberLabel}
                </button>
                {' '}(Section {section}) {rest}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

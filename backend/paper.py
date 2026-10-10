"""
Phase 5A: exam paper patterns and the blueprint planner.

Deliberately pure Python -- no imports from main.py, llm.py, database.py or
any other part of this app (bloom.py is the one exception: it's the single
source of truth for Bloom's six levels and which question *types* suit
each one, and this module builds directly on that). No API routes live
here; this is just the planning logic a later phase's endpoints will call.

Two kinds of "total marks" recur throughout, and the difference matters:
  - marks_on_paper(): every DISTINCT numbered question on the printed
    paper counts once, at its full marks -- an either_or question (two
    alternative options, one answered) counts once, not twice, since both
    options are worth the same marks and test the same Bloom level.
  - total_marks(): what a student who answers optimally actually scores --
    identical to marks_on_paper() except for an any_n section, where only
    `attempt` of the `num_questions` printed are actually answered/graded.
"""
from dataclasses import dataclass

import bloom

CHOICE_KINDS = ("none", "any_n", "either_or")
PAPER_KINDS = ("written", "mcq")


# -----------------------------
# Pattern model
# -----------------------------
@dataclass(frozen=True)
class Section:
    key: str
    title: str
    instructions: str
    marks_each: int
    num_questions: int
    choice: str = "none"          # "none" | "any_n" | "either_or"
    attempt: int = None           # only meaningful when choice == "any_n"


@dataclass(frozen=True)
class Pattern:
    key: str
    name: str
    duration_minutes: int
    sections: tuple
    paper_kind: str = "written"   # "written" | "mcq"


def validate_pattern(p: Pattern) -> list:
    """Returns a list of human-readable error strings; empty means valid."""
    errors = []

    if p.duration_minutes <= 0:
        errors.append(f"Pattern '{p.key}': duration_minutes must be positive.")
    if p.paper_kind not in PAPER_KINDS:
        errors.append(f"Pattern '{p.key}': unknown paper_kind '{p.paper_kind}'.")
    if not p.sections:
        errors.append(f"Pattern '{p.key}': has no sections.")
        return errors

    seen_keys = set()
    for section in p.sections:
        if not section.key:
            errors.append(f"Pattern '{p.key}': a section is missing its key.")
        elif section.key in seen_keys:
            errors.append(f"Pattern '{p.key}': duplicate section key '{section.key}'.")
        else:
            seen_keys.add(section.key)

        label = f"Pattern '{p.key}' section '{section.key}'"

        if section.num_questions <= 0:
            errors.append(f"{label}: num_questions must be positive (an empty section).")
        if section.marks_each <= 0:
            errors.append(f"{label}: marks_each must be positive.")
        if section.choice not in CHOICE_KINDS:
            errors.append(f"{label}: unknown choice '{section.choice}'.")
        if section.choice == "any_n":
            if not section.attempt or section.attempt <= 0:
                errors.append(f"{label}: choice 'any_n' needs a positive attempt count.")
            elif section.num_questions > 0 and section.attempt > section.num_questions:
                errors.append(
                    f"{label}: attempt ({section.attempt}) exceeds num_questions ({section.num_questions})."
                )
        if p.paper_kind == "mcq" and section.marks_each > 2:
            errors.append(f"{label}: MCQ papers can't have marks_each > 2 (got {section.marks_each}).")

    return errors


def _section_marks_on_paper(section: Section) -> int:
    return section.num_questions * section.marks_each


def _section_total_marks(section: Section) -> int:
    if section.choice == "any_n":
        return (section.attempt or 0) * section.marks_each
    return _section_marks_on_paper(section)


def marks_on_paper(p: Pattern) -> int:
    """Every distinct numbered question on the printed paper, once each."""
    return sum(_section_marks_on_paper(s) for s in p.sections)


def total_marks(p: Pattern) -> int:
    """What a student answering optimally actually scores."""
    return sum(_section_total_marks(s) for s in p.sections)


# -----------------------------
# Preset patterns
# -----------------------------
PRESET_PATTERNS = {
    "mid_sem_50": Pattern(
        key="mid_sem_50",
        name="Mid-Semester Exam (50 marks)",
        duration_minutes=120,
        paper_kind="written",
        sections=(
            Section(
                key="A", title="Section A", instructions="Answer all questions.",
                marks_each=2, num_questions=5, choice="none"
            ),
            Section(
                key="B", title="Section B", instructions="Attempt any 4 of the following 5 questions.",
                marks_each=5, num_questions=5, choice="any_n", attempt=4
            ),
            Section(
                key="C", title="Section C", instructions="Answer EITHER (a) OR (b) for each question.",
                marks_each=10, num_questions=2, choice="either_or"
            ),
        ),
    ),
    "end_sem_100": Pattern(
        key="end_sem_100",
        name="End-Semester Exam (100 marks)",
        duration_minutes=180,
        paper_kind="written",
        sections=(
            Section(
                key="A", title="Section A", instructions="Answer all questions.",
                marks_each=2, num_questions=10, choice="none"
            ),
            Section(
                key="B", title="Section B", instructions="Attempt any 6 of the following 8 questions.",
                marks_each=5, num_questions=8, choice="any_n", attempt=6
            ),
            Section(
                key="C", title="Section C", instructions="Answer EITHER (a) OR (b) for each question.",
                marks_each=10, num_questions=5, choice="either_or"
            ),
        ),
    ),
    "mcq_30": Pattern(
        key="mcq_30",
        name="MCQ Test (30 marks)",
        duration_minutes=60,
        paper_kind="mcq",
        sections=(
            Section(
                key="A", title="Section A", instructions="Choose the correct option for each question.",
                marks_each=1, num_questions=30, choice="none"
            ),
        ),
    ),
}


# -----------------------------
# Bloom-percentage presets
# -----------------------------
BLOOM_PRESETS = {
    "balanced": {
        "remember": 15, "understand": 20, "apply": 20,
        "analyze": 20, "evaluate": 15, "create": 10
    },
    "basics": {
        "remember": 30, "understand": 30, "apply": 20,
        "analyze": 10, "evaluate": 5, "create": 5
    },
    "higher_order": {
        "remember": 5, "understand": 15, "apply": 20,
        "analyze": 25, "evaluate": 20, "create": 15
    },
    # MCQ papers can't use Evaluate/Create -- neither level's question_types
    # includes "mcq" in bloom.py, so a slot can never be assigned either on
    # an MCQ paper (see allowed_types_for_slot below).
    "mcq_friendly": {
        "remember": 25, "understand": 30, "apply": 30,
        "analyze": 15, "evaluate": 0, "create": 0
    },
}


def validate_bloom_targets(targets: dict) -> list:
    """Returns a list of human-readable error strings; empty means valid."""
    errors = []
    if not targets:
        errors.append("No Bloom targets given.")
        return errors

    for key, pct in targets.items():
        if not bloom.is_valid_level(key):
            errors.append(f"Unknown Bloom's level: '{key}'")
        elif pct < 0:
            errors.append(f"Bloom target for '{key}' cannot be negative (got {pct}).")

    total = sum(pct for pct in targets.values())
    if abs(total - 100) > 0.5:
        errors.append(f"Bloom targets must sum to 100 (got {total:g}).")

    return errors


# -----------------------------
# Level/type rules by marks
# -----------------------------
# marks -> the Bloom levels a question of that size can test, in canonical
# (remember -> create) order.
_LEVELS_BY_MARKS_BAND = {
    "low": ("remember", "understand", "apply"),            # 1-2 marks
    "mid": ("understand", "apply", "analyze", "evaluate"),  # 3-6 marks
    "high": ("analyze", "evaluate", "create"),               # 7+ marks
}

# marks -> which question *types* fit in that much space, regardless of
# level. mcq/true_false (a single pre-written correct option) only ever
# make sense for a 1-2 mark slot -- a 3+ mark slot needs an actual written
# answer (short_answer/scenario/long_answer), never a multiple-choice or
# true/false pick (Phase 5B quality fix: a 5+ mark MCQ was both unrealistic
# for a written exam and easy for the model to get subtly wrong). MCQ
# papers are unaffected: validate_pattern already rejects any MCQ-paper
# section with marks_each > 2, so they only ever land in the "low" band.
_TYPES_BY_MARKS_BAND = {
    "low": ("mcq", "true_false", "fill_blank", "short_answer"),
    "mid": ("short_answer", "scenario", "long_answer"),
    "high": ("long_answer", "scenario"),
}


def _marks_band(marks: int) -> str:
    if marks <= 2:
        return "low"
    if marks <= 6:
        return "mid"
    return "high"


def levels_allowed_for_marks(marks: int) -> list:
    """The Bloom levels a question worth `marks` marks is allowed to test."""
    return list(_LEVELS_BY_MARKS_BAND[_marks_band(marks)])


def allowed_types_for_slot(level_key: str, marks: int, paper_kind: str = "written") -> list:
    """
    Question types valid for a slot at `level_key` worth `marks` marks:
    bloom.py's allowed types for that level, intersected with the types
    that fit `marks` marks, further intersected with just "mcq" for an
    MCQ paper. Empty means this (level, marks, paper_kind) combination is
    impossible -- e.g. Evaluate/Create on an MCQ paper, always.
    """
    level = bloom.get_level(level_key)
    band_types = _TYPES_BY_MARKS_BAND[_marks_band(marks)]
    types = [t for t in level.question_types if t in band_types]
    if paper_kind == "mcq":
        types = [t for t in types if t == "mcq"]
    return types


# -----------------------------
# Blueprint planner
# -----------------------------
def _enumerate_slot_groups(pattern: Pattern) -> list:
    """
    One group per printed question: a list with one slot dict for "none"/
    "any_n", or two (option "a" and "b", same marks) for "either_or".
    Question numbers run consecutively across the whole paper, section by
    section, in pattern.sections order -- deterministic given the pattern.
    """
    groups = []
    number = 1
    for section in pattern.sections:
        for _ in range(section.num_questions):
            if section.choice == "either_or":
                groups.append([
                    {"section": section.key, "number": str(number), "option": "a", "marks": section.marks_each},
                    {"section": section.key, "number": str(number), "option": "b", "marks": section.marks_each},
                ])
            else:
                groups.append([
                    {"section": section.key, "number": str(number), "option": None, "marks": section.marks_each},
                ])
            number += 1
    return groups


def _group_marks(group: list) -> int:
    # Both options of an either_or pair carry the same marks -- this is
    # the ONE value that counts toward marks_on_paper for the whole group.
    return group[0]["marks"]


def _eligible_levels_for_group(group: list, paper_kind: str) -> list:
    marks = _group_marks(group)
    return [
        level for level in levels_allowed_for_marks(marks)
        if allowed_types_for_slot(level, marks, paper_kind)
    ]


def _pick_level(eligible_levels: list, assigned: dict, targets: dict) -> str:
    """
    Picks the eligible level with the largest remaining gap (target minus
    marks already assigned) -- i.e. whichever allowed level most needs
    marks right now. If every eligible level has already met or exceeded
    its target, this naturally falls back to minimizing overshoot instead
    (the least-negative gap), covering "fill with levels still under
    target, then the rest" as one consistent rule. Ties favour the higher
    Bloom level.
    """
    def gap(level):
        return targets.get(level, 0) - assigned.get(level, 0)

    return max(eligible_levels, key=lambda level: (gap(level), bloom.get_level(level).order))


def _assign_coverage(groups: list, group_eligibility: list, bloom_targets: dict) -> tuple:
    """
    Phase 0, run before any marks-balancing: every level with a target > 0
    that has at least one eligible slot ANYWHERE on the paper must get
    assigned to one of them, even if that overshoots its target -- a level
    only skips this if NO slot allows it at all (that's a real
    impossibility, reported separately). Processes the scarcest levels
    first (fewest eligible slots) so a level that can only ever use one
    kind of slot (e.g. Create, high-marks-only) claims one before a level
    with many alternatives (e.g. Understand) crowds it out; among a
    level's eligible slots, picks the smallest-marks one first so a big
    slot isn't burned on coverage when a small one would do.

    Returns (level_by_group_index, levels_with_no_eligible_slot_at_all).
    """
    needs_coverage = [lvl for lvl in bloom.level_keys() if bloom_targets.get(lvl, 0) > 0]

    eligible_indices = {
        lvl: [i for i, elig in enumerate(group_eligibility) if lvl in elig]
        for lvl in needs_coverage
    }
    impossible = [lvl for lvl in needs_coverage if not eligible_indices[lvl]]
    coverable = [lvl for lvl in needs_coverage if eligible_indices[lvl]]

    # Scarcest first; ties broken by canonical level order for determinism.
    coverable.sort(key=lambda lvl: (len(eligible_indices[lvl]), bloom.get_level(lvl).order))

    level_by_group = [None] * len(groups)
    used_groups = set()
    for lvl in coverable:
        candidates = [i for i in eligible_indices[lvl] if i not in used_groups]
        if not candidates:
            # Every eligible slot was already claimed by a scarcer level's
            # coverage pick -- this level may still pick up marks during
            # the balancing pass if it shares eligibility with a slot that
            # ends up unclaimed, but has no slot reserved just for it.
            continue
        chosen = min(candidates, key=lambda i: (_group_marks(groups[i]), i))
        level_by_group[chosen] = lvl
        used_groups.add(chosen)

    return level_by_group, impossible


def _impossibility_reason(level_key: str, paper_kind: str) -> str:
    bands = [
        band_label for band_label, sample_marks in (("1-2 mark", 2), ("3-6 mark", 5), ("7+ mark", 8))
        if level_key in levels_allowed_for_marks(sample_marks)
    ]
    if paper_kind == "mcq" and "mcq" not in bloom.get_level(level_key).question_types:
        return "MCQ papers don't allow this level"
    if not bands:
        return "no marks band allows this level"
    return f"no {'/'.join(bands)} slots on this paper"


def _pick_unit(unit_ids: list, assigned: dict, targets: dict, exclude=None) -> str:
    candidates = [uid for uid in unit_ids if uid != exclude] or list(unit_ids)
    return max(candidates, key=lambda uid: targets.get(uid, 0) - assigned.get(uid, 0))


def _apportion_seats(total_seats: int, weights: dict) -> dict:
    """
    Hamilton's largest-remainder method: splits `total_seats` discrete
    seats across `weights` (unit_id -> weight) as proportionally as
    possible -- the standard way to turn weight ratios into whole counts
    that still sum to exactly `total_seats`. Ties on the fractional
    remainder favour the larger-weight unit, then dict order, so the
    result is deterministic.
    """
    unit_ids = list(weights.keys())
    total_weight = sum(weights.values()) or 1
    exact = {uid: total_seats * weights[uid] / total_weight for uid in unit_ids}
    seats = {uid: int(exact[uid]) for uid in unit_ids}  # floor

    remaining = total_seats - sum(seats.values())
    order = sorted(
        unit_ids,
        key=lambda uid: (-(exact[uid] - seats[uid]), -weights[uid], unit_ids.index(uid))
    )
    for uid in order[:remaining]:
        seats[uid] += 1

    return seats


def _assign_units(groups: list, pattern: Pattern, units: list) -> dict:
    """
    Assigns a unit to every slot, section by section. Each section gets
    its own per-unit QUOTA -- `section.num_questions` seats apportioned
    across the units by weight (_apportion_seats), the same global
    weights every section uses, so the whole paper still follows the
    overall unit weights even though each section is balanced on its own
    (e.g. weights 3:2:1 over a 5-question section apportions to 2/2/1 --
    the standard largest-remainder answer; a cruder split like 3/1/1
    would over-represent the heaviest unit relative to its actual share).
    Groups within a section are assigned to whichever quota-holding unit
    needs it most (most seats still unused); either_or pairs always draw
    two DIFFERENT units for their two options (when 2+ units exist), which
    on its own already guarantees that section isn't monotone.

    Returns {
      "unit_by_slot_index": {...},
      "by_unit": {...}          -- marks_on_paper convention (either_or's
                                    option (a) only), so sums to marks_on_paper
      "units_including_alternatives": {...}  -- every slot counted, both
                                    either_or options included
      "unit_ids": [...], "targets_main": {...}  -- for the warning check
    }
    """
    unit_ids = [u["id"] for u in units]
    if not unit_ids:
        return {
            "unit_by_slot_index": {},
            "by_unit": {},
            "units_including_alternatives": {},
            "unit_ids": [],
            "targets_main": {},
        }

    weights = {u["id"]: (u.get("weight") or 1) for u in units}
    total_weight = sum(weights.values()) or 1

    section_of_group = []
    for section in pattern.sections:
        section_of_group.extend([section.key] * section.num_questions)

    section_group_indices = {}
    for gi, key in enumerate(section_of_group):
        section_group_indices.setdefault(key, []).append(gi)

    unit_by_group = [None] * len(groups)       # "none"/"any_n": a single unit
    unit_pair_by_group = [None] * len(groups)  # "either_or": (unit_a, unit_b)

    # Secondary tie-break for _pick_quota_unit below, tracked globally
    # (not reset per section) so that when several units are equally
    # under-quota, picks spread across units instead of always favouring
    # whichever happens to be first in `unit_ids`.
    global_draws = {uid: 0 for uid in unit_ids}

    def _pick_quota_unit(quota_remaining, exclude=None):
        candidates = [uid for uid in unit_ids if uid != exclude and quota_remaining.get(uid, 0) > 0]
        if not candidates:
            candidates = [uid for uid in unit_ids if uid != exclude] or list(unit_ids)
        chosen = min(candidates, key=lambda uid: (-quota_remaining.get(uid, 0), global_draws[uid]))
        global_draws[chosen] += 1
        return chosen

    for section in pattern.sections:
        group_indices = section_group_indices.get(section.key, [])
        if not group_indices:
            continue
        # Each either_or question draws a unit TWICE (one per option), so
        # the quota basis is total draws, not question count, or an
        # either_or-heavy section would run out of quota halfway through.
        total_draws = sum(2 if len(groups[gi]) == 2 else 1 for gi in group_indices)
        quota_remaining = _apportion_seats(total_draws, weights)

        for gi in group_indices:
            group = groups[gi]
            if len(group) == 2:
                u1 = _pick_quota_unit(quota_remaining)
                quota_remaining[u1] -= 1
                exclude = u1 if len(unit_ids) >= 2 else None
                u2 = _pick_quota_unit(quota_remaining, exclude=exclude)
                quota_remaining[u2] -= 1
                unit_pair_by_group[gi] = (u1, u2)
            else:
                u = _pick_quota_unit(quota_remaining)
                quota_remaining[u] -= 1
                unit_by_group[gi] = u

    # --- Flatten into per-slot-index maps, and the two reporting totals ---
    unit_by_slot_index = {}
    by_unit = {uid: 0 for uid in unit_ids}                    # marks_on_paper convention
    units_including_alternatives = {uid: 0 for uid in unit_ids}

    slot_index = 0
    for gi, group in enumerate(groups):
        if unit_pair_by_group[gi] is not None:
            u1, u2 = unit_pair_by_group[gi]
            unit_by_slot_index[slot_index] = u1
            unit_by_slot_index[slot_index + 1] = u2
            by_unit[u1] += group[0]["marks"]  # option (a) only -- matches marks_on_paper
            units_including_alternatives[u1] += group[0]["marks"]
            units_including_alternatives[u2] += group[1]["marks"]
        else:
            u = unit_by_group[gi]
            unit_by_slot_index[slot_index] = u
            by_unit[u] += group[0]["marks"]
            units_including_alternatives[u] += group[0]["marks"]
        slot_index += len(group)

    targets_main = {uid: (weights[uid] / total_weight) * marks_on_paper(pattern) for uid in unit_ids}

    return {
        "unit_by_slot_index": unit_by_slot_index,
        "by_unit": by_unit,
        "units_including_alternatives": units_including_alternatives,
        "unit_ids": unit_ids,
        "targets_main": targets_main,
    }


def plan_paper(pattern: Pattern, bloom_targets: dict, units: list) -> dict:
    """
    Builds a deterministic blueprint: one plan entry per printed slot
    (section, number, option, marks, bloom_level, unit_id, allowed_types),
    plus blueprint_target (marks per level), blueprint_planned (marks per
    level and per unit), and warnings for anything the targets/units asked
    for that the pattern structurally can't deliver. Never raises on an
    unreachable target -- it just gets a warning and the rest of the plan
    still comes back usable.
    """
    warnings = []
    groups = _enumerate_slot_groups(pattern)
    paper_total = marks_on_paper(pattern)

    targets_marks = {
        level: (bloom_targets.get(level, 0) / 100.0) * paper_total
        for level in bloom.level_keys()
    }

    group_eligibility = [_eligible_levels_for_group(group, pattern.paper_kind) for group in groups]

    for i, group in enumerate(groups):
        if not group_eligibility[i]:
            slot0 = group[0]
            warnings.append(
                f"Section '{slot0['section']}' question {slot0['number']}: no Bloom level fits "
                f"{_group_marks(group)} mark(s) on a {pattern.paper_kind} paper."
            )

    # Phase 0: every targeted level that has ANY eligible slot gets one,
    # even if that overshoots -- before marks-balancing runs at all.
    level_by_group, impossible_levels = _assign_coverage(groups, group_eligibility, bloom_targets)

    assigned_marks = {level: 0 for level in bloom.level_keys()}
    for i, level in enumerate(level_by_group):
        if level:
            assigned_marks[level] += _group_marks(groups[i])

    # Phase 1: balance the remaining (uncovered) slots, biggest first
    # (stable sort keeps paper order among ties, so the result is
    # deterministic for a given pattern/targets/units).
    remaining = [i for i, level in enumerate(level_by_group) if level is None and group_eligibility[i]]
    remaining.sort(key=lambda i: -_group_marks(groups[i]))

    for i in remaining:
        level = _pick_level(group_eligibility[i], assigned_marks, targets_marks)
        level_by_group[i] = level
        assigned_marks[level] += _group_marks(groups[i])

    for level in impossible_levels:
        target_pct = bloom_targets.get(level, 0)
        reason = _impossibility_reason(level, pattern.paper_kind)
        warnings.append(f"{bloom.get_level(level).label} target {target_pct:g}% can't be met: {reason}")

    # Reorder each section's questions lower Bloom level first (an
    # either_or pair is one group, so both its options always move
    # together). Stable sort keeps same-level questions in their original
    # relative order, so this is still fully deterministic. Units are
    # assigned, and the plan numbered, AFTER this reorder, against the
    # final sequence.
    def _level_order(gi):
        level = level_by_group[gi]
        return bloom.get_level(level).order if level else len(bloom.LEVELS) + 1

    reordered_groups = []
    reordered_levels = []
    start = 0
    for section in pattern.sections:
        section_indices = sorted(range(start, start + section.num_questions), key=_level_order)
        for gi in section_indices:
            reordered_groups.append(groups[gi])
            reordered_levels.append(level_by_group[gi])
        start += section.num_questions
    groups = reordered_groups
    level_by_group = reordered_levels

    number = 1
    for group in groups:
        for slot in group:
            slot["number"] = str(number)
        number += 1

    # --- Units ---
    unit_plan = _assign_units(groups, pattern, units)
    unit_by_slot_index = unit_plan["unit_by_slot_index"]

    if unit_plan["unit_ids"]:
        unit_names = {u["id"]: u.get("name", u["id"]) for u in units}
        for uid in unit_plan["unit_ids"]:
            fair_share = unit_plan["targets_main"][uid]
            if fair_share > 0 and unit_plan["by_unit"][uid] < fair_share * 0.5:
                warnings.append(
                    f"Unit '{unit_names[uid]}' gets only {unit_plan['by_unit'][uid]:g} of {paper_total} marks"
                )

    # --- Assemble the plan ---
    plan = []
    slot_index = 0
    for gi, group in enumerate(groups):
        level = level_by_group[gi]
        marks = _group_marks(group)
        types = allowed_types_for_slot(level, marks, pattern.paper_kind) if level else []
        for slot in group:
            plan.append({
                "section": slot["section"],
                "number": slot["number"],
                "option": slot["option"],
                "marks": slot["marks"],
                "bloom_level": level,
                "unit_id": unit_by_slot_index.get(slot_index),
                "allowed_types": types,
            })
            slot_index += 1

    return {
        "plan": plan,
        "blueprint_target": {level: round(targets_marks[level], 2) for level in bloom.level_keys()},
        "blueprint_planned": {
            "by_level": dict(assigned_marks),
            # marks_on_paper convention (either_or's option (a) only) --
            # counted the same way as Bloom levels, so this sums to
            # marks_on_paper(pattern).
            "by_unit": dict(unit_plan["by_unit"]),
            # Every printed slot, both either_or options included -- how
            # much of each unit's content actually appears on the page,
            # regardless of which option gets answered. Sums to more than
            # marks_on_paper(pattern) whenever any either_or section exists.
            "units_including_alternatives": dict(unit_plan["units_including_alternatives"]),
        },
        "warnings": warnings,
    }

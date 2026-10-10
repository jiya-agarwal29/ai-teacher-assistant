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

# marks -> which question *types* fit in that much space, regardless of level.
_TYPES_BY_MARKS_BAND = {
    "low": ("mcq", "true_false", "fill_blank", "short_answer"),
    "mid": ("short_answer", "scenario", "long_answer", "mcq"),
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


def _assign_units(groups: list, pattern: Pattern, units: list) -> dict:
    """
    Assigns a unit to every slot. Driven by ONE running balance
    (`assigned_full`, every printed slot counted once each -- both
    either_or options included, since that's what's really spread across
    the page) against `targets_full` (the same convention). Two things
    layered on top of that single greedy pick:

    - Either_or pairs always get two DIFFERENT units for their two options
      (when 2+ units exist).
    - A repair pass afterward: no section with 2+ questions may end up
      using only one unit when 2+ units exist overall -- if the greedy
      picks left a section monotone, its last question is moved to the
      next-best alternative unit.

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

    all_slots = [slot for group in groups for slot in group]
    full_total = sum(slot["marks"] for slot in all_slots)
    targets_full = {uid: (weights[uid] / total_weight) * full_total for uid in unit_ids}
    assigned_full = {uid: 0 for uid in unit_ids}

    section_of_group = []
    for section in pattern.sections:
        section_of_group.extend([section.key] * section.num_questions)

    unit_by_group = [None] * len(groups)       # "none"/"any_n": a single unit
    unit_pair_by_group = [None] * len(groups)  # "either_or": (unit_a, unit_b)

    for gi, group in enumerate(groups):
        if len(group) == 2:
            u1 = _pick_unit(unit_ids, assigned_full, targets_full)
            assigned_full[u1] += group[0]["marks"]
            exclude = u1 if len(unit_ids) >= 2 else None
            u2 = _pick_unit(unit_ids, assigned_full, targets_full, exclude=exclude)
            assigned_full[u2] += group[1]["marks"]
            unit_pair_by_group[gi] = (u1, u2)
        else:
            u = _pick_unit(unit_ids, assigned_full, targets_full)
            assigned_full[u] += group[0]["marks"]
            unit_by_group[gi] = u

    # Repair pass: no 2+-question section may be monotone when 2+ units exist.
    if len(unit_ids) >= 2:
        section_group_indices = {}
        for gi, key in enumerate(section_of_group):
            section_group_indices.setdefault(key, []).append(gi)

        for group_indices in section_group_indices.values():
            if len(group_indices) < 2:
                continue  # exempt -- the section has only 1 question

            units_in_section = set()
            for gi in group_indices:
                if unit_pair_by_group[gi] is not None:
                    units_in_section.update(unit_pair_by_group[gi])
                else:
                    units_in_section.add(unit_by_group[gi])

            if len(units_in_section) > 1:
                continue  # already spread -- nothing to repair

            # Monotone (either_or pairs already guarantee 2+ units on
            # their own, so only a run of "none"/"any_n" slots can land
            # here) -- move the section's last slot to the next-best unit.
            only_unit = next(iter(units_in_section))
            last_gi = group_indices[-1]
            last_group = groups[last_gi]
            assigned_full[only_unit] -= last_group[0]["marks"]
            new_unit = _pick_unit(unit_ids, assigned_full, targets_full, exclude=only_unit)
            assigned_full[new_unit] += last_group[0]["marks"]
            unit_by_group[last_gi] = new_unit

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

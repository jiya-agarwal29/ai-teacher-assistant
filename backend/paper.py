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

    # Biggest slots first (stable sort keeps paper order among ties, so
    # the result is deterministic for a given pattern/targets/units).
    order = sorted(range(len(groups)), key=lambda i: -_group_marks(groups[i]))

    assigned_marks = {level: 0 for level in bloom.level_keys()}
    level_by_group = [None] * len(groups)

    for i in order:
        eligible = group_eligibility[i]
        if not eligible:
            slot0 = groups[i][0]
            warnings.append(
                f"Section '{slot0['section']}' question {slot0['number']}: no Bloom level fits "
                f"{_group_marks(groups[i])} mark(s) on a {pattern.paper_kind} paper."
            )
            continue
        level = _pick_level(eligible, assigned_marks, targets_marks)
        level_by_group[i] = level
        assigned_marks[level] += _group_marks(groups[i])

    for level in bloom.level_keys():
        target_pct = bloom_targets.get(level, 0)
        if target_pct <= 0:
            continue
        ever_eligible = any(level in elig for elig in group_eligibility)
        if not ever_eligible:
            reason = _impossibility_reason(level, pattern.paper_kind)
            warnings.append(f"{bloom.get_level(level).label} target {target_pct:g}% can't be met: {reason}")
        elif assigned_marks[level] == 0:
            warnings.append(
                f"{bloom.get_level(level).label} target {target_pct:g}% could not be scheduled -- "
                "its eligible slots all went to other levels competing for the same marks."
            )

    # --- Units ---
    all_slots = [slot for group in groups for slot in group]
    unit_ids = [u["id"] for u in units]
    unit_by_slot_index = {}
    assigned_by_unit = {uid: 0 for uid in unit_ids}

    if units:
        weights = {u["id"]: (u.get("weight") or 1) for u in units}
        total_weight = sum(weights.values()) or 1
        # Every printed slot counts once here (both either_or options
        # included) -- a deliberately different convention from
        # marks_on_paper()'s "one option" rule, since unit *coverage* on
        # the page is what this is balancing, not gradable Bloom-level value.
        printed_total = sum(slot["marks"] for slot in all_slots)
        targets_by_unit = {uid: (w / total_weight) * printed_total for uid, w in weights.items()}

        slot_index = 0
        for group in groups:
            if len(group) == 2:
                first_unit = _pick_unit(unit_ids, assigned_by_unit, targets_by_unit)
                assigned_by_unit[first_unit] += group[0]["marks"]
                unit_by_slot_index[slot_index] = first_unit

                exclude = first_unit if len(unit_ids) >= 2 else None
                second_unit = _pick_unit(unit_ids, assigned_by_unit, targets_by_unit, exclude=exclude)
                assigned_by_unit[second_unit] += group[1]["marks"]
                unit_by_slot_index[slot_index + 1] = second_unit
            else:
                picked = _pick_unit(unit_ids, assigned_by_unit, targets_by_unit)
                assigned_by_unit[picked] += group[0]["marks"]
                unit_by_slot_index[slot_index] = picked
            slot_index += len(group)

        paper_face_value = total_marks(pattern)
        unit_names = {u["id"]: u.get("name", u["id"]) for u in units}
        for uid in unit_ids:
            fair_share = targets_by_unit[uid]
            if fair_share > 0 and assigned_by_unit[uid] < fair_share * 0.5:
                warnings.append(
                    f"Unit '{unit_names[uid]}' gets only {assigned_by_unit[uid]:g} of {paper_face_value} marks"
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
            "by_unit": dict(assigned_by_unit),
        },
        "warnings": warnings,
    }

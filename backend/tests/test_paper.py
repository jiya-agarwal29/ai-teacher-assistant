"""
Tests for paper.py -- pure Python, no server/database/network needed.
"""
import pytest

import bloom
import paper


# -----------------------------
# Preset totals
# -----------------------------
def test_mid_sem_50_totals():
    p = paper.PRESET_PATTERNS["mid_sem_50"]
    assert paper.validate_pattern(p) == []
    assert paper.total_marks(p) == 50
    # A: 5x2=10, B: 5x5=25 (all 5 printed), C: 2x10=20 (one option each) -> 55.
    assert paper.marks_on_paper(p) == 55


def test_end_sem_100_totals():
    p = paper.PRESET_PATTERNS["end_sem_100"]
    assert paper.validate_pattern(p) == []
    assert paper.total_marks(p) == 100
    # A: 10x2=20, B: 8x5=40 (all 8 printed), C: 5x10=50 -> 110.
    assert paper.marks_on_paper(p) == 110


def test_mcq_30_totals():
    p = paper.PRESET_PATTERNS["mcq_30"]
    assert paper.validate_pattern(p) == []
    assert paper.total_marks(p) == 30
    assert paper.marks_on_paper(p) == 30  # no choice sections -- both are equal


# -----------------------------
# validate_pattern
# -----------------------------
def test_validate_pattern_empty_sections():
    p = paper.Pattern(key="bad", name="Bad", duration_minutes=60, sections=())
    errors = paper.validate_pattern(p)
    assert any("no sections" in e for e in errors)


def test_validate_pattern_non_positive_marks():
    p = paper.Pattern(key="bad", name="Bad", duration_minutes=60, sections=(
        paper.Section(key="A", title="A", instructions="", marks_each=0, num_questions=5),
    ))
    errors = paper.validate_pattern(p)
    assert any("marks_each must be positive" in e for e in errors)


def test_validate_pattern_non_positive_num_questions():
    p = paper.Pattern(key="bad", name="Bad", duration_minutes=60, sections=(
        paper.Section(key="A", title="A", instructions="", marks_each=5, num_questions=0),
    ))
    errors = paper.validate_pattern(p)
    assert any("num_questions must be positive" in e for e in errors)


def test_validate_pattern_attempt_exceeds_num_questions():
    p = paper.Pattern(key="bad", name="Bad", duration_minutes=60, sections=(
        paper.Section(key="A", title="A", instructions="", marks_each=5, num_questions=3, choice="any_n", attempt=5),
    ))
    errors = paper.validate_pattern(p)
    assert any("exceeds num_questions" in e for e in errors)


def test_validate_pattern_any_n_without_attempt():
    p = paper.Pattern(key="bad", name="Bad", duration_minutes=60, sections=(
        paper.Section(key="A", title="A", instructions="", marks_each=5, num_questions=5, choice="any_n"),
    ))
    errors = paper.validate_pattern(p)
    assert any("positive attempt count" in e for e in errors)


def test_validate_pattern_mcq_marks_too_high():
    p = paper.Pattern(key="bad", name="Bad", duration_minutes=60, paper_kind="mcq", sections=(
        paper.Section(key="A", title="A", instructions="", marks_each=5, num_questions=3),
    ))
    errors = paper.validate_pattern(p)
    assert any("MCQ papers can't have marks_each > 2" in e for e in errors)


def test_validate_pattern_duplicate_section_keys():
    p = paper.Pattern(key="bad", name="Bad", duration_minutes=60, sections=(
        paper.Section(key="A", title="A", instructions="", marks_each=2, num_questions=5),
        paper.Section(key="A", title="A2", instructions="", marks_each=5, num_questions=3),
    ))
    errors = paper.validate_pattern(p)
    assert any("duplicate section key" in e for e in errors)


def test_validate_pattern_valid_presets_have_no_errors():
    for p in paper.PRESET_PATTERNS.values():
        assert paper.validate_pattern(p) == []


# -----------------------------
# Bloom presets
# -----------------------------
def test_bloom_presets_sum_to_100():
    for name, targets in paper.BLOOM_PRESETS.items():
        assert paper.validate_bloom_targets(targets) == [], f"{name} should be valid"
        assert abs(sum(targets.values()) - 100) < 0.01


def test_mcq_friendly_excludes_evaluate_and_create():
    assert paper.BLOOM_PRESETS["mcq_friendly"]["evaluate"] == 0
    assert paper.BLOOM_PRESETS["mcq_friendly"]["create"] == 0


def test_validate_bloom_targets_unknown_level():
    errors = paper.validate_bloom_targets({"bogus": 100})
    assert any("Unknown Bloom's level" in e for e in errors)


def test_validate_bloom_targets_negative():
    errors = paper.validate_bloom_targets({"remember": -5, "understand": 105})
    assert any("cannot be negative" in e for e in errors)


def test_validate_bloom_targets_not_summing_to_100():
    errors = paper.validate_bloom_targets({"remember": 50, "understand": 40})
    assert any("must sum to 100" in e for e in errors)


def test_validate_bloom_targets_within_half_point_tolerance_is_valid():
    assert paper.validate_bloom_targets({"remember": 50.3, "understand": 49.4}) == []  # sums to 99.7


def test_validate_bloom_targets_empty_is_invalid():
    assert paper.validate_bloom_targets({}) != []


# -----------------------------
# Level/type rules by marks
# -----------------------------
def test_levels_allowed_for_marks_bands():
    assert paper.levels_allowed_for_marks(1) == ["remember", "understand", "apply"]
    assert paper.levels_allowed_for_marks(2) == ["remember", "understand", "apply"]
    assert paper.levels_allowed_for_marks(3) == ["understand", "apply", "analyze", "evaluate"]
    assert paper.levels_allowed_for_marks(6) == ["understand", "apply", "analyze", "evaluate"]
    assert paper.levels_allowed_for_marks(7) == ["analyze", "evaluate", "create"]
    assert paper.levels_allowed_for_marks(10) == ["analyze", "evaluate", "create"]


@pytest.mark.parametrize("marks", [1, 2, 3, 5, 6, 7, 10])
def test_allowed_types_for_slot_non_empty_on_written_papers(marks):
    for level in paper.levels_allowed_for_marks(marks):
        types = paper.allowed_types_for_slot(level, marks, "written")
        assert types, f"{level} at {marks} marks should have at least one allowed type"
        # Every type returned must actually be valid for that level in bloom.py.
        assert set(types) <= set(bloom.get_level(level).question_types)


def test_allowed_types_for_slot_mcq_paper_restricts_to_mcq_only():
    for marks in (1, 2, 3, 5, 7, 10):
        for level in paper.levels_allowed_for_marks(marks):
            types = paper.allowed_types_for_slot(level, marks, "mcq")
            assert types in ([], ["mcq"])


def test_allowed_types_for_slot_evaluate_and_create_impossible_on_mcq():
    for marks in (3, 5, 7, 10):
        if "evaluate" in paper.levels_allowed_for_marks(marks):
            assert paper.allowed_types_for_slot("evaluate", marks, "mcq") == []
        if "create" in paper.levels_allowed_for_marks(marks):
            assert paper.allowed_types_for_slot("create", marks, "mcq") == []


# -----------------------------
# plan_paper: mid_sem_50 + balanced + 3 units
# -----------------------------
UNITS_3 = [
    {"id": "u1", "name": "Unit 1: Foundations", "weight": 3},
    {"id": "u2", "name": "Unit 2: Core", "weight": 2},
    {"id": "u3", "name": "Unit 3: Advanced", "weight": 1},
]


def test_plan_mid_sem_50_balanced_every_slot_has_level_and_types():
    pattern = paper.PRESET_PATTERNS["mid_sem_50"]
    result = paper.plan_paper(pattern, paper.BLOOM_PRESETS["balanced"], UNITS_3)
    for entry in result["plan"]:
        assert entry["bloom_level"] is not None
        assert bloom.is_valid_level(entry["bloom_level"])
        assert entry["allowed_types"], f"slot {entry} has no allowed types"
        assert entry["bloom_level"] in paper.levels_allowed_for_marks(entry["marks"])


def test_plan_mid_sem_50_balanced_levels_close_to_target_where_possible():
    pattern = paper.PRESET_PATTERNS["mid_sem_50"]
    result = paper.plan_paper(pattern, paper.BLOOM_PRESETS["balanced"], UNITS_3)
    planned = result["blueprint_planned"]["by_level"]
    target = result["blueprint_target"]
    warned_levels = {
        level for level in bloom.level_keys()
        if any(bloom.get_level(level).label in w for w in result["warnings"])
    }
    max_slot_marks = max(s.marks_each for s in pattern.sections)
    for level in bloom.level_keys():
        if level in warned_levels:
            continue  # explicitly flagged as structurally unreachable -- exempt
        assert abs(planned[level] - target[level]) <= max_slot_marks, (
            f"{level}: planned {planned[level]} vs target {target[level]}"
        )


def test_plan_mid_sem_50_balanced_every_unit_used():
    pattern = paper.PRESET_PATTERNS["mid_sem_50"]
    result = paper.plan_paper(pattern, paper.BLOOM_PRESETS["balanced"], UNITS_3)
    by_unit = result["blueprint_planned"]["by_unit"]
    for unit in UNITS_3:
        assert by_unit[unit["id"]] > 0, f"unit {unit['id']} was never used"


def test_plan_mid_sem_50_balanced_either_or_options_share_level_and_marks_differ_in_unit():
    pattern = paper.PRESET_PATTERNS["mid_sem_50"]
    result = paper.plan_paper(pattern, paper.BLOOM_PRESETS["balanced"], UNITS_3)
    pairs = {}
    for entry in result["plan"]:
        if entry["option"] is not None:
            pairs.setdefault((entry["section"], entry["number"]), []).append(entry)

    assert len(pairs) == 2  # mid_sem_50's section C has 2 either_or questions
    for (_section, _number), options in pairs.items():
        assert len(options) == 2
        a, b = options
        assert a["bloom_level"] == b["bloom_level"]
        assert a["marks"] == b["marks"]
        assert a["unit_id"] != b["unit_id"]


def test_plan_mid_sem_50_balanced_sums_match_marks_on_paper():
    pattern = paper.PRESET_PATTERNS["mid_sem_50"]
    result = paper.plan_paper(pattern, paper.BLOOM_PRESETS["balanced"], UNITS_3)
    assert sum(result["blueprint_planned"]["by_level"].values()) == paper.marks_on_paper(pattern)


# -----------------------------
# plan_paper: mcq_30 + mcq_friendly
# -----------------------------
def test_plan_mcq_30_mcq_friendly_uses_only_mcq():
    pattern = paper.PRESET_PATTERNS["mcq_30"]
    result = paper.plan_paper(pattern, paper.BLOOM_PRESETS["mcq_friendly"], [{"id": "u1", "weight": 1}])
    for entry in result["plan"]:
        assert entry["allowed_types"] == ["mcq"]


def test_plan_mcq_30_mcq_friendly_impossible_analyze_target_warns_not_crashes():
    pattern = paper.PRESET_PATTERNS["mcq_30"]
    # mcq_friendly asks for 15% Analyze, but every mcq_30 slot is 1 mark
    # (the "low" band), which never allows Analyze regardless of paper_kind.
    result = paper.plan_paper(pattern, paper.BLOOM_PRESETS["mcq_friendly"], [{"id": "u1", "weight": 1}])
    assert any("Analyze" in w and "can't be met" in w for w in result["warnings"])
    assert result["blueprint_planned"]["by_level"]["analyze"] == 0
    assert result["blueprint_planned"]["by_level"]["evaluate"] == 0
    assert result["blueprint_planned"]["by_level"]["create"] == 0
    # The rest of the plan is still perfectly usable.
    assert len(result["plan"]) == 30
    assert sum(result["blueprint_planned"]["by_level"].values()) == paper.marks_on_paper(pattern)


# -----------------------------
# Determinism
# -----------------------------
def test_plan_paper_is_deterministic():
    pattern = paper.PRESET_PATTERNS["mid_sem_50"]
    result1 = paper.plan_paper(pattern, paper.BLOOM_PRESETS["balanced"], UNITS_3)
    result2 = paper.plan_paper(pattern, paper.BLOOM_PRESETS["balanced"], UNITS_3)
    assert result1 == result2


def test_plan_paper_deterministic_for_end_sem_higher_order():
    pattern = paper.PRESET_PATTERNS["end_sem_100"]
    units = [{"id": "u1", "weight": 1}, {"id": "u2", "weight": 1}]
    result1 = paper.plan_paper(pattern, paper.BLOOM_PRESETS["higher_order"], units)
    result2 = paper.plan_paper(pattern, paper.BLOOM_PRESETS["higher_order"], units)
    assert result1 == result2
    assert len(result1["plan"]) == 10 + 8 + 5 * 2  # A + B + C(a/b each)


def test_plan_paper_no_units_does_not_crash():
    pattern = paper.PRESET_PATTERNS["mid_sem_50"]
    result = paper.plan_paper(pattern, paper.BLOOM_PRESETS["balanced"], [])
    assert all(entry["unit_id"] is None for entry in result["plan"])
    assert result["blueprint_planned"]["by_unit"] == {}

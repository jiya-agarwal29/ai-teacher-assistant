"""
Tests for bloom.py -- pure Python, no server/database/network needed.
"""
import pytest

import bloom


# -----------------------------
# LEVELS content
# -----------------------------
def test_every_level_has_a_description():
    for level in bloom.LEVELS:
        assert isinstance(level.description, str) and level.description.strip()


def test_every_level_has_verbs():
    for level in bloom.LEVELS:
        assert len(level.verbs) >= 1
        assert all(isinstance(v, str) and v.strip() for v in level.verbs)


def test_every_level_question_types_are_all_valid_quiz_types():
    for level in bloom.LEVELS:
        assert len(level.question_types) >= 1
        for q_type in level.question_types:
            assert q_type in bloom.QUIZ_TYPES


def test_every_level_has_prompt_rules():
    for level in bloom.LEVELS:
        assert isinstance(level.prompt_rules, str) and level.prompt_rules.strip()


def test_levels_are_in_canonical_order_one_through_six():
    assert bloom.level_keys() == [
        "remember", "understand", "apply", "analyze", "evaluate", "create"
    ]
    for expected_order, level in enumerate(bloom.LEVELS, start=1):
        assert level.order == expected_order


# -----------------------------
# get_level / level_keys / is_valid_level
# -----------------------------
def test_get_level_returns_the_matching_level():
    assert bloom.get_level("apply").key == "apply"
    assert bloom.get_level("apply").label == "Apply"


def test_get_level_raises_for_unknown_key():
    with pytest.raises(ValueError, match="bogus"):
        bloom.get_level("bogus")


def test_is_valid_level():
    assert bloom.is_valid_level("create") is True
    assert bloom.is_valid_level("bogus") is False


# -----------------------------
# parse_levels
# -----------------------------
def test_parse_levels_default_is_all_six_in_order():
    assert bloom.parse_levels(None) == bloom.level_keys()
    assert bloom.parse_levels("") == bloom.level_keys()
    assert bloom.parse_levels("   ") == bloom.level_keys()


def test_parse_levels_custom_subset():
    assert bloom.parse_levels("apply,analyze") == ["apply", "analyze"]


def test_parse_levels_is_reordered_to_canonical_order_regardless_of_input_order():
    assert bloom.parse_levels("analyze,apply") == ["apply", "analyze"]
    assert bloom.parse_levels("create,remember,evaluate") == ["remember", "evaluate", "create"]


def test_parse_levels_removes_duplicates():
    assert bloom.parse_levels("apply,apply,analyze,apply") == ["apply", "analyze"]


def test_parse_levels_is_case_and_whitespace_insensitive():
    assert bloom.parse_levels(" Apply , ANALYZE ") == ["apply", "analyze"]


def test_parse_levels_invalid_key_raises_naming_it():
    with pytest.raises(ValueError, match="bogus"):
        bloom.parse_levels("apply,bogus")


# -----------------------------
# distribute
# -----------------------------
def test_distribute_totals_always_equal_count():
    for count in (0, 1, 5, 7, 13, 20, 37):
        result = bloom.distribute(count, bloom.level_keys())
        assert sum(result.values()) == count


def test_distribute_six_over_six_levels_is_one_each():
    result = bloom.distribute(6, bloom.level_keys())
    assert result == {key: 1 for key in bloom.level_keys()}


def test_distribute_eight_over_six_levels_gives_lower_levels_the_extra():
    result = bloom.distribute(8, bloom.level_keys())
    assert result == {
        "remember": 2, "understand": 2, "apply": 1,
        "analyze": 1, "evaluate": 1, "create": 1
    }


def test_distribute_three_over_a_single_level():
    assert bloom.distribute(3, ["apply"]) == {"apply": 3}


def test_distribute_rejects_empty_levels():
    with pytest.raises(ValueError):
        bloom.distribute(5, [])


def test_distribute_rejects_an_unknown_level():
    with pytest.raises(ValueError, match="bogus"):
        bloom.distribute(5, ["apply", "bogus"])


# -----------------------------
# classify_by_verbs
# -----------------------------
# 4 realistic questions per level, across varied subjects (not tied to any
# one document/domain) -- the labelled set the accuracy report below runs
# against too.
LABELLED_QUESTIONS = [
    ("What is the chemical formula for water?", "remember"),
    ("List the three branches of the US government.", "remember"),
    ("Define the term 'ecosystem'.", "remember"),
    ("Name the longest river in Africa.", "remember"),

    ("Explain why leaves change color in autumn.", "understand"),
    ("Summarize the plot of Romeo and Juliet in two sentences.", "understand"),
    ("Describe how a rainbow forms.", "understand"),
    ("Paraphrase the main idea of this paragraph.", "understand"),

    ("Use the Pythagorean theorem to find the length of the hypotenuse.", "apply"),
    ("Demonstrate how to balance this chemical equation.", "apply"),
    ("How would you use the water cycle to explain flooding in a new city?", "apply"),
    ("Solve this budgeting problem using the concept of compound interest.", "apply"),

    ("Compare and contrast mitosis and meiosis.", "analyze"),
    ("What is the difference between a simile and a metaphor?", "analyze"),
    ("Why does ice float on water?", "analyze"),
    ("Examine the causes of the French Revolution.", "analyze"),

    ("Do you agree with the author's conclusion? Justify your answer.", "evaluate"),
    ("Evaluate the effectiveness of renewable energy policies.", "evaluate"),
    ("Which is better: solar power or wind power? Defend your choice.", "evaluate"),
    ("Critique this essay's argument for stricter gun control.", "evaluate"),

    ("Design an experiment to test plant growth under different light conditions.", "create"),
    ("Propose a new system for recycling in your school.", "create"),
    ("Come up with a plan to reduce food waste in your community.", "create"),
    ("Invent a new gadget that solves a common household problem.", "create"),
]


@pytest.mark.parametrize("text,expected_level", LABELLED_QUESTIONS)
def test_classify_by_verbs_labelled_set(text, expected_level):
    result = bloom.classify_by_verbs(text)
    assert result["level"] == expected_level


def test_classify_by_verbs_whole_word_not_substring():
    # "listen" must never match the Remember verb "list".
    result = bloom.classify_by_verbs("Listen to the audio clip carefully.")
    assert result == {"level": None, "matched": None}


def test_classify_by_verbs_highest_level_wins_when_several_match():
    # "explain" (understand) and "justify" (evaluate) both appear --
    # evaluate must win since it's the higher level.
    result = bloom.classify_by_verbs("Explain and justify your choice of algorithm.")
    assert result["level"] == "evaluate"
    assert result["matched"] == "justify"


def test_classify_by_verbs_no_verb_present_returns_none():
    result = bloom.classify_by_verbs("Photosynthesis?")
    assert result == {"level": None, "matched": None}


def test_classify_by_verbs_accuracy_report(capsys):
    """
    Not an assertion -- just prints the classifier's accuracy on the full
    labelled set above, so it can be quoted in a report. Run with
    `pytest -s` to see the printed line (pytest hides stdout otherwise).
    """
    correct = sum(
        1 for text, expected in LABELLED_QUESTIONS
        if bloom.classify_by_verbs(text)["level"] == expected
    )
    total = len(LABELLED_QUESTIONS)
    print(f"\nclassify_by_verbs accuracy on {total} labelled questions: "
          f"{correct}/{total} ({100 * correct / total:.1f}%)")

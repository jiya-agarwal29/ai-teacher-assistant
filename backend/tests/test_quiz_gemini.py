"""
Tests for the Gemini-quiz validation step in main.py --
_validate_quiz_questions(). Pure validation logic, no network calls
(real or mocked) needed. The full generate-retry-then-fallback flow is
covered separately in test_api.py, since that needs the live route.
"""
import main


def _mcq(**overrides):
    q = {
        "id": 1, "type": "mcq",
        "question": "What is the capital of France?",
        "options": ["Paris", "London", "Berlin", "Madrid"],
        "correctAnswer": "A",
        "explanation": "Paris is the capital of France.",
        "source_index": 1
    }
    q.update(overrides)
    return q


def _true_false(**overrides):
    q = {
        "id": 2, "type": "true_false",
        "question": "True or False: Paris is the capital of France.",
        "options": ["True", "False"],
        "correctAnswer": "A",
        "explanation": "True -- Paris is the capital.",
        "source_index": 1
    }
    q.update(overrides)
    return q


def _short_answer(**overrides):
    q = {
        "id": 3, "type": "short_answer",
        "question": "What process do plants use to make food?",
        "correctAnswer": "Photosynthesis",
        "explanation": "Plants use photosynthesis to make food.",
        "source_index": 1
    }
    q.update(overrides)
    return q


def test_valid_questions_pass_through():
    result = main._validate_quiz_questions([_mcq(), _true_false(), _short_answer()], num_sources=1)
    assert len(result) == 3
    assert result[0]["options"] == ["Paris", "London", "Berlin", "Madrid"]
    assert result[1]["options"] == ["True", "False"]
    assert "options" not in result[2]


def test_mcq_option_letter_prefixes_are_stripped():
    # Gemini sometimes prefixes its own option text with a letter even
    # though it also returns a separate lettered correctAnswer -- that
    # must not survive into the cleaned options (it would otherwise render
    # double-lettered, e.g. "A. A) ...").
    q = _mcq(options=["A) Paris", "B) London", "C) Berlin", "D) Madrid"])
    result = main._validate_quiz_questions([q], num_sources=1)
    assert len(result) == 1
    assert result[0]["options"] == ["Paris", "London", "Berlin", "Madrid"]


def test_mcq_with_wrong_option_count_is_dropped():
    bad = _mcq(options=["Paris", "London", "Berlin"])  # only 3
    result = main._validate_quiz_questions([bad, _short_answer()], num_sources=1)
    assert len(result) == 1
    assert result[0]["type"] == "short_answer"


def test_mcq_with_duplicate_options_is_dropped():
    bad = _mcq(options=["Paris", "Paris", "Berlin", "Madrid"])
    assert main._validate_quiz_questions([bad], num_sources=1) == []


def test_mcq_with_invalid_correct_answer_letter_is_dropped():
    bad = _mcq(correctAnswer="E")
    assert main._validate_quiz_questions([bad], num_sources=1) == []


def test_true_false_with_wrong_options_is_dropped():
    bad = _true_false(options=["Yes", "No"])
    assert main._validate_quiz_questions([bad], num_sources=1) == []


def test_true_false_with_invalid_correct_answer_is_dropped():
    bad = _true_false(correctAnswer="C")
    assert main._validate_quiz_questions([bad], num_sources=1) == []


def test_question_that_leaks_its_own_answer_is_dropped():
    leaky = _short_answer(
        question="What process, called Photosynthesis, do plants use to make their own food?"
    )
    assert main._validate_quiz_questions([leaky], num_sources=1) == []


def test_answer_leak_check_does_not_apply_to_mcq_or_true_false():
    # mcq/true_false correctAnswer is just a letter ("A"/"B"), which
    # legitimately appears inside ordinary English question text -- the
    # leak check must only apply to types with real answer text.
    mcq_q = _mcq(question="A country in Europe has this as its capital city.")
    tf_q = _true_false(question="True or False: a European capital is being described.")
    result = main._validate_quiz_questions([mcq_q, tf_q], num_sources=1)
    assert len(result) == 2


def test_invalid_source_index_is_dropped():
    bad = _short_answer(source_index=5)  # only 1 source available
    assert main._validate_quiz_questions([bad], num_sources=1) == []


def test_missing_source_index_is_dropped():
    q = _short_answer()
    del q["source_index"]
    assert main._validate_quiz_questions([q], num_sources=1) == []


def test_duplicate_questions_are_dropped():
    q1 = _short_answer()
    q2 = _short_answer(id=4, explanation="A different explanation for the same question text.")
    result = main._validate_quiz_questions([q1, q2], num_sources=1)
    assert len(result) == 1


def test_unknown_question_type_is_dropped():
    bad = _short_answer(type="essay")
    assert main._validate_quiz_questions([bad], num_sources=1) == []


def test_non_dict_entries_are_dropped():
    result = main._validate_quiz_questions(["not a dict", None, _short_answer()], num_sources=1)
    assert len(result) == 1


def test_non_list_input_returns_empty():
    assert main._validate_quiz_questions(None, num_sources=1) == []
    assert main._validate_quiz_questions("nope", num_sources=1) == []


def test_ids_are_reassigned_sequentially_after_dropping_invalid_ones():
    bad = _mcq(options=["Paris", "London", "Berlin"])  # dropped
    good = _short_answer(id=99)
    result = main._validate_quiz_questions([bad, good], num_sources=1)
    assert len(result) == 1
    assert result[0]["id"] == 1

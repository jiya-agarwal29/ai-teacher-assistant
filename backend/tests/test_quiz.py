import re

from rag import generate_hybrid_quiz

# Ten distinct, well-formed definitions so generate_hybrid_quiz has more than
# enough concepts to fill all 8 question types without falling back to the
# sparse-content path.
SAMPLE_CONTEXT = "\n".join([
    "- Encapsulation: The bundling of data and methods that operate on that data within a single unit.",
    "- Inheritance: A mechanism where a new class derives properties and behavior from an existing class.",
    "- Polymorphism: The ability of different objects to respond to the same function call in different ways.",
    "- Abstraction: The concept of hiding complex implementation details behind a simple interface.",
    "- Recursion: A technique where a function calls itself to solve smaller instances of a problem.",
    "- Compilation: The process of translating source code into machine code before execution.",
    "- Interpretation: The process of executing source code line by line without prior compilation.",
    "- Concurrency: The ability of a program to execute multiple tasks during overlapping time periods.",
    "- Serialization: The process of converting an object into a format that can be stored or transmitted.",
    "- Caching: The technique of storing frequently accessed data for faster future retrieval.",
])

TOPIC = "Software Engineering Concepts"


def _concept_key(question):
    # The concept a question tests is named via a quoted term in its
    # question text (e.g. "...definition of 'Encapsulation'?").
    match = re.search(r"'([^']+)'", question["question"])
    return match.group(1).lower() if match else question["question"].lower()


def test_scenario_question_never_contains_its_own_answer():
    _, questions = generate_hybrid_quiz(SAMPLE_CONTEXT, TOPIC)
    scenario_questions = [q for q in questions if q["type"] == "scenario"]

    assert scenario_questions, "expected a scenario question from a 10-definition sample context"

    for q in scenario_questions:
        answer = q["correctAnswer"].strip()
        assert answer.lower() not in q["question"].lower(), (
            f"scenario question leaked its answer: {q['question']!r} contains {answer!r}"
        )


def test_true_false_answers_are_not_all_the_same_over_20_runs():
    seen_answers = set()

    for _ in range(20):
        _, questions = generate_hybrid_quiz(SAMPLE_CONTEXT, TOPIC)
        tf_questions = [q for q in questions if q["type"] == "true_false"]
        assert tf_questions, "expected a true_false question from a 10-definition sample context"
        seen_answers.add(tf_questions[0]["correctAnswer"])

    assert seen_answers == {"A", "B"}, (
        f"expected both True (A) and False (B) to appear across 20 runs, got {seen_answers}"
    )


def test_no_concept_repeated_within_one_quiz():
    _, questions = generate_hybrid_quiz(SAMPLE_CONTEXT, TOPIC)

    assert len(questions) >= 3

    keys = [_concept_key(q) for q in questions]
    assert len(keys) == len(set(keys)), f"a concept was reused across questions: {keys}"

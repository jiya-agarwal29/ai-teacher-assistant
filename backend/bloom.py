"""
Bloom's taxonomy: the single source of truth for the six cognitive levels
used to classify and generate quiz questions (Phase 4).

Deliberately pure Python -- no imports from main.py, llm.py, database.py,
or any other part of this app -- so it's trivial to unit test on its own
and safe to reuse from anywhere later (Phase 5's question generator, an
analytics breakdown, etc.) without pulling in FastAPI, SQLAlchemy, or a
Gemini client.

A Bloom level is chosen *per question* (by whoever/whatever writes it) and
can be *checked* afterward with classify_by_verbs() -- it is never derived
from a question's existing "type" (mcq/scenario/etc.); those are a
different axis (the answer *format*), and the mapping in each level's
`question_types` only says which formats suit that level, not the other
way around.
"""
import re
from dataclasses import dataclass


# The 8 existing quiz question formats (main.py's QUIZ_QUESTION_TYPES).
# Duplicated here, not imported, to keep this module dependency-free --
# see the module docstring.
QUIZ_TYPES = (
    "mcq", "true_false", "fill_blank", "short_answer",
    "long_answer", "scenario", "viva", "interview"
)

# Every level's prompt_rules ends with this -- a question at any level
# must still be grounded in the uploaded material; only the *situation* a
# concept is placed into may be invented.
SHARED_SOURCE_CONSTRAINT = (
    "The concepts must come from the sources; only the situation may be new."
)

# Apply and every level above it add this -- a question testing real
# application/analysis/evaluation/creation is too easy (and really just
# Remember/Understand in disguise) if the sources already spell out the
# answer almost word for word. See also the matching checker-side rule in
# main.py's _bloom_level_descriptions_block.
NO_DIRECT_ANSWER_CONSTRAINT = (
    "The answer must NOT be stated directly (or almost word for word) in the "
    "sources -- the student has to work it out, not find and copy it."
)


@dataclass(frozen=True)
class BloomLevel:
    key: str
    label: str
    order: int
    description: str
    verbs: tuple
    question_types: tuple
    prompt_rules: str


LEVELS = (
    BloomLevel(
        key="remember",
        label="Remember",
        order=1,
        description="Recall facts, terms, or basic ideas exactly as learned.",
        verbs=(
            "define", "list", "name", "identify", "recall", "state", "label",
            "match", "recognize", "select", "memorize", "repeat", "record",
            "who is", "when did", "where is", "what is"
        ),
        question_types=("mcq", "true_false", "fill_blank"),
        prompt_rules=(
            "Ask the student to recall a fact, term, or definition exactly as "
            "stated in the sources. Do not require reasoning or a new situation. "
            + SHARED_SOURCE_CONSTRAINT
        ),
    ),
    BloomLevel(
        key="understand",
        label="Understand",
        order=2,
        description="Explain ideas or concepts in your own words.",
        verbs=(
            "explain", "describe", "summarize", "interpret", "paraphrase",
            "discuss", "classify", "illustrate", "translate", "restate",
            "outline", "report", "infer", "give an example",
            "in your own words", "what does it mean"
        ),
        question_types=("mcq", "short_answer", "true_false"),
        prompt_rules=(
            "Ask the student to explain, summarize, or restate an idea from the "
            "sources in their own words, without giving them the exact wording "
            "to copy. " + SHARED_SOURCE_CONSTRAINT
        ),
    ),
    BloomLevel(
        key="apply",
        label="Apply",
        order=3,
        description="Use what was learned in a new situation.",
        verbs=(
            "apply", "use", "demonstrate", "solve", "show", "implement",
            "calculate", "execute", "practice", "employ", "operate", "sketch",
            "how would you use", "what would happen if", "how could you apply",
            "put into practice"
        ),
        question_types=("mcq", "scenario", "short_answer"),
        prompt_rules=(
            "Put the concept into a NEW situation not described in the sources; "
            "the student must use the idea, not repeat it. " + SHARED_SOURCE_CONSTRAINT
            + " " + NO_DIRECT_ANSWER_CONSTRAINT
        ),
    ),
    BloomLevel(
        key="analyze",
        label="Analyze",
        order=4,
        description="Break information into parts and see how they relate.",
        verbs=(
            "analyze", "differentiate", "compare", "contrast", "examine",
            "categorize", "investigate", "distinguish", "deconstruct",
            "compare and contrast", "what is the difference", "break down",
            "how does", "why does", "what causes", "how are they related"
        ),
        question_types=("mcq", "scenario", "long_answer"),
        prompt_rules=(
            "Ask about relationships, causes, differences or how parts fit "
            "together. " + SHARED_SOURCE_CONSTRAINT
            + " " + NO_DIRECT_ANSWER_CONSTRAINT
        ),
    ),
    BloomLevel(
        key="evaluate",
        label="Evaluate",
        order=5,
        description="Make a judgement about something and justify it.",
        verbs=(
            "evaluate", "judge", "justify", "critique", "assess", "argue",
            "defend", "support", "recommend", "prioritize", "rate", "appraise",
            "conclude", "validate", "do you agree", "which is better"
        ),
        question_types=("scenario", "long_answer", "viva"),
        prompt_rules=(
            "Ask for a judgement or decision AND its justification against "
            "clear criteria. " + SHARED_SOURCE_CONSTRAINT
            + " " + NO_DIRECT_ANSWER_CONSTRAINT
        ),
    ),
    BloomLevel(
        key="create",
        label="Create",
        order=6,
        description="Design, propose, or build something new using the ideas.",
        verbs=(
            "create", "design", "propose", "construct", "develop", "formulate",
            "compose", "invent", "plan", "devise", "generate", "produce",
            "imagine", "come up with", "design a", "build a plan"
        ),
        question_types=("long_answer", "scenario", "interview"),
        prompt_rules=(
            "Ask the student to design, propose or plan something new using "
            "the concepts. " + SHARED_SOURCE_CONSTRAINT
            + " " + NO_DIRECT_ANSWER_CONSTRAINT
        ),
    ),
)

_LEVELS_BY_KEY = {level.key: level for level in LEVELS}


def get_level(key: str) -> BloomLevel:
    """Returns the BloomLevel for `key`. Raises ValueError for an unknown key."""
    try:
        return _LEVELS_BY_KEY[key]
    except KeyError:
        raise ValueError(f"Unknown Bloom's level: '{key}'") from None


def level_keys() -> list:
    """All six level keys, in canonical (remember -> create) order."""
    return [level.key for level in LEVELS]


def is_valid_level(key: str) -> bool:
    return key in _LEVELS_BY_KEY


def parse_levels(raw) -> list:
    """
    Parses a comma-separated string of level keys (e.g. "apply,analyze")
    into a de-duplicated list, always returned in canonical Bloom order
    regardless of the order they were given in -- so the result is
    predictable and can be handed straight to distribute(). `raw` of None
    or an empty/whitespace-only string returns all six levels. Raises
    ValueError naming the first unrecognized key found.
    """
    if raw is None or not str(raw).strip():
        return level_keys()

    tokens = [t.strip().lower() for t in str(raw).split(",") if t.strip()]
    if not tokens:
        return level_keys()

    deduped = []
    seen = set()
    for token in tokens:
        if not is_valid_level(token):
            raise ValueError(f"Unknown Bloom's level: '{token}'")
        if token not in seen:
            seen.add(token)
            deduped.append(token)

    deduped.sort(key=lambda k: _LEVELS_BY_KEY[k].order)
    return deduped


def distribute(count: int, levels) -> dict:
    """
    Spreads `count` questions as evenly as possible across `levels` (an
    iterable of level keys), in the order given -- the levels earliest in
    that order get one extra question each until the remainder runs out.
    Passing parse_levels()'s output (canonical order) means the *lower*
    Bloom levels get the extras, matching how a typical quiz is weighted
    toward the foundational levels.

    Example: distribute(8, ["remember", "understand", "apply", "analyze",
    "evaluate", "create"]) -> remember=2, understand=2, the other four=1
    each (totals 8).
    """
    level_list = list(levels)
    if not level_list:
        raise ValueError("distribute() needs at least one level.")
    for key in level_list:
        if not is_valid_level(key):
            raise ValueError(f"Unknown Bloom's level: '{key}'")

    base, remainder = divmod(count, len(level_list))
    return {
        key: base + (1 if idx < remainder else 0)
        for idx, key in enumerate(level_list)
    }


def _instruction_fragment(text: str) -> str:
    """The part of `text` before its first '?' or '.' -- the first sentence/instruction is enough to classify."""
    if not text:
        return ""
    question_mark = text.find("?")
    period = text.find(".")
    candidates = [i for i in (question_mark, period) if i != -1]
    if not candidates:
        return text
    return text[:min(candidates)]


def _compile_level_patterns():
    """
    One compiled whole-word/phrase regex per verb, per level -- multi-word
    phrases ordered before single words within each level (stable sort
    keeps each group's original declared order), so a phrase match is
    found before a looser single-word one when checking a given level.
    """
    compiled = {}
    for level in LEVELS:
        ordered_verbs = sorted(level.verbs, key=lambda v: " " not in v)
        compiled[level.key] = [
            (verb, re.compile(r"\b" + re.escape(verb) + r"\b", re.IGNORECASE))
            for verb in ordered_verbs
        ]
    return compiled


_VERB_PATTERNS_BY_LEVEL = _compile_level_patterns()


def classify_by_verbs(question_text: str) -> dict:
    """
    Guesses a question's Bloom level from its action verbs/phrases, as a
    sanity check on a level that was *chosen* when the question was
    written -- never used to assign a level in the first place.

    Matching is case-insensitive and whole-word/phrase (so "list" never
    matches inside "listen"), looking only at the first sentence/
    instruction (the text before the first "?" or "."). If verbs from
    more than one level are present, the HIGHEST level found wins (e.g.
    "Explain and justify..." -> evaluate, not understand).

    Returns {"level": key, "matched": phrase} on a match, or
    {"level": None, "matched": None} if nothing matched.
    """
    fragment = _instruction_fragment(question_text)

    matches = []  # (order, key, matched_phrase), at most one per level
    for level in LEVELS:
        for verb, pattern in _VERB_PATTERNS_BY_LEVEL[level.key]:
            if pattern.search(fragment):
                matches.append((level.order, level.key, verb))
                break

    if not matches:
        return {"level": None, "matched": None}

    matches.sort(key=lambda m: m[0])
    _, best_key, best_verb = matches[-1]
    return {"level": best_key, "matched": best_verb}

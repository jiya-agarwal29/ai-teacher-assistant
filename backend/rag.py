from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
import torch
import re
import random
import collections
import logging

logger = logging.getLogger(__name__)

model_name = "google/flan-t5-small"
tokenizer = None
model = None
device = None


def load_model():
    """
    Loads the Flan-T5 tokenizer/model. Called once from the FastAPI lifespan
    at startup so requests never pay the load cost; safe to call again
    (no-op if already loaded). Also lets pure text-processing functions in
    this module (quiz generation, definition extraction, etc.) be imported
    and used — e.g. in tests — without paying the model-load cost at all.
    """
    global tokenizer, model, device
    if model is not None:
        return
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name).to(device)
    logger.info("Loaded Flan-T5 model '%s' on device=%s", model_name, device)

def check_context_relevance(query: str, context: str, score: float) -> bool:
    """
    Checks if the retrieved context is relevant to the query.
    If cosine similarity is low (< 0.33) and there is no lexical content word overlap (excluding stopwords),
    we treat it as irrelevant to prevent hallucinations or false matches.
    """
    if not query or not context:
        return False
        
    # If the semantic score is very high, trust it
    if score >= 0.33:
        return True
        
    # If the semantic score is extremely low, reject it immediately
    if score <= 0.20:
        return False
        
    # Exclude common stopwords and general verbs/nouns to prevent false lexical overlap matches
    stopwords = {
        "is", "are", "was", "were", "the", "a", "an", "and", "or", "but", "if", "then", "else",
        "of", "at", "by", "for", "with", "about", "against", "between", "into", "through", "during",
        "before", "after", "above", "below", "to", "from", "up", "down", "in", "out", "on", "off", "over",
        "under", "again", "further", "then", "once", "here", "there", "when", "where", "why", "how",
        "all", "any", "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not",
        "only", "own", "same", "so", "than", "too", "very", "s", "t", "can", "will", "just", "don",
        "should", "now", "describe", "explain", "what", "which", "who", "whom", "this", "that", "these",
        "those", "am", "does", "do", "did", "doing", "having", "have", "has", "get", "got", "make", "made",
        "system", "software", "hardware", "user", "process", "concept", "work", "works", "working", "run",
        "runs", "running", "use", "uses", "used", "using", "example", "examples", "different", "similar",
        "type", "types", "many", "some", "like", "well", "also", "take", "takes", "taking", "need", "needs",
        "needed", "want", "wants", "wanted", "know", "knows", "known", "find", "finds", "found", "show",
        "shows", "shown", "explain", "explains", "explained", "describe", "describes", "described",
        "define", "defines", "defined", "role", "roles", "importance", "important", "feature", "features",
        "term", "terms", "detail", "details", "base", "based", "first", "second", "third", "one", "two",
        "three", "firstly", "secondly", "create", "creates", "created", "creating", "design", "designs",
        "designed", "designing", "implement", "implements", "implemented", "implementing", "give", "gives",
        "given", "giving", "write", "writes", "written", "writing", "read", "reads", "reading", "call",
        "calls", "called", "calling", "add", "adds", "added", "adding", "remove", "removes", "removed",
        "removing", "delete", "deletes", "deleted", "deleting", "update", "updates", "updated", "updating",
        "change", "changes", "changed", "changing", "result", "results", "resulting", "set", "sets",
        "setting", "allow", "allows", "allowed", "allowing", "provide", "provides", "provided", "providing",
        "perform", "performs", "performed", "performing", "functions", "function", "functional", "state",
        "states", "stated", "stating", "step", "steps", "algorithm", "algorithms", "model", "models"
    }
    
    # Tokenize query and context
    query_words = set(re.findall(r'\b\w+\b', query.lower()))
    context_words = set(re.findall(r'\b\w+\b', context.lower()))
    
    # Filter stopwords from query
    query_content_words = query_words - stopwords
    
    if not query_content_words:
        # If query has only stopwords/generic words, accept if semantic score is > 0.22
        return score > 0.22
        
    # Check if there is any overlap
    overlap = query_content_words.intersection(context_words)
    return len(overlap) > 0

def clean_extracted_text(text: str) -> str:
    """
    Cleans up heading, list markers, and markdown characters.
    """
    if not text:
        return ""
    text = re.sub(r'^[#\s\-*•+]+', '', text)
    text = re.sub(r'[\*_`#]', '', text)
    return text.strip()

def generate_focused_answer(context, prompt_instruction, max_tokens=120):
    """
    Generates a localized response for a specific section using the context.
    """
    if not context or not context.strip():
        return ""
    load_model()
    words = context.split()
    if len(words) > 250:
        context_compressed = " ".join(words[:250])
    else:
        context_compressed = context

    prompt = f"Context: {context_compressed}\nInstructions: {prompt_instruction}\nAnswer:"
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=512).to(device)
    
    outputs = model.generate(
        **inputs,
        max_new_tokens=max_tokens,
        do_sample=False,
        repetition_penalty=1.3,
        no_repeat_ngram_size=3
    )
    
    res = tokenizer.decode(outputs[0], skip_special_tokens=True).strip()
    if not res or len(res) < 5 or "do not contain" in res.lower() or "not enough information" in res.lower():
        return ""
    return res

def summarize_text_in_bullets(text: str, piece_words: int = 350) -> list:
    """
    Summarizes arbitrary pasted text (not retrieved document context) into
    one bullet per ~350-word piece, using the local model. Every bullet is
    grounded in that piece of the user's own text — nothing else is added.
    """
    words = text.split()
    pieces = []
    current_piece = []
    for w in words:
        current_piece.append(w)
        if len(current_piece) >= piece_words:
            pieces.append(" ".join(current_piece))
            current_piece = []
    if current_piece:
        pieces.append(" ".join(current_piece))

    bullets = []
    for piece in pieces:
        summary = clean_extracted_text(generate_focused_answer(
            piece,
            "Summarize this text in one clear, concise sentence.",
            max_tokens=60
        ))
        if summary:
            bullets.append(summary)

    return bullets

def generate_answer(context, question):
    """
    Generates a concise, document-grounded answer using the local Flan-T5 model.
    Keeps the question intact and truncates the context (via the tokenizer,
    not a naive word count) so the whole prompt fits Flan-T5's 512-token
    limit without risking the question itself being cut off.
    """
    if not context or not context.strip():
        return "The uploaded documents do not contain enough information for this question."

    load_model()

    instruction = "Answer the question using only the context."
    prefix = f"{instruction} Question: {question} Context: "
    suffix = " Answer:"

    prefix_ids = tokenizer(prefix, add_special_tokens=False)["input_ids"]
    suffix_ids = tokenizer(suffix, add_special_tokens=False)["input_ids"]
    # Leave a little headroom for the model's end-of-sequence token.
    max_context_tokens = max(0, 512 - len(prefix_ids) - len(suffix_ids) - 1)

    context_ids = tokenizer(context, add_special_tokens=False)["input_ids"][:max_context_tokens]
    truncated_context = tokenizer.decode(context_ids, skip_special_tokens=True)

    prompt = f"{prefix}{truncated_context}{suffix}"

    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=512).to(device)

    outputs = model.generate(
        **inputs,
        max_new_tokens=120,
        do_sample=False,
        repetition_penalty=1.3,
        no_repeat_ngram_size=3
    )

    answer = tokenizer.decode(outputs[0], skip_special_tokens=True).strip()

    # Clean answer
    if not answer or len(answer) < 5 or "do not contain enough information" in answer.lower():
        return "The uploaded documents do not contain enough information for this question."

    return answer

def paraphrase_concept(concept_text):
    """
    Paraphrases a raw document line or concept using the T5 model to sound professional and ChatGPT-like.
    Includes self-healing validation to prevent Flan-T5 hallucinations or infinite loops.
    """
    if not concept_text or len(concept_text.strip()) < 10:
        return ""

    load_model()

    clean_text = re.sub(r'\s+', ' ', concept_text).strip()
    
    # Instruct Flan-T5 to explain/simplify
    prompt = f"Concept: {clean_text}\nParaphrase this concept simply and professionally:"
    
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=128).to(device)
    
    outputs = model.generate(
        **inputs,
        max_new_tokens=60,
        do_sample=False,
        repetition_penalty=1.3,
        no_repeat_ngram_size=2
    )
    
    paraphrased = tokenizer.decode(outputs[0], skip_special_tokens=True).strip()
    
    # ---- SELF-HEALING VALIDATION RULES ----
    # 1. Repetition check (repeated phrases, e.g., "CPU to CPU" or repeating the same word > 3 times)
    words = paraphrased.split()
    word_counts = collections.Counter([w.lower() for w in words])
    has_repetition = False
    for word, count in word_counts.items():
        if len(word) >= 3 and count > 3:
            has_repetition = True
            break
            
    # Check for consecutive word repetition
    for idx in range(len(words) - 1):
        if words[idx].lower() == words[idx+1].lower() and len(words[idx]) >= 3:
            has_repetition = True
            break
            
    # 2. Length check (e.g., if it's too short < 8 chars, or disproportionately long > 2.0x of clean_text)
    is_invalid_length = (
        not paraphrased 
        or len(paraphrased) < 8 
        or len(paraphrased) > len(clean_text) * 2.0
    )
    
    # 3. Hallucination check
    # We check if Flan-T5 output contains content words (len >= 4) that are completely absent in the source context
    # (allowing common helper verbs/connectives)
    source_words = set(re.findall(r'\b\w+\b', clean_text.lower()))
    gen_words = set(re.findall(r'\b\w+\b', paraphrased.lower()))
    
    # Common allowed words in paraphrases
    allowed_vocab = {
        "this", "refers", "means", "describes", "represents", "which", "that", "used", "performs",
        "responsible", "helps", "provides", "ensures", "system", "process", "allows", "controls",
        "manages", "defines", "method", "handles", "tasks", "operation", "executes", "simple",
        "terms", "concept", "conceptually", "technique", "approach", "disciplined", "systematic"
    }
    
    hallucinated_words = []
    for w in gen_words:
        if len(w) >= 4 and w not in source_words and w not in allowed_vocab:
            hallucinated_words.append(w)
            
    # If the generated text has too many hallucinated words (> 2), reject it
    has_hallucinations = len(hallucinated_words) > 2
    
    # If any validation rule fails, heal it by falling back to clean_text
    ret = paraphrased
    if has_repetition or is_invalid_length or has_hallucinations:
        if ":" in clean_text:
            parts = clean_text.split(":", 1)
            term = parts[0].strip()
            def_text = parts[1].strip()
            if def_text.lower().startswith(term.lower()):
                ret = def_text[0].upper() + def_text[1:]
            else:
                ret = clean_text
        else:
            ret = clean_text
        
    return clean_extracted_text(ret)

def clean_pdf_text_for_quiz(text: str) -> str:
    if not text:
        return ""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r'^(page\s+)?\d+(\s+of\s+\d+)?$', stripped, re.IGNORECASE):
            continue
        cleaned_line = re.sub(r'[ \t]+', ' ', stripped)
        cleaned_lines.append(cleaned_line)
    return "\n".join(cleaned_lines)

def extract_definitions_and_statements(context: str):
    """
    Extracts terms, definitions, and distinct statements from context.
    """
    lines = context.split('\n')
    definitions = []
    statements = []
    seen_terms = set()
    seen_raw = set()

    def normalize_raw(text):
        # Strips bullet/numbering markers and collapses whitespace so the
        # same underlying line can't be captured twice — once as a bulleted
        # definition, once again as a "statement" via the sentence split
        # below (they operate on the same text through different regexes).
        stripped = re.sub(r'^[-*•+]\s*', '', text.strip())
        stripped = re.sub(r'^\d+[\.\)]\s*', '', stripped)
        return re.sub(r'\s+', ' ', stripped).strip().lower()

    # 1. Extract explicit list definitions
    for line in lines:
        line_strip = line.strip()
        if not line_strip:
            continue

        bullet_match = re.match(r'^[-*•+]\s*(.+)$', line_strip)
        numbered_match = re.match(r'^\d+[\.\)]\s*(.+)$', line_strip)

        content_line = line_strip
        if bullet_match:
            content_line = bullet_match.group(1).strip()
        elif numbered_match:
            content_line = numbered_match.group(1).strip()

        parts = None
        if ':' in content_line:
            parts = content_line.split(':', 1)
        elif ' - ' in content_line:
            parts = content_line.split(' - ', 1)

        if parts:
            term = parts[0].strip()
            explanation = parts[1].strip()
            if 2 <= len(term) <= 45 and len(explanation) >= 15:
                term_clean = re.sub(r'^[“"\'\s]+|[”"\'\s\.\,]+$', '', term)
                term_key = term_clean.lower()
                if term_key not in seen_terms:
                    definitions.append({
                        "term": term_clean,
                        "explanation": explanation,
                        "raw": line_strip
                    })
                    seen_terms.add(term_key)
                    seen_raw.add(normalize_raw(line_strip))

    # 2. Extract implicit definitions (is a, refers to, etc.)
    sentences = re.split(r'(?<=[.!?])\s+', context)
    for sent in sentences:
        sent = sent.strip()
        sent = re.sub(r'\s+', ' ', sent)
        if len(sent) < 35 or len(sent) > 200:
            continue

        if normalize_raw(sent) in seen_raw:
            continue

        verb_match = re.search(r'\b(is a|is an|is defined as|refers to|represents|is the process of|acts as)\b', sent, re.IGNORECASE)
        if verb_match:
            verb = verb_match.group(1)
            idx = sent.lower().find(verb.lower())
            term = sent[:idx].strip()
            explanation = sent[idx:].strip()

            term_clean = re.sub(r'^(an?|the)\s+', '', term, flags=re.IGNORECASE).strip()
            term_clean = re.sub(r'^[“"\'\s]+|[”"\'\s\.\,]+$', '', term_clean)

            if 2 <= len(term_clean) <= 45 and len(explanation) >= 15:
                term_key = term_clean.lower()
                if term_key not in seen_terms:
                    definitions.append({
                        "term": term_clean,
                        "explanation": explanation,
                        "raw": sent
                    })
                    seen_terms.add(term_key)
                    seen_raw.add(normalize_raw(sent))
        else:
            if len(sent) >= 40 and len(sent) <= 150:
                if not any(x in sent.lower() for x in ["question:", "context:", "instructions:", "---"]) and "#" not in sent:
                    statements.append(sent)
                    seen_raw.add(normalize_raw(sent))

    return definitions, statements

def get_distractors(correct_explanation, definitions, statements, topic, num_needed=3):
    """
    Picks distinct, real-text distractors — definitions before statements —
    and only reaches for the generic filler sentences when there truly isn't
    enough real content, using at most one of them so the options stay
    similar in style.
    """
    seen = {correct_explanation.strip().lower()}
    distractors = []

    for d in definitions:
        exp = d["explanation"].strip()
        key = exp.lower()
        if key not in seen:
            distractors.append(exp)
            seen.add(key)
            if len(distractors) >= num_needed:
                return distractors

    for s in statements:
        s = s.strip()
        key = s.lower()
        if key not in seen:
            distractors.append(s)
            seen.add(key)
            if len(distractors) >= num_needed:
                return distractors

    if len(distractors) < num_needed:
        fallbacks = [
            f"A design standard defined by the {topic} specification.",
            f"A legacy protocol used in older versions of the {topic} system.",
            f"A security extension implemented to prevent external intrusions.",
            f"An optimization technique designed to reduce execution overhead."
        ]
        random.shuffle(fallbacks)
        for fb in fallbacks:
            if fb.lower() not in seen:
                distractors.append(fb)
                break  # never more than one generic filler

    return distractors

def mask_sentence(sentence, term=None):
    """
    Picks the word (or, for a real extracted concept, the exact term) to
    blank out. Never returns an answer shorter than 3 letters or a stopword.
    """
    sentence_clean = re.sub(r'^[-*•+]\s*', '', sentence)
    sentence_clean = re.sub(r'^\d+[\.\)]\s*', '', sentence_clean)

    stopwords = {"is", "are", "the", "and", "that", "this", "with", "from", "into", "acts", "between", "under", "over", "system", "software", "hardware", "user", "users"}

    if term:
        term_clean = term.strip()
        if len(term_clean) >= 3 and term_clean.lower() not in stopwords and term_clean.lower() in sentence_clean.lower():
            pattern = re.compile(re.escape(term_clean), re.IGNORECASE)
            masked = pattern.sub("________", sentence_clean)
            return masked, term_clean

    words = sentence_clean.split()
    candidates = []
    for w in words:
        w_clean = re.sub(r'^\W+|\W+$', '', w)
        if len(w_clean) >= 3 and w_clean.lower() not in stopwords:
            candidates.append(w_clean)

    if candidates:
        chosen_word = candidates[len(candidates) // 2]
        pattern = re.compile(r'\b' + re.escape(chosen_word) + r'\b', re.IGNORECASE)
        masked = pattern.sub("________", sentence_clean)
        return masked, chosen_word

    return sentence_clean, None

def generate_hybrid_quiz(context, topic):
    cleaned = clean_pdf_text_for_quiz(context)
    if not cleaned or len(cleaned.strip()) < 30:
        return "Not enough information found in uploaded documents.", []

    definitions, statements = extract_definitions_and_statements(cleaned)

    # Shuffle so re-generating a quiz for the same topic (clicking "Build
    # Test" again) picks a different set/order of concepts each time.
    random.shuffle(definitions)
    random.shuffle(statements)

    # Pre-clean term/explanation structures to avoid formatting headers
    for d in definitions:
        d["term"] = clean_extracted_text(d["term"])
        d["explanation"] = clean_extracted_text(d["explanation"])

    total_concepts = len(definitions) + len(statements)
    if total_concepts < 3:
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', cleaned) if len(s.strip()) > 20]
        random.shuffle(sentences)
        statements = sentences[:12]
        total_concepts = len(statements)

    if total_concepts < 3:
        return "Not enough information found in uploaded documents.", []

    # One entry per distinct concept — definitions first (a real term, safe
    # to mask verbatim in fill-in-the-blank), then statements (only the
    # leading words as a placeholder term — a grammatical fragment, never
    # usable as an answer). Every question below is assigned a different
    # index into this list, so no two questions test the same concept.
    concepts = []
    for d in definitions:
        concepts.append((d["term"], d["explanation"], d["raw"], True))
    for s in statements:
        words = s.split()
        term = " ".join(words[:2]) if len(words) > 2 else "Core Concept"
        concepts.append((term, s, s, False))

    num_questions = min(8, len(concepts))
    question_types = [
        "mcq", "true_false", "fill_blank", "short_answer",
        "long_answer", "scenario", "viva", "interview"
    ][:num_questions]

    questions = []

    for i, q_type in enumerate(question_types):
        term, explanation, raw, is_concept = concepts[i]

        if q_type == "mcq":
            dist = get_distractors(explanation, definitions, statements, topic, 3)
            options = [explanation] + dist
            random.shuffle(options)
            correct_letter = ['A', 'B', 'C', 'D'][options.index(explanation)]
            questions.append({
                "id": len(questions) + 1,
                "type": "mcq",
                "question": f"Based on the study materials, what is the primary function or definition of '{term}'?",
                "options": options,
                "correctAnswer": correct_letter,
                "explanation": f"According to the notes: '{clean_extracted_text(raw)}'"
            })

        elif q_type == "true_false":
            other_idx = random.choice([j for j in range(len(concepts)) if j != i])
            other_term, other_exp, _, _ = concepts[other_idx]
            if random.random() < 0.5:
                # True statement: the concept paired with its own definition.
                questions.append({
                    "id": len(questions) + 1,
                    "type": "true_false",
                    "question": f"True or False: According to the documents, '{term}' refers to {explanation.rstrip('.')}.",
                    "options": ["True", "False"],
                    "correctAnswer": "A",
                    "explanation": f"True. The material states: '{clean_extracted_text(raw)}'"
                })
            else:
                # False statement: the concept paired with a different concept's definition.
                questions.append({
                    "id": len(questions) + 1,
                    "type": "true_false",
                    "question": f"True or False: According to the documents, '{term}' refers to {other_exp.rstrip('.')}.",
                    "options": ["True", "False"],
                    "correctAnswer": "B",
                    "explanation": f"False. The document defines '{term}' as: {explanation.rstrip('.')}. That description instead belongs to '{other_term}'."
                })

        elif q_type == "fill_blank":
            # Only mask the extracted term itself when it's a real concept
            # (from a definition) — otherwise `term` is just the leading
            # words of a raw sentence, a grammatical fragment rather than a
            # real answer. mask_sentence() also refuses stopwords/answers
            # under 3 letters; if it can't find anything usable, skip this
            # question rather than present a broken blank.
            # clean_extracted_text() strips underscores, so it must run
            # BEFORE masking — otherwise it deletes the "________" blank
            # itself, leaving a question with no visible blank at all.
            cleaned_raw = clean_extracted_text(raw)
            mask_term = term if is_concept else None
            masked_q, masked_word = mask_sentence(cleaned_raw, mask_term)
            if not masked_word:
                continue
            questions.append({
                "id": len(questions) + 1,
                "type": "fill_blank",
                "question": f"Fill in the blank: {masked_q}",
                "correctAnswer": clean_extracted_text(masked_word),
                "explanation": f"The document outlines: '{cleaned_raw}'"
            })

        elif q_type == "short_answer":
            questions.append({
                "id": len(questions) + 1,
                "type": "short_answer",
                "question": f"Explain the role and definition of '{term}' based on the study materials.",
                "correctAnswer": explanation,
                "explanation": f"The notes clarify that '{term}' is defined as: '{explanation}'"
            })

        elif q_type == "long_answer":
            questions.append({
                "id": len(questions) + 1,
                "type": "long_answer",
                "question": f"Describe in detail the functionality, design, or operational flow of '{term}' as described in the documents. Explain how it interacts with other system elements.",
                "correctAnswer": explanation,
                "explanation": f"The notes highlight: '{clean_extracted_text(raw)}'"
            })

        elif q_type == "scenario":
            # Names the concept and a realistic situation only — the answer
            # itself must never appear inside the question text.
            questions.append({
                "id": len(questions) + 1,
                "type": "scenario",
                "question": f"Scenario: A system administrator is working on a task involving '{term}'. Explain how '{term}' applies in this situation.",
                "correctAnswer": explanation,
                "explanation": f"The document states: '{clean_extracted_text(raw)}'"
            })

        elif q_type == "viva":
            questions.append({
                "id": len(questions) + 1,
                "type": "viva",
                "question": f"Viva Question: If an examiner asks you to summarize the core characteristics of '{term}', how would you present it clearly and academically?",
                "correctAnswer": explanation,
                "explanation": f"The documents note: '{clean_extracted_text(raw)}'"
            })

        elif q_type == "interview":
            questions.append({
                "id": len(questions) + 1,
                "type": "interview",
                "question": f"Interview Question: During a technical job interview, how would you describe the difference, importance, or implementation details of '{term}' as covered in the study documents?",
                "correctAnswer": explanation,
                "explanation": f"The material details: '{clean_extracted_text(raw)}'"
            })

    if len(questions) < 3:
        return "Not enough information found in uploaded documents.", []

    # Format a raw text version for legacy support
    raw_text_parts = []
    for q in questions:
        raw_text_parts.append(f"Q{q['id']}: {q['question']}")
        if q['type'] in ['mcq', 'true_false']:
            raw_text_parts.append("Options:")
            for idx, opt in enumerate(q['options']):
                letter = ['A', 'B', 'C', 'D'][idx]
                raw_text_parts.append(f"{letter}. {opt}")
        raw_text_parts.append(f"Correct Answer: {q['correctAnswer']}")
        raw_text_parts.append(f"Explanation: {q['explanation']}\n")

    quiz_text = "\n".join(raw_text_parts)
    return quiz_text, questions

def generate_quiz(context, topic):
    return generate_hybrid_quiz(context, topic)

def is_duplicate_or_too_short(text, existing_texts, min_len=20):
    if not text or len(text.strip()) < min_len:
        return True
    text_lower = text.lower().strip()
    words = set(re.findall(r'\b\w+\b', text_lower))
    if len(words) < 3:
        return True
        
    for existing in existing_texts:
        existing_lower = existing.lower().strip()
        if text_lower in existing_lower or existing_lower in text_lower:
            return True
        existing_words = set(re.findall(r'\b\w+\b', existing_lower))
        if not existing_words:
            continue
        intersection = words.intersection(existing_words)
        union = words.union(existing_words)
        overlap = len(intersection) / len(union)
        if overlap > 0.45:
            return True
    return False

def synthesize_educational_response(question, top_pages, direct_answer, topic):
    # Combine top page contents
    full_text = "\n".join([page.content for _, page in top_pages])
    
    # Clean text
    full_text_clean = clean_pdf_text_for_quiz(full_text)
    
    # Extract definitions and statements
    definitions, statements = extract_definitions_and_statements(full_text_clean)
    
    # Clean terms and explanations
    for d in definitions:
        d["term"] = clean_extracted_text(d["term"])
        d["explanation"] = clean_extracted_text(d["explanation"])
        
    sections = []
    generated_texts = []

    # Introduction — neutral template text, no model call, no invented facts.
    sections.append("### Introduction")
    sections.append(f"Here's what your study material says about **{question}**:\n")

    # Formal Definition — 1 model call. Falls back to the already-computed,
    # doc-grounded direct_answer or a raw extracted definition (never a
    # hard-coded fact); omits the section entirely if none of those exist.
    def_p = clean_extracted_text(generate_focused_answer(
        full_text_clean,
        f"Explain what '{question}' is by giving a formal, precise definition from the context in one or two clear sentences.",
        max_tokens=80
    ))
    if is_duplicate_or_too_short(def_p, generated_texts, 20):
        if direct_answer and not is_duplicate_or_too_short(direct_answer, generated_texts, 20):
            def_p = direct_answer
        elif definitions:
            def_p = f"**{definitions[0]['term']}**: {definitions[0]['explanation']}"
        else:
            def_p = ""

    if def_p:
        sections.append("### Formal Definition")
        sections.append(f"{def_p}\n")
        generated_texts.append(def_p)

    # Key Concepts & Terms — at most 3 paraphrase_concept() calls total,
    # regardless of how many succeed. Each call self-heals to the raw
    # document text on failure (see paraphrase_concept), so this never
    # invents a fact; the section is omitted if there's nothing to paraphrase.
    candidates = []
    for d in definitions:
        if d["term"].lower() in question.lower() and len(definitions) > 1:
            continue
        candidates.append((d["term"], d["explanation"]))
    for s in statements:
        if len(s) > 25:
            candidates.append((None, s))

    features = []
    for term, text in candidates[:3]:
        paraphrased = paraphrase_concept(text)
        if paraphrased and not is_duplicate_or_too_short(paraphrased, generated_texts, 15):
            features.append(f"- **{term}:** {paraphrased}" if term else f"- {paraphrased}")
            generated_texts.append(paraphrased)

    if features:
        sections.append("### Key Concepts & Terms")
        sections.extend(features)
        sections.append("")

    # Detailed Explanation (How & Why it Works) — 1 model call. Falls back to
    # raw (unparaphrased) document statements, or omits the section.
    detailed_p = clean_extracted_text(generate_focused_answer(
        full_text_clean,
        f"Provide a detailed step-by-step explanation of how and why '{question}' works based on the context.",
        max_tokens=150
    ))
    if is_duplicate_or_too_short(detailed_p, generated_texts, 30):
        remaining = [s for s in statements if not is_duplicate_or_too_short(s, generated_texts, 15)]
        detailed_p = " ".join(remaining[:2]) if remaining else ""

    if detailed_p:
        sections.append("### Detailed Explanation (How & Why it Works)")
        sections.append(f"{detailed_p}\n")
        generated_texts.append(detailed_p)

    # Importance & Applications — doc-sourced only, no model call, omitted
    # if nothing in the material matches.
    importance_items = []
    for s in statements:
        if is_duplicate_or_too_short(s, generated_texts, 15):
            continue
        if any(kw in s.lower() for kw in ["help", "allow", "provid", "ensur", "reduc", "improv", "make", "enabl", "key", "critic", "import"]):
            importance_items.append(s)
            if len(importance_items) >= 2:
                break

    if importance_items:
        importance_p = " ".join(importance_items)
        sections.append("### Importance & Applications")
        sections.append(f"{importance_p}\n")
        generated_texts.append(importance_p)

    # Practical & Real-World Examples — doc-sourced only, no model call,
    # omitted if nothing in the material matches.
    example_items = []
    for s in statements:
        if is_duplicate_or_too_short(s, generated_texts, 15):
            continue
        if any(kw in s.lower() for kw in ["example", "e.g.", "such as", "instance", "for example"]):
            example_items.append(s)
            if len(example_items) >= 2:
                break

    if example_items:
        examples_p = " ".join(example_items)
        sections.append("### Practical & Real-World Examples")
        sections.append(f"{examples_p}\n")
        generated_texts.append(examples_p)

    # In Simple Words — reuses an unused, real extracted definition or
    # statement verbatim, no model call and no invented analogy. Omitted if
    # everything's already been used elsewhere.
    simple_source = None
    for d in definitions:
        if not is_duplicate_or_too_short(d["explanation"], generated_texts, 15):
            simple_source = d["explanation"]
            break
    if not simple_source:
        for s in statements:
            if not is_duplicate_or_too_short(s, generated_texts, 15):
                simple_source = s
                break

    if simple_source:
        sections.append("### In Simple Words")
        sections.append(f"In simple words: {simple_source}\n")
        generated_texts.append(simple_source)

    # Conclusion — neutral template text, no model call, no invented facts.
    sections.append("### Summary")
    sections.append(f"That's what your uploaded material covers on **{question}** — feel free to ask a follow-up question!\n")

    sections.append("---\n*Source: Uploaded study materials.*")
    return "\n".join(sections)
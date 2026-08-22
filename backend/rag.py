from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
import torch
import re
import random
import collections

model_name = "google/flan-t5-small"
tokenizer = AutoTokenizer.from_pretrained(model_name)

# Detect device
device = "cuda" if torch.cuda.is_available() else "cpu"
model = AutoModelForSeq2SeqLM.from_pretrained(model_name).to(device)

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

def generate_answer(context, question):
    """
    Generates a concise, document-grounded answer using the local Flan-T5 model.
    Follows the strict system prompt instructions.
    """
    if not context or not context.strip():
        return "The uploaded documents do not contain enough information for this question."

    # Build prompt prepending the exact system prompt rules
    system_prompt = """You are an intelligent AI Teacher Assistant.

Answer questions using the uploaded documents as the PRIMARY knowledge source.

IMPORTANT RULES:
- Explain concepts in detail
- Behave like ChatGPT
- Teach concepts naturally
- Elaborate important ideas clearly
- Make answers easy for students to understand
- Use educational and professional language
- Add logical explanation flow
- Use headings and bullet points
- Explain “why” and “how” wherever possible
- Avoid excessive summarization
- Avoid raw chunk dumping
- Avoid repetition
- Avoid robotic responses

You may improve readability and elaboration using your language intelligence, but the core concepts must come from the uploaded documents.

Your goal is to TEACH concepts deeply and clearly like a professional tutor."""

    # Compress context to first 250 words to avoid Flan-T5 truncation (512 token limit)
    words = context.split()
    if len(words) > 250:
        context_compressed = " ".join(words[:250])
    else:
        context_compressed = context

    prompt = f"""Context: {context_compressed}
Instructions: {system_prompt}
Question: {question}
Answer:"""

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
                    
    # 2. Extract implicit definitions (is a, refers to, etc.)
    sentences = re.split(r'(?<=[.!?])\s+', context)
    for sent in sentences:
        sent = sent.strip()
        sent = re.sub(r'\s+', ' ', sent)
        if len(sent) < 35 or len(sent) > 200:
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
        else:
            if len(sent) >= 40 and len(sent) <= 150:
                if not any(x in sent.lower() for x in ["question:", "context:", "instructions:", "---"]) and "#" not in sent:
                    statements.append(sent)
                    
    return definitions, statements

def get_distractors(correct_explanation, definitions, statements, topic, num_needed=3):
    distractors = []
    
    for d in definitions:
        exp = d["explanation"]
        if exp.lower() != correct_explanation.lower() and exp not in distractors:
            distractors.append(exp)
            if len(distractors) >= num_needed:
                return distractors
                
    for s in statements:
        if s.lower() not in correct_explanation.lower() and s not in distractors:
            distractors.append(s)
            if len(distractors) >= num_needed:
                return distractors
                
    fallbacks = [
        f"A design standard defined by the {topic} specification.",
        f"A legacy protocol used in older versions of the {topic} system.",
        f"A security extension implemented to prevent external intrusions.",
        f"An optimization technique designed to reduce execution overhead."
    ]
    for fb in fallbacks:
        if fb not in distractors and fb.lower() not in correct_explanation.lower():
            distractors.append(fb)
            if len(distractors) >= num_needed:
                return distractors
                
    return distractors

def mask_sentence(sentence, term=None):
    sentence_clean = re.sub(r'^[-*•+]\s*', '', sentence)
    sentence_clean = re.sub(r'^\d+[\.\)]\s*', '', sentence_clean)
    
    if term and term.lower() in sentence_clean.lower():
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        masked = pattern.sub("________", sentence_clean)
        return masked, term
        
    words = sentence_clean.split()
    stopwords = {"is", "are", "the", "and", "that", "this", "with", "from", "into", "acts", "between", "under", "over", "system", "software", "hardware", "user", "users"}
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
        
    return sentence_clean, "None"

def generate_hybrid_quiz(context, topic):
    cleaned = clean_pdf_text_for_quiz(context)
    if not cleaned or len(cleaned.strip()) < 30:
        return "Not enough information found in uploaded documents.", []
        
    definitions, statements = extract_definitions_and_statements(cleaned)

    # Shuffle so re-generating a quiz for the same topic (clicking "Build
    # Test" again) picks a different set of concepts instead of the exact
    # same questions every time — get_concept() below always reads by
    # fixed index (0, 1, 2...), so the only way to vary the output across
    # calls is to vary the order of these lists first.
    random.shuffle(definitions)
    random.shuffle(statements)

    # Pre-clean term/explanation structures to avoid formatting headers
    for d in definitions:
        d["term"] = clean_extracted_text(d["term"])
        d["explanation"] = clean_extracted_text(d["explanation"])

    total_concepts = len(definitions) + len(statements)
    if total_concepts < 2:
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', cleaned) if len(s.strip()) > 20]
        random.shuffle(sentences)
        statements = sentences[:12]
        total_concepts = len(statements)
        
    if total_concepts < 2:
        return "Not enough information found in uploaded documents.", []
        
    num_defs = len(definitions)
    num_states = len(statements)
    
    def get_concept(index):
        # The boolean marks whether `term` is a real extracted concept
        # (safe to mask verbatim) or just the leading words of a raw
        # sentence (a grammatical fragment, not a concept — must NOT be
        # used as a fill-in-the-blank answer).
        if num_defs > 0:
            item = definitions[index % num_defs]
            return item["term"], item["explanation"], item["raw"], True
        elif num_states > 0:
            s = statements[index % num_states]
            words = s.split()
            term = " ".join(words[:2]) if len(words) > 2 else "Core Concept"
            return term, s, s, False
        else:
            return "Concept", "No details available.", "No details available.", False

    questions = []
    q_id = 1
    
    # 1. MCQ
    t1, e1, r1, _ = get_concept(0)
    dist = get_distractors(e1, definitions, statements, topic, 3)
    options = [e1] + dist
    random.shuffle(options)
    correct_letter = ['A', 'B', 'C', 'D'][options.index(e1)]
    questions.append({
        "id": q_id,
        "type": "mcq",
        "question": f"Based on the study materials, what is the primary function or definition of '{t1}'?",
        "options": options,
        "correctAnswer": correct_letter,
        "explanation": f"According to the notes: '{clean_extracted_text(r1)}'"
    })
    q_id += 1
    
    # 2. True/False
    t2, e2, r2, _ = get_concept(1)
    if num_defs >= 2:
        questions.append({
            "id": q_id,
            "type": "true_false",
            "question": f"True or False: According to the documents, '{t2}' refers to {e1.rstrip('.')}.",
            "options": ["True", "False"],
            "correctAnswer": "B",
            "explanation": f"False. The document defines '{t2}' as: {e2.rstrip('.')}. Meanwhile, {e1.rstrip('.').lower()} describes '{t1}'."
        })
    else:
        questions.append({
            "id": q_id,
            "type": "true_false",
            "question": f"True or False: According to the documents, '{t2}' is defined as: {e2.rstrip('.')}.",
            "options": ["True", "False"],
            "correctAnswer": "A",
            "explanation": f"True. The material states: '{clean_extracted_text(r2)}'"
        })
    q_id += 1
    
    # 3. Fill in the Blank
    blank_term, blank_exp, blank_raw, blank_is_concept = get_concept(2)
    # Only mask the extracted term itself when it's a real concept (from a
    # definition). Otherwise `blank_term` is just the leading words of a raw
    # sentence — masking that would make the "correct answer" a grammatical
    # fragment (e.g. "A program") instead of a real word. Passing term=None
    # makes mask_sentence fall back to picking an actual content word from
    # the middle of the sentence instead.
    mask_term = blank_term if blank_is_concept else None
    masked_q, masked_word = mask_sentence(blank_raw, mask_term)
    questions.append({
        "id": q_id,
        "type": "fill_blank",
        "question": f"Fill in the blank: {clean_extracted_text(masked_q)}",
        "correctAnswer": clean_extracted_text(masked_word),
        "explanation": f"The document outlines: '{clean_extracted_text(blank_raw)}'"
    })
    q_id += 1
    
    # 4. Short Answer
    t4, e4, r4, _ = get_concept(3)
    questions.append({
        "id": q_id,
        "type": "short_answer",
        "question": f"Explain the role and definition of '{t4}' based on the study materials.",
        "correctAnswer": e4,
        "explanation": f"The notes clarify that '{t4}' is defined as: '{e4}'"
    })
    q_id += 1
    
    # 5. Long Answer
    t5, e5, r5, _ = get_concept(4)
    questions.append({
        "id": q_id,
        "type": "long_answer",
        "question": f"Describe in detail the functionality, design, or operational flow of '{t5}' as described in the documents. Explain how it interacts with other system elements.",
        "correctAnswer": e5,
        "explanation": f"The notes highlight: '{clean_extracted_text(r5)}'"
    })
    q_id += 1
    
    # 6. Scenario-based
    t6, e6, r6, _ = get_concept(5)
    questions.append({
        "id": q_id,
        "type": "scenario",
        "question": f"Scenario: A system administrator notices a challenge or needs to deploy a system related to '{t6}'. Explain how '{t6}' applies in this scenario based on the document text: '{e6}'",
        "correctAnswer": e6,
        "explanation": f"The document states: '{clean_extracted_text(r6)}'"
    })
    q_id += 1
    
    # 7. Viva Question
    t7, e7, r7, _ = get_concept(6)
    questions.append({
        "id": q_id,
        "type": "viva",
        "question": f"Viva Question: If an examiner asks you to summarize the core characteristics of '{t7}', how would you present it clearly and academically?",
        "correctAnswer": e7,
        "explanation": f"The documents note: '{clean_extracted_text(r7)}'"
    })
    q_id += 1
    
    # 8. Interview Question
    t8, e8, r8, _ = get_concept(7)
    questions.append({
        "id": q_id,
        "type": "interview",
        "question": f"Interview Question: During a technical job interview, how would you describe the difference, importance, or implementation details of '{t8}' as covered in the study documents?",
        "correctAnswer": e8,
        "explanation": f"The material details: '{clean_extracted_text(r8)}'"
    })
    q_id += 1
    
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
        
    title = question.strip('?').strip()
    title = title[0].upper() + title[1:] if title else "Study Assistant Explanation"
    
    sections = []
    generated_texts = []
    
    # Section 1: Introduction
    intro_p = generate_focused_answer(
        full_text_clean,
        f"Write a conversational, friendly tutor introduction greeting the student and saying what we are going to learn about '{question}'.",
        max_tokens=80
    )
    intro_p = clean_extracted_text(intro_p)
    if not intro_p or len(intro_p) < 20:
        intro_p = f"Hello! Let's explore the concept of **{question}** together. Below is a detailed breakdown from your study documents to help you understand this topic."
    
    sections.append("### Introduction")
    sections.append(f"{intro_p}\n")
    generated_texts.append(intro_p)
    
    # Section 2: Formal Definition
    def_p = generate_focused_answer(
        full_text_clean,
        f"Explain what '{question}' is by giving a formal, precise definition from the context in one or two clear sentences.",
        max_tokens=80
    )
    def_p = clean_extracted_text(def_p)
    if is_duplicate_or_too_short(def_p, generated_texts, 20):
        if definitions:
            def_p = f"Based on the provided materials, **{definitions[0]['term']}** is defined as: {definitions[0]['explanation']}"
        else:
            def_p = direct_answer
            
    if is_duplicate_or_too_short(def_p, generated_texts, 20):
        def_p = f"In the context of our study material, **{question}** refers to the core concept and details outlined below, which helps manage and coordinate related systems."
        
    sections.append("### Formal Definition")
    sections.append(f"{def_p}\n")
    generated_texts.append(def_p)
    
    # Section 3: Key Concepts & Terms
    features = []
    for d in definitions:
        term = d["term"]
        exp = d["explanation"]
        if term.lower() in question.lower() and len(definitions) > 1:
            continue
        paraphrased_exp = paraphrase_concept(exp)
        if paraphrased_exp and not is_duplicate_or_too_short(paraphrased_exp, generated_texts, 15):
            features.append(f"- **{term}:** {paraphrased_exp}")
            if len(features) >= 4:
                break
            
    if len(features) < 3:
        for s in statements:
            if len(s) > 25:
                para_s = paraphrase_concept(s)
                if para_s and not is_duplicate_or_too_short(para_s, generated_texts, 15):
                    features.append(f"- {para_s}")
                    if len(features) >= 4:
                        break
                      
    if not features:
        features.append("- No specific sub-concepts were explicitly listed in the study materials, but the main concept is described in detail below.")
        
    sections.append("### Key Concepts & Terms")
    for feat in features[:4]:
        sections.append(feat)
    sections.append("")
    generated_texts.append(" ".join(features))
    
    # Section 4: Detailed Explanation (How & Why it Works)
    detailed_p = generate_focused_answer(
        full_text_clean,
        f"Provide a detailed step-by-step explanation of how and why '{question}' works based on the context.",
        max_tokens=150
    )
    detailed_p = clean_extracted_text(detailed_p)
    if is_duplicate_or_too_short(detailed_p, generated_texts, 30):
        para_statements = []
        for s in statements:
            para_s = paraphrase_concept(s)
            if para_s and not is_duplicate_or_too_short(para_s, generated_texts, 15):
                para_statements.append(para_s)
                if len(para_statements) >= 3:
                    break
        if para_statements:
            detailed_p = " ".join(para_statements)
        else:
            detailed_p = f"The operation of {question} relies on the systematic flow defined in the document. It coordinates different elements to ensure consistency and reliability, acting as a crucial component of the overall process."
            
    sections.append("### Detailed Explanation (How & Why it Works)")
    sections.append(f"{detailed_p}\n")
    generated_texts.append(detailed_p)
    
    # Section 5: Importance & Applications
    importance_p = generate_focused_answer(
        full_text_clean,
        f"Explain the educational importance, significance, or applications of '{question}' based on the context.",
        max_tokens=120
    )
    importance_p = clean_extracted_text(importance_p)
    if is_duplicate_or_too_short(importance_p, generated_texts, 25):
        importance_items = []
        for s in statements:
            if any(keyword in s.lower() for keyword in ["help", "allow", "provid", "ensur", "reduc", "improv", "make", "enabl", "key", "critic", "import"]):
                para_s = paraphrase_concept(s)
                if para_s and not is_duplicate_or_too_short(para_s, generated_texts, 15):
                    importance_items.append(para_s)
                    if len(importance_items) >= 2:
                        break
        if importance_items:
            importance_p = " ".join(importance_items)
        else:
            importance_p = f"Understanding this concept is highly important because it forms the baseline for configuring and optimizing standard operations as highlighted in the study guide."
            
    sections.append("### Importance & Applications")
    sections.append(f"{importance_p}\n")
    generated_texts.append(importance_p)
    
    # Section 6: Practical/Real-world Examples
    examples_p = generate_focused_answer(
        full_text_clean,
        f"Provide concrete examples or case studies of '{question}' mentioned in the context.",
        max_tokens=100
    )
    examples_p = clean_extracted_text(examples_p)
    if is_duplicate_or_too_short(examples_p, generated_texts, 20):
        ex_sentences = []
        for s in statements:
            if any(ex_kw in s.lower() for ex_kw in ["example", "e.g.", "such as", "instance", "for example"]):
                para_ex = paraphrase_concept(s)
                if para_ex and not is_duplicate_or_too_short(para_ex, generated_texts, 15):
                    ex_sentences.append(para_ex)
                    if len(ex_sentences) >= 2:
                        break
        if ex_sentences:
            examples_p = " ".join(ex_sentences)
        else:
            examples_p = f"While the uploaded documents do not highlight specific real-world case studies for this concept, we can observe its practical use in standard operations within the field of {topic}."
            
    sections.append("### Practical & Real-World Examples")
    sections.append(f"{examples_p}\n")
    generated_texts.append(examples_p)
    
    # Section 7: Simple Explanation
    simple_p = generate_focused_answer(
        full_text_clean,
        f"Explain the concept of '{question}' simply using an analogy or in layperson terms for a student. Start with the phrase 'In simple words'.",
        max_tokens=100
    )
    simple_p = clean_extracted_text(simple_p)
    if is_duplicate_or_too_short(simple_p, generated_texts, 20):
        if definitions:
            simple_p = f"In simple words, you can think of this concept like a structured queue where each item is processed one by one to keep things organized and avoid confusion."
        else:
            simple_p = f"In simple words, this concept works like a set of clear rules that everyone follows so that the whole system runs smoothly and without errors."
    else:
        simple_p_clean = simple_p.strip()
        if not simple_p_clean.lower().startswith("in simple words"):
            first_char = simple_p_clean[0]
            if first_char.isupper() and not simple_p_clean[:3].isupper():
                first_char = first_char.lower()
            simple_p = f"In simple words, {first_char}{simple_p_clean[1:]}"
            
    sections.append("### In Simple Words")
    sections.append(f"{simple_p}\n")
    generated_texts.append(simple_p)
    
    # Section 8: Short Conclusion
    conclusion_p = generate_focused_answer(
        full_text_clean,
        f"Write a short, encouraging tutor-like summary or takeaway about '{question}' for a student.",
        max_tokens=80
    )
    conclusion_p = clean_extracted_text(conclusion_p)
    if is_duplicate_or_too_short(conclusion_p, generated_texts, 20):
        conclusion_p = f"In summary, mastering '{question}' is a key milestone in learning about {topic}. Keep up the great work, and feel free to ask more questions if any part needs further clarification!"
        
    sections.append("### Summary & Tutor's Conclusion")
    sections.append(f"{conclusion_p}\n")
    
    sections.append("---\n*Source: Uploaded study materials.*")
    return "\n".join(sections)
// Fix round 3, item 5 (+ item 4 for the word floor): pure validators so
// every AI tool -- Summarizer, Flashcards, AI Tutor, Semantic search --
// rejects empty/space-only input with a clear message and no AI call,
// matching Quiz Builder's existing behaviour. Kept in their own file so
// they're unit testable without mounting the whole page.

// Mirrors the backend's MIN_SUMMARIZE_WORDS (main.py) -- caught here too
// so the student sees the message instantly, without a round trip just
// to be told the same thing.
export const MIN_SUMMARIZE_WORDS = 40;

/**
 * A plain "please enter something" check, for a single text field (topic,
 * question, search term). Returns the given message if `value` is empty
 * or only whitespace, otherwise null.
 */
export function validateNonEmpty(value, message) {
  return value && value.trim() ? null : message;
}

/**
 * The Notes Summarizer has two modes (Fix round 3, item 3): 'paste' needs
 * non-empty text of at least MIN_SUMMARIZE_WORDS words; 'document' needs
 * a chosen document (the optional topic/unit field has no minimum).
 * Returns an error message, or null when the input is fine to submit.
 */
export function validateSummarizerInput({ mode, text, bookId }) {
  if (mode === 'document') {
    return bookId ? null : 'Please choose a document first.';
  }
  if (!text || !text.trim()) return 'Please paste some text first.';
  if (text.trim().split(/\s+/).length < MIN_SUMMARIZE_WORDS) {
    return 'Please paste at least a paragraph to summarize.';
  }
  return null;
}

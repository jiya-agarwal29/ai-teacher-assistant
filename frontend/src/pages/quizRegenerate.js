// Fix round 4, item 2: the regenerate endpoint's response
// (main.py's _question_summary -- snake_case) has to be merged into the
// Quiz Builder's own question shape (camelCase, from _save_generated_
// questions), keeping the item's local sequential id (used as the React
// key and for score calculation) rather than the response's question_id.
// Kept in its own file so this mapping is unit testable without mounting
// the whole page.
export function mergeRegeneratedQuestion(existing, updated) {
  return {
    ...existing,
    type: updated.type,
    question: updated.question,
    correctAnswer: updated.correct_answer,
    options: updated.options || undefined,
    explanation: updated.explanation,
    bloom_level: updated.bloom_level,
    bloom_label: updated.bloom_label,
    bloom_check: updated.bloom_check,
    detected_level: updated.detected_level,
    source: updated.source,
  };
}

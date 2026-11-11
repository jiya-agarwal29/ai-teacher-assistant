import { describe, it, expect } from 'vitest';
import { mergeRegeneratedQuestion } from './quizRegenerate';

// Fix round 4, item 2: the regenerate endpoint's snake_case response must
// land correctly on the Quiz Builder's own camelCase question shape,
// without losing the item's local sequential id.
describe('mergeRegeneratedQuestion', () => {
  const existing = {
    id: 3,
    question_id: 42,
    type: 'short_answer',
    question: 'Old question text.',
    correctAnswer: 'Old answer.',
    explanation: 'Old explanation.',
    bloom_level: 'understand',
    bloom_label: 'Understand',
    bloom_check: 'verified',
    detected_level: null,
    source: { book_name: 'Notes.pdf', page_number: 3 },
  };

  it('keeps the local sequential id and question_id', () => {
    const updated = {
      question_id: 42, type: 'short_answer', question: 'New question text.',
      correct_answer: 'New answer.', explanation: 'New explanation.',
      bloom_level: 'understand', bloom_label: 'Understand', bloom_check: 'verified',
      detected_level: null, source: { book_name: 'Notes.pdf', page_number: 5 },
    };
    const merged = mergeRegeneratedQuestion(existing, updated);
    expect(merged.id).toBe(3);
    expect(merged.question_id).toBe(42);
  });

  it('maps correct_answer (snake_case) to correctAnswer (camelCase)', () => {
    const updated = {
      question_id: 42, type: 'short_answer', question: 'New question text.',
      correct_answer: 'New answer.', explanation: 'New explanation.',
      bloom_level: 'understand', bloom_label: 'Understand', bloom_check: 'verified',
      detected_level: null, source: null,
    };
    const merged = mergeRegeneratedQuestion(existing, updated);
    expect(merged.correctAnswer).toBe('New answer.');
    expect(merged.correct_answer).toBeUndefined();
  });

  it('replaces question/explanation/bloom fields/source with the new values', () => {
    const updated = {
      question_id: 42, type: 'mcq', question: 'A brand new question?',
      correct_answer: 'B', explanation: 'A new explanation.',
      bloom_level: 'apply', bloom_label: 'Apply', bloom_check: 'flagged',
      detected_level: 'analyze', source: { book_name: 'Other.pdf', page_number: 9 },
    };
    const merged = mergeRegeneratedQuestion(existing, updated);
    expect(merged.question).toBe('A brand new question?');
    expect(merged.explanation).toBe('A new explanation.');
    expect(merged.bloom_level).toBe('apply');
    expect(merged.bloom_label).toBe('Apply');
    expect(merged.bloom_check).toBe('flagged');
    expect(merged.detected_level).toBe('analyze');
    expect(merged.source).toEqual({ book_name: 'Other.pdf', page_number: 9 });
  });

  it('carries options for an mcq replacement, and omits them when absent', () => {
    const asMcq = mergeRegeneratedQuestion(existing, {
      question_id: 42, type: 'mcq', question: 'Pick one.', correct_answer: 'A',
      options: ['A', 'B', 'C', 'D'], explanation: 'x',
      bloom_level: 'remember', bloom_label: 'Remember', bloom_check: 'verified',
      detected_level: null, source: null,
    });
    expect(asMcq.options).toEqual(['A', 'B', 'C', 'D']);

    const asShortAnswer = mergeRegeneratedQuestion(existing, {
      question_id: 42, type: 'short_answer', question: 'Explain it.', correct_answer: 'x',
      explanation: 'x', bloom_level: 'understand', bloom_label: 'Understand',
      bloom_check: 'verified', detected_level: null, source: null,
    });
    expect(asShortAnswer.options).toBeUndefined();
  });
});

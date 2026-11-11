import { describe, it, expect } from 'vitest';
import { validateNonEmpty, validateSummarizerInput, MIN_SUMMARIZE_WORDS } from './aiToolsValidation';

// Fix round 3, item 5: every AI tool must reject empty/space-only input
// with a clear message, same as Quiz Builder already does.
describe('validateNonEmpty', () => {
  it('returns the message for an empty string', () => {
    expect(validateNonEmpty('', 'Please enter a topic.')).toBe('Please enter a topic.');
  });

  it('returns the message for a whitespace-only string', () => {
    expect(validateNonEmpty('   ', 'Please enter a topic.')).toBe('Please enter a topic.');
  });

  it('returns the message for null/undefined', () => {
    expect(validateNonEmpty(null, 'Please enter a topic.')).toBe('Please enter a topic.');
    expect(validateNonEmpty(undefined, 'Please enter a topic.')).toBe('Please enter a topic.');
  });

  it('returns null for real content', () => {
    expect(validateNonEmpty('bees', 'Please enter a topic.')).toBeNull();
  });
});

// Fix round 3, items 3 + 4 + 5: the Summarizer's two modes each have
// their own rule for what counts as valid input.
describe('validateSummarizerInput', () => {
  it('rejects empty pasted text', () => {
    expect(validateSummarizerInput({ mode: 'paste', text: '' }))
      .toBe('Please paste some text first.');
  });

  it('rejects whitespace-only pasted text', () => {
    expect(validateSummarizerInput({ mode: 'paste', text: '    ' }))
      .toBe('Please paste some text first.');
  });

  it('rejects pasted text under the word floor', () => {
    const shortText = 'Too short to summarize meaningfully.';
    expect(validateSummarizerInput({ mode: 'paste', text: shortText }))
      .toBe('Please paste at least a paragraph to summarize.');
  });

  it('accepts pasted text at or over the word floor', () => {
    const longEnoughText = Array(MIN_SUMMARIZE_WORDS).fill('word').join(' ');
    expect(validateSummarizerInput({ mode: 'paste', text: longEnoughText })).toBeNull();
  });

  it('rejects document mode with no document chosen', () => {
    expect(validateSummarizerInput({ mode: 'document', bookId: '' }))
      .toBe('Please choose a document first.');
  });

  it('accepts document mode once a document is chosen, regardless of text', () => {
    expect(validateSummarizerInput({ mode: 'document', bookId: '42', text: '' })).toBeNull();
  });
});

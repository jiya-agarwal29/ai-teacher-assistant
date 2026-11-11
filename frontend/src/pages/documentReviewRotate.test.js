import { describe, it, expect, vi } from 'vitest';
import { shouldRereadAfterRotate } from './documentReviewRotate';

// Fix round 2, item 0: rotate must not silently overwrite a teacher's
// edits. An untouched page re-reads automatically (no popup); an edited
// page asks first, and Cancel keeps the edited text (rotate only).

describe('shouldRereadAfterRotate', () => {
  it('re-reads automatically, with no confirm prompt, when the page was never edited', () => {
    const confirmFn = vi.fn();
    const result = shouldRereadAfterRotate({ isDirty: false, alreadyEdited: false, confirmFn });
    expect(result).toBe(true);
    expect(confirmFn).not.toHaveBeenCalled();
  });

  it('asks first, and re-reads, when the current draft is dirty and the teacher confirms', () => {
    const confirmFn = vi.fn(() => true);
    const result = shouldRereadAfterRotate({ isDirty: true, alreadyEdited: false, confirmFn });
    expect(result).toBe(true);
    expect(confirmFn).toHaveBeenCalledWith('Re-read and replace your edits?');
  });

  it('keeps the edited text (does not re-read) when the teacher cancels the prompt', () => {
    const confirmFn = vi.fn(() => false);
    const result = shouldRereadAfterRotate({ isDirty: true, alreadyEdited: false, confirmFn });
    expect(result).toBe(false);
  });

  it('also asks when the page was edited earlier this session but the draft is no longer dirty', () => {
    const confirmFn = vi.fn(() => false);
    const result = shouldRereadAfterRotate({ isDirty: false, alreadyEdited: true, confirmFn });
    expect(result).toBe(false);
    expect(confirmFn).toHaveBeenCalledWith('Re-read and replace your edits?');
  });
});

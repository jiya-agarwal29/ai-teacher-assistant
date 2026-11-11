import { describe, it, expect } from 'vitest';
import { draftKeyFor, shouldRetryChatError } from './chatHelpers';

// Fix round 2, item 11: drafts are kept per chat, keyed by conversation id.
describe('draftKeyFor', () => {
  it('returns "new" for a not-yet-created chat (no id yet)', () => {
    expect(draftKeyFor(null)).toBe('new');
    expect(draftKeyFor(undefined)).toBe('new');
  });

  it('returns the conversation id as a string for an existing chat', () => {
    expect(draftKeyFor(42)).toBe('42');
  });

  it('gives different chats different keys', () => {
    expect(draftKeyFor(1)).not.toBe(draftKeyFor(2));
  });
});

// Fix round 2, item 13: only a 503 (AI busy/rate-limited) is retried, and
// only while attempts remain.
describe('shouldRetryChatError', () => {
  it('retries a 503 when attempts remain', () => {
    expect(shouldRetryChatError({ status: 503 }, 1, 3)).toBe(true);
    expect(shouldRetryChatError({ status: 503 }, 2, 3)).toBe(true);
  });

  it('does not retry a 503 once attempts are exhausted', () => {
    expect(shouldRetryChatError({ status: 503 }, 3, 3)).toBe(false);
  });

  it('does not retry any other status', () => {
    expect(shouldRetryChatError({ status: 500 }, 1, 3)).toBe(false);
    expect(shouldRetryChatError({ status: 401 }, 1, 3)).toBe(false);
  });

  it('does not retry a network error with no status at all', () => {
    expect(shouldRetryChatError(new Error('network down'), 1, 3)).toBe(false);
  });
});

import { describe, it, expect } from 'vitest';
import { decodeHtmlEntities, cleanSnippetText } from './textCleanup';

// Fix round 2, Part A item 5: semantic search snippets showed raw HTML
// entities ("&amp;") and a doubled-up bullet marker ("- –") per line.

describe('decodeHtmlEntities', () => {
  it('decodes &amp; to a plain ampersand', () => {
    expect(decodeHtmlEntities('firewalls &amp; VPNs')).toBe('firewalls & VPNs');
  });

  it('decodes multiple different entities in one string', () => {
    expect(decodeHtmlEntities('a &lt; b &amp; b &gt; c')).toBe('a < b & b > c');
  });

  it('leaves text with no entities unchanged', () => {
    expect(decodeHtmlEntities('plain text')).toBe('plain text');
  });
});

describe('cleanSnippetText', () => {
  it('collapses a doubled-up bullet marker to a single one', () => {
    expect(cleanSnippetText('- – Point one')).toBe('- Point one');
  });

  it('decodes entities and fixes bullets together, line by line', () => {
    const text = 'Topics:\n- – Firewalls &amp; IDS\n- Encryption';
    expect(cleanSnippetText(text)).toBe('Topics:\n- Firewalls & IDS\n- Encryption');
  });

  it('leaves a normal single bullet line unchanged', () => {
    expect(cleanSnippetText('- Just one marker')).toBe('- Just one marker');
  });
});

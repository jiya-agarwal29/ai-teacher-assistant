import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import MarkdownRenderer from './MarkdownRenderer';
import { stripMathDelimiters } from '../utils/textCleanup';

// Fix round 2, Part A: one shared renderer for AI text everywhere, instead
// of each page showing raw "###"/"**"/"$x$" or rolling its own parser.

describe('MarkdownRenderer', () => {
  it('renders a GitHub-style table as real table markup', () => {
    const content = [
      '| Name | Value |',
      '| --- | --- |',
      '| alpha | 1 |',
      '| beta | 2 |',
    ].join('\n');
    render(<MarkdownRenderer content={content} />);

    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Name' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: 'alpha' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: 'beta' })).toBeInTheDocument();
  });

  it('keeps nested list indentation as real nested lists', () => {
    const content = '- Parent item\n  - Child item\n  - Another child\n- Second parent';
    const { container } = render(<MarkdownRenderer content={content} />);

    const topLevelList = container.querySelector('ul');
    expect(topLevelList).toBeTruthy();
    const nestedList = topLevelList.querySelector('ul');
    expect(nestedList).toBeTruthy();
    expect(nestedList.querySelectorAll('li')).toHaveLength(2);
  });

  it('renders a heading as a real heading element, not raw "###"', () => {
    render(<MarkdownRenderer content={'## Slide 2: Overview'} />);
    expect(screen.getByRole('heading', { level: 2, name: 'Slide 2: Overview' })).toBeInTheDocument();
    expect(screen.queryByText(/##/)).not.toBeInTheDocument();
  });

  it('renders bold text as a real <strong>, not raw "**"', () => {
    render(<MarkdownRenderer content={'This is **very important**.'} />);
    const strong = screen.getByText('very important');
    expect(strong.tagName).toBe('STRONG');
    expect(screen.queryByText(/\*\*/)).not.toBeInTheDocument();
  });

  it('strips simple $...$ maths delimiters as a fallback', () => {
    render(<MarkdownRenderer content={'Solve for $x$: $x + 1$ equals $2^6$.'} />);
    expect(screen.getByText(/Solve for x: x \+ 1 equals 2\^6\./)).toBeInTheDocument();
    expect(screen.queryByText(/\$/)).not.toBeInTheDocument();
  });

  it('returns null for empty content instead of rendering an empty wrapper', () => {
    const { container } = render(<MarkdownRenderer content="" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('stripMathDelimiters', () => {
  it('strips a matched $...$ pair', () => {
    expect(stripMathDelimiters('the formula is $x + 1$ here')).toBe('the formula is x + 1 here');
  });

  it('leaves a single, unmatched $ alone (no partner to pair with)', () => {
    expect(stripMathDelimiters('that costs $5.')).toBe('that costs $5.');
  });

  it('passes through text with no math delimiters unchanged', () => {
    expect(stripMathDelimiters('plain text, nothing special')).toBe('plain text, nothing special');
  });
});

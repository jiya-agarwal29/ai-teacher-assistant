// Shared by semantic search snippets and the chat citation panel -- both
// show a raw indexed document chunk, not AI-written markdown, so they get
// a plain-text cleanup instead of the full MarkdownRenderer treatment.

const HTML_ENTITIES = {
  '&amp;': '&',
  '&lt;': '<',
  '&gt;': '>',
  '&quot;': '"',
  '&#39;': "'",
  '&apos;': "'",
  '&nbsp;': ' ',
};

const HTML_ENTITY_PATTERN = /&(?:amp|lt|gt|quot|#39|apos|nbsp);/g;

export function decodeHtmlEntities(text) {
  if (!text) return text;
  return text.replace(HTML_ENTITY_PATTERN, (match) => HTML_ENTITIES[match] || match);
}

// Collapses a line that starts with two bullet markers in a row (e.g.
// "- – Point one", from a source line that already began with its own
// dash) down to a single leading marker.
const DOUBLE_BULLET_PATTERN = /^(\s*)[-*•–]\s*[-*•–]\s+/;

export function cleanSnippetText(text) {
  if (!text) return text;
  return decodeHtmlEntities(text)
    .split('\n')
    .map((line) => line.replace(DOUBLE_BULLET_PATTERN, '$1- '))
    .join('\n');
}

// Used by MarkdownRenderer -- lives here (not in that component file) so
// that file can export only the component itself (react-refresh lint
// rule). AI prompts are told to write maths as plain text (n, x + 1, 2^6);
// this is a fallback for when one slips through anyway, stripping simple,
// non-nested $...$ delimiters. Deliberately regex-simple -- no real
// maths-rendering library, to keep bundle size down for the future phone app.
const MATH_DELIMITER_PATTERN = /\$([^$\n]+)\$/g;

export function stripMathDelimiters(text) {
  if (!text) return text;
  return text.replace(MATH_DELIMITER_PATTERN, '$1');
}

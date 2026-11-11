import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import { stripMathDelimiters } from '../utils/textCleanup';

const components = {
  h1: ({ children }) => <h1 className="text-base font-extrabold text-slate-900 dark:text-white mt-4 mb-2">{children}</h1>,
  h2: ({ children }) => <h2 className="text-sm font-bold text-slate-900 dark:text-white mt-3.5 mb-2">{children}</h2>,
  h3: ({ children }) => <h3 className="text-xs font-bold text-slate-900 dark:text-white mt-3 mb-1.5">{children}</h3>,
  h4: ({ children }) => <h4 className="text-[11px] font-bold text-slate-800 dark:text-slate-200 mt-2.5 mb-1">{children}</h4>,
  h5: ({ children }) => <h5 className="text-[11px] font-bold text-slate-800 dark:text-slate-200 mt-2 mb-1">{children}</h5>,
  h6: ({ children }) => <h6 className="text-[11px] font-bold text-slate-800 dark:text-slate-200 mt-2 mb-1">{children}</h6>,
  p: ({ children }) => <p className="my-1.5 text-slate-700 dark:text-slate-200 leading-relaxed text-xs">{children}</p>,
  ul: ({ children }) => <ul className="list-disc ml-5 my-2 space-y-1.5">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal ml-5 my-2 space-y-1.5">{children}</ol>,
  li: ({ children }) => <li className="text-slate-700 dark:text-slate-200 text-xs">{children}</li>,
  strong: ({ children }) => <strong className="font-bold text-slate-900 dark:text-white">{children}</strong>,
  em: ({ children }) => <em className="italic text-slate-500 dark:text-slate-400">{children}</em>,
  hr: () => <hr className="my-4 border-slate-200 dark:border-slate-800/80" />,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="font-semibold text-violet-600 dark:text-violet-400 hover:underline">
      {children}
    </a>
  ),
  // pre's `[&_code]:...` resets undo the inline-code badge styling below
  // for the code element that sits inside a fenced code block, without
  // needing to tell block code apart from inline code in JS -- a fenced
  // block without a language tag has no className to detect it by.
  pre: ({ children }) => (
    <pre className="my-3 p-4 rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-950 text-slate-200 overflow-x-auto text-[11px] font-mono leading-relaxed [&_code]:bg-transparent [&_code]:p-0 [&_code]:border-0 [&_code]:rounded-none [&_code]:text-slate-200">
      {children}
    </pre>
  ),
  code: ({ children }) => (
    <code className="px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-900/60 text-slate-800 dark:text-slate-200 font-mono text-[10px] border border-slate-200/30 dark:border-slate-800/60">
      {children}
    </code>
  ),
  // Scrolls sideways inside its own box on a narrow screen instead of the
  // whole page (Problem log row 7).
  table: ({ children }) => (
    <div className="my-3 max-w-full overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
      <table className="w-full text-[11px] border-collapse">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-slate-50 dark:bg-slate-900">{children}</thead>,
  tbody: ({ children }) => <tbody>{children}</tbody>,
  tr: ({ children }) => <tr className="border-b border-slate-100 dark:border-slate-800/60 last:border-0">{children}</tr>,
  th: ({ children }) => <th className="px-3 py-2 text-left font-bold text-slate-700 dark:text-slate-200 whitespace-nowrap">{children}</th>,
  td: ({ children }) => <td className="px-3 py-2 text-slate-600 dark:text-slate-300 align-top">{children}</td>,
};

/**
 * The one shared renderer for AI-generated text (markdown) -- Chat, AI
 * Tutor, Notes Summarizer, Flashcards, semantic search results, quiz
 * explanations and question-paper previews all use this instead of each
 * showing raw "###"/"**"/"## Slide 2" or rolling their own parser.
 *
 * remark-gfm adds GitHub-style tables, strikethrough, and task lists.
 * remark-breaks turns a single line break into a visual one (<br>)
 * instead of commonmark's default of merging consecutive lines into one
 * paragraph -- AI answers are written one line per thought, not with a
 * blank line between every line, so this keeps that intended structure.
 */
export default function MarkdownRenderer({ content, className = '' }) {
  const text = stripMathDelimiters(content || '');
  if (!text) return null;
  return (
    <div className={`space-y-1 ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

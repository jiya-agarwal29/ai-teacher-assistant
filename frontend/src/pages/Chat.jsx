import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { api, PENDING_DOCUMENTS_MESSAGE, userScopedKey } from '../services/api';
import MessageWithDocsLink from '../components/MessageWithDocsLink';
import MarkdownRenderer from '../components/MarkdownRenderer';
import { cleanSnippetText } from '../utils/textCleanup';
import { draftKeyFor, shouldRetryChatError } from './chatHelpers';
import {
  Send,
  Plus,
  MessageSquare,
  BookOpen,
  Trash2,
  Pencil,
  Sparkles,
  Loader2,
  AlertCircle
} from 'lucide-react';

const MAX_TITLE_CHARS = 40;

// Mirrors main._auto_title_from_question() exactly, so the sidebar/header
// show the right title immediately instead of waiting on a refetch.
function autoTitleFromQuestion(question) {
  const text = question.trim();
  if (!text) return 'New chat';
  if (text.length <= MAX_TITLE_CHARS) return text;
  return `${text.slice(0, MAX_TITLE_CHARS - 1).trimEnd()}…`;
}

export default function Chat() {
  const [conversations, setConversations] = useState([]);
  const [conversationsLoading, setConversationsLoading] = useState(true);
  const [conversationsError, setConversationsError] = useState('');

  const [activeConvId, setActiveConvId] = useState(null);
  // Fix round 2, item 12: messages are cached per conversation (not one
  // flat array for "whatever's on screen"), so a question sent from a
  // brand-new chat stays visible in THAT chat even if the teacher clicks
  // "New Chat" or switches to another conversation while the answer is
  // still loading in the background.
  const [messagesByConv, setMessagesByConv] = useState({});
  const [loadingMessages, setLoadingMessages] = useState(false);

  // Which conversation(s) currently have an answer loading in the
  // background -- keyed by id, not a single global flag, for the same
  // reason as messagesByConv above.
  const [loadingConvIds, setLoadingConvIds] = useState(() => new Set());
  // Per conversation: 'working' (shown after ~10s of silence) or 'busy'
  // (the AI was rate-limited and is being retried) -- Fix round 2, item 13.
  const [statusByConv, setStatusByConv] = useState({});

  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState('');

  const [question, setQuestion] = useState('');
  const [error, setError] = useState('');

  // Citations side panel
  const [selectedCitation, setSelectedCitation] = useState(null);

  // Powers the welcome screen's personalized suggestion chips -- loaded
  // once; a failure here just means a plainer welcome screen, not a
  // page-level error, so it's swallowed rather than surfaced.
  const [books, setBooks] = useState([]);
  useEffect(() => {
    let ignore = false;
    (async () => {
      try {
        const data = await api.books.getAll();
        if (!ignore) setBooks(data || []);
      } catch {
        // Ignored -- see comment above.
      }
    })();
    return () => { ignore = true; };
  }, []);

  // Fix round 2, item 11: restored once on mount (a page refresh, or
  // coming back from another page -- this component unmounts on page
  // navigation, so only something written to localStorage, not component
  // state, survives that) and written back on every change -- not just on
  // logout/unload like the old single, unkeyed draft slot did, since
  // switching pages inside the app never fires beforeunload.
  const draftsStorageKey = userScopedKey('chat_drafts_by_conversation');
  const [draftsByConv, setDraftsByConv] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(draftsStorageKey)) || {};
    } catch {
      return {};
    }
  });
  const draftsByConvRef = useRef(draftsByConv);
  useEffect(() => {
    draftsByConvRef.current = draftsByConv;
    try {
      localStorage.setItem(draftsStorageKey, JSON.stringify(draftsByConv));
    } catch {
      // Storage full/unavailable -- the draft just won't survive a
      // refresh this time; nothing else depends on this write succeeding.
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftsByConv]);

  const setDraftForConv = (convId, text) => {
    const key = draftKeyFor(convId);
    setDraftsByConv((prev) => {
      if (!text) {
        if (!(key in prev)) return prev;
        const next = { ...prev };
        delete next[key];
        return next;
      }
      return { ...prev, [key]: text };
    });
  };

  // Lets an async handler check, once it resolves, whether the teacher is
  // still even looking at the chat it was sent from -- `activeConvId`
  // itself is captured stale inside a closure started before a navigation.
  const activeConvIdRef = useRef(activeConvId);
  useEffect(() => { activeConvIdRef.current = activeConvId; }, [activeConvId]);

  const stillWorkingTimers = useRef({});

  const messagesEndRef = useRef(null);

  const refreshConversations = useCallback(() => (
    api.conversations.list()
      .then((data) => {
        setConversations(data);
        setConversationsError('');
        return data;
      })
      .catch((err) => {
        setConversationsError(err.message || 'Could not load your conversations.');
        return [];
      })
  ), []);

  const openConversation = useCallback(async (id) => {
    setActiveConvId(id);
    setQuestion(draftsByConvRef.current[draftKeyFor(id)] || '');
    setError('');

    if (loadingConvIds.has(id)) {
      // An answer for this chat is still being generated in the
      // background -- the server has nothing saved for it yet (it only
      // saves once the answer is ready), so trust the local cache (the
      // question, and the loading indicator) instead of refetching and
      // briefly showing it as empty.
      return;
    }

    setLoadingMessages(true);
    try {
      const detail = await api.conversations.get(id);
      setMessagesByConv((prev) => ({ ...prev, [id]: detail.messages }));
    } catch (err) {
      setError(err.message || 'Could not load this conversation.');
      setMessagesByConv((prev) => ({ ...prev, [id]: [] }));
    } finally {
      setLoadingMessages(false);
    }
  }, [loadingConvIds]);

  // Load the conversation list once, from the server -- and open the most
  // recently active one automatically, the same "always something on
  // screen" feel the old localStorage version had.
  useEffect(() => {
    let ignore = false;
    (async () => {
      const data = await refreshConversations();
      if (!ignore) {
        setConversationsLoading(false);
        if (data.length > 0) {
          openConversation(data[0].id);
        }
      }
    })();
    return () => { ignore = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Memoized so the scroll effect below doesn't refire on every render
  // just because an empty/new chat's fallback `[]` is a new array each time.
  const messages = useMemo(
    () => (activeConvId != null ? (messagesByConv[activeConvId] || []) : []),
    [activeConvId, messagesByConv]
  );
  const isTyping = activeConvId != null && loadingConvIds.has(activeConvId);
  const activeTitle = activeConvId == null
    ? 'New chat'
    : (conversations.find((c) => c.id === activeConvId)?.title ?? 'New chat');

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping]);

  const handleNewChat = () => {
    setActiveConvId(null);
    setQuestion(draftsByConvRef.current[draftKeyFor(null)] || '');
    setError('');
  };

  const handleDeleteChat = async (e, id) => {
    e.stopPropagation();
    if (!window.confirm('Delete this conversation? This cannot be undone.')) return;
    try {
      await api.conversations.remove(id);
      const remaining = conversations.filter((c) => c.id !== id);
      setConversations(remaining);
      setMessagesByConv((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      setDraftForConv(id, '');
      if (activeConvId === id) {
        if (remaining.length > 0) {
          openConversation(remaining[0].id);
        } else {
          handleNewChat();
        }
      }
    } catch (err) {
      setError(err.message || 'Could not delete this conversation.');
    }
  };

  const startRename = (e, conv) => {
    e.stopPropagation();
    setRenamingId(conv.id);
    setRenameValue(conv.title);
  };

  const commitRename = async (id) => {
    const title = renameValue.trim();
    setRenamingId(null);
    if (!title) return;
    try {
      await api.conversations.rename(id, title);
      setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, title } : c)));
    } catch (err) {
      setError(err.message || 'Could not rename this conversation.');
    }
  };

  // Fix round 2, item 13: retries a rate-limited (503) chat request itself
  // instead of surfacing a failure for something that usually succeeds a
  // few seconds later -- the backend's own retry/backoff (llm.py) is what
  // runs *within* one such attempt; this is the outer, whole-request retry
  // for when even that gave up. Marks `convId` as "busy" so the UI can say
  // so instead of just sitting there silently.
  const sendChatWithRetry = async (queryText, convId) => {
    const maxAttempts = 3;
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      try {
        return await api.chat.send(queryText, convId);
      } catch (err) {
        if (!shouldRetryChatError(err, attempt, maxAttempts)) throw err;
        setStatusByConv((prev) => ({ ...prev, [convId]: 'busy' }));
        await new Promise((resolve) => setTimeout(resolve, 2000 * attempt));
      }
    }
    return undefined; // unreachable -- the loop above always returns or throws
  };

  const handleSendMessage = async (textToSend) => {
    const queryText = (textToSend || question).trim();
    const sendingConvId = activeConvId;
    if (!queryText || (sendingConvId != null && loadingConvIds.has(sendingConvId))) return;

    const optimisticUserMessage = { role: 'user', content: queryText };

    setQuestion('');
    setDraftForConv(sendingConvId, '');
    setError('');

    let convId = sendingConvId;
    let justCreated = false;

    try {
      // Conversations are created lazily, on the first message sent while
      // none is selected -- never on a plain page visit, so "New Chat"
      // never litters the account with empty conversations. Fix round 2,
      // item 12: created (and added to the sidebar) the moment the
      // question is sent, not after the answer comes back -- so clicking
      // "New Chat" (or any other chat) while this one is still loading
      // doesn't hide the question; it's already saved under this chat's
      // own id, independent of whatever conversation happens to be open.
      if (convId == null) {
        const created = await api.conversations.create();
        convId = created.id;
        justCreated = true;
        setActiveConvId(convId);
        setConversations((prev) => [{ ...created, title: autoTitleFromQuestion(queryText) }, ...prev]);
      }

      setMessagesByConv((prev) => ({
        ...prev,
        [convId]: [...(prev[convId] || []), optimisticUserMessage],
      }));
      setLoadingConvIds((prev) => new Set(prev).add(convId));
      stillWorkingTimers.current[convId] = setTimeout(() => {
        setStatusByConv((prev) => (prev[convId] ? prev : { ...prev, [convId]: 'working' }));
      }, 10000);

      const res = await sendChatWithRetry(queryText, convId);

      const assistantMessage = {
        role: 'assistant',
        content: res.answer || res.message || 'No response returned.',
        sources: res.sources || []
      };
      setMessagesByConv((prev) => ({
        ...prev,
        [convId]: [...(prev[convId] || []), assistantMessage],
      }));

      refreshConversations();
    } catch (err) {
      const stillOnThisChat = activeConvIdRef.current === convId;
      if (stillOnThisChat) {
        setError(err.message || 'Failed to get response from AI. Please make sure the backend is active.');
      }
      // Roll back the optimistic message -- the server saved nothing
      // either, on any answer error -- and hand the question back as a
      // draft so it isn't lost.
      setMessagesByConv((prev) => ({
        ...prev,
        [convId]: (prev[convId] || []).filter((m) => m !== optimisticUserMessage),
      }));
      setDraftForConv(sendingConvId, queryText);
      if (stillOnThisChat) setQuestion(queryText);
      if (justCreated) {
        setConversations((prev) => prev.filter((c) => c.id !== convId));
        setMessagesByConv((prev) => {
          const next = { ...prev };
          delete next[convId];
          return next;
        });
        if (activeConvIdRef.current === convId) setActiveConvId(null);
        api.conversations.remove(convId).catch(() => {});
      }
    } finally {
      clearTimeout(stillWorkingTimers.current[convId]);
      delete stillWorkingTimers.current[convId];
      setLoadingConvIds((prev) => {
        const next = new Set(prev);
        next.delete(convId);
        return next;
      });
      setStatusByConv((prev) => {
        if (!(convId in prev)) return prev;
        const next = { ...prev };
        delete next[convId];
        return next;
      });
    }
  };

  // The chat response now includes each source's chunk text directly, so
  // opening a citation is just showing it — no extra lookup needed.
  const handleOpenCitation = (source) => {
    setSelectedCitation(source);
  };

  const renderMessageContent = (content) => <MarkdownRenderer content={content} />;

  // Personalized welcome-screen suggestions, built from the user's own
  // ready (searchable) documents -- up to 2 documents x 2 question
  // templates each, capped at 4 chips total. No hard-coded subject matter.
  const truncateBookName = (name) => (name.length > 40 ? `${name.slice(0, 39)}…` : name);
  const readyBooks = books.filter((b) => b.status === 'ready');
  const pendingBooks = books.filter((b) => b.status === 'processing' || b.status === 'needs_review');
  const suggestions = readyBooks.slice(0, 2).flatMap((book) => {
    const name = truncateBookName(book.name);
    return [
      `Summarize the key points of ${name}`,
      `What are the main topics in ${name}?`
    ];
  }).slice(0, 4);

  const showWelcome = messages.length === 0;

  return (
    <div className="flex h-[calc(100vh-80px)] md:h-[calc(100vh-80px)] rounded-[32px] overflow-hidden border border-slate-200 dark:border-slate-800/80 bg-white/40 dark:bg-slate-900/30 backdrop-blur-md relative z-10">

      {/* Chats Sidebar */}
      <div className="hidden md:flex flex-col w-64 bg-white/60 dark:bg-slate-900/40 border-r border-slate-200 dark:border-slate-800/50 p-4">
        <button
          onClick={handleNewChat}
          className="flex items-center justify-center gap-2 w-full py-2.5 rounded-xl border border-dashed border-violet-500/30 dark:border-violet-500/20 hover:border-violet-500 text-violet-600 dark:text-violet-400 hover:bg-violet-500/5 font-semibold text-xs tracking-tight transition-all"
        >
          <Plus size={14} />
          New Chat
        </button>

        {conversationsError && (
          <p className="text-[9px] font-semibold text-rose-500 mt-3">{conversationsError}</p>
        )}

        <div className="flex-1 overflow-y-auto mt-4 space-y-1.5 no-scrollbar">
          {conversationsLoading ? (
            <div className="flex justify-center py-6">
              <Loader2 size={16} className="animate-spin text-slate-400" />
            </div>
          ) : (
            conversations.map((conv) => (
              <div key={conv.id} className="group relative">
                {renamingId === conv.id ? (
                  <input
                    autoFocus
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') commitRename(conv.id);
                      if (e.key === 'Escape') setRenamingId(null);
                    }}
                    onBlur={() => commitRename(conv.id)}
                    className="w-full px-3 py-3 rounded-xl text-[11px] font-semibold bg-white dark:bg-slate-800 border border-violet-500 outline-none text-slate-800 dark:text-white"
                  />
                ) : (
                  <button
                    onClick={() => openConversation(conv.id)}
                    className={`flex items-center justify-between w-full p-3 rounded-xl text-left text-[11px] font-semibold tracking-tight transition-all duration-200
                      ${activeConvId === conv.id
                        ? 'bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-white border-l-2 border-violet-600'
                        : 'text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-800/20'}`}
                  >
                    <div className="flex items-center gap-2 truncate min-w-0">
                      <MessageSquare size={14} className="shrink-0 text-slate-400" />
                      <span className="truncate">{conv.title}</span>
                    </div>
                    <span className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 shrink-0">
                      <span
                        onClick={(e) => startRename(e, conv)}
                        className="p-1 rounded-md text-slate-400 hover:text-violet-500 hover:bg-violet-500/10 transition-all duration-150"
                      >
                        <Pencil size={11} />
                      </span>
                      <span
                        onClick={(e) => handleDeleteChat(e, conv.id)}
                        className="p-1 rounded-md text-slate-400 hover:text-rose-500 hover:bg-rose-500/10 transition-all duration-150"
                      >
                        <Trash2 size={12} />
                      </span>
                    </span>
                  </button>
                )}
              </div>
            ))
          )}
        </div>
      </div>

      {/* Main Conversation Window */}
      <div className="flex-1 flex flex-col h-full bg-transparent overflow-hidden">
        {/* Active conversation title header */}
        <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-800/50 flex justify-between items-center bg-white/40 dark:bg-slate-900/20 backdrop-blur-md">
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-violet-500" />
            <h2 className="text-xs font-bold text-slate-800 dark:text-white truncate max-w-sm">
              {activeTitle}
            </h2>
          </div>
          <button
            onClick={handleNewChat}
            className="md:hidden p-1.5 rounded-lg border border-slate-200 dark:border-slate-800 text-slate-600 dark:text-slate-300"
          >
            <Plus size={16} />
          </button>
        </div>

        {/* Chat Bubbles */}
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
          {loadingMessages ? (
            <div className="flex justify-center items-center h-full">
              <Loader2 size={24} className="animate-spin text-violet-400" />
            </div>
          ) : showWelcome ? (
            <div className="flex flex-col items-center justify-center h-full text-center max-w-lg mx-auto py-10 space-y-6">
              <div className="w-12 h-12 rounded-2xl bg-gradient-to-tr from-violet-600 to-indigo-500 flex items-center justify-center text-white shadow-lg shadow-violet-500/10">
                <Sparkles size={22} className="animate-pulse" />
              </div>
              <div>
                <h3 className="text-base font-bold text-slate-800 dark:text-white">RAG-Powered AI Study Partner</h3>
                <p className="text-[10px] text-slate-500 dark:text-slate-400 mt-2 leading-relaxed">
                  Ask questions about your uploaded notes and documents. Answers come only from your documents, with sources cited.
                </p>
              </div>

              {readyBooks.length === 0 ? (
                <div className="text-[10px] text-slate-500 dark:text-slate-400 leading-relaxed">
                  Upload a document on the Documents page to start asking questions.{' '}
                  <Link to="/documents" className="font-bold text-violet-600 dark:text-violet-400 hover:underline">
                    Go to Documents
                  </Link>
                </div>
              ) : (
                /* Suggestion Chips */
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full pt-4">
                  {suggestions.map((sug, idx) => (
                    <button
                      key={idx}
                      onClick={() => handleSendMessage(sug)}
                      className="p-3 text-[10px] font-semibold text-slate-600 dark:text-slate-300 text-left rounded-xl border border-slate-200/60 dark:border-slate-800/60 bg-white/50 dark:bg-slate-900/20 hover:border-violet-500/50 hover:bg-violet-500/5 dark:hover:bg-violet-500/10 transition-all hover:scale-[1.01]"
                    >
                      {sug}
                    </button>
                  ))}
                </div>
              )}

              {pendingBooks.length > 0 && (
                <p className="text-[9px] font-semibold text-amber-600 dark:text-amber-400">
                  Some documents are still processing or need review.
                </p>
              )}
            </div>
          ) : messages.map((msg, index) => (
            <div
              key={msg.id ?? index}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'} animate-fade-in`}
            >
              <div className={`max-w-[85%] rounded-[24px] px-5 py-4 text-xs leading-relaxed border shadow-sm
                ${msg.role === 'user'
                  ? 'bg-gradient-to-r from-violet-600 to-indigo-500 border-violet-500 text-white shadow-violet-500/5'
                  : 'bg-white dark:bg-slate-800 border-slate-200 dark:border-slate-800 text-slate-700 dark:text-slate-200'}`}
              >
                {/* Message Text */}
                <div className="space-y-2">
                  {msg.content === PENDING_DOCUMENTS_MESSAGE
                    ? <MessageWithDocsLink message={msg.content} />
                    : renderMessageContent(msg.content)}
                </div>

                {/* Citations list */}
                {msg.sources && msg.sources.length > 0 && (
                  <div className="mt-4 pt-3 border-t border-slate-100 dark:border-slate-700/50">
                    <span className="text-[9px] font-semibold text-slate-400 dark:text-slate-500 block mb-2">
                      Cited Context Sources:
                    </span>
                    <div className="flex flex-wrap gap-2">
                      {msg.sources.map((src, srcIdx) => (
                        <button
                          key={srcIdx}
                          onClick={() => handleOpenCitation(src)}
                          className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-slate-50 hover:bg-violet-500/10 border border-slate-200 dark:border-slate-700 dark:bg-slate-900/60 dark:hover:bg-violet-500/20 text-[9px] font-bold text-slate-600 dark:text-slate-300 hover:text-violet-600 dark:hover:text-violet-400 hover:border-violet-500/30 transition-all"
                        >
                          <BookOpen size={10} className="text-slate-400" />
                          <span className="truncate max-w-[120px]">{src.book_name}</span>
                          <span className="text-slate-400 font-normal">p. {src.page_number}</span>
                          {src.similarity_score != null && (
                            <span className="text-green-500 text-[8px] ml-1 bg-green-500/10 px-1 rounded">
                              {Math.round(src.similarity_score * 100)}%
                            </span>
                          )}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}

          {/* Typing Indicator -- Fix round 2, item 13: after ~10s of
              silence this adds "Still working…", or "The AI is busy,
              retrying…" instead if a rate limit is what's actually slowing
              this one down, rather than a plain silent wait either way. */}
          {isTyping && (
            <div className="flex justify-start">
              <div className="rounded-[24px] px-5 py-4 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-800 text-slate-400 flex items-center gap-2 shadow-sm">
                <span className="flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></span>
                  <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></span>
                  <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></span>
                </span>
                {statusByConv[activeConvId] === 'busy' && (
                  <span className="text-[10px] font-semibold text-amber-500">The AI is busy, retrying…</span>
                )}
                {statusByConv[activeConvId] === 'working' && (
                  <span className="text-[10px] font-semibold text-slate-400">Still working…</span>
                )}
              </div>
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 p-3 text-[10px] bg-rose-500/10 border border-rose-500/20 text-rose-500 rounded-xl">
              <AlertCircle size={13} className="shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input Bar */}
        <div className="p-4 border-t border-slate-200 dark:border-slate-800/50 bg-white/40 dark:bg-slate-900/20 backdrop-blur-md">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSendMessage();
            }}
            className="flex items-center gap-3 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl px-4 py-2 shadow-sm focus-within:ring-2 focus-within:ring-violet-500/15 focus-within:border-violet-500 transition-all"
          >
            <input
              type="text"
              value={question}
              onChange={(e) => {
                setQuestion(e.target.value);
                setDraftForConv(activeConvId, e.target.value);
              }}
              placeholder={isTyping ? 'Generating answer...' : 'Ask assistant about operating systems...'}
              disabled={isTyping}
              className="flex-1 bg-transparent border-none outline-none py-2 text-xs text-slate-800 dark:text-slate-100 placeholder-slate-400"
            />
            <button
              type="submit"
              disabled={!question.trim() || isTyping}
              className="p-2.5 rounded-xl bg-gradient-to-tr from-violet-600 to-indigo-500 text-white hover:scale-105 active:scale-[0.98] transition-all disabled:opacity-50 disabled:pointer-events-none shadow-sm shadow-violet-500/10"
            >
              <Send size={14} />
            </button>
          </form>
        </div>
      </div>

      {/* Side Citations Drawer */}
      {selectedCitation && (
        <div className="absolute inset-y-0 right-0 w-80 bg-white dark:bg-slate-900 border-l border-slate-200 dark:border-slate-800 z-30 shadow-2xl flex flex-col animate-slide-in">
          <div className="p-4 border-b border-slate-200 dark:border-slate-800 flex justify-between items-center">
            <div className="flex items-center gap-2">
              <BookOpen size={16} className="text-violet-500" />
              <span className="text-xs font-bold text-slate-800 dark:text-white">Citation Detail</span>
            </div>
            <button
              onClick={() => setSelectedCitation(null)}
              className="text-xs font-semibold text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 px-2 py-1 rounded hover:bg-slate-100 dark:hover:bg-slate-800"
            >
              Close
            </button>
          </div>

          <div className="p-5 flex-1 overflow-y-auto space-y-4 text-xs no-scrollbar">
            <div className="p-3.5 bg-slate-50 dark:bg-slate-800/40 rounded-xl space-y-2 border border-slate-100 dark:border-slate-800">
              <div className="flex justify-between text-[10px] text-slate-400 dark:text-slate-500 font-semibold">
                <span>FILE NAME</span>
                <span className="text-violet-500">p. {selectedCitation.page_number}</span>
              </div>
              <h4 className="font-bold text-slate-700 dark:text-slate-200 break-words">{selectedCitation.book_name}</h4>
              <div className="flex justify-between items-center text-[9px] pt-1 border-t border-slate-200/50 dark:border-slate-700/50">
                <span className="text-slate-400">Match score</span>
                <span className="text-green-500 font-bold">{Math.round(selectedCitation.similarity_score * 100)}% Match</span>
              </div>
            </div>

            <div className="space-y-2">
              <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider block">Cited Context Text Segment</span>

              <div className="p-4 rounded-xl bg-slate-50/50 dark:bg-slate-950/30 text-[11px] leading-relaxed text-slate-600 dark:text-slate-300 whitespace-pre-wrap select-all border border-slate-200/30 dark:border-slate-800/50">
                {selectedCitation?.content
                  ? cleanSnippetText(selectedCitation.content)
                  : "Exact context block could not be fetched, but the chunk reference is valid."}
              </div>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}

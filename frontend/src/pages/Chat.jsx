import { useState, useEffect, useRef } from 'react';
import { api, userScopedKey, PENDING_DOCUMENTS_MESSAGE } from '../services/api';
import { recordQuery } from '../hooks/useQueryHistory';
import { useDraftPersistence } from '../hooks/useDraftPersistence';
import MessageWithDocsLink from '../components/MessageWithDocsLink';
import {
  Send,
  Plus,
  MessageSquare,
  BookOpen,
  Trash2,
  Sparkles
} from 'lucide-react';

export default function Chat() {
  const [conversations, setConversations] = useState(() => {
    const saved = localStorage.getItem(userScopedKey('chat_conversations'));
    return saved ? JSON.parse(saved) : [
      { id: '1', title: 'DBMS Fundamentals Chat', messages: [
        { role: 'user', content: 'What is database normalization?' },
        { role: 'assistant', content: 'Database normalization is the process of structuring a relational database in accordance with a series of normal forms (e.g., 1NF, 2NF, 3NF, BCNF) to reduce data redundancy and improve data integrity. It involves organizing tables to ensure dependencies are properly enforced.', sources: [] }
      ]}
    ];
  });
  const [activeConvId, setActiveConvId] = useState(() => {
    return conversations[0]?.id || '';
  });
  const [question, setQuestion] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [error, setError] = useState('');

  // Citations side panel
  const [selectedCitation, setSelectedCitation] = useState(null);

  // Don't lose an unsent question to a session-expiry (or manual) logout --
  // saved right before the token is cleared, restored once on the next visit.
  useDraftPersistence('chat_draft_input', question, setQuestion);

  const messagesEndRef = useRef(null);

  // Monotonic id source for new chats, seeded above any id already loaded
  // from localStorage so a fresh chat never collides with a saved one.
  const nextChatIdRef = useRef(
    1 + conversations.reduce((max, c) => {
      const n = Number(c.id);
      return Number.isFinite(n) && n > max ? n : max;
    }, 0)
  );
  const generateChatId = () => {
    const id = String(nextChatIdRef.current);
    nextChatIdRef.current += 1;
    return id;
  };

  // Save conversations to localStorage
  useEffect(() => {
    localStorage.setItem(userScopedKey('chat_conversations'), JSON.stringify(conversations));
  }, [conversations]);

  const activeConv = conversations.find(c => c.id === activeConvId);

  // Scroll to bottom
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [activeConv?.messages, isTyping]);

  const handleNewChat = () => {
    const newId = generateChatId();
    const newChat = {
      id: newId,
      title: `New Chat Session`,
      messages: [
        { 
          role: 'assistant', 
          content: 'Hello! I am your AI Assistant. Ask me anything about your uploaded study materials, and I will reference the exact pages.' 
        }
      ]
    };
    setConversations([newChat, ...conversations]);
    setActiveConvId(newId);
  };

  const handleDeleteChat = (e, id) => {
    e.stopPropagation();
    const updated = conversations.filter(c => c.id !== id);
    setConversations(updated);
    if (activeConvId === id) {
      if (updated.length > 0) {
        setActiveConvId(updated[0].id);
      } else {
        const newId = generateChatId();
        const newChat = {
          id: newId,
          title: `New Chat Session`,
          messages: [{ role: 'assistant', content: 'Hello! Ask me anything about your documents.' }]
        };
        setConversations([newChat]);
        setActiveConvId(newId);
      }
    }
  };

  const handleSendMessage = async (textToSend) => {
    const queryText = textToSend || question;
    if (!queryText.trim() || isTyping) return;

    // Add user message
    const updatedMessages = [
      ...activeConv.messages,
      { role: 'user', content: queryText }
    ];

    // Update active conversation locally
    const currentConvIndex = conversations.findIndex(c => c.id === activeConvId);
    const updatedConversations = [...conversations];
    
    // Auto-rename chat title if it's the first query
    let title = activeConv.title;
    if (activeConv.title === 'New Chat Session' && updatedMessages.length <= 3) {
      title = queryText.length > 28 ? queryText.substring(0, 25) + '...' : queryText;
    }

    updatedConversations[currentConvIndex] = {
      ...activeConv,
      title,
      messages: updatedMessages
    };
    
    setConversations(updatedConversations);
    setQuestion('');
    setIsTyping(true);
    setError('');

    try {
      // API call to backend /chat?question=...
      const res = await api.chat.send(queryText);

      // Record real usage for Analytics (total + per-day breakdown)
      recordQuery();

      // Add assistant response
      const finalMessages = [
        ...updatedMessages,
        { 
          role: 'assistant', 
          content: res.answer || res.message || 'No response returned.', 
          sources: res.sources || [] 
        }
      ];

      const refreshedConversations = [...conversations];
      refreshedConversations[currentConvIndex] = {
        ...activeConv,
        title,
        messages: finalMessages
      };
      setConversations(refreshedConversations);
    } catch (err) {
      setError(err.message || 'Failed to get response from AI. Please make sure the backend is active.');
      // Add error placeholder message
      const finalMessages = [
        ...updatedMessages,
        { 
          role: 'assistant', 
          content: 'Sorry, I encountered an issue connecting to the RAG pipeline. Please verify that you have uploaded documents and the backend is running.', 
          isError: true 
        }
      ];
      const refreshedConversations = [...conversations];
      refreshedConversations[currentConvIndex] = {
        ...activeConv,
        title,
        messages: finalMessages
      };
      setConversations(refreshedConversations);
    } finally {
      setIsTyping(false);
    }
  };

  // The chat response now includes each source's chunk text directly, so
  // opening a citation is just showing it — no extra lookup needed.
  const handleOpenCitation = (source) => {
    setSelectedCitation(source);
  };

  // Simple, high-fidelity markdown parser
  const renderMessageContent = (content) => {
    // 1. Split code blocks (which should remain preformatted blocks)
    const blocks = content.split(/(```[\s\S]*?```)/g);
    
    return blocks.map((block, blockIdx) => {
      if (block.startsWith('```')) {
        // Code Block
        const match = block.match(/```(\w*)\n?([\s\S]*?)```/);
        const lang = match ? match[1] : '';
        const code = match ? match[2].trim() : block.replace(/```/g, '').trim();
        return (
          <div key={blockIdx} className="my-3 rounded-xl border border-slate-200 dark:border-slate-800 overflow-hidden">
            {lang && (
              <div className="bg-slate-100 dark:bg-slate-900 px-4 py-1.5 text-[9px] font-mono text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-800 flex justify-between items-center">
                <span>{lang.toUpperCase()}</span>
              </div>
            )}
            <pre className="p-4 bg-slate-950 text-slate-200 overflow-x-auto text-[11px] font-mono leading-relaxed">
              <code>{code}</code>
            </pre>
          </div>
        );
      }

      // 2. For non-code blocks, split into lines and parse block elements (headings, list items, paragraphs, horizontal rules)
      const lines = block.split('\n');
      const elements = [];
      let currentList = null; // { type: 'ul' | 'ol', items: [] }

      const flushList = (key) => {
        if (currentList) {
          const ListTag = currentList.type;
          const listClass = currentList.type === 'ul' ? 'list-disc ml-5 my-2 space-y-1.5' : 'list-decimal ml-5 my-2 space-y-1.5';
          elements.push(
            <ListTag key={key} className={listClass}>
              {currentList.items.map((item, idx) => (
                <li key={idx} className="text-slate-700 dark:text-slate-200">
                  {parseInline(item)}
                </li>
              ))}
            </ListTag>
          );
          currentList = null;
        }
      };

      // Inline formatter for bold, italic, inline code
      const parseInline = (text) => {
        if (!text) return '';
        // Parse bold (**bold**) and inline code (`code`) and italic (*italic*)
        const tokens = text.split(/(\*\*.*?\*\*|`.*?`|\*.*?\*)/g);
        return tokens.map((token, tIdx) => {
          if (token.startsWith('**') && token.endsWith('**')) {
            return (
              <strong key={tIdx} className="font-bold text-slate-900 dark:text-white">
                {token.slice(2, -2)}
              </strong>
            );
          }
          if (token.startsWith('`') && token.endsWith('`')) {
            return (
              <code key={tIdx} className="px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-900/60 text-slate-800 dark:text-slate-200 font-mono text-[10px] border border-slate-200/30 dark:border-slate-800/60">
                {token.slice(1, -1)}
              </code>
            );
          }
          if (token.startsWith('*') && token.endsWith('*')) {
            return (
              <em key={tIdx} className="italic text-slate-500 dark:text-slate-400">
                {token.slice(1, -1)}
              </em>
            );
          }
          return token;
        });
      };

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const trimmed = line.trim();

        // Horizontal Rule
        if (trimmed === '---') {
          flushList(`list-flush-${blockIdx}-${i}`);
          elements.push(
            <hr key={`hr-${blockIdx}-${i}`} className="my-4 border-slate-200 dark:border-slate-800/80" />
          );
          continue;
        }

        // Headings
        if (trimmed.startsWith('#')) {
          flushList(`list-flush-${blockIdx}-${i}`);
          const match = trimmed.match(/^(#{1,6})\s+(.*)$/);
          if (match) {
            const level = match[1].length;
            const headingText = match[2];
            if (level === 1) {
              elements.push(<h1 key={`h1-${blockIdx}-${i}`} className="text-base font-extrabold text-slate-900 dark:text-white mt-4 mb-2">{parseInline(headingText)}</h1>);
            } else if (level === 2) {
              elements.push(<h2 key={`h2-${blockIdx}-${i}`} className="text-sm font-bold text-slate-900 dark:text-white mt-3.5 mb-2">{parseInline(headingText)}</h2>);
            } else if (level === 3) {
              elements.push(<h3 key={`h3-${blockIdx}-${i}`} className="text-xs font-bold text-slate-900 dark:text-white mt-3 mb-1.5">{parseInline(headingText)}</h3>);
            } else {
              elements.push(<h4 key={`h4-${blockIdx}-${i}`} className="text-[11px] font-bold text-slate-800 dark:text-slate-200 mt-2.5 mb-1">{parseInline(headingText)}</h4>);
            }
            continue;
          }
        }

        // Bullet Lists (- or * or • or +)
        const bulletMatch = trimmed.match(/^[-*•+]\s+(.*)$/);
        if (bulletMatch) {
          if (!currentList || currentList.type !== 'ul') {
            flushList(`list-flush-${blockIdx}-${i}`);
            currentList = { type: 'ul', items: [] };
          }
          currentList.items.push(bulletMatch[1]);
          continue;
        }

        // Numbered Lists (1. or 2. etc.)
        const numberedMatch = trimmed.match(/^\d+[.)]\s+(.*)$/);
        if (numberedMatch) {
          if (!currentList || currentList.type !== 'ol') {
            flushList(`list-flush-${blockIdx}-${i}`);
            currentList = { type: 'ol', items: [] };
          }
          currentList.items.push(numberedMatch[1]);
          continue;
        }

        // Empty line
        if (trimmed === '') {
          flushList(`list-flush-${blockIdx}-${i}`);
          elements.push(<div key={`br-${blockIdx}-${i}`} className="h-2.5" />);
          continue;
        }

        // Regular Paragraph text
        flushList(`list-flush-${blockIdx}-${i}`);
        elements.push(
          <p key={`p-${blockIdx}-${i}`} className="my-1.5 text-slate-700 dark:text-slate-200 leading-relaxed text-xs">
            {parseInline(line)}
          </p>
        );
      }

      flushList(`list-flush-final-${blockIdx}`);
      return <div key={blockIdx} className="space-y-1">{elements}</div>;
    });
  };

  const suggestions = [
    "What is process scheduling?",
    "Explain Database Joins vs Unions",
    "List ACID properties of DBMS",
    "How does virtual memory work?"
  ];

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

        <div className="flex-1 overflow-y-auto mt-4 space-y-1.5 no-scrollbar">
          {conversations.map((conv) => (
            <button
              key={conv.id}
              onClick={() => {
                setActiveConvId(conv.id);
                setError('');
              }}
              className={`flex items-center justify-between w-full p-3 rounded-xl text-left text-[11px] font-semibold tracking-tight transition-all duration-200 group
                ${activeConvId === conv.id 
                  ? 'bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-white border-l-2 border-violet-600' 
                  : 'text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-800/20'}`}
            >
              <div className="flex items-center gap-2 truncate">
                <MessageSquare size={14} className="shrink-0 text-slate-400" />
                <span className="truncate">{conv.title}</span>
              </div>
              <span
                onClick={(e) => handleDeleteChat(e, conv.id)}
                className="opacity-0 group-hover:opacity-100 p-1 rounded-md text-slate-400 hover:text-rose-500 hover:bg-rose-500/10 transition-all duration-150 shrink-0"
              >
                <Trash2 size={12} />
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Main Conversation Window */}
      <div className="flex-1 flex flex-col h-full bg-transparent overflow-hidden">
        {/* Active conversation title header */}
        <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-800/50 flex justify-between items-center bg-white/40 dark:bg-slate-900/20 backdrop-blur-md">
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-violet-500" />
            <h2 className="text-xs font-bold text-slate-800 dark:text-white truncate max-w-sm">
              {activeConv?.title}
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
          {activeConv?.messages.length <= 1 && (
            <div className="flex flex-col items-center justify-center h-full text-center max-w-lg mx-auto py-10 space-y-6">
              <div className="w-12 h-12 rounded-2xl bg-gradient-to-tr from-violet-600 to-indigo-500 flex items-center justify-center text-white shadow-lg shadow-violet-500/10">
                <Sparkles size={22} className="animate-pulse" />
              </div>
              <div>
                <h3 className="text-base font-bold text-slate-800 dark:text-white">RAG-Powered AI Study Partner</h3>
                <p className="text-[10px] text-slate-500 dark:text-slate-400 mt-2 leading-relaxed">
                  Ask questions about your uploaded database, networks, or custom documents. The assistant will search matching document chunks semantically and cite its source automatically.
                </p>
              </div>

              {/* Suggestion Chips */}
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
            </div>
          )}

          {activeConv?.messages.map((msg, index) => (
            <div 
              key={index}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'} animate-fade-in`}
            >
              <div className={`max-w-[85%] rounded-[24px] px-5 py-4 text-xs leading-relaxed border shadow-sm
                ${msg.role === 'user' 
                  ? 'bg-gradient-to-r from-violet-600 to-indigo-500 border-violet-500 text-white shadow-violet-500/5' 
                  : msg.isError 
                    ? 'bg-rose-500/10 border-rose-500/20 text-rose-600 dark:text-rose-400' 
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
                          <span className="text-green-500 text-[8px] ml-1 bg-green-500/10 px-1 rounded">
                            {Math.round(src.similarity_score * 100)}%
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}

          {/* Typing Indicator */}
          {isTyping && (
            <div className="flex justify-start">
              <div className="rounded-[24px] px-5 py-4 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-800 text-slate-400 flex items-center gap-1.5 shadow-sm">
                <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></span>
                <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></span>
                <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></span>
              </div>
            </div>
          )}

          {error && (
            <div className="p-3 text-[10px] bg-rose-500/10 border border-rose-500/20 text-rose-500 rounded-xl text-center">
              {error}
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
              onChange={(e) => setQuestion(e.target.value)}
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
                {selectedCitation?.content || "Exact context block could not be fetched, but the chunk reference is valid."}
              </div>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}

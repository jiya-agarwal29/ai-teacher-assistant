import { api, userScopedKey } from '../services/api';

// The old (pre-Phase-6C) per-user localStorage key this one-time import
// reads from and, once imported, deletes -- see pages/Chat.jsx's
// `chat_conversations` shape: [{id, title, messages: [{role, content,
// sources?}]}] (an id/extra fields on each entry are just ignored).
const LEGACY_CHAT_CONVERSATIONS_KEY = 'chat_conversations';

function readLegacyConversations() {
  try {
    const raw = localStorage.getItem(userScopedKey(LEGACY_CHAT_CONVERSATIONS_KEY));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function clearLegacyKey() {
  try {
    localStorage.removeItem(userScopedKey(LEGACY_CHAT_CONVERSATIONS_KEY));
  } catch {
    // Best-effort cleanup -- a leftover key is harmless, it's just never read again.
  }
}

/**
 * Runs once per login (see hooks/AuthProvider.jsx): if this account hasn't
 * imported its old browser-only chat history yet AND this specific
 * browser still has some, uploads it to POST /conversations/import and
 * deletes the local copy afterward. Mirrors utils/analyticsImport.js
 * exactly -- see there for why a browser with no legacy data of its own
 * makes no API call at all (so it doesn't burn the account's one-time
 * import before a different browser that actually has real history gets
 * a chance to use it), and why a failure here is just logged and retried
 * on the next login rather than thrown. `onImported` fires once real
 * data was uploaded (200) or this browser's now-redundant copy was
 * cleared after discovering the account was already imported elsewhere
 * (409) -- never on any other error.
 */
export async function runOneTimeChatImport({ onImported } = {}) {
  try {
    const { imported } = await api.conversations.importStatus();
    if (imported) return;

    const rawConversations = readLegacyConversations();
    if (rawConversations.length === 0) return;

    const payload = rawConversations
      .map((c) => ({
        title: (c && typeof c.title === 'string' && c.title.trim()) || null,
        messages: (Array.isArray(c?.messages) ? c.messages : [])
          .filter((m) => (
            m && (m.role === 'user' || m.role === 'assistant')
            && typeof m.content === 'string' && m.content.trim()
          ))
          .map((m) => ({
            role: m.role,
            content: m.content,
            sources: Array.isArray(m.sources) && m.sources.length > 0 ? m.sources : null,
          })),
      }))
      .filter((c) => c.messages.length > 0);

    if (payload.length === 0) {
      // Nothing worth importing (e.g. only ever had empty/placeholder
      // chats) -- clean up the stale key without spending the one-time
      // import on an empty payload.
      clearLegacyKey();
      return;
    }

    await api.conversations.import(payload);
    clearLegacyKey();
    onImported?.();
  } catch (err) {
    if (err?.status === 409) {
      clearLegacyKey();
      onImported?.();
      return;
    }
    console.warn('One-time chat import failed; will retry on next login.', err);
  }
}

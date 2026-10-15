import { recordActivity } from '../utils/activity';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

/**
 * Exact text main.py returns from /chat, /tools/tutor, /semantic-search,
 * /tools/flashcards, and /generate-quiz when the user has uploaded
 * documents but none are "ready" yet (still processing, awaiting OCR
 * review, or failed) -- kept here, not duplicated per-page, so every
 * surface that checks for it (to add a link to Documents) stays in sync
 * with the backend's exact wording.
 */
export const PENDING_DOCUMENTS_MESSAGE = 'Your documents are still being processed or waiting for review.';

/**
 * Helper to get the JWT token from localStorage
 */
export const getToken = () => localStorage.getItem('token');

/**
 * Helper to save the JWT token to localStorage
 */
export const setToken = (token) => {
  if (token) {
    localStorage.setItem('token', token);
  } else {
    localStorage.removeItem('token');
  }
};

/**
 * Helper to get the current username from localStorage
 */
export const getUsername = () => localStorage.getItem('username');

/**
 * Helper to save the username to localStorage
 */
export const setUsername = (username) => {
  if (username) {
    localStorage.setItem('username', username);
  } else {
    localStorage.removeItem('username');
  }
};

/**
 * Prefixes a localStorage key with the logged-in username, so per-user data
 * (analytics, quiz/query history, saved chats) doesn't leak between accounts
 * sharing the same browser. Falls back to the bare key when logged out.
 */
export const userScopedKey = (key) => {
  const username = getUsername();
  return username ? `${username}::${key}` : key;
};

/**
 * Decodes a JWT's payload with no library: split on '.', base64url-decode
 * the middle segment, parse the JSON. Returns null for a missing/malformed
 * token rather than throwing. Exported so AuthProvider can read `exp` to
 * schedule the session-expiry warning/logout timers.
 */
export function decodeJwtPayload(token) {
  try {
    const base64Url = token.split('.')[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
    return JSON.parse(atob(padded));
  } catch {
    return null;
  }
}

/**
 * True when the token is missing, malformed, or past its `exp` claim.
 * Lets the app log out immediately on load instead of waiting for the
 * backend to reject the first request with a 401.
 */
export const isTokenExpired = (token) => {
  if (!token) return true;
  const payload = decodeJwtPayload(token);
  if (!payload || typeof payload.exp !== 'number') return false;
  return Date.now() >= payload.exp * 1000;
};

/**
 * Core request helper that wraps fetch and automatically handles:
 * - JWT Authorization header insertion
 * - Error propagation
 * - Session expiration (401)
 */
/**
 * A 4xx `detail` is usually a plain string, but FastAPI/Pydantic's own 422
 * validation errors (e.g. RegisterRequest's username/password rules) send
 * a list of {msg, ...} objects instead -- turned into one readable string
 * here so every caller (the register/login error banner included) always
 * gets back plain text, never "[object Object]".
 */
function formatErrorDetail(detail, fallback) {
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((item) => (item && typeof item.msg === 'string' ? item.msg.replace(/^Value error,\s*/, '') : null))
      .filter(Boolean)
      .join(' ') || fallback;
  }
  return fallback;
}

async function request(endpoint, options = {}) {
  // Any API call counts as user activity for AuthProvider's idle-aware
  // session handling (see utils/activity.js) -- a logged-in user making
  // real requests shouldn't get the "session ending" popup just because
  // their mouse happened to be still.
  recordActivity();

  const token = getToken();
  
  // Build headers
  const headers = {
    ...options.headers,
  };
  
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  
  const url = `${API_BASE_URL}${endpoint}`;
  
  let response;
  try {
    response = await fetch(url, { ...options, headers });
  } catch (netError) {
    // True network connection failure (e.g. server down)
    console.error(`Network connection failed on ${endpoint}:`, netError.message);
    throw new Error('Failed to connect to backend server. Please ensure the backend is running.', { cause: netError });
  }

  // Handle HTTP status errors (401, 400, etc.)
  if (response.status === 401) {
    // The backend now always sends one generic message for a failed login
    if (endpoint === '/login') {
      let detail = 'Invalid username or password';
      try {
        const data = await response.json();
        detail = formatErrorDetail(data.detail, detail);
      } catch {
        // Response body wasn't JSON — keep the default detail message above.
      }
      throw new Error(detail);
    }
    // Don't clear the token/username here -- just report the 401 and let
    // AuthProvider's 'auth-expired' listener (performLogout) do it, in the
    // right order. performLogout dispatches 'before-logout' *before*
    // clearing anything, so pages with unsaved work can still resolve
    // userScopedKey() (which reads username from localStorage) to the
    // right per-user slot. If we cleared the username here first, that
    // event would fire with no username left to scope the draft key to,
    // and the save would land in the wrong (unscoped) slot -- never found
    // again after the next login. Mirrors api.auth.logout(), which also
    // deliberately leaves the username alone.
    window.dispatchEvent(new Event('auth-expired'));
    throw new Error('Session expired. Please log in again.');
  }

  let data;
  try {
    data = await response.json();
  } catch (jsonErr) {
    if (!response.ok) {
      throw Object.assign(new Error(`Request failed with status ${response.status}`, { cause: jsonErr }), {
        status: response.status
      });
    }
    throw new Error('Failed to parse server response.', { cause: jsonErr });
  }

  if (!response.ok) {
    // `status` lets a caller branch on a specific code (e.g. 409 "already
    // done") without parsing the message text -- see analyticsImport.js.
    throw Object.assign(new Error(formatErrorDetail(data.detail, `Request failed with status ${response.status}`)), {
      status: response.status
    });
  }

  return data;
}

/**
 * API services mapped to backend endpoints
 */
export const api = {
  auth: {
    /**
     * POST /login
     * Note: Backend OAuth2PasswordRequestForm expects application/x-www-form-urlencoded
     */
    login: async (username, password) => {
      const params = new URLSearchParams();
      params.append('username', username);
      params.append('password', password);
      
      const data = await request('/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: params.toString(),
      });
      
      if (data.access_token) {
        setToken(data.access_token);
        setUsername(username);
      }
      return data;
    },
    
    /**
     * POST /register
     */
    register: async (username, password) => {
      return await request('/register', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ username, password }),
      });
    },
    
    logout: () => {
      // Only the token is cleared — username stays so per-user localStorage
      // keys (analytics, history, saved chats) remain intact for next login.
      setToken(null);
    },

    /**
     * POST /refresh-token -- exchanges a still-valid token for a new one
     * with a fresh expiry ("Stay logged in"). An already-expired or invalid
     * token gets the usual 401 (surfaced as a thrown Error), same as any
     * other authenticated endpoint.
     */
    refresh: async () => {
      const data = await request('/refresh-token', { method: 'POST' });
      if (data.access_token) {
        setToken(data.access_token);
      }
      return data;
    }
  },
  
  books: {
    /**
     * GET /books
     */
    getAll: () => request('/books'),
    
    /**
     * DELETE /books/{book_id}
     */
    delete: (bookId) => request(`/books/${bookId}`, {
      method: 'DELETE',
    }),

    /**
     * GET /books/{book_id}/status -- poll while a document is "processing".
     */
    status: (bookId) => request(`/books/${bookId}/status`),

    /**
     * POST /books/{book_id}/retry -- re-runs processing for a "failed" document.
     */
    retry: (bookId) => request(`/books/${bookId}/retry`, {
      method: 'POST',
    }),

    /**
     * POST /upload-book (with XHR for upload progress estimation).
     * `fileOrFiles` is a single File (a traditional document -- sent as
     * "file") or an array of Files (one or more page photos/scans -- sent
     * as "files", in array order, OCR'd as one document). `reviewOcr`
     * (default true) is the "Let me check the text before it's used"
     * choice -- only matters for pages that get OCR'd.
     * Returns immediately once the file(s) are saved (202 {book_id,
     * status}) -- the caller should refresh the book list and poll
     * status() until it reaches "ready"/"needs_review"/"failed".
     */
    upload: (fileOrFiles, onProgress, { reviewOcr = true } = {}) => {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', `${API_BASE_URL}/upload-book`);

        // Attach JWT token
        const token = getToken();
        if (token) {
          xhr.setRequestHeader('Authorization', `Bearer ${token}`);
        }

        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable && onProgress) {
            const percentComplete = Math.round((event.loaded / event.total) * 100);
            onProgress(percentComplete);
          }
        };

        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              const res = JSON.parse(xhr.responseText);
              resolve(res);
            } catch {
              resolve({ status: 'processing' });
            }
          } else {
            let errorMsg = 'Failed to upload document';
            try {
              const res = JSON.parse(xhr.responseText);
              errorMsg = res.detail || errorMsg;
            } catch {
              // Response body wasn't JSON — keep the default errorMsg above.
            }
            reject(new Error(errorMsg));
          }
        };

        xhr.onerror = () => {
          reject(new Error('Network error during file upload.'));
        };

        const formData = new FormData();
        if (Array.isArray(fileOrFiles)) {
          fileOrFiles.forEach((f) => formData.append('files', f));
        } else {
          formData.append('file', fileOrFiles);
        }
        formData.append('review_ocr', reviewOcr ? 'true' : 'false');
        xhr.send(formData);
      });
    },

    /**
     * GET /books/{book_id}/file -- fetched manually (not through request(),
     * which assumes a JSON body) since neither an <iframe>/<object> nor a
     * download link can carry the Authorization header on its own. Returns
     * {url, contentType, filename}; the caller must URL.revokeObjectURL(url)
     * once done with it (e.g. on unmount), same as pages.getImageUrl below.
     */
    getFileUrl: async (bookId) => {
      const token = getToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      let response;
      try {
        response = await fetch(`${API_BASE_URL}/books/${bookId}/file`, { headers });
      } catch (netError) {
        throw new Error('Failed to connect to backend server. Please ensure the backend is running.', { cause: netError });
      }
      if (!response.ok) {
        let detail = 'Failed to load the original file';
        try {
          detail = (await response.json()).detail || detail;
        } catch {
          // Response body wasn't JSON — keep the default detail above.
        }
        throw new Error(detail);
      }
      const contentType = response.headers.get('content-type') || 'application/octet-stream';
      const disposition = response.headers.get('content-disposition') || '';
      const filenameMatch = disposition.match(/filename="?([^"]+)"?/);
      const filename = filenameMatch ? filenameMatch[1] : 'document';
      const blob = await response.blob();
      return { url: URL.createObjectURL(blob), contentType, filename };
    },

    pages: {
      /**
       * GET /books/{book_id}/pages
       */
      list: (bookId) => request(`/books/${bookId}/pages`),

      /**
       * GET /books/{book_id}/pages/{page_number}/image -- fetched manually
       * (not through the request() helper, which assumes a JSON body) since
       * a plain <img src> can't carry the Authorization header. Returns an
       * object URL the caller must revoke (URL.revokeObjectURL) once done
       * with it, e.g. when switching pages or unmounting.
       */
      getImageUrl: async (bookId, pageNumber) => {
        const token = getToken();
        const headers = token ? { Authorization: `Bearer ${token}` } : {};
        let response;
        try {
          response = await fetch(`${API_BASE_URL}/books/${bookId}/pages/${pageNumber}/image`, { headers });
        } catch (netError) {
          throw new Error('Failed to connect to backend server. Please ensure the backend is running.', { cause: netError });
        }
        if (!response.ok) {
          let detail = 'Failed to load page image';
          try {
            detail = (await response.json()).detail || detail;
          } catch {
            // Response body wasn't JSON — keep the default detail above.
          }
          throw new Error(detail);
        }
        const blob = await response.blob();
        return URL.createObjectURL(blob);
      },

      /**
       * PUT /books/{book_id}/pages/{page_number} {extracted_text}
       */
      updateText: (bookId, pageNumber, extractedText) => request(`/books/${bookId}/pages/${pageNumber}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ extracted_text: extractedText }),
      }),

      /**
       * POST /books/{book_id}/pages/{page_number}/approve
       */
      approve: (bookId, pageNumber) => request(`/books/${bookId}/pages/${pageNumber}/approve`, {
        method: 'POST',
      }),

      /**
       * POST /books/{book_id}/approve-all
       */
      approveAll: (bookId) => request(`/books/${bookId}/approve-all`, {
        method: 'POST',
      }),

      /**
       * POST /books/{book_id}/pages/{page_number}/reread -- re-runs OCR for
       * just this page in the background; poll list() afterward to see the
       * updated text land.
       */
      reread: (bookId, pageNumber) => request(`/books/${bookId}/pages/${pageNumber}/reread`, {
        method: 'POST',
      }),

      /**
       * POST /books/{book_id}/pages/{page_number}/rotate {direction}
       * direction is "left" (counter-clockwise) or "right" (clockwise).
       * Rotates and overwrites the saved page image in place.
       */
      rotate: (bookId, pageNumber, direction) => request(`/books/${bookId}/pages/${pageNumber}/rotate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ direction }),
      }),
    }
  },

  chat: {
    /**
     * POST /chat {question, conversation_id?} -- conversationId is
     * optional; when given (and owned), the server saves this question
     * and the answer into that conversation itself (Phase 6C). POST,
     * not GET (Phase 6D), so the question never ends up sitting in a URL.
     */
    send: (question, conversationId) => request('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        conversation_id: conversationId ?? null,
      }),
    })
  },

  conversations: {
    /**
     * GET /conversations -- [{id, title, updated_at, message_count}, ...],
     * newest-first.
     */
    list: () => request('/conversations'),

    /**
     * POST /conversations {title?} -- returns the full (empty) detail
     * shape, same as get(id), so the caller can switch straight to it.
     */
    create: (title) => request('/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(title ? { title } : {}),
    }),

    /**
     * GET /conversations/{id} -- {id, title, created_at, updated_at,
     * messages: [{id, role, content, sources, created_at}]}.
     */
    get: (id) => request(`/conversations/${id}`),

    /**
     * PATCH /conversations/{id} {title} -- rename.
     */
    rename: (id, title) => request(`/conversations/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    }),

    /**
     * DELETE /conversations/{id}
     */
    remove: (id) => request(`/conversations/${id}`, {
      method: 'DELETE',
    }),

    /**
     * GET /conversations/import-status -- {imported: bool}.
     */
    importStatus: () => request('/conversations/import-status'),

    /**
     * POST /conversations/import [{title?, messages: [{role, content,
     * sources?}]}] -- one-time migration of this browser's pre-Phase-6C
     * localStorage chat history. 409s if this account already imported,
     * from any browser -- see utils/chatImport.js, the only caller.
     */
    import: (conversations) => request('/conversations/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(conversations),
    }),
  },

  search: {
    /**
     * GET /search?query=... (Normal exact text search)
     */
    normal: (query) => {
      const queryParams = new URLSearchParams({ query });
      return request(`/search?${queryParams.toString()}`);
    },
    
    /**
     * GET /semantic-search?query=... (Semantic embedding vector search)
     */
    semantic: (query) => {
      const queryParams = new URLSearchParams({ query });
      return request(`/semantic-search?${queryParams.toString()}`);
    }
  },
  
  quiz: {
    /**
     * GET /generate-quiz?topic=...&levels=...&count=...
     * `levels` (array of level keys, or an already-comma-joined string)
     * and `count` are both optional -- omitting them keeps the backend's
     * own defaults (all six levels, count=6), so existing callers that
     * only pass `topic` keep working unchanged.
     */
    generate: (topic, { levels, count } = {}) => {
      const queryParams = new URLSearchParams({ topic });
      if (levels) {
        queryParams.append('levels', Array.isArray(levels) ? levels.join(',') : levels);
      }
      if (count !== undefined && count !== null) {
        queryParams.append('count', String(count));
      }
      return request(`/generate-quiz?${queryParams.toString()}`);
    }
  },

  bloom: {
    /**
     * GET /bloom/levels -- the six Bloom's taxonomy levels (key, label,
     * order, description, question_types), the single source of truth for
     * the quiz builder's level picker and the BloomBadge component.
     */
    levels: () => request('/bloom/levels')
  },

  questions: {
    /**
     * GET /questions?level=...&topic=...&check=...&source=...&limit=...&offset=...
     * `level` may be a single key, an array of keys, or an already
     * comma-joined string (multi-select filtering on the Question Bank
     * page) -- the backend accepts a comma-separated list for this param.
     * `source` is "quiz" (never written for a paper), "paper" (written
     * for a paper slot, whether or not it's still in one), or "unused"
     * (not currently in any paper slot -- true of every quiz question,
     * and of a paper question swapped/regenerated out of its slot).
     */
    list: ({ level, topic, check, source, limit, offset } = {}) => {
      const params = new URLSearchParams();
      if (level && (!Array.isArray(level) || level.length > 0)) {
        params.append('level', Array.isArray(level) ? level.join(',') : level);
      }
      if (topic) params.append('topic', topic);
      if (check) params.append('check', check);
      if (source) params.append('source', source);
      if (limit !== undefined && limit !== null) params.append('limit', String(limit));
      if (offset !== undefined && offset !== null) params.append('offset', String(offset));
      return request(`/questions?${params.toString()}`);
    },

    /**
     * GET /questions/summary -- {total, by_level: {key: n}, flagged}.
     */
    summary: () => request('/questions/summary'),

    /**
     * PATCH /questions/{id} {question?, options?, correct_answer?, explanation?, bloom_level?}
     */
    update: (id, payload) => request(`/questions/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

    /**
     * POST /questions/{id}/regenerate {type?} -- writes one new question
     * for the same topic/level (optionally a different allowed type) and
     * replaces this row's content in place.
     */
    regenerate: (id, type) => request(`/questions/${id}/regenerate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(type ? { type } : {}),
    }),

    /**
     * DELETE /questions/{id}
     */
    delete: (id) => request(`/questions/${id}`, {
      method: 'DELETE',
    }),

    /**
     * DELETE /questions/unused -- removes every bank question not
     * currently filling a paper slot (not origin -- a quiz question
     * counts as unused too). Returns {deleted: n}.
     */
    deleteUnused: () => request('/questions/unused', {
      method: 'DELETE',
    }),
  },

  papers: {
    /**
     * GET /paper-patterns -- preset patterns + Bloom presets for the
     * "New paper" builder.
     */
    patterns: () => request('/paper-patterns'),

    /**
     * GET /papers -- {id, title, status, total_marks, created_at} per paper.
     */
    list: () => request('/papers'),

    /**
     * GET /papers/{id} -- full detail: settings, plan, slots (each with
     * its question, if filled), blueprint target/planned/actual, warnings.
     */
    get: (id) => request(`/papers/${id}`),

    /**
     * GET /papers/{id}/status -- lightweight poll target while generating:
     * {id, status, error, waiting_for_ai, progress_done, progress_total}.
     */
    status: (id) => request(`/papers/${id}/status`),

    /**
     * POST /papers {title, subject?, pattern_key|pattern, bloom_preset|
     * bloom_targets, book_ids, duration_minutes?, unit_weighting?,
     * unit_weights?} -- plans the paper (no AI call yet); returns the same
     * shape as get(id), status "draft".
     */
    create: (body) => request('/papers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

    /**
     * PATCH /papers/{id} {title?, subject?, duration_minutes?}
     */
    update: (id, body) => request(`/papers/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

    /**
     * DELETE /papers/{id} -- keeps the underlying bank questions.
     */
    remove: (id) => request(`/papers/${id}`, {
      method: 'DELETE',
    }),

    /**
     * POST /papers/{id}/generate -- 202, runs in the background; only
     * fills slots still empty (safe to call again after a "failed" or a
     * partially-filled paper). Rate-limited 3/minute.
     */
    generate: (id) => request(`/papers/${id}/generate`, {
      method: 'POST',
    }),

    /**
     * PATCH /papers/{id}/slots/{slotId} {question_id} -- swaps in a bank
     * question (same marks, an allowed level for the slot).
     */
    swapSlot: (id, slotId, questionId) => request(`/papers/${id}/slots/${slotId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question_id: questionId }),
    }),

    /**
     * POST /papers/{id}/slots/{slotId}/regenerate -- writes a new question
     * for just this slot. Rate-limited 10/minute.
     */
    regenerateSlot: (id, slotId) => request(`/papers/${id}/slots/${slotId}/regenerate`, {
      method: 'POST',
    }),

    /**
     * GET /papers/{id}/slots/{slotId}/candidates -- up to 20 bank
     * questions that fit this slot (same book first).
     */
    candidates: (id, slotId) => request(`/papers/${id}/slots/${slotId}/candidates`),

    /**
     * POST /papers/import-pattern (multipart) -- reads an old/existing
     * question paper's layout (a document, or a single photo/scan) with
     * Gemini: {pattern, questions, bloom_mix}. Never saved as a document
     * -- just returns the detected pattern for the "New paper" builder's
     * custom pattern editor to start from. `file` is a single File.
     */
    importPattern: (file) => {
      const formData = new FormData();
      formData.append('file', file);
      return request('/papers/import-pattern', {
        method: 'POST',
        body: formData,
      });
    },

    /**
     * GET /papers/{id}/export?kind=student|teacher|answer_key -- fetched
     * manually (not through the request() helper, which assumes a JSON
     * body), same as books.pages.getImageUrl: a plain <a href> can't carry
     * the Authorization header, so this fetches the .docx as a blob itself
     * and triggers the browser's save-file flow via a throwaway <a download>
     * element. The saved filename comes from the response's
     * Content-Disposition header.
     */
    download: async (id, kind) => {
      const token = getToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      let response;
      try {
        response = await fetch(`${API_BASE_URL}/papers/${id}/export?kind=${kind}`, { headers });
      } catch (netError) {
        throw new Error('Failed to connect to backend server. Please ensure the backend is running.', { cause: netError });
      }
      if (!response.ok) {
        let detail = 'Failed to export this paper.';
        try {
          detail = (await response.json()).detail || detail;
        } catch {
          // Response body wasn't JSON -- keep the default detail above.
        }
        throw new Error(detail);
      }

      const blob = await response.blob();
      const disposition = response.headers.get('Content-Disposition') || '';
      const match = disposition.match(/filename="([^"]+)"/);
      const filename = match ? match[1] : `paper-${kind}.docx`;

      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    },
  },

  analytics: {
    /**
     * GET /analytics/summary?days=7|30|90&tz=<IANA> -- totals, per-day
     * query/active-time buckets, accuracy by Bloom's level, topics/weak
     * topics, recent quiz attempts, and paper_coverage. `tz` defaults to
     * the browser's own timezone so day buckets line up with the user's
     * real calendar days, not UTC's.
     */
    summary: (days = 30, tz = Intl.DateTimeFormat().resolvedOptions().timeZone) => {
      const params = new URLSearchParams({ days: String(days), tz });
      return request(`/analytics/summary?${params.toString()}`);
    },

    /**
     * POST /analytics/active-time {seconds} -- a heartbeat for real
     * "actively using the app" time (1-120s per call); rate-limited
     * 4/minute server-side.
     */
    activeTime: (seconds) => request('/analytics/active-time', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seconds }),
    }),

    /**
     * POST /analytics/quiz-attempts {topic, answers: [{question_id?,
     * bloom_level?, question_type, is_correct, self_graded}]} -- one
     * entry per question; the server computes correct/total itself.
     */
    quizAttempt: (body) => request('/analytics/quiz-attempts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

    /**
     * POST /analytics/import?tz=<IANA> {queries_by_day, active_seconds_by_day,
     * quiz_history} -- one-time migration of this browser's pre-Phase-6A
     * localStorage history. 409s if this account already imported, from
     * any browser -- see utils/analyticsImport.js, the only caller.
     */
    import: (body, tz = Intl.DateTimeFormat().resolvedOptions().timeZone) => {
      const params = new URLSearchParams({ tz });
      return request(`/analytics/import?${params.toString()}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    },

    /**
     * GET /analytics/import-status -- {imported: bool}.
     */
    importStatus: () => request('/analytics/import-status'),

    /**
     * DELETE /analytics -- wipes this user's activity events and quiz
     * attempts; documents, bank questions, and papers are untouched.
     */
    remove: () => request('/analytics', { method: 'DELETE' }),
  },

  tools: {
    /**
     * POST /tools/summarize {text} — summarizes pasted text only, no retrieval.
     */
    summarize: (text) => request('/tools/summarize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }),

    /**
     * POST /tools/flashcards {topic} — builds cards from the user's own documents.
     */
    flashcards: (topic) => request('/tools/flashcards', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic }),
    }),

    /**
     * POST /tools/tutor {question} — same grounded pipeline as /chat, plain question.
     */
    tutor: (question) => request('/tools/tutor', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    }),
  }
};

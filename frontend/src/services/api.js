const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

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
 * token rather than throwing.
 */
function decodeJwtPayload(token) {
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
async function request(endpoint, options = {}) {
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
        detail = data.detail || detail;
      } catch {
        // Response body wasn't JSON — keep the default detail message above.
      }
      throw new Error(detail);
    }
    // Clear auth on unauthorized and dispatch event or handle redirect
    setToken(null);
    setUsername(null);
    window.dispatchEvent(new Event('auth-expired'));
    throw new Error('Session expired. Please log in again.');
  }

  let data;
  try {
    data = await response.json();
  } catch (jsonErr) {
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`, { cause: jsonErr });
    }
    throw new Error('Failed to parse server response.', { cause: jsonErr });
  }
  
  if (!response.ok) {
    throw new Error(data.detail || `Request failed with status ${response.status}`);
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
    }
  },

  chat: {
    /**
     * GET /chat?question=...
     */
    send: (question) => {
      const queryParams = new URLSearchParams({ question });
      return request(`/chat?${queryParams.toString()}`);
    }
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
     * GET /generate-quiz?topic=...
     */
    generate: (topic) => {
      const queryParams = new URLSearchParams({ topic });
      return request(`/generate-quiz?${queryParams.toString()}`);
    }
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

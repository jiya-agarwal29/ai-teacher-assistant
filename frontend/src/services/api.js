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
    throw new Error('Failed to connect to backend server. Please ensure the backend is running.');
  }
  
  // Handle HTTP status errors (401, 400, etc.)
  if (response.status === 401) {
    // The backend now always sends one generic message for a failed login
    if (endpoint === '/login') {
      let detail = 'Invalid username or password';
      try {
        const data = await response.json();
        detail = data.detail || detail;
      } catch (e) {}
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
      throw new Error(`Request failed with status ${response.status}`);
    }
    throw new Error('Failed to parse server response.');
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
     * POST /upload-book (with XHR for upload progress estimation)
     */
    upload: (file, onProgress) => {
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
            } catch (e) {
              resolve({ status: 'Book uploaded and chunked successfully' });
            }
          } else {
            let errorMsg = 'Failed to upload document';
            try {
              const res = JSON.parse(xhr.responseText);
              errorMsg = res.detail || errorMsg;
            } catch (e) {}
            reject(new Error(errorMsg));
          }
        };
        
        xhr.onerror = () => {
          reject(new Error('Network error during file upload.'));
        };
        
        const formData = new FormData();
        formData.append('file', file);
        xhr.send(formData);
      });
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
  }
};

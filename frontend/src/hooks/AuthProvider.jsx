import { useState, useEffect, useRef, useCallback } from 'react';
import { api, getToken, getUsername, setToken as persistToken, isTokenExpired, decodeJwtPayload } from '../services/api';
import { AuthContext } from './authContext';

const DEFAULT_SESSION_WARNING_MINUTES = 5;
const SESSION_WARNING_MINUTES = Number(import.meta.env.VITE_SESSION_WARNING_MINUTES) || DEFAULT_SESSION_WARNING_MINUTES;

export function AuthProvider({ children }) {
  const [user, setUser] = useState(getUsername());
  const [token, setToken] = useState(() => {
    const stored = getToken();
    return stored && isTokenExpired(stored) ? null : stored;
  });
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);

  // Session-expiry warning popup state -- see components/SessionExpiryModal.
  const [showSessionWarning, setShowSessionWarning] = useState(false);
  const [sessionExpiresAt, setSessionExpiresAt] = useState(null);

  const warningTimerRef = useRef(null);
  const logoutTimerRef = useRef(null);

  // Synced state check
  const isAuthenticated = !!token;

  useEffect(() => {
    // `token` state above already initializes to null when the stored token
    // is expired, so the app is correctly "logged out" from the first
    // render — this just removes the stale value from storage instead of
    // leaving it there until the backend would otherwise reject it with a 401.
    const stored = getToken();
    if (stored && isTokenExpired(stored)) {
      persistToken(null);
    }
  }, []);

  const clearSessionTimers = useCallback(() => {
    if (warningTimerRef.current) {
      clearTimeout(warningTimerRef.current);
      warningTimerRef.current = null;
    }
    if (logoutTimerRef.current) {
      clearTimeout(logoutTimerRef.current);
      logoutTimerRef.current = null;
    }
  }, []);

  // The single path every logout (manual, session-expiry timer, or a real
  // 401 from the API client) goes through. Dispatched *before* anything is
  // cleared, so a page with unsaved work (Chat's input, the OCR review
  // page's text) can still resolve the right per-user localStorage key via
  // userScopedKey() and persist a draft -- see hooks/useDraftPersistence.js.
  const performLogout = useCallback((message) => {
    window.dispatchEvent(new Event('before-logout'));
    clearSessionTimers();
    setShowSessionWarning(false);
    setSessionExpiresAt(null);
    api.auth.logout();
    setUser(null);
    setToken(null);
    setError(message ?? null);
  }, [clearSessionTimers]);

  useEffect(() => {
    // A real 401 from any API call (api.js's request() dispatches this) --
    // distinct from the proactive timer below, which fires with no request
    // in flight at all.
    const handleAuthExpired = () => {
      performLogout('Your session has expired. Please log in again.');
    };

    window.addEventListener('auth-expired', handleAuthExpired);
    return () => window.removeEventListener('auth-expired', handleAuthExpired);
  }, [performLogout]);

  // Schedules the warning popup and the hard logout against the current
  // token's own `exp` claim. Re-runs (and reschedules both timers) every
  // time `token` changes: initial login, a page load picking up a stored
  // token, or a successful "Stay logged in" refresh.
  useEffect(() => {
    clearSessionTimers();
    // Resetting here is deliberate: this effect's whole job is to
    // re-derive the warning/expiry state from whatever `token` just became,
    // including clearing a stale warning the instant the token changes
    // (e.g. a successful refresh) rather than leaving it up a render longer.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setShowSessionWarning(false);
    setSessionExpiresAt(null);

    if (!token) return;

    const payload = decodeJwtPayload(token);
    if (!payload || typeof payload.exp !== 'number') return;

    const expiresAtMs = payload.exp * 1000;
    const now = Date.now();
    const logoutDelay = expiresAtMs - now;

    if (logoutDelay <= 0) {
      performLogout('Your session has expired. Please log in again.');
      return;
    }

    setSessionExpiresAt(expiresAtMs);
    logoutTimerRef.current = setTimeout(() => {
      performLogout('Your session has expired. Please log in again.');
    }, logoutDelay);

    const warningDelay = expiresAtMs - SESSION_WARNING_MINUTES * 60 * 1000 - now;
    if (warningDelay <= 0) {
      setShowSessionWarning(true);
    } else {
      warningTimerRef.current = setTimeout(() => setShowSessionWarning(true), warningDelay);
    }

    return clearSessionTimers;
  }, [token, clearSessionTimers, performLogout]);

  const login = async (username, password) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await api.auth.login(username, password);
      setToken(data.access_token);
      setUser(username);
      return true;
    } catch (err) {
      setError(err.message || 'Login failed. Please check your credentials.');
      return false;
    } finally {
      setIsLoading(false);
    }
  };

  const register = async (username, password) => {
    setIsLoading(true);
    setError(null);
    try {
      await api.auth.register(username, password);
      // Automatically log in after registration
      return await login(username, password);
    } catch (err) {
      setError(err.message || 'Registration failed. Username may already exist.');
      return false;
    } finally {
      setIsLoading(false);
    }
  };

  const logout = () => performLogout(null);

  // "Stay logged in" (SessionExpiryModal). The token-change effect above
  // automatically reschedules both timers off the new token's expiry.
  // Returns false, without logging anyone out, on failure -- the modal
  // shows "Couldn't extend your session" and keeps its Log out button.
  const refreshSession = async () => {
    try {
      const data = await api.auth.refresh();
      setToken(data.access_token);
      setShowSessionWarning(false);
      setError(null);
      return true;
    } catch {
      return false;
    }
  };

  const clearError = () => setError(null);

  const value = {
    user,
    token,
    isAuthenticated,
    isLoading,
    error,
    login,
    register,
    logout,
    clearError,
    showSessionWarning,
    sessionExpiresAt,
    refreshSession,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

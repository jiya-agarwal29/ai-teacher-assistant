import { useState, useEffect, useRef, useCallback } from 'react';
import { api, getToken, getUsername, setToken as persistToken, isTokenExpired, decodeJwtPayload } from '../services/api';
import { startActivityTracking, getLastActivityAt, subscribeToActivity } from '../utils/activity';
import { runOneTimeAnalyticsImport } from '../utils/analyticsImport';
import { runOneTimeChatImport } from '../utils/chatImport';
import { AuthContext } from './authContext';

let nextImportToastId = 0;

const DEFAULT_SESSION_WARNING_MINUTES = 5;
const SESSION_WARNING_MINUTES = Number(import.meta.env.VITE_SESSION_WARNING_MINUTES) || DEFAULT_SESSION_WARNING_MINUTES;

// "Active" for the purposes of the silent background refresh and of
// suppressing the idle popup -- mouse/keyboard/touch or any API call
// within this window counts.
const ACTIVE_WINDOW_MS = 10 * 60 * 1000;
// How often the idle-aware check below re-evaluates while a token is live.
const SESSION_CHECK_INTERVAL_MS = 15 * 1000;
// At most one silent background refresh this often, even if the user
// stays continuously active.
const MIN_SILENT_REFRESH_INTERVAL_MS = 5 * 60 * 1000;

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

  // One-time "your old data was moved" toasts -- one entry per import
  // that actually fired (activity, chats, ...) -- see utils/
  // analyticsImport.js, utils/chatImport.js, components/ImportedDataToast.
  const [importToasts, setImportToasts] = useState([]);
  const pushImportToast = useCallback((message) => {
    const id = nextImportToastId++;
    setImportToasts((prev) => [...prev, { id, message }]);
  }, []);
  const dismissImportToast = useCallback((id) => {
    setImportToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const sessionCheckIntervalRef = useRef(null);
  const logoutTimerRef = useRef(null);
  const lastSilentRefreshAtRef = useRef(0);
  const popupRefreshInFlightRef = useRef(false);

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
    // Mouse/keyboard/touch activity, module-level so it's only wired up
    // once no matter how many times AuthProvider itself re-renders.
    startActivityTracking();
  }, []);

  const clearSessionTimers = useCallback(() => {
    if (sessionCheckIntervalRef.current) {
      clearInterval(sessionCheckIntervalRef.current);
      sessionCheckIntervalRef.current = null;
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

  // "Stay logged in" (SessionExpiryModal), and also the silent background
  // refresh below. The token-change effect further down automatically
  // reschedules everything off the new token's expiry. Returns false,
  // without logging anyone out, on failure -- the modal shows "Couldn't
  // extend your session" and keeps its Log out button; the silent path
  // just leaves the existing token/timers alone and tries again later.
  const refreshSession = useCallback(async () => {
    try {
      const data = await api.auth.refresh();
      setToken(data.access_token);
      setShowSessionWarning(false);
      setError(null);
      return true;
    } catch {
      return false;
    }
  }, []);

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

  // Schedules the hard logout against the current token's own `exp` claim
  // (an absolute security deadline, unaffected by activity), and starts
  // the idle-aware periodic check below. Re-runs (reschedules everything)
  // every time `token` changes: initial login, a page load picking up a
  // stored token, or a successful refresh (silent or "Stay logged in").
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

    // Re-evaluated every tick against whatever's true *right now* --
    // while the user is active, silently extend the session (at most once
    // per MIN_SILENT_REFRESH_INTERVAL_MS, and never once the token itself
    // has expired); only once they've gone idle does the warning popup
    // actually appear, instead of firing unconditionally at a fixed delay.
    sessionCheckIntervalRef.current = setInterval(() => {
      const nowTick = Date.now();
      const msLeft = expiresAtMs - nowTick;
      if (msLeft <= 0) return; // the hard logout timer above handles this

      const isActive = nowTick - getLastActivityAt() <= ACTIVE_WINDOW_MS;
      const refreshThreshold = (SESSION_WARNING_MINUTES + 1) * 60 * 1000;

      if (isActive) {
        const sinceLastRefresh = nowTick - lastSilentRefreshAtRef.current;
        if (msLeft <= refreshThreshold && sinceLastRefresh >= MIN_SILENT_REFRESH_INTERVAL_MS) {
          lastSilentRefreshAtRef.current = nowTick;
          refreshSession(); // fire-and-forget; the token-change effect reschedules everything on success
        }
        return;
      }

      const warningThreshold = SESSION_WARNING_MINUTES * 60 * 1000;
      if (msLeft <= warningThreshold) {
        setShowSessionWarning(true);
      }
    }, SESSION_CHECK_INTERVAL_MS);

    return clearSessionTimers;
  }, [token, clearSessionTimers, performLogout, refreshSession]);

  // While the popup is showing, any activity at all (mouse/keyboard/touch,
  // or an API call from elsewhere in the app) counts as "Stay logged in".
  // Guarded against overlapping calls -- a burst of mousemove events
  // firing before the first refresh resolves must not fire /refresh-token
  // more than once.
  useEffect(() => {
    if (!showSessionWarning) {
      popupRefreshInFlightRef.current = false;
      return;
    }
    return subscribeToActivity(() => {
      if (popupRefreshInFlightRef.current) return;
      popupRefreshInFlightRef.current = true;
      refreshSession().finally(() => {
        popupRefreshInFlightRef.current = false;
      });
    });
  }, [showSessionWarning, refreshSession]);

  const login = async (username, password) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await api.auth.login(username, password);
      setToken(data.access_token);
      setUser(username);
      // Fire-and-forget -- never blocks login; see utils/analyticsImport.js
      // for why a failure here is just retried on the next login instead.
      runOneTimeAnalyticsImport({
        onImported: () => pushImportToast('Your earlier activity was moved to your account.')
      });
      runOneTimeChatImport({
        onImported: () => pushImportToast('Your earlier chats were moved to your account.')
      });
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
    importToasts,
    dismissImportToast,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

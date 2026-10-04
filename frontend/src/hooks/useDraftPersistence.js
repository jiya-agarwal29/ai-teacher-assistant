import { useEffect, useRef } from 'react';
import { userScopedKey } from '../services/api';

/**
 * Keeps unsent/unsaved work from being silently lost across a logout
 * (session expiry or a manual "Log out") or a page refresh/close: saves
 * `value` to a per-user localStorage slot the instant AuthProvider's
 * `before-logout` event fires (dispatched before the token is cleared, so
 * the slot is still scoped to the right user) -- and the same way on the
 * browser's own `beforeunload`/`pagehide` (both are listened for since
 * neither alone fires reliably in every browser/navigation case). Restores
 * + clears it once, on mount.
 *
 * `value` should be `null`/empty when there's nothing worth persisting --
 * an empty draft clears any previously-saved one instead of writing it.
 * Pass `serialize`/`deserialize` for a non-string value (e.g. an object);
 * they default to the identity function for plain strings.
 */
export function useDraftPersistence(storageKey, value, onRestore, { serialize, deserialize } = {}) {
  const toStorage = serialize || ((v) => v);
  const fromStorage = deserialize || ((v) => v);

  const valueRef = useRef(value);
  useEffect(() => { valueRef.current = value; }, [value]);

  // Restore once, on mount -- then clear it, so it isn't restored again on
  // a later, unrelated visit.
  useEffect(() => {
    const key = userScopedKey(storageKey);
    const raw = localStorage.getItem(key);
    if (raw != null) {
      try {
        onRestore(fromStorage(raw));
      } catch {
        // Corrupted or old-shape draft -- nothing sensible to restore.
      }
      localStorage.removeItem(key);
    }
    // Deliberately mount-only: this hook's job is a one-time restore, not a
    // reactive sync with `onRestore`/`storageKey` identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const handler = () => {
      const key = userScopedKey(storageKey);
      const current = valueRef.current;
      const isEmpty = current == null || (typeof current === 'string' && !current.trim());
      if (isEmpty) {
        localStorage.removeItem(key);
      } else {
        localStorage.setItem(key, toStorage(current));
      }
    };
    // Deliberately doesn't call preventDefault/set returnValue on
    // beforeunload -- this listener's only job is the silent save; any
    // "are you sure you want to leave" prompt is a separate, page-specific
    // concern (e.g. DocumentReview's own beforeunload handler) and the two
    // coexist fine as independent listeners on the same event.
    window.addEventListener('before-logout', handler);
    window.addEventListener('beforeunload', handler);
    window.addEventListener('pagehide', handler);
    return () => {
      window.removeEventListener('before-logout', handler);
      window.removeEventListener('beforeunload', handler);
      window.removeEventListener('pagehide', handler);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);
}

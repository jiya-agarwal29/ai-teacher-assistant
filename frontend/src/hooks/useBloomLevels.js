import { useEffect, useState } from 'react';
import { api } from '../services/api';

// Module-level cache shared by every component using this hook, so
// GET /bloom/levels (key/label/order/description/question_types -- the
// six levels are the single source of truth in the backend's bloom.py) is
// fetched once per page load rather than once per component instance.
let cachedLevels = null;
let inFlightRequest = null;

function fetchLevels() {
  if (cachedLevels) return Promise.resolve(cachedLevels);
  if (!inFlightRequest) {
    inFlightRequest = api.bloom.levels()
      .then((data) => {
        cachedLevels = data;
        return data;
      })
      .finally(() => {
        inFlightRequest = null;
      });
  }
  return inFlightRequest;
}

/**
 * The six Bloom's taxonomy levels, fetched once and cached. Returns
 * `levels` (ordered remember -> create, as the backend returns them) and
 * `byKey` (the same list indexed by level key) so callers never need to
 * hard-code a level's label/description/question_types themselves.
 */
export default function useBloomLevels() {
  const [levels, setLevels] = useState(cachedLevels || []);
  const [loading, setLoading] = useState(!cachedLevels);
  const [error, setError] = useState('');

  useEffect(() => {
    if (cachedLevels) return; // already reflected in the lazy useState initializers above

    let cancelled = false;
    fetchLevels()
      .then((data) => {
        if (!cancelled) {
          setLevels(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || 'Could not load Bloom\'s taxonomy levels.');
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const byKey = Object.fromEntries(levels.map((level) => [level.key, level]));

  return { levels, byKey, loading, error };
}

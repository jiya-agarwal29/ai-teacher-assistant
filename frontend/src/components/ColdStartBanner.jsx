import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { subscribeColdStart } from '../utils/coldStart';

/**
 * Phase 7C item 8: Render's free plan sleeps the backend after a period of
 * no traffic, so the *first* request after that can take up to roughly a
 * minute instead of the usual sub-second response. Shown whenever any
 * in-flight request (see utils/coldStart.js, wired into every api.js call
 * site) has been running for more than 4s, and hidden again the moment it
 * settles -- mounted once at the top of App.jsx so it's visible on every
 * page, including Login (the most likely place to hit a cold start).
 */
export default function ColdStartBanner() {
  const [visible, setVisible] = useState(false);

  useEffect(() => subscribeColdStart(setVisible), []);

  if (!visible) return null;

  return (
    <div
      role="status"
      className="fixed top-0 inset-x-0 z-[200] flex items-center justify-center gap-2 bg-amber-500 text-amber-950 text-xs font-semibold px-4 py-2 shadow-md print:hidden"
    >
      <Loader2 size={14} className="animate-spin shrink-0" />
      <span>Waking up the server — the free plan sleeps when unused, this can take up to a minute.</span>
    </div>
  );
}

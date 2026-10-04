/**
 * A tiny, router-version-agnostic way for the current page to ask "is it
 * OK to navigate away from me right now?" before a Sidebar nav link click
 * takes effect. Exists because this app uses a plain <BrowserRouter>, not
 * a data router, and react-router's useBlocker only works with the latter
 * (it throws without a data router context) -- so that's not an option
 * here without a much larger routing change.
 *
 * A page registers a guard function on mount and clears it on unmount;
 * only one guard is active at a time, matching there only ever being one
 * page mounted under the Sidebar at once.
 */
let guard = null;

export function setNavigationGuard(fn) {
  guard = fn;
}

export function clearNavigationGuard() {
  guard = null;
}

/** True if navigating away right now is fine (no guard, or the guard allowed it). */
export function confirmNavigationAllowed() {
  return guard ? guard() : true;
}

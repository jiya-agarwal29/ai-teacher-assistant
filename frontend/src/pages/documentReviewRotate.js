// Lives in its own file (not inside DocumentReview.jsx) so that component
// file can export only the component itself (react-refresh lint rule).

/**
 * Rotate always rotates the image. Whether it should ALSO re-read the page
 * (which overwrites textDraft with a fresh OCR transcription) depends on
 * whether the teacher has already hand-edited this page: an untouched page
 * re-reads automatically, but an edited one asks first, and Cancel means
 * "rotate only" -- the edited text is never silently thrown away.
 * Exported so this one decision point (Fix round 2, item 0) can be unit
 * tested directly, without mounting the whole page.
 */
export function shouldRereadAfterRotate({ isDirty, alreadyEdited, confirmFn }) {
  const wasEdited = isDirty || alreadyEdited;
  if (!wasEdited) return true;
  return confirmFn('Re-read and replace your edits?');
}

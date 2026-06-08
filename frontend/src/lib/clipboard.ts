/**
 * Copy text to the clipboard, working in both secure and insecure contexts.
 *
 * `navigator.clipboard` is only available in a secure context (HTTPS or
 * localhost). The portal is commonly served over plain HTTP behind a
 * TLS-terminating reverse proxy, where `navigator.clipboard` is `undefined`, so
 * a legacy `document.execCommand("copy")` fallback (via an off-screen textarea)
 * is used when the async Clipboard API is unavailable or fails.
 *
 * @returns `true` when the text was copied, `false` when all strategies failed
 * (the caller should then prompt the user to select and copy manually).
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  // Preferred path: the async Clipboard API (secure contexts only).
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to the legacy path (e.g. permission denied).
    }
  }
  return legacyCopy(text);
}

/** Off-screen-textarea + execCommand fallback for insecure contexts. */
function legacyCopy(text: string): boolean {
  if (typeof document === "undefined" || !document.body) {
    return false;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  // Keep it out of view and out of the layout/scroll flow.
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "-9999px";
  textarea.style.left = "-9999px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  try {
    textarea.focus();
    textarea.select();
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    document.body.removeChild(textarea);
  }
}

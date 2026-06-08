/**
 * Client-side logo preparation.
 *
 * A user-chosen logo is downscaled in the browser to a small square and encoded
 * as a base64 `data:` URI so it can travel inside the normal JSON create/update
 * request (no multipart, no separate upload endpoint). Only raster output is
 * produced (WebP, falling back to PNG); the backend rejects anything else. The
 * encoded result is size-capped to keep request bodies and stored values small.
 */

/** Maximum edge length, in pixels, of the downscaled square logo. */
export const LOGO_MAX_PX = 64;

/**
 * Maximum size, in bytes, of the decoded image. Mirrors the backend
 * `MAX_ICON_DATA_BYTES` cap so the client fails fast before sending.
 */
export const LOGO_MAX_BYTES = 64 * 1024;

/** Image MIME types accepted for upload. */
export const ACCEPTED_LOGO_TYPES = ["image/png", "image/webp", "image/jpeg"];

/** Estimate the decoded byte length of a base64 `data:` URI without decoding it. */
function dataUrlByteLength(dataUrl: string): number {
  const comma = dataUrl.indexOf(",");
  const b64 = comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl;
  const padding = b64.endsWith("==") ? 2 : b64.endsWith("=") ? 1 : 0;
  return Math.floor((b64.length * 3) / 4) - padding;
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("That file is not a readable image."));
    img.src = src;
  });
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(new Error("Could not read the selected file."));
    reader.readAsDataURL(file);
  });
}

/**
 * Convert a chosen image file into a small square logo `data:` URI.
 *
 * The image is drawn onto a transparent {@link LOGO_MAX_PX}-square canvas,
 * preserving aspect ratio, and exported as WebP (PNG fallback). Throws if the
 * file is not an accepted image type or the encoded result exceeds
 * {@link LOGO_MAX_BYTES}.
 */
export async function fileToLogoDataUrl(file: File): Promise<string> {
  if (!ACCEPTED_LOGO_TYPES.includes(file.type)) {
    throw new Error("Logo must be a PNG, WebP, or JPEG image.");
  }

  const sourceUrl = await readFileAsDataUrl(file);
  const img = await loadImage(sourceUrl);

  const size = LOGO_MAX_PX;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    throw new Error("Image processing is not available in this browser.");
  }

  // Fit the image inside the square, preserving aspect ratio, centred.
  const scale = Math.min(size / img.width, size / img.height, 1);
  const drawW = Math.max(1, Math.round(img.width * scale));
  const drawH = Math.max(1, Math.round(img.height * scale));
  const dx = Math.round((size - drawW) / 2);
  const dy = Math.round((size - drawH) / 2);
  ctx.clearRect(0, 0, size, size);
  ctx.drawImage(img, dx, dy, drawW, drawH);

  let dataUrl = canvas.toDataURL("image/webp", 0.9);
  if (!dataUrl.startsWith("data:image/webp")) {
    // Browser does not support WebP encoding; fall back to PNG.
    dataUrl = canvas.toDataURL("image/png");
  }

  if (dataUrlByteLength(dataUrl) > LOGO_MAX_BYTES) {
    throw new Error(
      `Logo is too large after processing (limit ${Math.round(
        LOGO_MAX_BYTES / 1024,
      )} KB). Try a simpler image.`,
    );
  }
  return dataUrl;
}

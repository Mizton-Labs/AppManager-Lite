/// <reference types="vite/client" />

// Build-time constants injected via Vite `define` (see vite.config.ts).
/** Application version (from package.json), e.g. "0.1.0". */
declare const __APP_VERSION__: string;
/** Short git commit hash baked into the build, or "dev". */
declare const __APP_COMMIT__: string;
/** Development-team contributor names (Javier Santillan first). */
declare const __APP_CONTRIBUTORS__: string[];

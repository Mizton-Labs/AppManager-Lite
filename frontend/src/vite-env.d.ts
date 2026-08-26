/// <reference types="vite/client" />

// Build-time constants injected via Vite `define` (see vite.config.ts).
/** Application version (from package.json), e.g. "0.1.0". */
declare const __APP_VERSION__: string;
/** Short git commit hash baked into the build, or "dev". */
declare const __APP_COMMIT__: string;
/** Git branch baked into the build ("detached HEAD" or "unavailable" as fallbacks). */
declare const __APP_BRANCH__: string;
/** Development-team GitHub handles, derived from the git commit history. */
declare const __APP_CONTRIBUTORS__: { handle: string; url: string }[];

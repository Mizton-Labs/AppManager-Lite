/// <reference types="vitest/config" />
import { execSync } from "node:child_process";
import { createRequire } from "node:module";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const require = createRequire(import.meta.url);
const pkg = require("./package.json") as { version: string };

/** Run a git command at build time, returning "" when git is unavailable. */
function git(args: string): string {
  try {
    return execSync(`git ${args}`, { stdio: ["ignore", "pipe", "ignore"] })
      .toString()
      .trim();
  } catch {
    return "";
  }
}

/** Short commit hash baked into the build, or "dev" outside a git checkout. */
function commitHash(): string {
  return git("rev-parse --short HEAD") || "dev";
}

/** Names that are bots/automation, not human contributors. */
const BOT_AUTHORS = new Set(["cortex"]);

/**
 * Build the development-team list from git authors with at least one commit:
 * trim, drop bots, and de-duplicate (case-insensitively), preserving discovery
 * order. This is purely the repository's commit authors; an administrator can
 * additionally configure "Collaborators" at runtime (shown separately on the
 * About page).
 */
function contributors(): string[] {
  const raw = git("log --format=%an")
    .split("\n")
    .map((n) => n.trim())
    .filter(Boolean);

  const seen = new Set<string>();
  const names: string[] = [];
  for (const author of raw) {
    const key = author.toLowerCase();
    if (BOT_AUTHORS.has(key) || seen.has(key)) continue;
    seen.add(key);
    names.push(author);
  }
  return names;
}

// Relative base so the built assets resolve under any deployment path prefix.
// The backend injects a <base href> matching APP_BASE_PREFIX at serve time.
export default defineConfig({
  base: "./",
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
    __APP_COMMIT__: JSON.stringify(commitHash()),
    __APP_CONTRIBUTORS__: JSON.stringify(contributors()),
  },
  server: {
    port: 5173,
    proxy: {
      // During local development the API is served by the backend on :8000.
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});

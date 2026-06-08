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
 * Build the development-team list from the repository's commit authors.
 *
 * Identities are de-duplicated by author **email** (one entry per email), and
 * each is displayed with the name from that email's most recent commit. This
 * collapses an author who has committed under several different names (the same
 * person, same email) into a single, current name. Bots are dropped. Order
 * follows most-recent-commit order.
 *
 * This is purely the repository's commit authors; an administrator can
 * additionally configure "Collaborators" at runtime (shown separately on the
 * About page).
 */
function contributors(): string[] {
  // One record per commit, newest first (git log default order). Email and name
  // are separated by a unit-separator that cannot appear in either field.
  const SEP = "\x1f";
  const lines = git(`log --format=%ae${SEP}%an`)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const byEmail = new Map<string, string>();
  for (const line of lines) {
    const idx = line.indexOf(SEP);
    if (idx < 0) continue;
    const email = line.slice(0, idx).trim().toLowerCase();
    const name = line.slice(idx + 1).trim();
    if (!email || !name) continue;
    if (BOT_AUTHORS.has(name.toLowerCase())) continue;
    // First occurrence wins -> the name from this email's most recent commit.
    if (!byEmail.has(email)) {
      byEmail.set(email, name);
    }
  }
  return [...byEmail.values()];
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

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

/**
 * Git branch baked into the build, so the About page can show exactly which
 * checkout produced the running frontend (helpful when the same commit is
 * built from more than one branch, e.g. after a fast-forward merge).
 *
 *  - a valid HEAD on a named branch: the short branch name (e.g. "main").
 *  - a valid HEAD not on a branch (detached checkout, e.g. CI building a
 *    tag/commit directly): "detached HEAD".
 *  - no git checkout at all (HEAD cannot be resolved): "unavailable".
 */
function branchName(): string {
  if (!git("rev-parse --verify --quiet HEAD")) return "unavailable";
  return git("symbolic-ref --quiet --short HEAD") || "detached HEAD";
}

/** Names that are bots/automation, not human contributors. */
const BOT_AUTHORS = new Set(["cortex"]);

/**
 * Build the development-team list from the repository's commit authors.
 *
 * Git's mailmap-aware author identity is treated as the canonical GitHub
 * handle. Handles are validated and de-duplicated case-insensitively; bots and
 * unmapped/non-handle identities are omitted. Order follows most-recent commit
 * order, so this remains dynamic while `.mailmap` reconciles known aliases.
 *
 * This is purely the repository's commit authors; an administrator can
 * additionally configure "Collaborators" at runtime (shown separately on the
 * About page).
 */
function contributors(): { handle: string; url: string }[] {
  // %aN/%aE honor .mailmap. One record per commit, newest first.
  const SEP = "\x1f";
  const lines = git(`log --format=%aE${SEP}%aN`)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const handles = new Set<string>();
  const result: { handle: string; url: string }[] = [];
  for (const line of lines) {
    const idx = line.indexOf(SEP);
    if (idx < 0) continue;
    const name = line.slice(idx + 1).trim();
    const normalized = name.toLowerCase();
    if (!name || BOT_AUTHORS.has(normalized)) continue;
    // GitHub handles are 1-39 alphanumerics/hyphens, never ending in a hyphen.
    if (!/^(?=.{1,39}$)[a-z\d](?:[a-z\d-]*[a-z\d])?$/i.test(name)) continue;
    if (handles.has(normalized)) continue;
    handles.add(normalized);
    result.push({ handle: name, url: `https://github.com/${name}` });
  }
  return result;
}

// Relative base so the built assets resolve under any deployment path prefix.
// The backend injects a <base href> matching APP_BASE_PREFIX at serve time.
export default defineConfig({
  base: "./",
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
    __APP_COMMIT__: JSON.stringify(commitHash()),
    __APP_BRANCH__: JSON.stringify(branchName()),
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

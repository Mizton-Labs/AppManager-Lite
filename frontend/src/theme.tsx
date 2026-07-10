import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

export const THEME_STORAGE_KEY = "appmanager-lite.theme";
export const DEFAULT_THEME = "dark-modern" as const;

export const THEMES = [
  { id: "dark-modern", label: "Dark modern" },
  { id: "light", label: "Light" },
  { id: "energy", label: "Energy" },
  { id: "classic", label: "Classic" },
] as const;

export type ThemeId = (typeof THEMES)[number]["id"];

function isTheme(value: string | null | undefined): value is ThemeId {
  return THEMES.some((theme) => theme.id === value);
}

/** Whether the user has explicitly chosen a theme (persisted locally). */
export function hasStoredTheme(): boolean {
  try {
    return isTheme(window.localStorage.getItem(THEME_STORAGE_KEY));
  } catch {
    return false;
  }
}

// The admin-selected deployment default, delivered with the session. Applied
// only when the user has not made an explicit choice of their own.
let adminDefaultTheme: ThemeId = DEFAULT_THEME;

export function readTheme(): ThemeId {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (isTheme(stored)) return stored;
  } catch {
    return adminDefaultTheme;
  }
  return adminDefaultTheme;
}

export function applyTheme(theme: ThemeId): void {
  document.documentElement.dataset.theme = theme;
}

type ThemeContextValue = {
  theme: ThemeId;
  setTheme: (theme: ThemeId) => void;
  /** Whether the current theme comes from an explicit user choice. */
  isExplicit: boolean;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider(props: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(readTheme);
  const [isExplicit, setIsExplicit] = useState<boolean>(hasStoredTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // A user's explicit selection is applied immediately and persisted (which
  // marks it as an explicit choice that wins over the admin default).
  const setTheme = (next: ThemeId) => {
    setThemeState(next);
    setIsExplicit(true);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // A privacy-restricted browser can still use the in-memory selection.
    }
  };

  // The admin default (from the session) fills in only when the user has not
  // chosen their own theme; it is never persisted, so a later change to the
  // admin default can still take effect.
  const applyAdminDefault = (next: ThemeId) => {
    adminDefaultTheme = next;
    if (!hasStoredTheme()) {
      setThemeState(next);
      setIsExplicit(false);
    }
  };

  return (
    <ThemeContext.Provider value={{ theme, setTheme, isExplicit }}>
      <AdminDefaultBridge apply={applyAdminDefault} />
      {props.children}
    </ThemeContext.Provider>
  );
}

// Exposes the provider's admin-default applier to non-context callers (App) via
// a module ref, so the session loader can push the deployment default in.
let applyAdminDefaultRef: ((theme: ThemeId) => void) | null = null;

function AdminDefaultBridge(props: { apply: (theme: ThemeId) => void }) {
  applyAdminDefaultRef = props.apply;
  return null;
}

/** Apply the admin-selected default theme (no-op if the user has chosen). */
export function setAdminDefaultTheme(theme: string | undefined | null): void {
  if (!isTheme(theme)) return;
  if (applyAdminDefaultRef) {
    applyAdminDefaultRef(theme);
  } else {
    adminDefaultTheme = theme;
  }
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside ThemeProvider");
  return value;
}

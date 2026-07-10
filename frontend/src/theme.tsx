import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./api";

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

export function applyTheme(theme: ThemeId): void {
  document.documentElement.dataset.theme = theme;
}

/**
 * Resolve the theme to show for a session.
 *
 * issue_020: the theme is per-user and stored on the account. The signed-in
 * user's own choice wins; otherwise the deployment default (set by an admin)
 * applies. Before authentication there is no user, so the default is shown.
 */
export function resolveTheme(
  userTheme: string | null | undefined,
  adminDefault: string | null | undefined,
): ThemeId {
  if (isTheme(userTheme)) return userTheme;
  if (isTheme(adminDefault)) return adminDefault;
  return DEFAULT_THEME;
}

type ThemeContextValue = {
  theme: ThemeId;
  /** Change and persist the signed-in user's own theme. */
  setTheme: (theme: ThemeId) => void;
  /** Apply the theme resolved from a session (user choice or admin default). */
  applySessionTheme: (
    userTheme: string | null | undefined,
    adminDefault: string | null | undefined,
  ) => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

// The current deployment default, remembered so a user who clears their choice
// (or is signed out) falls back to it rather than the hard-coded default.
let currentAdminDefault: ThemeId = DEFAULT_THEME;

export function ThemeProvider(props: {
  children: ReactNode;
  /** When false, changing the theme is not persisted to the server (pre-auth). */
  persist?: boolean;
}) {
  const persist = props.persist ?? true;
  const [theme, setThemeState] = useState<ThemeId>(DEFAULT_THEME);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = (next: ThemeId) => {
    setThemeState(next);
    if (persist) {
      // Best-effort: the visible change applies immediately regardless.
      api.updateAccountTheme(next).catch(() => undefined);
    }
  };

  const applySessionTheme = (
    userTheme: string | null | undefined,
    adminDefault: string | null | undefined,
  ) => {
    if (isTheme(adminDefault)) currentAdminDefault = adminDefault;
    setThemeState(resolveTheme(userTheme, adminDefault ?? currentAdminDefault));
  };

  return (
    <ThemeContext.Provider value={{ theme, setTheme, applySessionTheme }}>
      {props.children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside ThemeProvider");
  return value;
}

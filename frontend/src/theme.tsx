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

function isTheme(value: string | null): value is ThemeId {
  return THEMES.some((theme) => theme.id === value);
}

export function readTheme(): ThemeId {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(stored) ? stored : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

export function applyTheme(theme: ThemeId): void {
  document.documentElement.dataset.theme = theme;
}

type ThemeContextValue = {
  theme: ThemeId;
  setTheme: (theme: ThemeId) => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider(props: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(readTheme);

  useEffect(() => {
    applyTheme(theme);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // A privacy-restricted browser can still use the in-memory selection.
    }
  }, [theme]);

  return (
    <ThemeContext.Provider value={{ theme, setTheme: setThemeState }}>
      {props.children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside ThemeProvider");
  return value;
}

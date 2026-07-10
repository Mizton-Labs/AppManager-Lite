import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemePicker } from "./components/ThemePicker";
import {
  applyTheme,
  DEFAULT_THEME,
  readTheme,
  setAdminDefaultTheme,
  THEME_STORAGE_KEY,
  ThemeProvider,
  useTheme,
} from "./theme";

function ThemeValue() {
  return <output>{useTheme().theme}</output>;
}

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  setAdminDefaultTheme(DEFAULT_THEME);
  vi.restoreAllMocks();
});

describe("theme preference", () => {
  it("defaults invalid or absent storage to Dark-modern", () => {
    expect(readTheme()).toBe(DEFAULT_THEME);
    localStorage.setItem(THEME_STORAGE_KEY, "not-a-theme");
    expect(readTheme()).toBe(DEFAULT_THEME);
  });

  it("applies every supported theme to the document root", () => {
    for (const theme of ["dark-modern", "light", "energy", "classic"] as const) {
      applyTheme(theme);
      expect(document.documentElement).toHaveAttribute("data-theme", theme);
    }
  });

  it("changes immediately and persists through a remount", async () => {
    const first = render(
      <ThemeProvider>
        <ThemePicker />
        <ThemeValue />
      </ThemeProvider>,
    );
    await userEvent.selectOptions(screen.getByLabelText("Theme"), "energy");
    expect(screen.getByRole("status")).toHaveTextContent("energy");
    expect(document.documentElement).toHaveAttribute("data-theme", "energy");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("energy");

    first.unmount();
    render(
      <ThemeProvider>
        <ThemeValue />
      </ThemeProvider>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("energy");
  });

  it("keeps Dark-modern when local storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readTheme()).toBe(DEFAULT_THEME);
  });

  it("applies the admin default when the user has not chosen (issue_019)", () => {
    render(
      <ThemeProvider>
        <ThemeValue />
      </ThemeProvider>,
    );
    // No stored choice -> starts at the built-in default.
    expect(screen.getByRole("status")).toHaveTextContent(DEFAULT_THEME);
    act(() => setAdminDefaultTheme("classic"));
    expect(screen.getByRole("status")).toHaveTextContent("classic");
    expect(document.documentElement).toHaveAttribute("data-theme", "classic");
    // The admin default is not persisted as the user's own choice.
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it("keeps an explicit user choice over the admin default", async () => {
    render(
      <ThemeProvider>
        <ThemePicker />
        <ThemeValue />
      </ThemeProvider>,
    );
    await userEvent.selectOptions(screen.getByLabelText("Theme"), "energy");
    expect(screen.getByRole("status")).toHaveTextContent("energy");
    // A later admin default must NOT override the explicit choice.
    act(() => setAdminDefaultTheme("classic"));
    expect(screen.getByRole("status")).toHaveTextContent("energy");
  });
});

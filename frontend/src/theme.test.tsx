import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemePicker } from "./components/ThemePicker";
import {
  applyTheme,
  DEFAULT_THEME,
  resolveTheme,
  ThemeProvider,
  useTheme,
} from "./theme";
import { api } from "./api";

function ThemeValue() {
  return <output>{useTheme().theme}</output>;
}

// Applies a session (user theme + admin default) from inside the provider.
function ApplySession(props: {
  userTheme?: string | null;
  adminDefault?: string | null;
}) {
  const { applySessionTheme } = useTheme();
  return (
    <button
      onClick={() => applySessionTheme(props.userTheme, props.adminDefault)}
    >
      apply
    </button>
  );
}

afterEach(() => {
  delete document.documentElement.dataset.theme;
  vi.restoreAllMocks();
});

describe("theme resolution (issue_020)", () => {
  it("prefers the user's own theme, then admin default, then dark-modern", () => {
    expect(resolveTheme("energy", "light")).toBe("energy");
    expect(resolveTheme("", "light")).toBe("light");
    expect(resolveTheme(null, null)).toBe(DEFAULT_THEME);
    expect(resolveTheme("not-a-theme", "classic")).toBe("classic");
  });

  it("applies every supported theme to the document root", () => {
    for (const theme of ["dark-modern", "light", "energy", "classic"] as const) {
      applyTheme(theme);
      expect(document.documentElement).toHaveAttribute("data-theme", theme);
    }
  });

  it("applies the user's theme from the session over the admin default", async () => {
    render(
      <ThemeProvider>
        <ApplySession userTheme="energy" adminDefault="light" />
        <ThemeValue />
      </ThemeProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "apply" }));
    expect(screen.getByRole("status")).toHaveTextContent("energy");
    expect(document.documentElement).toHaveAttribute("data-theme", "energy");
  });

  it("falls back to the admin default when the user has no choice", async () => {
    render(
      <ThemeProvider>
        <ApplySession userTheme="" adminDefault="classic" />
        <ThemeValue />
      </ThemeProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "apply" }));
    expect(screen.getByRole("status")).toHaveTextContent("classic");
  });

  it("persists a user's selection to their account", async () => {
    const spy = vi
      .spyOn(api, "updateAccountTheme")
      .mockResolvedValue({} as never);
    render(
      <ThemeProvider>
        <ThemePicker />
        <ThemeValue />
      </ThemeProvider>,
    );
    await userEvent.selectOptions(screen.getByLabelText("Theme"), "energy");
    expect(screen.getByRole("status")).toHaveTextContent("energy");
    expect(spy).toHaveBeenCalledWith("energy");
  });

  it("does not persist when the provider is non-persistent (pre-auth)", async () => {
    const spy = vi
      .spyOn(api, "updateAccountTheme")
      .mockResolvedValue({} as never);
    render(
      <ThemeProvider persist={false}>
        <ThemePicker />
      </ThemeProvider>,
    );
    await userEvent.selectOptions(screen.getByLabelText("Theme"), "light");
    expect(spy).not.toHaveBeenCalled();
  });
});

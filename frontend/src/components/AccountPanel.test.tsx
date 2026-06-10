import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AccountPanel } from "./AccountPanel";
import { makeUser } from "../test/fixtures";

function jsonResponse(payload: unknown): Response {
  return { ok: true, status: 200, json: async () => payload } as Response;
}

function textResponse(payload: string): Response {
  return {
    ok: true,
    status: 200,
    text: async () => payload,
    headers: new Headers({ "content-disposition": 'attachment; filename="profile.txt"' }),
  } as Response;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("AccountPanel", () => {
  it("lists and downloads account bundles", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:bundle"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    const fetchMock = vi.fn(async (input: string) => {
      const url = String(input);
      if (url.endsWith("/api/account/bundles")) {
        return jsonResponse([{ id: 7, name: "Shell profile" }]);
      }
      if (url.endsWith("/api/account/bundles/7/download")) {
        return textResponse("personal bundle");
      }
      return jsonResponse({ detail: "unexpected" });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AccountPanel
        user={makeUser({ username: "analyst@example.com" })}
        onPasswordChanged={() => undefined}
      />,
    );

    expect(await screen.findByText("Shell profile")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /download bundle/i }));

    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).endsWith("/api/account/bundles/7/download"),
      ),
    ).toBe(true);
    expect(click).toHaveBeenCalled();
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { copyToClipboard } from "./clipboard";

/** jsdom has no execCommand; install a controllable stub returning `result`. */
function stubExecCommand(result: boolean): ReturnType<typeof vi.fn> {
  const exec = vi.fn().mockReturnValue(result);
  (document as unknown as { execCommand: unknown }).execCommand = exec;
  return exec;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete (document as unknown as { execCommand?: unknown }).execCommand;
});

describe("copyToClipboard", () => {
  it("uses the async Clipboard API when available", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });

    const ok = await copyToClipboard("s3cret");
    expect(ok).toBe(true);
    expect(writeText).toHaveBeenCalledWith("s3cret");
  });

  it("falls back to execCommand when the Clipboard API is missing", async () => {
    vi.stubGlobal("navigator", {}); // no clipboard (insecure context)
    const exec = stubExecCommand(true);

    const ok = await copyToClipboard("s3cret");
    expect(ok).toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
  });

  it("falls back to execCommand when the Clipboard API throws", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const exec = stubExecCommand(true);

    const ok = await copyToClipboard("s3cret");
    expect(ok).toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
  });

  it("returns false when every strategy fails", async () => {
    vi.stubGlobal("navigator", {});
    stubExecCommand(false);

    const ok = await copyToClipboard("s3cret");
    expect(ok).toBe(false);
  });
});

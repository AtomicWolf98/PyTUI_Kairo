import { afterEach, describe, expect, it, vi } from "vitest";

describe("local auth token bootstrap", () => {
  afterEach(() => {
    vi.resetModules();
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  it("moves startup token from URL to sessionStorage", async () => {
    window.history.replaceState({}, "", "/?token=secret-token&tab=settings");

    const api = await import("./api");

    expect(api.token).toBe("secret-token");
    expect(window.sessionStorage.getItem("kairo.local.token")).toBe("secret-token");
    expect(window.location.search).toBe("?tab=settings");
  });

  it("reuses the session token after reload without URL query token", async () => {
    window.sessionStorage.setItem("kairo.local.token", "stored-token");

    const api = await import("./api");

    expect(api.token).toBe("stored-token");
    expect(window.location.search).toBe("");
  });

  it("preserves structured runtime error metadata", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: "Finish the active task first.",
      code: "runtime_busy",
      retryable: true
    }), { status: 409, headers: { "content-type": "application/json" } })));
    const api = await import("./api");

    await expect(api.getStatus()).rejects.toMatchObject({
      name: "ApiError",
      message: "Finish the active task first.",
      status: 409,
      code: "runtime_busy",
      retryable: true
    });
    vi.unstubAllGlobals();
  });
});

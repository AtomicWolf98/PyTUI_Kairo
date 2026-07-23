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
});

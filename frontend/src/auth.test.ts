import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, clearAuthToken, getCurrentUser, login, logout, register, setAccessToken, setAuthToken } from "./api";

afterEach(() => { clearAuthToken(); setAccessToken(""); vi.unstubAllGlobals(); });

describe("session lifecycle", () => {
  it("sends the project gate token for both sign-in and registration", async () => {
    const request = vi.fn().mockImplementation(() => Promise.resolve(new Response('{"token":"session","user":{}}')));
    vi.stubGlobal("fetch", request);
    setAccessToken("project-token");
    await login("user", "password");
    await register("user", "long test passphrase");
    for (const [, init] of request.mock.calls) expect(init.headers["x-agent-access-token"]).toBe("project-token");
  });

  it("revokes the server session before forgetting the browser token", async () => {
    setAuthToken("synthetic-token");
    const request = vi.fn().mockImplementation(() => Promise.resolve(new Response(null, { status: 204 })));
    vi.stubGlobal("fetch", request);
    await logout();
    expect(request.mock.calls[0][0]).toBe("/api/auth/logout");
    expect(request.mock.calls[0][1].headers.Authorization).toBe("Bearer synthetic-token");
    request.mockResolvedValue(new Response('{}'));
    await getCurrentUser();
    expect(request.mock.calls[1][1].headers.Authorization).toBeUndefined();
  });

  it("retains the credential when session revocation is unavailable", async () => {
    setAuthToken("synthetic-token");
    const request = vi.fn().mockResolvedValue(new Response('{"detail":"Database unavailable"}', { status: 503 }));
    vi.stubGlobal("fetch", request);
    await expect(logout()).rejects.toBeInstanceOf(ApiError);
    request.mockResolvedValue(new Response('{}'));
    await getCurrentUser();
    expect(request.mock.calls[1][1].headers.Authorization).toBe("Bearer synthetic-token");
  });

  it("expired sessions abort outstanding requests and notify the workspace", async () => {
    const dispatchEvent = vi.fn();
    vi.stubGlobal("window", { dispatchEvent });
    setAuthToken("expired-token");
    const request = vi.fn().mockResolvedValue(new Response('{"detail":"Authentication required"}', { status: 401 }));
    vi.stubGlobal("fetch", request);
    await expect(getCurrentUser()).rejects.toThrow("Session expired");
    expect(request.mock.calls[0][1].signal.aborted).toBe(true);
    expect(dispatchEvent.mock.calls[0][0].type).toBe("agent-session-expired");
  });

  it("service failures do not erase a recoverable session", async () => {
    setAuthToken("preserved-token");
    const request = vi.fn().mockResolvedValue(new Response('{"detail":"temporarily unavailable"}', { status: 503 }));
    vi.stubGlobal("fetch", request);
    await expect(getCurrentUser()).rejects.toMatchObject({ status: 503 });
    request.mockResolvedValue(new Response('{}'));
    await getCurrentUser();
    expect(request.mock.calls[1][1].headers.Authorization).toBe("Bearer preserved-token");
  });
});

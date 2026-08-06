import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest } from "./client";

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiRequest", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  it("uses same-origin browser credentials", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest("/api/v1/example");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/example",
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });

  it("returns undefined for an empty successful response", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      apiRequest<void>("/api/v1/auth/logout", { method: "POST" }),
    ).resolves.toBeUndefined();
  });

  it("maps the common error envelope to ApiError", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: "validation_error",
            message: "请修正表单错误",
            retryable: false,
            field_errors: { username: "用户名太短" },
          },
        },
        422,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const error = await apiRequest("/api/v1/auth/login").catch(
      (reason: unknown) => reason,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 422,
      code: "validation_error",
      message: "请修正表单错误",
      fieldErrors: { username: "用户名太短" },
    });
  });

  it.each(["POST", "PUT", "PATCH", "DELETE"])(
    "adds CSRF for %s requests",
    async (method) => {
      fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
      vi.stubGlobal("fetch", fetchMock);

      await apiRequest("/api/v1/protected", {
        method,
        csrfToken: "csrf-value",
      });

      const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
      expect(headers.get("X-CSRF-Token")).toBe("csrf-value");
    },
  );

  it.each(["GET", "HEAD"])("does not add CSRF for %s requests", async (method) => {
    fetchMock.mockResolvedValue(
      method === "HEAD" ? new Response(null) : jsonResponse({ ok: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest("/api/v1/protected", {
      method,
      csrfToken: "csrf-value",
      headers: { "X-CSRF-Token": "caller-supplied-value" },
    });

    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.has("X-CSRF-Token")).toBe(false);
  });

  it("notifies the provider when a request is unauthorized", async () => {
    const onUnauthorized = vi.fn();
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: "invalid_session",
            message: "会话已过期",
            retryable: false,
            field_errors: {},
          },
        },
        401,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      apiRequest("/api/v1/protected", { onUnauthorized }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });
});

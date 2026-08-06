export type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    retryable: boolean;
    field_errors: Record<string, string>;
  };
};

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly fieldErrors: Record<string, string> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export type ApiRequestOptions = RequestInit & {
  csrfToken?: string;
  onUnauthorized?: () => void;
};

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }

  const error = value.error;
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    typeof error.code === "string" &&
    "message" in error &&
    typeof error.message === "string" &&
    "field_errors" in error &&
    typeof error.field_errors === "object" &&
    error.field_errors !== null
  );
}

export async function apiRequest<T>(
  input: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const { csrfToken, onUnauthorized, ...requestInit } = options;
  const method = (requestInit.method ?? "GET").toUpperCase();
  const headers = new Headers(requestInit.headers);
  headers.delete("X-CSRF-Token");

  if (csrfToken && MUTATING_METHODS.has(method)) {
    headers.set("X-CSRF-Token", csrfToken);
  }

  const response = await fetch(input, {
    ...requestInit,
    method,
    credentials: "same-origin",
    headers,
  });
  const text = await response.text();
  let body: unknown;

  if (text) {
    try {
      body = JSON.parse(text) as unknown;
    } catch {
      body = undefined;
    }
  }

  if (!response.ok) {
    if (response.status === 401) {
      onUnauthorized?.();
    }

    if (isApiErrorBody(body)) {
      throw new ApiError(
        response.status,
        body.error.code,
        body.error.message,
        body.error.field_errors,
      );
    }

    throw new ApiError(
      response.status,
      "http_error",
      response.statusText || "请求失败，请稍后重试",
    );
  }

  return body as T;
}

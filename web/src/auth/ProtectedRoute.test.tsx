import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "./AuthProvider";
import { ProtectedRoute } from "./ProtectedRoute";

const fetchMock = vi.fn<typeof fetch>();
const SESSION_QUERY_KEY = ["auth", "session"] as const;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function SessionConsumer() {
  const auth = useAuth();
  return <p data-testid="login-state">{auth.status}</p>;
}

function ProtectedScreen() {
  const auth = useAuth();
  return (
    <main>
      <h1>私有文档</h1>
      <button
        type="button"
        onClick={() => void auth.request("/api/v1/documents").catch(() => undefined)}
      >
        加载文档
      </button>
    </main>
  );
}

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderProtected(
  initialEntry = "/documents?sort=recent",
  queryClient = createTestQueryClient(),
) {
  const router = createMemoryRouter(
    [
      {
        element: (
          <QueryClientProvider client={queryClient}>
            <AuthProvider>
              <Outlet />
            </AuthProvider>
          </QueryClientProvider>
        ),
        children: [
          {
            element: <ProtectedRoute />,
            children: [{ path: "/documents", element: <ProtectedScreen /> }],
          },
          { path: "/login", element: <SessionConsumer /> },
        ],
      },
    ],
    { initialEntries: [initialEntry] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe("ProtectedRoute", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  it("shows a visible recovery state while session restoration is pending", () => {
    fetchMock.mockReturnValue(new Promise(() => undefined));
    vi.stubGlobal("fetch", fetchMock);

    renderProtected();

    expect(screen.getByRole("status")).toHaveTextContent("正在恢复会话…");
    expect(screen.queryByRole("heading", { name: "私有文档" })).not.toBeInTheDocument();
  });

  it("replaces anonymous navigation with login and remembers the full route", async () => {
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
    const router = renderProtected();

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.historyAction).toBe("REPLACE");
    expect(router.state.location.state).toEqual({ from: "/documents?sort=recent" });
    expect(screen.getByTestId("login-state")).toHaveTextContent("anonymous");
  });

  it("renders protected content after successful restoration", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ username: "reader", csrf_token: "csrf-value" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderProtected();

    expect(await screen.findByRole("heading", { name: "私有文档" })).toBeVisible();
  });

  it("clears memory and cached session data after a current-session protected 401", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ username: "reader", csrf_token: "csrf-value" }),
      )
      .mockResolvedValueOnce(
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
    const queryClient = createTestQueryClient();
    const router = renderProtected("/documents?sort=recent", queryClient);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "加载文档" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.historyAction).toBe("REPLACE");
    expect(screen.getByTestId("login-state")).toHaveTextContent("anonymous");
    expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toBeUndefined();

    cleanup();
    fetchMock.mockResolvedValueOnce(
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
    renderProtected("/login", queryClient);

    await waitFor(() => {
      expect(screen.getByTestId("login-state")).toHaveTextContent("anonymous");
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  createMemoryRouter,
  Outlet,
  RouterProvider,
  useNavigate,
} from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AuthProvider,
  resolveAuthDestination,
  useAuth,
} from "./AuthProvider";
import { ProtectedRoute } from "./ProtectedRoute";
import { LoginPage } from "../pages/LoginPage";
import { RegisterPage } from "../pages/RegisterPage";

const fetchMock = vi.fn<typeof fetch>();
const SESSION_QUERY_KEY = ["auth", "session"] as const;
let latestProtectedRequest: Promise<unknown> | undefined;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function unauthorized(code = "invalid_session", message = "会话已过期") {
  return jsonResponse(
    {
      error: {
        code,
        message,
        retryable: false,
        field_errors: {},
      },
    },
    401,
  );
}

function StatusHarness() {
  const auth = useAuth();
  const navigate = useNavigate();

  return (
    <main>
      <p data-testid="auth-status">{auth.status}</p>
      {auth.status === "authenticated" ? <p>{auth.username}</p> : null}
      <button
        type="button"
        onClick={() => void auth.login({ username: "reader", password: "correct horse battery" }, "/documents")}
      >
        登录测试
      </button>
      <button type="button" onClick={() => void auth.logout()}>
        退出测试
      </button>
      <button
        type="button"
        onClick={() => {
          latestProtectedRequest = auth
            .request("/api/v1/documents")
            .catch((reason: unknown) => reason);
        }}
      >
        加载受保护资源
      </button>
      <button type="button" onClick={() => navigate(-1)}>
        返回
      </button>
    </main>
  );
}

function OverviewHarness() {
  const auth = useAuth();
  return (
    <main>
      <h1>学习概览</h1>
      {auth.status === "authenticated" ? <p>{auth.username}</p> : null}
    </main>
  );
}

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderApp(
  initialEntry = "/login",
  intendedPath?: string,
  queryClient = createTestQueryClient(),
  sessionExpired = false,
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
          { path: "/login", element: <LoginPage /> },
          { path: "/register", element: <RegisterPage /> },
          { path: "/status", element: <StatusHarness /> },
          {
            element: <ProtectedRoute />,
            children: [
              { path: "/documents", element: <h1>文档空间</h1> },
              { path: "/overview", element: <OverviewHarness /> },
            ],
          },
        ],
      },
    ],
    {
      initialEntries: [
        intendedPath
          ? {
              pathname: initialEntry,
              state: {
                from: intendedPath,
                ...(sessionExpired ? { sessionExpired: true } : {}),
              },
            }
          : initialEntry,
      ],
    },
  );

  render(<RouterProvider router={router} />);
  return router;
}

describe("AuthProvider", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    latestProtectedRequest = undefined;
  });

  it.each(["//external.example", "/\\external.example", "/login", "/register"])(
    "normalizes unsafe post-auth destination %s",
    (destination) => {
      expect(resolveAuthDestination(destination)).toBe("/overview");
    },
  );

  it("starts in loading while restoring the session", () => {
    fetchMock.mockReturnValue(new Promise(() => undefined));
    vi.stubGlobal("fetch", fetchMock);

    renderApp("/status");

    expect(screen.getByTestId("auth-status")).toHaveTextContent("loading");
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("becomes anonymous after a session 401", async () => {
    fetchMock.mockResolvedValue(unauthorized());
    vi.stubGlobal("fetch", fetchMock);

    renderApp("/status");

    await waitFor(() => {
      expect(screen.getByTestId("auth-status")).toHaveTextContent("anonymous");
    });
  });

  it("shows an expired-session dialog and returns focus to login", async () => {
    fetchMock.mockResolvedValue(unauthorized());
    vi.stubGlobal("fetch", fetchMock);
    renderApp(
      "/login",
      "/overview",
      createTestQueryClient(),
      true,
    );
    const user = userEvent.setup();

    const dialog = await screen.findByRole("dialog", { name: "会话已过期" });
    expect(dialog).toBeVisible();
    expect(screen.getByRole("button", { name: "重新登录" })).toHaveFocus();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: "会话已过期" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("用户名")).toHaveFocus();
  });

  it("renders the approved login controls and toggles password visibility", async () => {
    fetchMock.mockResolvedValueOnce(unauthorized());
    vi.stubGlobal("fetch", fetchMock);
    renderApp();
    const user = userEvent.setup();

    expect(await screen.findByRole("complementary", { name: "知研介绍" })).toBeVisible();
    expect(screen.queryByRole("checkbox", { name: "保持登录状态" })).not.toBeInTheDocument();
    const password = screen.getByLabelText("密码");
    const toggle = screen.getByRole("button", { name: "显示密码" });
    expect(password).toHaveAttribute("type", "password");

    await user.click(toggle);

    expect(password).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "隐藏密码" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("restores an authenticated session without persisting secrets", async () => {
    const localSet = vi.spyOn(window.localStorage, "setItem");
    const sessionSet = vi.spyOn(window.sessionStorage, "setItem");
    fetchMock.mockResolvedValue(
      jsonResponse({ username: "reader", csrf_token: "restored-csrf" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderApp("/status");

    expect(await screen.findByText("reader")).toBeVisible();
    expect(localSet).not.toHaveBeenCalled();
    expect(sessionSet).not.toHaveBeenCalled();
  });

  it("rejects an unsafe intended route during restored authentication", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ username: "reader", csrf_token: "restored-csrf" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const router = renderApp("/login", "//external.example");

    expect(await screen.findByRole("heading", { name: "学习概览" })).toBeVisible();
    expect(router.state.location.pathname).toBe("/overview");
  });

  it("avoids redirecting an authenticated user back into an auth route", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ username: "reader", csrf_token: "restored-csrf" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const router = renderApp("/login", "/login");

    expect(await screen.findByRole("heading", { name: "学习概览" })).toBeVisible();
    expect(router.state.location.pathname).toBe("/overview");
  });

  it("logs in and replaces history with the intended protected route", async () => {
    fetchMock
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(
        jsonResponse({ username: "reader", csrf_token: "login-csrf" }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const router = renderApp("/login", "/documents");
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("用户名"), "reader");
    await user.type(screen.getByLabelText("密码"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByRole("heading", { name: "文档空间" })).toBeVisible();
    expect(router.state.location.pathname).toBe("/documents");
    expect(router.state.historyAction).toBe("REPLACE");
  });

  it("shows an invalid-credentials banner and field guidance", async () => {
    fetchMock
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(
        unauthorized("invalid_credentials", "用户名或密码错误"),
      );
    vi.stubGlobal("fetch", fetchMock);
    renderApp();
    const user = userEvent.setup();

    const username = await screen.findByLabelText("用户名");
    expect(username).toHaveAccessibleDescription("请输入 3–32 个字符的用户名");
    await user.type(username, "reader");
    await user.type(screen.getByLabelText("密码"), "wrong password");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("用户名或密码错误");
  });

  it("connects API field errors to the invalid form control", async () => {
    fetchMock
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(
        jsonResponse(
          {
            error: {
              code: "validation_error",
              message: "请修正表单错误",
              retryable: false,
              field_errors: { username: "用户名格式无效" },
            },
          },
          422,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    renderApp();
    const user = userEvent.setup();

    const username = await screen.findByLabelText("用户名");
    await user.type(username, "reader");
    await user.type(screen.getByLabelText("密码"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("用户名格式无效")).toBeVisible();
    expect(username).toHaveAttribute("aria-invalid", "true");
    expect(username).toHaveAccessibleDescription(
      "请输入 3–32 个字符的用户名 用户名格式无效",
    );
  });

  it("publishes the domain length limits on the login form", async () => {
    fetchMock.mockResolvedValueOnce(unauthorized());
    vi.stubGlobal("fetch", fetchMock);
    renderApp();

    const username = await screen.findByLabelText("用户名");
    const password = screen.getByLabelText("密码");
    expect(username).toHaveAttribute("minlength", "3");
    expect(username).toHaveAttribute("maxlength", "32");
    expect(username).toHaveAccessibleDescription("请输入 3–32 个字符的用户名");
    expect(password).toHaveAttribute("minlength", "8");
    expect(password).toHaveAttribute("maxlength", "128");
    expect(password).toHaveAccessibleDescription("请输入 8–128 个字符的密码");
  });

  it("shows and disables the pending login action", async () => {
    fetchMock
      .mockResolvedValueOnce(unauthorized())
      .mockReturnValueOnce(new Promise(() => undefined));
    vi.stubGlobal("fetch", fetchMock);
    renderApp();
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("用户名"), "reader");
    await user.type(screen.getByLabelText("密码"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(screen.getByRole("button", { name: "登录中…" })).toBeDisabled();
  });

  it("registers and replaces history with the default overview", async () => {
    fetchMock
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(
        jsonResponse({ username: "new_reader", csrf_token: "register-csrf" }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const router = renderApp("/register");
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("用户名"), "new_reader");
    await user.type(screen.getByLabelText("密码"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "注册" }));

    expect(await screen.findByRole("heading", { name: "学习概览" })).toBeVisible();
    expect(router.state.historyAction).toBe("REPLACE");
  });

  it("publishes the domain length limits on the registration form", async () => {
    fetchMock.mockResolvedValueOnce(unauthorized());
    vi.stubGlobal("fetch", fetchMock);
    renderApp("/register");

    const username = await screen.findByLabelText("用户名");
    const password = screen.getByLabelText("密码");
    expect(username).toHaveAttribute("minlength", "3");
    expect(username).toHaveAttribute("maxlength", "32");
    expect(username).toHaveAccessibleDescription("请输入 3–32 个字符的用户名");
    expect(password).toHaveAttribute("minlength", "8");
    expect(password).toHaveAttribute("maxlength", "128");
    expect(password).toHaveAccessibleDescription("请输入 8–128 个字符的密码");
  });

  it("preserves the complete protected target through login and registration", async () => {
    fetchMock
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(
        jsonResponse({ username: "new_reader", csrf_token: "register-csrf" }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const router = renderApp("/documents?sort=recent#page-3");
    const user = userEvent.setup();

    expect(await screen.findByRole("heading", { name: "登录" })).toBeVisible();
    await user.click(screen.getByRole("link", { name: "创建账号" }));

    expect(await screen.findByRole("heading", { name: "注册" })).toBeVisible();
    expect(router.state.location.state).toEqual({
      from: "/documents?sort=recent#page-3",
    });
    await user.type(screen.getByLabelText("用户名"), "new_reader");
    await user.type(screen.getByLabelText("密码"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "注册" }));

    expect(await screen.findByRole("heading", { name: "文档空间" })).toBeVisible();
    expect(router.state.location.pathname).toBe("/documents");
    expect(router.state.location.search).toBe("?sort=recent");
    expect(router.state.location.hash).toBe("#page-3");
    expect(router.state.historyAction).toBe("REPLACE");
  });

  it("preserves the intended target through the registration-to-login link", async () => {
    fetchMock.mockResolvedValueOnce(unauthorized());
    vi.stubGlobal("fetch", fetchMock);
    const router = renderApp(
      "/register",
      "/documents?sort=oldest#page-8",
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole("link", { name: "返回登录" }));

    expect(await screen.findByRole("heading", { name: "登录" })).toBeVisible();
    expect(router.state.location.state).toEqual({
      from: "/documents?sort=oldest#page-8",
    });
  });

  it("logs out with CSRF, removes cached session data, and cannot restore it after remount", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ username: "reader", csrf_token: "logout-csrf" }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const queryClient = createTestQueryClient();
    const router = renderApp("/status", undefined, queryClient);
    const user = userEvent.setup();

    expect(await screen.findByText("reader")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "退出测试" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.historyAction).toBe("REPLACE");
    const logoutCall = fetchMock.mock.calls[1];
    expect(logoutCall?.[0]).toBe("/api/v1/auth/logout");
    expect(new Headers(logoutCall?.[1]?.headers).get("X-CSRF-Token")).toBe(
      "logout-csrf",
    );
    expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toBeUndefined();

    cleanup();
    fetchMock.mockResolvedValueOnce(unauthorized());
    renderApp("/status", undefined, queryClient);

    await waitFor(() => {
      expect(screen.getByTestId("auth-status")).toHaveTextContent("anonymous");
    });
    expect(screen.queryByText("reader")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("shows session expiry when logout finds the server-side session invalid", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ username: "reader", csrf_token: "expired-csrf" }),
      )
      .mockResolvedValueOnce(unauthorized());
    vi.stubGlobal("fetch", fetchMock);
    const router = renderApp("/status");
    const user = userEvent.setup();

    expect(await screen.findByText("reader")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "退出测试" }));

    expect(await screen.findByRole("dialog", { name: "会话已过期" })).toBeVisible();
    expect(router.state.location.pathname).toBe("/login");
    expect(router.state.location.state).toEqual({
      from: "/status",
      sessionExpired: true,
    });
  });

  it("shows the service-unavailable message after a real network failure", async () => {
    fetchMock
      .mockResolvedValueOnce(unauthorized())
      .mockRejectedValueOnce(new TypeError("fetch failed"));
    vi.stubGlobal("fetch", fetchMock);
    renderApp();
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("用户名"), "reader");
    await user.type(screen.getByLabelText("密码"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "服务暂时不可用，请稍后重试",
    );
  });

  it("ignores a delayed 401 from the previous session after logout and re-login", async () => {
    const oldUnauthorized = deferred<Response>();
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ username: "old_reader", csrf_token: "old-csrf" }),
      )
      .mockReturnValueOnce(oldUnauthorized.promise)
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(
        jsonResponse({ username: "new_reader", csrf_token: "new-csrf" }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const queryClient = createTestQueryClient();
    const router = renderApp("/status", undefined, queryClient);
    const user = userEvent.setup();

    expect(await screen.findByText("old_reader")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "加载受保护资源" }));
    expect(latestProtectedRequest).toBeDefined();
    await user.click(screen.getByRole("button", { name: "退出测试" }));
    await user.type(await screen.findByLabelText("用户名"), "new_reader");
    await user.type(screen.getByLabelText("密码"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("new_reader")).toBeVisible();
    await act(async () => {
      oldUnauthorized.resolve(unauthorized());
      await latestProtectedRequest;
    });

    expect(router.state.location.pathname).toBe("/overview");
    expect(screen.getByText("new_reader")).toBeVisible();
    expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual({
      username: "new_reader",
      csrf_token: "new-csrf",
    });
  });
});

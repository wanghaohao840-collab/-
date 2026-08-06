import { type FormEvent, useState } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";

import { ApiError } from "../api/client";
import { resolveAuthDestination, useAuth } from "../auth/AuthProvider";

type LocationState = { from?: unknown };

export function RegisterPage() {
  const auth = useAuth();
  const location = useLocation();
  const [error, setError] = useState<ApiError>();
  const from = (location.state as LocationState | null)?.from;
  const intendedPath = typeof from === "string" ? from : undefined;

  if (auth.status === "loading") {
    return <div className="session-status" role="status">正在恢复会话…</div>;
  }
  if (auth.status === "authenticated") {
    return <Navigate to={resolveAuthDestination(intendedPath)} replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    const form = new FormData(event.currentTarget);

    try {
      await auth.register(
        {
          username: String(form.get("username") ?? ""),
          password: String(form.get("password") ?? ""),
        },
        intendedPath,
      );
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason
          : new ApiError(0, "client_error", "注册失败，请稍后重试"),
      );
    }
  }

  const usernameError = error?.fieldErrors.username;
  const passwordError = error?.fieldErrors.password;

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="register-title">
        <header className="auth-brand">
          <p className="brand-name">知研</p>
          <p>智能文档学习助手</p>
        </header>
        <h1 id="register-title">注册</h1>
        {error ? <div className="form-banner" role="alert">{error.message}</div> : null}
        <form onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="register-username">用户名</label>
            <input
              id="register-username"
              name="username"
              autoComplete="username"
              required
              minLength={3}
              maxLength={64}
              aria-invalid={Boolean(usernameError)}
              aria-describedby={`register-username-help${usernameError ? " register-username-error" : ""}`}
            />
            <p id="register-username-help" className="field-help">请输入 3–64 个字符的用户名</p>
            {usernameError ? <p id="register-username-error" className="field-error">{usernameError}</p> : null}
          </div>
          <div className="form-field">
            <label htmlFor="register-password">密码</label>
            <input
              id="register-password"
              name="password"
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              maxLength={256}
              aria-invalid={Boolean(passwordError)}
              aria-describedby={`register-password-help${passwordError ? " register-password-error" : ""}`}
            />
            <p id="register-password-help" className="field-help">密码至少包含 8 个字符</p>
            {passwordError ? <p id="register-password-error" className="field-error">{passwordError}</p> : null}
          </div>
          <button type="submit" disabled={auth.isRegisterPending}>
            {auth.isRegisterPending ? "注册中…" : "注册"}
          </button>
        </form>
        <p className="auth-switch">已有账号？ <Link to="/login">返回登录</Link></p>
      </section>
    </main>
  );
}

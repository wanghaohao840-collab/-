import type { PropsWithChildren } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "./AuthProvider";

export function ProtectedRoute({ children }: PropsWithChildren) {
  const auth = useAuth();
  const location = useLocation();

  if (auth.status === "loading") {
    return (
      <div className="session-status" role="status" aria-live="polite">
        正在恢复会话…
      </div>
    );
  }

  if (auth.status === "anonymous") {
    const from = `${location.pathname}${location.search}${location.hash}`;
    return (
      <Navigate
        to="/login"
        replace
        state={{
          from,
          ...(auth.sessionExpired ? { sessionExpired: true } : {}),
        }}
      />
    );
  }

  return children ?? <Outlet />;
}

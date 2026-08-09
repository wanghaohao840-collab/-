import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { ApiError, apiRequest, type ApiRequestOptions } from "../api/client";

export type AuthState =
  | { status: "loading" }
  | { status: "anonymous"; sessionExpired?: boolean }
  | { status: "authenticated"; username: string; csrfToken: string };

export type Credentials = {
  username: string;
  password: string;
};

type SessionResponse = {
  username: string;
  csrf_token: string;
};

type AuthActions = {
  login: (credentials: Credentials, intendedPath?: string) => Promise<void>;
  register: (credentials: Credentials, intendedPath?: string) => Promise<void>;
  logout: () => Promise<void>;
  request: <T>(input: string, options?: ApiRequestOptions) => Promise<T>;
  isLoginPending: boolean;
  isRegisterPending: boolean;
  isLogoutPending: boolean;
};

export type AuthContextValue = AuthState & AuthActions;

const AuthContext = createContext<AuthContextValue | undefined>(undefined);
const SESSION_QUERY_KEY = ["auth", "session"] as const;

function authenticatedState(session: SessionResponse): AuthState {
  return {
    status: "authenticated",
    username: session.username,
    csrfToken: session.csrf_token,
  };
}

export function resolveAuthDestination(intendedPath?: string): string {
  if (!intendedPath?.startsWith("/")) {
    return "/overview";
  }

  let decodedPath: string;
  try {
    decodedPath = decodeURIComponent(intendedPath);
  } catch {
    return "/overview";
  }

  const pathname = decodedPath.split(/[?#]/, 1)[0];
  const isAuthRoute = pathname === "/login" || pathname === "/register";
  if (!decodedPath.startsWith("//") && !decodedPath.includes("\\") && !isAuthRoute) {
    return intendedPath;
  }
  return "/overview";
}

export function AuthProvider({ children }: PropsWithChildren) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const authGenerationRef = useRef(0);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const sessionQuery = useQuery({
    queryKey: SESSION_QUERY_KEY,
    queryFn: ({ signal }) =>
      apiRequest<SessionResponse>("/api/v1/auth/session", { signal }),
    enabled: state.status === "loading",
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: Number.POSITIVE_INFINITY,
  });

  useEffect(() => {
    if (state.status !== "loading") {
      return;
    }
    if (sessionQuery.isSuccess) {
      authGenerationRef.current += 1;
      setState(authenticatedState(sessionQuery.data));
    } else if (sessionQuery.isError) {
      setState({ status: "anonymous" });
    }
  }, [sessionQuery.data, sessionQuery.isError, sessionQuery.isSuccess, state.status]);

  const finishAuthentication = useCallback(
    (session: SessionResponse, intendedPath?: string) => {
      authGenerationRef.current += 1;
      queryClient.setQueryData(SESSION_QUERY_KEY, session);
      setState(authenticatedState(session));
      navigate(resolveAuthDestination(intendedPath), { replace: true });
    },
    [navigate, queryClient],
  );

  const loginMutation = useMutation({
    mutationFn: async ({ credentials }: { credentials: Credentials }) => {
      await queryClient.cancelQueries({ queryKey: SESSION_QUERY_KEY });
      return apiRequest<SessionResponse>("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(credentials),
      });
    },
  });

  const registerMutation = useMutation({
    mutationFn: async ({ credentials }: { credentials: Credentials }) => {
      await queryClient.cancelQueries({ queryKey: SESSION_QUERY_KEY });
      return apiRequest<SessionResponse>("/api/v1/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(credentials),
      });
    },
  });

  const login = useCallback(
    async (credentials: Credentials, intendedPath?: string) => {
      const session = await loginMutation.mutateAsync({ credentials });
      finishAuthentication(session, intendedPath);
    },
    [finishAuthentication, loginMutation],
  );

  const register = useCallback(
    async (credentials: Credentials, intendedPath?: string) => {
      const session = await registerMutation.mutateAsync({ credentials });
      finishAuthentication(session, intendedPath);
    },
    [finishAuthentication, registerMutation],
  );

  const clearCachedSession = useCallback(() => {
    void queryClient.cancelQueries({ queryKey: SESSION_QUERY_KEY, exact: true });
    queryClient.removeQueries({ queryKey: SESSION_QUERY_KEY, exact: true });
  }, [queryClient]);

  const logoutMutation = useMutation({
    mutationFn: async () => {
      if (state.status !== "authenticated") {
        return;
      }
      await apiRequest<void>("/api/v1/auth/logout", {
        method: "POST",
        csrfToken: state.csrfToken,
      });
    },
  });

  const logout = useCallback(async () => {
    authGenerationRef.current += 1;
    let sessionExpired = false;
    try {
      await logoutMutation.mutateAsync();
    } catch (reason) {
      sessionExpired =
        reason instanceof ApiError &&
        reason.status === 401 &&
        reason.code === "invalid_session";
      if (!sessionExpired) {
        throw reason;
      }
    } finally {
      clearCachedSession();
      setState({ status: "anonymous", sessionExpired });
      const from = `${location.pathname}${location.search}${location.hash}`;
      navigate("/login", {
        replace: true,
        state: sessionExpired ? { from, sessionExpired: true } : undefined,
      });
    }
  }, [
    clearCachedSession,
    location.hash,
    location.pathname,
    location.search,
    logoutMutation,
    navigate,
  ]);

  const handleUnauthorized = useCallback(
    (requestGeneration: number) => {
      if (requestGeneration !== authGenerationRef.current) {
        return;
      }

      authGenerationRef.current += 1;
      clearCachedSession();
      setState({ status: "anonymous", sessionExpired: true });
      const from = `${location.pathname}${location.search}${location.hash}`;
      navigate("/login", {
        replace: true,
        state: { from, sessionExpired: true },
      });
    },
    [clearCachedSession, location.hash, location.pathname, location.search, navigate],
  );

  const requestGeneration = authGenerationRef.current;
  const request = useCallback(
    <T,>(input: string, options: ApiRequestOptions = {}) =>
      apiRequest<T>(input, {
        ...options,
        csrfToken: state.status === "authenticated" ? state.csrfToken : undefined,
        onUnauthorized: () => handleUnauthorized(requestGeneration),
      }),
    [handleUnauthorized, requestGeneration, state],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      login,
      register,
      logout,
      request,
      isLoginPending: loginMutation.isPending,
      isRegisterPending: registerMutation.isPending,
      isLogoutPending: logoutMutation.isPending,
    }),
    [
      login,
      loginMutation.isPending,
      logout,
      logoutMutation.isPending,
      register,
      registerMutation.isPending,
      request,
      state,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}

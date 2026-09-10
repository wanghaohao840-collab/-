import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { insightKeys, useCreateReport, useReports } from "./queries";
import type { LearningReport } from "./types";

const auth = vi.hoisted(() => ({ status: "authenticated", username: "alice", request: vi.fn() }));
vi.mock("../../auth/AuthProvider", () => ({ useAuth: () => ({ ...auth }) }));

function harness() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { client, wrapper };
}

describe("insight query isolation", () => {
  afterEach(() => { vi.restoreAllMocks(); auth.username = "alice"; });

  it("does not render Alice's cached reports while Bob's request is pending", async () => {
    const { client, wrapper } = harness();
    client.setQueryData(insightKeys.reports("alice"), [{ id: "alice-report", title: "private", created_at: "now" }]);
    vi.spyOn(api, "listReports").mockImplementation(() => new Promise(() => undefined));
    const { result, rerender } = renderHook(() => useReports(), { wrapper });
    expect(result.current.data?.[0].id).toBe("alice-report");
    auth.username = "bob";
    rerender();
    expect(result.current.data).toBeUndefined();
    expect(result.current.isPending).toBe(true);
  });

  it("keeps a late report generation result in its originating user's cache", async () => {
    const { client, wrapper } = harness();
    let resolveReport!: (value: LearningReport) => void;
    vi.spyOn(api, "createReport").mockImplementation(() => new Promise((resolve) => { resolveReport = resolve; }));
    const { result, rerender } = renderHook(() => useCreateReport(), { wrapper });
    act(() => result.current.mutate());
    await waitFor(() => expect(api.createReport).toHaveBeenCalledOnce());
    auth.username = "bob";
    rerender();
    await act(async () => resolveReport({ id: "r1", title: "private", created_at: "now", content: "Alice" }));
    await waitFor(() => expect(client.getQueryData(insightKeys.report("alice", "r1"))).toBeDefined());
    expect(client.getQueryData(insightKeys.report("bob", "r1"))).toBeUndefined();
  });
});

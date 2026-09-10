import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSearchMutation } from "./queries";

const mock = vi.hoisted(() => ({ request: vi.fn(), identity: "alice" }));
vi.mock("../../auth/AuthProvider", () => ({ useAuth: () => ({ status: "authenticated", username: mock.identity, csrfToken: mock.identity, request: mock.request }) }));
function setup() {
  const client = new QueryClient();
  const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { client, ...renderHook(({ fingerprint }) => useSearchMutation(fingerprint), { wrapper, initialProps: { fingerprint: "first" } }) };
}
const input = { query: "private text", document_ids: ["doc"], limit: 10 as const };
const response = { request_id: "r", document_ids: ["doc"], result_count: 0, results: [] };

describe("private search mutation", () => {
  beforeEach(() => { mock.request.mockReset(); mock.identity = "alice"; });
  it("publishes current results only in memory, with no query-cache entry", async () => {
    mock.request.mockResolvedValue(response);
    const { result, client } = setup();
    act(() => result.current.run(input));
    await waitFor(() => expect(result.current.data).toEqual(response));
    expect(client.getQueryCache().getAll()).toHaveLength(0);
  });
  it.each(["scope", "identity"])("ignores late response after %s changes", async (change) => {
    let resolve!: (value: unknown) => void;
    mock.request.mockImplementation(() => new Promise((done) => { resolve = done; }));
    const { result, rerender } = setup();
    act(() => result.current.run(input));
    await waitFor(() => expect(mock.request).toHaveBeenCalled());
    if (change === "identity") mock.identity = "bob";
    rerender({ fingerprint: change === "scope" ? "changed" : "first" });
    await act(async () => resolve(response));
    expect(result.current.data).toBeUndefined();
    expect(result.current.error).toBeUndefined();
    expect(result.current.isPending).toBe(false);
    rerender({ fingerprint: "first" });
    expect(result.current.data).toBeUndefined();
  });
  it("keeps only the latest request even when earlier transport ignores abort", async () => {
    const resolves: Array<(value: unknown) => void> = [];
    mock.request.mockImplementation(() => new Promise((done) => resolves.push(done)));
    const { result } = setup();
    act(() => result.current.run(input));
    await waitFor(() => expect(resolves).toHaveLength(1));
    act(() => result.current.run(input));
    await waitFor(() => expect(resolves).toHaveLength(2));
    await act(async () => resolves[1]!({ ...response, request_id: "new" }));
    await waitFor(() => expect(result.current.data?.request_id).toBe("new"));
    await act(async () => resolves[0]!(response));
    expect(result.current.data?.request_id).toBe("new");
  });
  it("does not retry transient failures and removes them when conditions change", async () => {
    mock.request.mockRejectedValue(new Error("temporary"));
    const { result, rerender } = setup();
    act(() => result.current.run(input));
    await waitFor(() => expect(result.current.error).toBeInstanceOf(Error));
    expect(mock.request).toHaveBeenCalledTimes(1);
    rerender({ fingerprint: "other" });
    expect(result.current.error).toBeUndefined();
  });
});

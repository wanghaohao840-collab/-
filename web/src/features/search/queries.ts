import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "../../auth/AuthProvider";
import { listDocuments } from "../documents/api";
import { searchDocuments } from "./api";
import type { SearchInput, SearchResponse } from "./types";

type Outcome = { generation: number; data?: SearchResponse; error?: unknown };

export function useSearchDocumentsQuery() {
  const auth = useAuth();
  const [instance] = useState(() => crypto.randomUUID());
  return useQuery({
    // Session-keyed SearchWorkspace remounts this namespace on every auth transition.
    // Retain the documents prefix so existing deletion/import invalidations still work.
    queryKey: ["documents", "search", auth.status === "authenticated" ? auth.username : "", instance],
    queryFn: ({ signal }) => listDocuments(auth.request, signal),
    enabled: auth.status === "authenticated", gcTime: 0, retry: false,
    refetchOnWindowFocus: true, refetchInterval: 30000,
  });
}

// No query-cache entries or persisted keys contain search text or excerpts.
export function useSearchMutation(fingerprint: string) {
  const auth = useAuth();
  const identity = auth.status === "authenticated" ? `${auth.username}:${auth.csrfToken}` : auth.status;
  const current = useRef({ fingerprint, identity, generation: 0 });
  const controller = useRef<AbortController | null>(null);
  if (current.current.fingerprint !== fingerprint || current.current.identity !== identity) {
    current.current = { fingerprint, identity, generation: current.current.generation + 1 };
  }
  useEffect(() => () => { current.current.generation += 1; controller.current?.abort(); }, []);
  useEffect(() => { controller.current?.abort(); }, [fingerprint, identity]);
  const mutation = useMutation({
    gcTime: 0, retry: false,
    mutationFn: async ({ input, generation, signal }: { input: SearchInput; generation: number; signal: AbortSignal }): Promise<Outcome> => {
      try { return { generation, data: await searchDocuments(auth.request, input, signal) }; }
      catch (error) { return { generation, error }; }
    },
  });
  const accepted = mutation.data?.generation === current.current.generation ? mutation.data : undefined;
  return {
    data: accepted?.data,
    error: accepted?.error,
    isPending: mutation.isPending && mutation.variables?.generation === current.current.generation,
    run(input: SearchInput) {
      controller.current?.abort();
      controller.current = new AbortController();
      const generation = ++current.current.generation;
      mutation.mutate({ input, generation, signal: controller.current.signal });
    },
  };
}

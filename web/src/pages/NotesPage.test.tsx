import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { NotesPage } from "./NotesPage";

vi.mock("../auth/AuthProvider", () => ({ useAuth: () => ({ request: vi.fn().mockResolvedValue({ enabled: true, items: [], next_cursor: null }) }) }));

describe("NotesPage", () => {
  it("renders the notes workspace route with URL filters", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/notes?query=memory&tags=rag,study"]}><NotesPage /></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByRole("heading", { level: 1, name: "学习笔记" })).toBeVisible();
    expect(screen.getByLabelText("搜索笔记")).toHaveValue("memory");
  });
});

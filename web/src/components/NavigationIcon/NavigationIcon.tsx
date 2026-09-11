const paths: Record<string, string> = {
  "/more": "M4 4h5v5H4Z M15 4h5v5h-5Z M4 15h5v5H4Z M15 15h5v5h-5Z",
  "/overview": "M3 10 12 3l9 7v10H3Z M9 20v-7h6v7",
  "/documents": "M5 3h9l5 5v13H5Z M14 3v6h5 M8 13h8 M8 17h6",
  "/qa": "M4 4h16v13H9l-5 4Z M8 8h8 M8 12h5",
  "/search": "M16 16l5 5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  "/notes": "M4 4h11v16H4Z M10 14l8-11 3 2-8 11-4 1Z",
  "/insights": "M4 3v18h17 M8 16v-5 M13 16V7 M18 16V4",
  "/learning": "M3 5c4-2 7-2 9 0 2-2 5-2 9 0v15c-4-2-7-2-9 0-2-2-5-2-9 0Z M12 5v15",
};
export function NavigationIcon({ path }: { path: string }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[path] ?? "M5 4h9v16H5 M10 12h12 M18 8l4 4-4 4"} /></svg>;
}

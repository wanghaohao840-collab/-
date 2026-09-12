export function headingTitle(body: string): string {
  return body.match(/^# ([^\r\n]*)/m)?.[1] ?? "";
}

export function withTitle(body: string, title: string): string {
  const clean = title.replace(/[\r\n]/g, " ");
  return /^# [^\r\n]*/m.test(body)
    ? body.replace(/^# [^\r\n]*/m, () => `# ${clean}`)
    : `# ${clean}\n\n${body}`;
}

export function noteTime(value?: string): string {
  if (!value || Number.isNaN(Date.parse(value))) return "尚未保存";
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export function sourceLabel(kind: string): string {
  return kind === "document_chunk" ? "文献证据" : kind === "qa_citation" ? "问答引用" : "问答回答";
}

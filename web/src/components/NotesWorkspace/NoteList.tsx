import type { ReactNode } from "react";
import { headingTitle, noteTime, sourceLabel } from "./notePresentation";
import { Button } from "../Button/Button";
import type { NoteListItem } from "../../features/notes/types";

type Props = {
  filters?: ReactNode;
  filtered?: boolean;
  items: NoteListItem[];
  selectedId?: string;
  hasMore: boolean;
  loadingMore: boolean;
  onSelect: (id: string) => void;
  onLoadMore: () => void;
  onCreate: () => void;
  onOpenQa: () => void;
};

export function noteTitle(markdown: string, concept: string | null): string {
  if (headingTitle(markdown).trim()) return headingTitle(markdown).trim();
  if (concept?.trim()) return concept.trim();
  const firstLine = markdown.split(/\r?\n/).map((line) => line.trim()).find(Boolean) ?? "";
  const stripped = firstLine
    .replace(/^#{1,6}\s+/, "")
    .replace(/^[-*>]\s+/, "")
    .replace(/[*_`~]/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .trim();
  return stripped || "未命名笔记";
}

export function NoteList({ filters, filtered, items, selectedId, hasMore, loadingMore, onSelect, onLoadMore, onCreate, onOpenQa }: Props) {
  return <aside className="notes-list" aria-label="笔记列表">
    <div className="notes-library-title"><span className="notes-eyebrow">NOTES LIBRARY</span><h2>笔记库</h2></div>{filters}
    <div className="notes-list__heading"><h2>{filtered ? "筛选结果" : "全部笔记"} · {items.length}{hasMore ? "+" : ""}</h2></div>
    {!items.length ? <div className="notes-list__empty"><h3>{filtered ? "没有符合条件的笔记" : "还没有笔记"}</h3><p>{filtered ? "试试其他关键词，或清空筛选。" : "从问答或文献中沉淀你的第一条知识"}</p><div className="notes-list__empty-actions"><Button onClick={onCreate}>新建笔记</Button><Button hierarchy="secondary" onClick={onOpenQa}>从 QA 导入</Button></div></div> : <ol>
      {items.map((item) => <li key={item.id}>
        <button type="button" className="notes-list__item" aria-current={item.id === selectedId ? "page" : undefined} onClick={() => onSelect(item.id)}>
          <strong>{noteTitle(item.body_markdown, item.concept)}</strong>
          <span>{item.body_markdown.replaceAll("#", "").replaceAll("*", "").replaceAll("`", "").replaceAll(">", "").replaceAll("-", "").replaceAll("[", "").replaceAll("]", "").trim().slice(0, 100) || "空白笔记"}</span>
          <small>{item.tags.length ? item.tags.map((tag) => `#${tag}`).join(" · ") : "无标签"}</small>
          <small>{item.sources.length ? `${Array.from(new Set(item.sources.map((source) => sourceLabel(source.kind)))).join(" / ")} · ${item.sources.length} 条来源` : "手动笔记"}</small><time dateTime={item.updated_at}>最近更新 {noteTime(item.updated_at)}</time>
          {item.projection_state === "pending" ? <em>正在同步记忆</em> : item.projection_state === "failed" ? <em>投影失败</em> : null}
        </button>
      </li>)}
    </ol>}
    {hasMore ? <Button hierarchy="secondary" className="notes-list__more" loading={loadingMore} onClick={onLoadMore}>加载更多笔记</Button> : null}
  </aside>;
}

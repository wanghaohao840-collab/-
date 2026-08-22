import type { ChangeEvent } from "react";

import { Button } from "../Button/Button";
import { TextField } from "../TextField/TextField";

type DocumentToolbarProps = {
  filter: string;
  onFilterChange: (value: string) => void;
  onOpenImport: (trigger: HTMLButtonElement) => void;
  showFilter: boolean;
};

export function DocumentToolbar({
  filter,
  onFilterChange,
  onOpenImport,
  showFilter,
}: DocumentToolbarProps) {
  function handleFilterChange(event: ChangeEvent<HTMLInputElement>) {
    onFilterChange(event.currentTarget.value);
  }

  return (
    <header className="document-toolbar">
      <h1>文档库</h1>
      <p>管理已导入文档与批量任务</p>
      <Button
        className="document-toolbar__import"
        onClick={(event) => onOpenImport(event.currentTarget)}
      >
        导入文档
      </Button>
      {showFilter ? (
        <TextField
          className="document-toolbar__filter-input"
          label="按名称筛选"
          placeholder="输入文件名"
          type="search"
          value={filter}
          onChange={handleFilterChange}
        />
      ) : null}
    </header>
  );
}

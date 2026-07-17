import { SearchOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { searchWorkspace } from "../../api/shell";
import { useCurrentWorkspace } from "../../stores/currentWorkspace";

const kindLabels = { client: "客户", project: "项目", account: "账号" } as const;

export function GlobalSearch() {
  const navigate = useNavigate();
  const hydrate = useCurrentWorkspace((state) => state.hydrate);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const normalizedQuery = query.trim();
  const resultsQuery = useQuery({
    queryKey: ["workspace-search", normalizedQuery],
    queryFn: () => searchWorkspace(normalizedQuery),
    enabled: open && normalizedQuery.length >= 2,
    staleTime: 15_000,
  });

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const isEditing = target?.matches("input, textarea, [contenteditable='true']");
      if (event.key === "/" && !isEditing) {
        event.preventDefault();
        setOpen(true);
      }
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (open) requestAnimationFrame(() => inputRef.current?.focus());
  }, [open]);

  const selectResult = (result: NonNullable<typeof resultsQuery.data>[number]) => {
    hydrate({
      clientId: result.client_id,
      projectId: result.project_id,
      platform: "douyin",
      accountId: result.account_id,
    });
    setOpen(false);
    setQuery("");
    navigate(result.path);
  };

  return (
    <>
      <button
        type="button"
        className="tz-global-search-trigger"
        aria-label="全局搜索"
        onClick={() => setOpen(true)}
      >
        <SearchOutlined />
        <span>搜索客户、项目或账号</span>
        <kbd>/</kbd>
      </button>
      {open ? (
        <div className="tz-command-backdrop" role="presentation" onMouseDown={() => setOpen(false)}>
          <section
            className="tz-command-panel"
            role="dialog"
            aria-modal="true"
            aria-label="全局搜索"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <label className="tz-command-input">
              <SearchOutlined />
              <input
                ref={inputRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="输入客户、项目或账号名称"
              />
              <kbd>ESC</kbd>
            </label>
            <div className="tz-command-results">
              {normalizedQuery.length < 2 ? (
                <p className="tz-command-hint">输入至少 2 个字符开始搜索</p>
              ) : resultsQuery.isLoading ? (
                <p className="tz-command-hint">正在搜索...</p>
              ) : resultsQuery.isError ? (
                <p className="tz-command-hint is-error">搜索暂时不可用，请稍后重试</p>
              ) : resultsQuery.data?.length ? (
                resultsQuery.data.map((result) => (
                  <button key={`${result.kind}-${result.id}`} type="button" onClick={() => selectResult(result)}>
                    <span className="tz-command-kind">{kindLabels[result.kind]}</span>
                    <span><strong>{result.title}</strong><small>{result.subtitle}</small></span>
                    <span className="tz-command-enter">进入</span>
                  </button>
                ))
              ) : (
                <p className="tz-command-hint">当前授权范围内没有匹配结果</p>
              )}
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}

import { useEffect, useState } from "react";

export interface PageMeta {
  total: number;
  limit: number;
  offset: number;
}

export const EMPTY_PAGE: PageMeta = { total: 0, limit: 50, offset: 0 };

export function pageQuery(offset: number, q = "", extra: Record<string, string> = {}): string {
  const params = new URLSearchParams({ limit: "50", offset: String(offset) });
  if (q.trim()) params.set("q", q.trim());
  for (const [key, value] of Object.entries(extra)) {
    if (value) params.set(key, value);
  }
  return params.toString();
}

export function Pager({ page, onOffset }: { page: PageMeta; onOffset: (offset: number) => void }) {
  const from = page.total === 0 ? 0 : page.offset + 1;
  const to = Math.min(page.offset + page.limit, page.total);
  return (
    <div className="pager">
      <span className="muted">
        {from.toLocaleString()}–{to.toLocaleString()} of {page.total.toLocaleString()}
      </span>
      <div className="pager-actions">
        <button className="ghost small" type="button" disabled={page.offset <= 0} onClick={() => onOffset(Math.max(0, page.offset - page.limit))}>
          Previous
        </button>
        <button className="ghost small" type="button" disabled={page.offset + page.limit >= page.total} onClick={() => onOffset(page.offset + page.limit)}>
          Next
        </button>
      </div>
    </div>
  );
}

export function useDebounced(value: string, delay = 250): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

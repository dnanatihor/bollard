import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { EMPTY_PAGE, Pager, pageQuery, useDebounced, type PageMeta } from "../paging";
import { useSession } from "../session";
import { Banner, Empty, Page, Pill, formatDuration, formatWhen, statusTone } from "../ui";

interface TraceRow {
  traceId: string;
  requestId: string;
  userId: string;
  model: string;
  provider: string;
  status: string;
  errorCode: string;
  startedAt: string;
  durationMs: number;
}

export function TracesPage() {
  const me = useSession();
  const [rows, setRows] = useState<TraceRow[] | null>(null);
  const [page, setPage] = useState<PageMeta>(EMPTY_PAGE);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");

  const debounced = useDebounced(query);

  useEffect(() => {
    setOffset(0);
  }, [debounced]);

  useEffect(() => {
    api<{ traces: TraceRow[]; page: PageMeta }>(`/api/traces?${pageQuery(offset, debounced)}`)
      .then((body) => {
        setRows(body.traces);
        setPage(body.page);
      })
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load traces."));
  }, [debounced, offset]);

  return (
    <Page title="Traces" lede={`The operational timeline for calls in ${me.workspaceName}: timing, status, and the steps inside each call. The prompt and the reply are on Audit.`}>
      {error ? <Banner tone="error">{error}</Banner> : null}
      <div className="toolbar">
        <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search user, model, or provider" aria-label="Search traces" />
      </div>
      <section className="card">
        {rows === null ? null : rows.length === 0 ? (
          <Empty title={query ? "No traces match" : "No traces yet"}>{query ? "Try another user or model." : "A call with a gateway key creates the first one."}</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>User</th>
                  <th>Model</th>
                  <th>Provider</th>
                  <th>Status</th>
                  <th>Duration</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((trace) => (
                  <tr key={trace.traceId}>
                    <td><Link to={`/traces/${trace.traceId}`}>{formatWhen(trace.startedAt)}</Link></td>
                    <td>{trace.userId}</td>
                    <td className="mono">{trace.model}</td>
                    <td>{trace.provider || "—"}</td>
                    <td><Pill tone={statusTone(trace.status)}>{trace.status}</Pill></td>
                    <td>{formatDuration(trace.durationMs)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pager page={page} onOffset={setOffset} />
      </section>
    </Page>
  );
}

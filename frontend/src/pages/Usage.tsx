import { useEffect, useState } from "react";

import { api } from "../api";
import { EMPTY_PAGE, Pager, pageQuery, useDebounced, type PageMeta } from "../paging";
import { useSession } from "../session";
import { Banner, Empty, Loading, Page, Pill, formatMoney, formatWhen } from "../ui";

interface UsageEvent {
  id: number;
  model: string;
  provider: string;
  kind: string;
  inputTokens: number;
  outputTokens: number;
  estimatedCost: number;
  createdAt: string;
}

interface UsageBody {
  period: string;
  requests: number;
  inputTokens: number;
  outputTokens: number;
  spentUsd: number;
  events: UsageEvent[];
  page: PageMeta;
}

export function UsagePage() {
  const me = useSession();
  const [body, setBody] = useState<UsageBody | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [offset, setOffset] = useState(0);
  const debounced = useDebounced(query);
  const mine = me.role === "inference";

  useEffect(() => {
    setOffset(0);
  }, [debounced, kind]);

  useEffect(() => {
    api<UsageBody>(`/api/usage?${pageQuery(offset, debounced, { kind })}`)
      .then(setBody)
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load usage."));
  }, [debounced, kind, offset]);

  if (!body && !error) return <Loading label="Loading usage…" />;

  const remaining = Math.max(me.monthlyUsd - me.spentUsd, 0);

  return (
    <Page
      title={mine ? "My usage" : "Usage"}
      lede={
        mine
          ? `You belong to ${me.homeName}. This key calls inside that workspace, and spend counts against your monthly cap.`
          : `Calls recorded this month for people in ${me.workspaceName}.`
      }
    >
      {error ? <Banner tone="error">{error}</Banner> : null}
      <div className="metric-grid">
        <article className="card metric-card">
          <div className="muted">Spent this month</div>
          <div className="metric">{formatMoney(me.spentUsd)}</div>
          <div className="muted">of {formatMoney(me.monthlyUsd)} · {formatMoney(remaining)} left</div>
        </article>
        <article className="card metric-card">
          <div className="muted">Requests</div>
          <div className="metric">{(body?.requests ?? 0).toLocaleString()}</div>
        </article>
        <article className="card metric-card">
          <div className="muted">Input tokens</div>
          <div className="metric">{(body?.inputTokens ?? 0).toLocaleString()}</div>
        </article>
        <article className="card metric-card">
          <div className="muted">Output tokens</div>
          <div className="metric">{(body?.outputTokens ?? 0).toLocaleString()}</div>
        </article>
      </div>
      <div className="compare">
        <article className="card">
          <h2>Chat</h2>
          <p>Send <span className="mono">POST /v1/chat/completions</span> with a chat model such as <span className="mono">general-fast</span>.</p>
          <pre className="sample">{`{"model":"general-fast","messages":[{"role":"user","content":"Hello"}]}`}</pre>
        </article>
        <article className="card">
          <h2>Embeddings</h2>
          <p>Send <span className="mono">POST /v1/embeddings</span> with an embedding model such as <span className="mono">embed-small</span>.</p>
          <pre className="sample">{`{"model":"embed-small","input":"The sentence to embed"}`}</pre>
        </article>
      </div>
      <section className="card">
        <div className="toolbar">
          <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter model or provider" aria-label="Filter usage" />
          <select aria-label="Kind" value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="">Chat and embeddings</option>
            <option value="chat">Chat</option>
            <option value="embedding">Embeddings</option>
          </select>
        </div>
        {(body?.events.length ?? 0) === 0 ? (
          <Empty title="No calls this month">A chat or embedding request with this key shows up here.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Kind</th>
                  <th>Model</th>
                  <th>Tokens</th>
                  <th>Cost</th>
                </tr>
              </thead>
              <tbody>
                {body?.events.map((event) => (
                  <tr key={event.id}>
                    <td>{formatWhen(event.createdAt)}</td>
                    <td><Pill tone={event.kind === "embedding" ? "info" : "ok"}>{event.kind}</Pill></td>
                    <td className="mono">{event.model}</td>
                    <td>{event.inputTokens.toLocaleString()} in · {event.outputTokens.toLocaleString()} out</td>
                    <td>{formatMoney(event.estimatedCost, 4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pager page={body?.page ?? EMPTY_PAGE} onOffset={setOffset} />
      </section>
    </Page>
  );
}

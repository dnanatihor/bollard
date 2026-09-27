import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { useSession } from "../session";
import { Empty, Loading, Page, Pill, formatDuration, formatMoney, formatWhen, statusTone } from "../ui";

interface Overview {
  requests: number;
  blocked: number;
  auditVerified: boolean;
  budget: { monthlyUsd: number; spentUsd: number } | null;
  providers: number;
  routes: number;
  inferenceKeys: number;
  traces: Array<{ traceId: string; model: string; status: string; durationMs: number; startedAt: string }>;
}

export function OverviewPage() {
  const me = useSession();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Overview>("/api/overview")
      .then(setData)
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load overview."));
  }, []);

  if (error) return <Page title="Overview"><p className="banner error" role="alert">{error}</p></Page>;
  if (!data) return <Loading label="Loading overview…" />;

  const cap = data.budget?.monthlyUsd ?? 0;
  const spent = data.budget?.spentUsd ?? 0;

  return (
    <Page title="Overview" lede={`Requests, keys, and spend for ${me.workspaceName}.`}>
      <div className="metric-grid">
        <Link className="card metric-card" to="/traces">
          <div className="muted">Requests</div>
          <div className="metric">{data.requests.toLocaleString()}</div>
        </Link>
        <Link className="card metric-card" to="/guardrails">
          <div className="muted">Guardrail blocks</div>
          <div className="metric">{data.blocked.toLocaleString()}</div>
        </Link>
        <Link className="card metric-card" to="/audit">
          <div className="muted">Audit chain</div>
          <div className="metric">{data.auditVerified ? "Intact" : "Check"}</div>
        </Link>
        <Link className="card metric-card" to="/budget">
          <div className="muted">Spent this month</div>
          <div className="metric">{data.budget ? formatMoney(spent) : "—"}</div>
          <div className="muted">{cap > 0 ? `of ${formatMoney(cap)} workspace budget` : "No workspace ceiling"}</div>
        </Link>
      </div>

      {data.requests === 0 ? (
        <section className="card checklist">
          <h2>Ready for the first call</h2>
          <ol>
            <li>
              <span>Connect a provider</span>
              {data.providers > 0 ? <Pill tone="ok">{data.providers} connected</Pill> : <Link to="/providers">Add a provider</Link>}
            </li>
            <li>
              <span>Publish a route</span>
              {data.routes > 0 ? <Pill tone="ok">{data.routes} routes</Pill> : <Link to="/routes">Add a route</Link>}
            </li>
            <li>
              <span>Issue an inference key</span>
              {data.inferenceKeys > 0 ? <Pill tone="ok">{data.inferenceKeys} active</Pill> : <Link to="/users">Open users</Link>}
            </li>
          </ol>
        </section>
      ) : null}

      <section className="card">
        <h2>Recent traces</h2>
        {data.traces.length === 0 ? (
          <Empty title="No calls yet">They appear here after a client sends a chat request with a gateway key.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Model</th>
                  <th>Status</th>
                  <th>Duration</th>
                </tr>
              </thead>
              <tbody>
                {data.traces.map((trace) => (
                  <tr key={trace.traceId}>
                    <td><Link to={`/traces/${trace.traceId}`}>{formatWhen(trace.startedAt)}</Link></td>
                    <td className="mono">{trace.model}</td>
                    <td><Pill tone={statusTone(trace.status)}>{trace.status}</Pill></td>
                    <td>{formatDuration(trace.durationMs)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </Page>
  );
}

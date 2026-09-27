import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";
import { Banner, Empty, Exchange, Loading, Page, Pill, formatDuration, statusTone } from "../ui";

interface Detail {
  trace: { traceId: string; requestId: string; model: string; status: string; errorCode: string; durationMs: number; userId: string };
  prompt: string;
  sent: string;
  response: string;
  spans: Array<{ name: string; offsetMs: number; durationMs: number; status: string; detail: string }>;
  guardrails: Array<{ phase: string; rule: string; action: string; reason: string }>;
}

export function TracePage() {
  const { traceId } = useParams();
  const [data, setData] = useState<Detail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!traceId) return;
    api<Detail>(`/api/traces/${traceId}`)
      .then(setData)
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load the trace."));
  }, [traceId]);

  if (error) {
    return (
      <Page title="Trace">
        <Link className="back" to="/traces">Back to traces</Link>
        <Banner tone="error">{error}</Banner>
      </Page>
    );
  }
  if (!data) return <Loading label="Loading trace…" />;

  const width = Math.max(...data.spans.map((span) => span.offsetMs + span.durationMs), 1);

  return (
    <Page title={data.trace.model || "Trace"} lede={data.trace.traceId}>
      <Link className="back" to="/traces">Back to traces</Link>
      <div className="trace-facts">
        <Pill tone={statusTone(data.trace.status)}>{data.trace.status}</Pill>
        <Pill tone="neutral">{data.trace.userId}</Pill>
        <Pill tone="neutral">{formatDuration(data.trace.durationMs)}</Pill>
        {data.trace.errorCode ? <Pill tone="bad">{data.trace.errorCode}</Pill> : null}
        <Pill tone="neutral">{data.trace.requestId}</Pill>
      </div>
      <section className="card" style={{ marginTop: "0.9rem" }}>
        <h2>What was sent</h2>
        <Exchange prompt={data.prompt} sent={data.sent} response={data.response} />
      </section>
      <section className="card">
        <h2>Spans</h2>
        {data.spans.length === 0 ? <Empty title="No spans">This trace finished before any span was recorded.</Empty> : null}
        {data.spans.map((span) => (
          <div className="waterfall-row" key={`${span.name}-${span.offsetMs}`}>
            <div>
              <div className="span-name">{span.name}</div>
              {span.detail ? <div className="muted">{span.detail}</div> : null}
            </div>
            <div className={span.status === "ok" ? "track" : "track error"} aria-hidden="true">
              <i style={{ width: `${Math.max(2, (span.durationMs / width) * 100)}%`, marginLeft: `${(span.offsetMs / width) * 100}%` }} />
            </div>
            <div>{formatDuration(span.durationMs)}</div>
          </div>
        ))}
      </section>
      <section className="card">
        <h2>Guardrail decisions</h2>
        {data.guardrails.length === 0 ? (
          <Empty title="No redaction or block">This call passed the rules that were enabled.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Phase</th>
                  <th>Rule</th>
                  <th>Action</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {data.guardrails.map((hit, index) => (
                  <tr key={`${hit.rule}-${index}`}>
                    <td>{hit.phase}</td>
                    <td>{hit.rule}</td>
                    <td><Pill tone={hit.action === "deny" ? "bad" : "warn"}>{hit.action}</Pill></td>
                    <td>{hit.reason}</td>
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

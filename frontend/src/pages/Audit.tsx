import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { EMPTY_PAGE, Pager, pageQuery, useDebounced, type PageMeta } from "../paging";
import { useSession } from "../session";
import { Banner, Empty, Exchange, Page, formatMoney, formatWhen } from "../ui";

interface EventRow {
  id: number;
  event: string;
  requestId: string;
  traceId: string;
  userId: string;
  userEmail: string;
  requestedModel: string;
  provider: string;
  inputTokens: number;
  outputTokens: number;
  estimatedCost: number;
  timestamp: string;
  prompt: string;
  sent: string;
  response: string;
}

export function AuditPage() {
  const me = useSession();
  const [verified, setVerified] = useState<boolean | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [page, setPage] = useState<PageMeta>(EMPTY_PAGE);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);

  const debounced = useDebounced(query);

  useEffect(() => {
    setOffset(0);
  }, [debounced]);

  useEffect(() => {
    api<{ verified: boolean; events: EventRow[]; page: PageMeta }>(`/api/audit?${pageQuery(offset, debounced)}`)
      .then((body) => {
        setVerified(body.verified);
        setEvents(body.events);
        setPage(body.page);
      })
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load audit."));
  }, [debounced, offset]);

  return (
    <Page
      title="Audit"
      lede={`The compliance record for ${me.workspaceName}: the prompt, the payload sent to the model, and the reply. The chain is hashed so a later edit is visible. Timing and steps for the same call are on Traces.`}
    >
      {error ? <Banner tone="error">{error}</Banner> : null}
      {verified === null ? null : (
        <Banner tone={verified ? "ok" : "error"}>
          {verified ? "Chain verified. Every event matches the previous hash, including the prompt and the reply." : "Chain check failed. An event was changed after it was written."}
        </Banner>
      )}
      <section className="card">
        <div className="toolbar">
          <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search a prompt, user, or model" aria-label="Search audit" />
          <span className="muted">{page.total.toLocaleString()} events</span>
        </div>
        {verified !== null && events.length === 0 ? (
          <Empty title={query ? "No events match" : "No audit events yet"}>{query ? "Try a phrase from a prompt, a user, or a model." : "The first chat call writes the prompt and the reply here."}</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Event</th>
                  <th>User</th>
                  <th>Model</th>
                  <th>Prompt</th>
                  <th>Cost</th>
                  <th className="actions"></th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <Fragment key={event.id}>
                    <tr>
                      <td>{formatWhen(event.timestamp)}</td>
                      <td>{event.event.replaceAll("_", " ").toLowerCase()}</td>
                      <td>{event.userEmail || event.userId}</td>
                      <td className="mono">{event.requestedModel}</td>
                      <td>{preview(event.prompt)}</td>
                      <td>{formatMoney(event.estimatedCost, 4)}</td>
                      <td className="actions">
                        <div className="row-actions">
                          <button className="ghost small" type="button" onClick={() => setOpenId(openId === event.id ? null : event.id)}>
                            {openId === event.id ? "Hide" : "Open"}
                          </button>
                          {event.traceId ? <Link to={`/traces/${event.traceId}`}>Trace</Link> : null}
                        </div>
                      </td>
                    </tr>
                    {openId === event.id ? (
                      <tr className="expand">
                        <td colSpan={7}>
                          <Exchange prompt={event.prompt} sent={event.sent} response={event.response} />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
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

function preview(value: string): string {
  const line = value.replace(/\s+/g, " ").trim();
  if (!line) return "—";
  return line.length > 72 ? `${line.slice(0, 72)}…` : line;
}

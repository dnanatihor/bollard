import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api";
import { EMPTY_PAGE, Pager, pageQuery, useDebounced, type PageMeta } from "../paging";
import { useSession } from "../session";
import { Banner, Empty, Loading, Page, Pill, formatMoney } from "../ui";

interface UserBudget {
  userId: string;
  email: string;
  displayName: string;
  status: string;
  monthlyUsd: number;
  spentUsd: number;
}

interface ModelUsage {
  model: string;
  kind: string;
  requests: number;
  inputTokens: number;
  outputTokens: number;
  spentUsd: number;
}

interface Leader {
  userId: string;
  email: string;
  displayName: string;
  requests: number;
  spentUsd: number;
  monthlyUsd: number;
}

interface BudgetBody {
  defaultMonthlyUsd: number;
  overallUsd: number;
  requestsPerMinute: number;
  cacheTtlSeconds: number;
  period: string;
  consumedUsd: number;
  usage: {
    requests: number;
    inputTokens: number;
    outputTokens: number;
    spentUsd: number;
    byKind: Array<{ kind: string; requests: number; spentUsd: number }>;
    byModel: ModelUsage[];
  };
  leaders: Leader[];
  users: UserBudget[];
  page: PageMeta;
}

export function BudgetPage() {
  const me = useSession();
  const canEdit = me.role === "admin";
  const [body, setBody] = useState<BudgetBody | null>(null);
  const [monthly, setMonthly] = useState("25");
  const [overall, setOverall] = useState("0");
  const [rpm, setRpm] = useState("60");
  const [cacheTtl, setCacheTtl] = useState("0");
  const [configCache, setConfigCache] = useState("0");
  const [caps, setCaps] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const debounced = useDebounced(query);

  async function load(nextOffset = offset, nextQuery = debounced) {
    const next = await api<BudgetBody>(`/api/budget?${pageQuery(nextOffset, nextQuery)}`);
    setBody(next);
    setMonthly(String(next.defaultMonthlyUsd));
    setOverall(String(next.overallUsd ?? 0));
    setRpm(String(next.requestsPerMinute));
    setCacheTtl(String(next.cacheTtlSeconds ?? 0));
    const config = await api<{ seconds: number }>("/api/settings/config-cache");
    setConfigCache(String(config.seconds));
    setCaps(Object.fromEntries(next.users.map((user) => [user.userId, String(user.monthlyUsd)])));
  }

  useEffect(() => {
    setOffset(0);
  }, [debounced]);

  useEffect(() => {
    load().catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load budgets."));
  }, [debounced, offset]);

  async function saveDefaults(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSaved("");
    setBusy("defaults");
    try {
      await api("/api/budget", {
        method: "PUT",
        body: JSON.stringify({ monthlyUsd: Number(monthly), overallUsd: Number(overall) }),
      });
      await api("/api/settings/rate-limit", { method: "PUT", body: JSON.stringify({ requestsPerMinute: Number(rpm) }) });
      await api("/api/settings/cache", { method: "PUT", body: JSON.stringify({ ttlSeconds: Number(cacheTtl) }) });
      await api("/api/settings/config-cache", { method: "PUT", body: JSON.stringify({ seconds: Number(configCache) }) });
      setSaved("Saved. The workspace ceiling is separate from each person’s cap. New people start at the default cap.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save.");
    } finally {
      setBusy("");
    }
  }

  async function saveUser(user: UserBudget) {
    setError("");
    setSaved("");
    setBusy(user.userId);
    try {
      await api(`/api/users/${user.userId}/budget`, {
        method: "PUT",
        body: JSON.stringify({ monthlyUsd: Number(caps[user.userId]) }),
      });
      setSaved(`Saved the cap for ${user.displayName}.`);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save the cap.");
    } finally {
      setBusy("");
    }
  }

  if (!body && !error) return <Loading label="Loading budgets…" />;

  const users = body?.users ?? [];
  const ceiling = body?.overallUsd ?? 0;
  const consumed = body?.consumedUsd ?? 0;
  const remaining = ceiling > 0 ? Math.max(ceiling - consumed, 0) : 0;

  return (
    <Page
      title="Budget"
      lede={`Spend for people in ${me.workspaceName}, recorded per call for ${body?.period ?? "this month"} and reset with the calendar month. Chat and embeddings both count.`}
    >
      {error ? <Banner tone="error">{error}</Banner> : null}
      {saved ? <Banner tone="ok">{saved}</Banner> : null}
      <section className="budget-hero">
        <article className="card donut-card">
          <Donut spent={consumed} cap={ceiling} />
          <div>
            <div className="muted">Used this month</div>
            <div className="metric">{ceiling > 0 ? `${Math.round((Math.min(consumed, ceiling) / ceiling) * 100)}%` : "—"}</div>
          </div>
        </article>
        <article className="card">
          <div className="metric-grid budget-metrics">
            <div>
              <div className="muted">Workspace budget</div>
              <div className="metric">{ceiling > 0 ? formatMoney(ceiling) : "No ceiling"}</div>
            </div>
            <div>
              <div className="muted">Consumed</div>
              <div className="metric">{formatMoney(consumed)}</div>
            </div>
            <div>
              <div className="muted">Remaining</div>
              <div className="metric">{ceiling > 0 ? formatMoney(remaining) : "—"}</div>
            </div>
          </div>
          <div className="legend">
            <span><i className="swatch cap" /> Cap</span>
            <span><i className="swatch spent" /> Consumed</span>
          </div>
          {users.length === 0 && (body?.leaders.length ?? 0) === 0 ? (
            <Empty title="No consumption yet">A chat or embedding call adds spend here.</Empty>
          ) : (
            <ConsumptionChart users={body?.leaders ?? []} />
          )}
        </article>
      </section>
      {canEdit ? (
      <form className="card budget-card" onSubmit={(event) => void saveDefaults(event)}>
        <h2>Defaults</h2>
        <p className="hint">The workspace budget is one ceiling for spend this month. Each person still has their own cap. Those caps do not have to add up to the workspace number. Zero means there is no workspace ceiling.</p>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="overall">Workspace budget, USD</label>
            <input id="overall" inputMode="decimal" value={overall} onChange={(event) => setOverall(event.target.value)} />
            <p className="hint">For example 3000. Calls stop when this month’s spend reaches it, even if a person still has room in their own cap.</p>
          </div>
          <div className="field">
            <label htmlFor="monthly">Default monthly cap, USD</label>
            <input id="monthly" inputMode="decimal" value={monthly} onChange={(event) => setMonthly(event.target.value)} />
            <p className="hint">Starting cap for a person you create after saving. Change a person below to give them a different cap.</p>
          </div>
          <div className="field">
            <label htmlFor="rpm">Shared requests per minute</label>
            <input id="rpm" inputMode="numeric" value={rpm} onChange={(event) => setRpm(event.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="cache-ttl">Exact cache, seconds</label>
            <input id="cache-ttl" inputMode="numeric" value={cacheTtl} onChange={(event) => setCacheTtl(event.target.value)} />
            <p className="hint">Zero turns the cache off. A repeat of the same guarded prompt skips the provider until the TTL ends. Tool calls are not cached.</p>
          </div>
          <div className="field">
            <label htmlFor="config-cache">Route resolve cache, seconds</label>
            <input id="config-cache" inputMode="numeric" value={configCache} onChange={(event) => setConfigCache(event.target.value)} />
            <p className="hint">Caches direct and least-cost route choices. Zero reads the database on every call. Round robin, canary, region, and latency stay per request.</p>
          </div>
        </div>
        <div className="modal-actions">
          <button className="primary" type="submit" disabled={busy === "defaults"}>{busy === "defaults" ? "Saving…" : "Save defaults"}</button>
        </div>
      </form>
      ) : null}
      <section className="card">
        <h2>This month by model</h2>
        {(body?.usage.byModel.length ?? 0) === 0 ? (
          <Empty title="No model spend yet">Chat and embedding calls land here with their token counts.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Kind</th>
                  <th>Requests</th>
                  <th>Tokens</th>
                  <th>Cost</th>
                </tr>
              </thead>
              <tbody>
                {body?.usage.byModel.map((row) => (
                  <tr key={`${row.kind}-${row.model}`}>
                    <td className="mono">{row.model}</td>
                    <td><Pill tone={row.kind === "embedding" ? "info" : "ok"}>{row.kind}</Pill></td>
                    <td>{row.requests.toLocaleString()}</td>
                    <td>{row.inputTokens.toLocaleString()} in · {row.outputTokens.toLocaleString()} out</td>
                    <td>{formatMoney(row.spentUsd, 4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="card">
        <div className="toolbar">
          <h2>People</h2>
          <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search a person" aria-label="Search people" />
        </div>
        {!body || body.users.length === 0 ? (
          <Empty title="No people yet">Create a user and they receive the default cap.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Person</th>
                  <th>Workspace</th>
                  <th>Spent</th>
                  <th className="actions">Cap, USD</th>
                </tr>
              </thead>
              <tbody>
                {body.users.map((user) => {
                  const cap = Number(caps[user.userId]) || 0;
                  const pct = cap > 0 ? Math.min(100, (user.spentUsd / cap) * 100) : user.spentUsd > 0 ? 100 : 0;
                  const over = cap > 0 && user.spentUsd > cap;
                  return (
                    <tr key={user.userId}>
                      <td>
                        <strong>{user.displayName}</strong>
                        <span className="muted">{user.email}</span>
                        <div className={over ? "meter slim over" : "meter slim"} aria-hidden="true">
                          <i style={{ width: `${pct}%` }} />
                        </div>
                      </td>
                      <td><Pill tone="info">{me.workspaceName}</Pill></td>
                      <td>
                        {formatMoney(user.spentUsd)}
                        <span className="muted">{cap > 0 ? `${formatMoney(Math.max(cap - user.spentUsd, 0))} left` : "No room left"}</span>
                      </td>
                      <td className="actions">
                        {canEdit ? (
                          <div className="inline-actions">
                            <input
                              aria-label={`Monthly cap for ${user.displayName}`}
                              inputMode="decimal"
                              value={caps[user.userId] ?? ""}
                              onChange={(event) => setCaps({ ...caps, [user.userId]: event.target.value })}
                            />
                            <button className="primary small" type="button" disabled={busy === user.userId} onClick={() => void saveUser(user)}>
                              {busy === user.userId ? "Saving…" : "Save"}
                            </button>
                          </div>
                        ) : (
                          formatMoney(user.monthlyUsd)
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <Pager page={body?.page ?? EMPTY_PAGE} onOffset={setOffset} />
      </section>
    </Page>
  );
}

function Donut({ spent, cap }: { spent: number; cap: number }) {
  const ratio = cap > 0 ? Math.min(spent / cap, 1) : 0;
  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  const dash = ratio * circumference;
  return (
    <svg className="donut" viewBox="0 0 120 120" role="img" aria-label={`${formatMoney(spent)} consumed of ${formatMoney(cap)}`}>
      <circle className="donut-track" cx="60" cy="60" r={radius} />
      <circle className="donut-value" cx="60" cy="60" r={radius} strokeDasharray={`${dash} ${circumference - dash}`} />
    </svg>
  );
}

function ConsumptionChart({ users }: { users: Leader[] }) {
  const scale = Math.max(...users.map((user) => Math.max(user.monthlyUsd, user.spentUsd)), 1);
  return (
    <div className="bars">
      {users.map((user) => {
        const over = user.monthlyUsd > 0 && user.spentUsd > user.monthlyUsd;
        return (
          <div className="bar-row" key={user.userId}>
            <div className="bar-name" title={user.email}>{user.displayName}</div>
            <div className="bar-track" aria-hidden="true">
              <i className="bar-cap" style={{ width: `${(user.monthlyUsd / scale) * 100}%` }} />
              <i className={over ? "bar-spent over" : "bar-spent"} style={{ width: `${(Math.min(user.spentUsd, scale) / scale) * 100}%` }} />
            </div>
            <div className="bar-value">{formatMoney(user.spentUsd)}</div>
          </div>
        );
      })}
    </div>
  );
}

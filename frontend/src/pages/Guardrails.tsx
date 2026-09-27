import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { EMPTY_PAGE, Pager, pageQuery, useDebounced, type PageMeta } from "../paging";
import { useSession } from "../session";
import { Banner, Empty, Page, Pill, Switch, formatWhen } from "../ui";

interface Rule {
  id: string;
  name: string;
  kind: string;
  phase: string;
  enabled: boolean;
  routeId?: string | null;
  config: Record<string, unknown>;
}

interface Hit {
  id: number;
  traceId: string;
  phase: string;
  rule: string;
  action: string;
  reason: string;
  createdAt: string;
}

export function GuardrailsPage() {
  const me = useSession();
  const canEdit = me.role === "admin";
  const [rules, setRules] = useState<Rule[]>([]);
  const [hits, setHits] = useState<Hit[]>([]);
  const [rulePage, setRulePage] = useState<PageMeta>(EMPTY_PAGE);
  const [hitPage, setHitPage] = useState<PageMeta>(EMPTY_PAGE);
  const [query, setQuery] = useState("");
  const [hitQuery, setHitQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [hitOffset, setHitOffset] = useState(0);
  const [error, setError] = useState("");
  const debounced = useDebounced(query);
  const debouncedHits = useDebounced(hitQuery);

  async function load() {
    const [ruleBody, hitBody] = await Promise.all([
      api<{ rules: Rule[]; page: PageMeta }>(`/api/guardrails?${pageQuery(offset, debounced)}`),
      api<{ hits: Hit[]; page: PageMeta }>(`/api/guardrail-hits?${pageQuery(hitOffset, debouncedHits)}`),
    ]);
    setRules(ruleBody.rules);
    setRulePage(ruleBody.page);
    setHits(hitBody.hits);
    setHitPage(hitBody.page);
  }

  useEffect(() => {
    setOffset(0);
  }, [debounced]);

  useEffect(() => {
    setHitOffset(0);
  }, [debouncedHits]);

  useEffect(() => {
    load().catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load guardrails."));
  }, [debounced, debouncedHits, offset, hitOffset]);

  async function save(rule: Rule, config: Record<string, unknown>, enabled = rule.enabled) {
    setError("");
    try {
      await api(`/api/guardrails/${rule.id}`, { method: "PATCH", body: JSON.stringify({ enabled, config }) });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update the rule.");
    }
  }

  return (
    <Page title="Guardrails" lede="Input is checked before the provider. Output is checked before the client sees the reply. The prompt and the reply are stored on the audit event.">
      {error ? <Banner tone="error">{error}</Banner> : null}
      <div className="toolbar">
        <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search a rule" aria-label="Search guardrails" />
      </div>
      <div className="rule-grid">
        {rules.map((rule) => (
          <RuleCard key={rule.id} rule={rule} canEdit={canEdit} onSave={save} />
        ))}
      </div>
      <Pager page={rulePage} onOffset={setOffset} />
      <section className="card" style={{ marginTop: "0.9rem" }}>
        <div className="toolbar">
          <h2>Recent decisions</h2>
          <input className="search" value={hitQuery} onChange={(event) => setHitQuery(event.target.value)} placeholder="Search rule or reason" aria-label="Search decisions" />
        </div>
        {hits.length === 0 ? (
          <Empty title="No blocks or redactions yet">Allowed checks stay on the trace. This list is only for deny and redact.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Rule</th>
                  <th>Phase</th>
                  <th>Action</th>
                  <th>Reason</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {hits.map((hit) => (
                  <tr key={hit.id}>
                    <td>{formatWhen(hit.createdAt)}</td>
                    <td>{hit.rule}</td>
                    <td>{hit.phase}</td>
                    <td><Pill tone={hit.action === "deny" ? "bad" : "warn"}>{hit.action}</Pill></td>
                    <td>{hit.reason}</td>
                    <td><Link to={`/traces/${hit.traceId}`}>Trace</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pager page={hitPage} onOffset={setHitOffset} />
      </section>
    </Page>
  );
}

function RuleCard({
  rule,
  canEdit,
  onSave,
}: {
  rule: Rule;
  canEdit: boolean;
  onSave: (rule: Rule, config: Record<string, unknown>, enabled?: boolean) => Promise<void>;
}) {
  const [words, setWords] = useState(listValue(rule, "words"));
  const [terms, setTerms] = useState(listValue(rule, "terms"));
  const [maxChars, setMaxChars] = useState(String(rule.config.maxChars ?? 100000));

  return (
    <section className="card rule-card">
      <div className="rule-top">
        <div>
          <h2>{rule.name}</h2>
          <p className="muted">{rule.phase} · {rule.routeId || "All routes"}</p>
        </div>
        {canEdit ? (
          <Switch
            checked={rule.enabled}
            label={`${rule.enabled ? "Disable" : "Enable"} ${rule.name}`}
            onChange={(enabled) => void onSave(rule, rule.config, enabled)}
          />
        ) : (
          <Pill tone={rule.enabled ? "ok" : "neutral"}>{rule.enabled ? "Enabled" : "Off"}</Pill>
        )}
      </div>
      {canEdit && rule.kind === "blocked-words" ? (
        <>
          <div className="field">
            <label htmlFor={`${rule.id}-words`}>Blocked words, one per line</label>
            <textarea id={`${rule.id}-words`} value={words} onChange={(event) => setWords(event.target.value)} />
          </div>
          <div className="modal-actions">
            <button className="primary small" type="button" onClick={() => void onSave(rule, { words: splitLines(words) })}>Save words</button>
          </div>
        </>
      ) : null}
      {canEdit && rule.kind === "toxicity" ? (
        <>
          <div className="field">
            <label htmlFor={`${rule.id}-terms`}>Blocked phrases, one per line</label>
            <textarea id={`${rule.id}-terms`} value={terms} onChange={(event) => setTerms(event.target.value)} />
          </div>
          <div className="modal-actions">
            <button className="primary small" type="button" onClick={() => void onSave(rule, { terms: splitLines(terms) })}>Save phrases</button>
          </div>
        </>
      ) : null}
      {canEdit && rule.kind === "max-input" ? (
        <>
          <div className="field">
            <label htmlFor={`${rule.id}-max`}>Maximum characters</label>
            <input id={`${rule.id}-max`} value={maxChars} onChange={(event) => setMaxChars(event.target.value)} />
          </div>
          <div className="modal-actions">
            <button className="primary small" type="button" onClick={() => void onSave(rule, { maxChars: Number(maxChars) })}>Save limit</button>
          </div>
        </>
      ) : null}
    </section>
  );
}

function listValue(rule: Rule, key: string): string {
  const value = rule.config[key];
  return Array.isArray(value) ? value.filter((item) => typeof item === "string").join("\n") : "";
}

function splitLines(value: string): string[] {
  return value.split("\n").map((line) => line.trim()).filter(Boolean);
}

import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api";
import { EMPTY_PAGE, Pager, pageQuery, useDebounced, type PageMeta } from "../paging";
import { Banner, Empty, Modal, Page, Pill, Switch } from "../ui";

interface Provider {
  id: string;
  type: string;
  baseUrl: string | null;
  region: string | null;
  enabled: boolean;
  hasSecret: boolean;
}

const TYPES = ["openai", "openai-compatible", "anthropic", "gemini", "azure-openai", "bedrock"];

const TYPE_HELP: Record<string, string> = {
  openai: "The official OpenAI API. Add a base URL only if you send traffic through a proxy.",
  "openai-compatible": "A host that speaks the OpenAI chat API. The base URL is required.",
  anthropic: "The Anthropic Messages API.",
  gemini: "The Gemini generateContent API.",
  "azure-openai": "The base URL is your Azure OpenAI resource endpoint.",
  bedrock: "The region is the AWS region, such as us-east-1. The secret is a Bedrock bearer token.",
};

const EMPTY = { id: "", type: "openai", baseUrl: "", region: "", secret: "" };

export function ProvidersPage() {
  const [rows, setRows] = useState<Provider[]>([]);
  const [page, setPage] = useState<PageMeta>(EMPTY_PAGE);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState(EMPTY);

  const debounced = useDebounced(query);

  async function load(nextOffset = offset) {
    const body = await api<{ providers: Provider[]; page: PageMeta }>(`/api/providers?${pageQuery(nextOffset, debounced)}`);
    setRows(body.providers);
    setPage(body.page);
  }

  useEffect(() => {
    setOffset(0);
  }, [debounced]);

  useEffect(() => {
    load().catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load providers."));
  }, [debounced, offset]);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await api("/api/providers", {
        method: "POST",
        body: JSON.stringify({
          id: form.id,
          type: form.type,
          baseUrl: form.baseUrl || null,
          region: form.region || null,
          secret: form.secret || null,
        }),
      });
      setForm(EMPTY);
      setOpen(false);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save the provider.");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(provider: Provider, enabled: boolean) {
    setError("");
    try {
      await api(`/api/providers/${provider.id}`, { method: "PATCH", body: JSON.stringify({ enabled }) });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update the provider.");
    }
  }

  const showBase = form.type !== "bedrock";
  const showRegion = form.type === "bedrock";

  return (
    <Page
      title="Providers"
      lede="Vendor accounts the gateway calls. A secret is encrypted on save and is never shown again."
      actions={<button className="primary" type="button" onClick={() => { setError(""); setOpen(true); }}>Add provider</button>}
    >
      {error && !open ? <Banner tone="error">{error}</Banner> : null}
      <div className="toolbar">
        <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search id or type" aria-label="Search providers" />
      </div>
      {rows.length === 0 ? (
        <section className="card">
          <Empty title="No providers yet">Add the first vendor account. Models and routes attach to it.</Empty>
        </section>
      ) : (
        <div className="provider-grid">
          {rows.map((provider) => (
            <article className="card provider-card" key={provider.id}>
              <div className="provider-top">
                <div>
                  <strong>{provider.id}</strong>
                  <span className="muted">{provider.baseUrl || provider.region || "Default endpoint"}</span>
                </div>
                <Switch
                  checked={provider.enabled}
                  label={`${provider.enabled ? "Disable" : "Enable"} ${provider.id}`}
                  onChange={(enabled) => void toggle(provider, enabled)}
                />
              </div>
              <div className="provider-meta">
                <Pill tone="neutral">{provider.type}</Pill>
                <Pill tone={provider.hasSecret ? "ok" : "warn"}>{provider.hasSecret ? "Secret stored" : "Secret missing"}</Pill>
              </div>
            </article>
          ))}
        </div>
      )}
      <Pager page={page} onOffset={setOffset} />

      <Modal open={open} title="Add provider" lede={TYPE_HELP[form.type]} onClose={() => setOpen(false)}>
        <form onSubmit={(event) => void create(event)}>
          {error ? <Banner tone="error">{error}</Banner> : null}
          <div className="field">
            <label htmlFor="pid">Id</label>
            <input id="pid" required autoFocus value={form.id} onChange={(event) => setForm({ ...form, id: event.target.value })} placeholder="openai" />
          </div>
          <div className="field">
            <label htmlFor="ptype">Type</label>
            <select id="ptype" value={form.type} onChange={(event) => setForm({ ...form, type: event.target.value })}>
              {TYPES.map((type) => <option key={type}>{type}</option>)}
            </select>
          </div>
          {showBase ? (
            <div className="field">
              <label htmlFor="base">Base URL</label>
              <input id="base" value={form.baseUrl} onChange={(event) => setForm({ ...form, baseUrl: event.target.value })} placeholder="https://api.example.com" />
            </div>
          ) : null}
          {showRegion ? (
            <div className="field">
              <label htmlFor="region">Region</label>
              <input id="region" value={form.region} onChange={(event) => setForm({ ...form, region: event.target.value })} placeholder="us-east-1" />
            </div>
          ) : null}
          <div className="field">
            <label htmlFor="secret">API key</label>
            <input id="secret" type="password" value={form.secret} onChange={(event) => setForm({ ...form, secret: event.target.value })} autoComplete="off" />
          </div>
          <div className="modal-actions">
            <button className="ghost" type="button" onClick={() => setOpen(false)}>Cancel</button>
            <button className="primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Save provider"}</button>
          </div>
        </form>
      </Modal>
    </Page>
  );
}

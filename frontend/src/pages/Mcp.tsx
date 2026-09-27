import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api";
import { EMPTY_PAGE, Pager, pageQuery, useDebounced, type PageMeta } from "../paging";
import { Banner, Empty, Loading, Page } from "../ui";

interface ServerRow {
  id: string;
  name: string;
  url: string;
  authType: string;
  enabled: boolean;
  headerNames?: string[];
  oauthConnected?: boolean;
}

interface GrantRow {
  id: string;
  serverId: string;
  userId: string;
  tools: string[];
}

interface UserRow {
  id: string;
  email: string;
  displayName: string;
}

const EMPTY = {
  id: "",
  name: "",
  url: "",
  authType: "none",
  secret: "",
  headers: [{ name: "", value: "" }],
  authorizeUrl: "",
  tokenUrl: "",
  oauthClientId: "",
  oauthClientSecret: "",
  oauthScopes: "",
};

export function McpPage() {
  const [servers, setServers] = useState<ServerRow[]>([]);
  const [grants, setGrants] = useState<GrantRow[]>([]);
  const [users, setUsers] = useState<UserRow[]>([]);
  const [form, setForm] = useState(EMPTY);
  const [grantUser, setGrantUser] = useState("");
  const [grantTools, setGrantTools] = useState<Record<string, string>>({});
  const [toolNames, setToolNames] = useState<Record<string, string[]>>({});
  const [toolError, setToolError] = useState<Record<string, string>>({});
  const [page, setPage] = useState<PageMeta>(EMPTY_PAGE);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const debounced = useDebounced(query);

  async function load(nextOffset = offset) {
    const [serverBody, userBody] = await Promise.all([
      api<{ servers: ServerRow[]; grants: GrantRow[]; page: PageMeta }>(`/api/mcp/servers?${pageQuery(nextOffset, debounced)}`),
      api<{ users: UserRow[] }>("/api/users?limit=100"),
    ]);
    setServers(serverBody.servers);
    setGrants(serverBody.grants);
    setPage(serverBody.page);
    setUsers(userBody.users);
    setGrantUser((current) => current || userBody.users[0]?.id || "");
    await loadTools(serverBody.servers.map((server) => server.id));
  }

  async function loadTools(ids: string[]) {
    const entries = await Promise.all(
      ids.map(async (id) => {
        try {
          const body = await api<{ tools: string[] }>(`/api/mcp/servers/${id}/tools`, { method: "POST" });
          return [id, body.tools, ""] as const;
        } catch (cause) {
          const message = cause instanceof Error ? cause.message : "Could not list tools.";
          return [id, [] as string[], message] as const;
        }
      }),
    );
    setToolNames(Object.fromEntries(entries.map(([id, tools]) => [id, tools])));
    setToolError(Object.fromEntries(entries.map(([id, , message]) => [id, message])));
  }

  useEffect(() => {
    setOffset(0);
  }, [debounced]);

  useEffect(() => {
    load()
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load MCP servers."))
      .finally(() => setLoading(false));
  }, [debounced, offset]);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSaved("");
    setBusy(true);
    try {
      await api("/api/mcp/servers", {
        method: "POST",
        body: JSON.stringify({
          id: form.id,
          name: form.name,
          url: form.url,
          authType: form.authType,
          secret: form.authType === "bearer" ? form.secret || null : null,
          headers: form.authType === "custom" ? form.headers.filter((header) => header.name.trim() || header.value.trim()) : [],
          authorizeUrl: form.authType === "oauth" ? form.authorizeUrl || null : null,
          tokenUrl: form.authType === "oauth" ? form.tokenUrl || null : null,
          oauthClientId: form.authType === "oauth" ? form.oauthClientId || null : null,
          oauthClientSecret: form.authType === "oauth" ? form.oauthClientSecret || null : null,
          oauthScopes: form.authType === "oauth" ? form.oauthScopes || null : null,
        }),
      });
      setForm(EMPTY);
      setSaved("Server registered. Grant a person before their key can call it.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not register the server.");
    } finally {
      setBusy(false);
    }
  }

  async function grant(serverId: string) {
    setError("");
    setSaved("");
    const tools = (grantTools[serverId] || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    try {
      await api(`/api/mcp/servers/${serverId}/grants`, {
        method: "POST",
        body: JSON.stringify({ userId: grantUser, tools }),
      });
      setSaved("Grant saved. An empty tool list allows every tool on that server.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save the grant.");
    }
  }

  async function remove(serverId: string) {
    setError("");
    await api(`/api/mcp/servers/${serverId}`, { method: "DELETE" });
    await load();
  }

  if (loading) return <Loading label="Loading MCP servers…" />;

  return (
    <Page
      title="MCP"
      lede="Agents call this gateway. Upstream auth is none, a bearer secret, OAuth, or up to five custom headers. The gateway checks the person’s tool grant and writes the tool call to the audit log. Clients post JSON-RPC to /mcp/{server id}."
    >
      {error ? <Banner tone="error">{error}</Banner> : null}
      {saved ? <Banner tone="ok">{saved}</Banner> : null}
      <form className="card" onSubmit={(event) => void create(event)}>
        <h2>Register a server</h2>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="mcp-id">Server id</label>
            <input id="mcp-id" required value={form.id} onChange={(event) => setForm({ ...form, id: event.target.value })} placeholder="orders" />
          </div>
          <div className="field">
            <label htmlFor="mcp-name">Name</label>
            <input id="mcp-name" required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
          </div>
        </div>
        <div className="field">
          <label htmlFor="mcp-url">Upstream URL</label>
          <input id="mcp-url" required value={form.url} onChange={(event) => setForm({ ...form, url: event.target.value })} placeholder="https://mcp.example/rpc" />
        </div>
        <div className="field">
          <label htmlFor="mcp-auth">Upstream auth</label>
          <select
            id="mcp-auth"
            value={form.authType}
            onChange={(event) => {
              const authType = event.target.value;
              setForm({
                ...form,
                authType,
                headers: authType === "custom" && form.headers.length === 0 ? [{ name: "", value: "" }] : form.headers,
              });
            }}
          >
            <option value="none">none</option>
            <option value="bearer">bearer</option>
            <option value="oauth">oauth</option>
            <option value="custom">custom</option>
          </select>
        </div>
        {form.authType === "bearer" ? (
          <div className="field">
            <label htmlFor="mcp-secret">Bearer secret</label>
            <input id="mcp-secret" type="password" required value={form.secret} onChange={(event) => setForm({ ...form, secret: event.target.value })} />
          </div>
        ) : null}
        {form.authType === "custom" ? (
          <div className="field">
            <label>Custom headers</label>
            <p className="hint">Up to five. Each needs a name and a value. Values are encrypted and are not shown again.</p>
            {form.headers.map((header, index) => (
              <div className="header-row" key={index}>
                <input
                  aria-label={`Header ${index + 1} name`}
                  placeholder="X-Header-Name"
                  required
                  value={header.name}
                  onChange={(event) => {
                    const headers = form.headers.map((row, rowIndex) => rowIndex === index ? { ...row, name: event.target.value } : row);
                    setForm({ ...form, headers });
                  }}
                />
                <input
                  aria-label={`Header ${index + 1} value`}
                  type="password"
                  placeholder="value"
                  required
                  value={header.value}
                  onChange={(event) => {
                    const headers = form.headers.map((row, rowIndex) => rowIndex === index ? { ...row, value: event.target.value } : row);
                    setForm({ ...form, headers });
                  }}
                />
                <button
                  className="ghost small"
                  type="button"
                  onClick={() => {
                    const headers = form.headers.filter((_, rowIndex) => rowIndex !== index);
                    setForm({ ...form, headers: headers.length ? headers : [{ name: "", value: "" }] });
                  }}
                >
                  Remove
                </button>
              </div>
            ))}
            {form.headers.length < 5 ? (
              <button className="ghost small" type="button" onClick={() => setForm({ ...form, headers: [...form.headers, { name: "", value: "" }] })}>
                Add header
              </button>
            ) : (
              <p className="hint">Five headers is the limit.</p>
            )}
          </div>
        ) : null}
        {form.authType === "oauth" ? (
          <div className="form-grid">
            <div className="field">
              <label htmlFor="mcp-authorize">Authorize URL</label>
              <input id="mcp-authorize" required value={form.authorizeUrl} onChange={(event) => setForm({ ...form, authorizeUrl: event.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="mcp-token">Token URL</label>
              <input id="mcp-token" required value={form.tokenUrl} onChange={(event) => setForm({ ...form, tokenUrl: event.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="mcp-client">Client id</label>
              <input id="mcp-client" required value={form.oauthClientId} onChange={(event) => setForm({ ...form, oauthClientId: event.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="mcp-client-secret">Client secret</label>
              <input id="mcp-client-secret" type="password" required value={form.oauthClientSecret} onChange={(event) => setForm({ ...form, oauthClientSecret: event.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="mcp-scopes">Scopes</label>
              <input id="mcp-scopes" value={form.oauthScopes} onChange={(event) => setForm({ ...form, oauthScopes: event.target.value })} />
            </div>
          </div>
        ) : null}
        <div className="modal-actions">
          <button className="primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Register server"}</button>
        </div>
      </form>
      <section className="card">
        <div className="toolbar">
          <h2>Servers</h2>
          <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search name or URL" aria-label="Search MCP servers" />
        </div>
        {servers.length === 0 ? (
          <Empty title="No MCP servers">Register the upstream server, then grant people the tools they may call.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Server</th>
                  <th>Grant</th>
                  <th className="actions"></th>
                </tr>
              </thead>
              <tbody>
                {servers.map((server) => {
                  const allowed = grants.filter((grant) => grant.serverId === server.id);
                  return (
                    <tr key={server.id}>
                      <td>
                        <strong>{server.name}</strong>
                        <span className="muted mono">{server.id}</span>
                        <span className="muted">{server.url}</span>
                        <span className="muted">{allowed.length} {allowed.length === 1 ? "grant" : "grants"}</span>
                        <span className="muted">{server.authType}</span>
                        {server.authType === "oauth" ? <span className="muted">{server.oauthConnected ? "OAuth connected" : "OAuth not connected"}</span> : null}
                        {server.authType === "custom" && server.headerNames?.length ? <span className="muted">Headers: {server.headerNames.join(", ")}</span> : null}
                        {toolError[server.id] ? <span className="muted">{toolError[server.id]}</span> : null}
                        {toolNames[server.id] ? (
                          <span className="muted">{toolNames[server.id].length ? toolNames[server.id].join(", ") : "No tools returned."}</span>
                        ) : null}
                      </td>
                      <td>
                        <div className="stack-fields">
                          <select aria-label={`Person for ${server.name}`} value={grantUser} onChange={(event) => setGrantUser(event.target.value)}>
                            {users.map((user) => <option key={user.id} value={user.id}>{user.displayName}</option>)}
                          </select>
                          <input
                            aria-label={`Tools for ${server.name}`}
                            placeholder="lookup, search — empty allows all"
                            value={grantTools[server.id] ?? ""}
                            onChange={(event) => setGrantTools({ ...grantTools, [server.id]: event.target.value })}
                          />
                        </div>
                      </td>
                      <td className="actions">
                        <div className="row-actions">
                          {server.authType === "oauth" ? (
                            <button
                              className="ghost small"
                              type="button"
                              onClick={() => {
                                void api<{ url: string }>(`/api/mcp/servers/${server.id}/oauth/start`).then((body) => {
                                  window.location.assign(body.url);
                                });
                              }}
                            >
                              Connect
                            </button>
                          ) : null}
                          <button className="primary small" type="button" onClick={() => void grant(server.id)}>Grant</button>
                          <button className="ghost small" type="button" onClick={() => void remove(server.id)}>Remove</button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <Pager page={page} onOffset={setOffset} />
      </section>
    </Page>
  );
}

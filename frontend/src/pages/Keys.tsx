import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { EMPTY_PAGE, Pager, pageQuery, useDebounced, type PageMeta } from "../paging";
import { useSession } from "../session";
import { Banner, Confirm, Empty, Modal, Page, Pill, SecretReveal, Tabs, formatWhen, roleTone, statusTone } from "../ui";

interface KeyRow {
  id: string;
  prefix: string;
  userEmail: string;
  applicationId: string;
  workspaceName: string;
  role: string;
  status: string;
  expiresAt: string | null;
  lastUsedAt: string | null;
}

export function KeysPage() {
  const me = useSession();
  const [rows, setRows] = useState<KeyRow[]>([]);
  const [page, setPage] = useState<PageMeta>(EMPTY_PAGE);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState<string>("");
  const [pending, setPending] = useState<KeyRow | null>(null);

  const debounced = useDebounced(query);

  async function load(nextOffset = offset) {
    const body = await api<{ keys: KeyRow[]; page: PageMeta }>(
      `/api/keys?${pageQuery(nextOffset, debounced, { status: filter === "all" ? "" : filter })}`,
    );
    setRows(body.keys);
    setPage(body.page);
  }

  useEffect(() => {
    setOffset(0);
  }, [debounced, filter]);

  useEffect(() => {
    load().catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load keys."));
  }, [debounced, filter, offset]);

  async function revoke(key: KeyRow) {
    setError("");
    setBusy(true);
    try {
      await api(`/api/keys/${key.id}/revoke`, { method: "POST" });
      setPending(null);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not revoke the key.");
      setPending(null);
    } finally {
      setBusy(false);
    }
  }

  async function rotate(key: KeyRow) {
    setError("");
    setIssued("");
    setBusy(true);
    try {
      const body = await api<{ plaintext: string }>(`/api/keys/${key.id}/rotate`, { method: "POST" });
      setIssued(body.plaintext);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not rotate the key.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page
      title="API keys"
      lede={`Each key is issued to a person in ${me.workspaceName} and is allowed until the date chosen for it. After that date the key is rejected. Rotate replaces the secret. Revoke stops that key immediately.`}
      actions={<Link className="primary" to="/users">Issue from Users</Link>}
    >
      {error ? <Banner tone="error">{error}</Banner> : null}
      <section className="card">
        <div className="toolbar">
          <Tabs
            value={filter}
            onChange={setFilter}
            options={[
              ["all", "All"],
              ["active", "Active"],
              ["revoked", "Revoked"],
            ]}
          />
          <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search email or prefix" aria-label="Search keys" />
        </div>
        {rows.length === 0 ? (
          <Empty title={query || filter !== "all" ? "No keys in this view" : "No keys yet"}>
            {query || filter !== "all" ? "Clear the search or switch the status filter." : <><Link to="/users">Open Users</Link> and issue a key for a person.</>}
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>User</th>
                  <th>Workspace</th>
                  <th>Role</th>
                  <th>Prefix</th>
                  <th>Status</th>
                  <th>Expires</th>
                  <th>Last used</th>
                  <th className="actions">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((key) => (
                  <tr key={key.id}>
                    <td>
                      <strong>{key.userEmail}</strong>
                      <span className="muted">{key.applicationId}</span>
                    </td>
                    <td><Pill tone="info">{key.workspaceName || me.workspaceName}</Pill></td>
                    <td><Pill tone={roleTone(key.role)}>{key.role}</Pill></td>
                    <td className="mono">{key.prefix}</td>
                    <td><Pill tone={statusTone(key.status)}>{key.status}</Pill></td>
                    <td>{key.expiresAt ? formatWhen(key.expiresAt) : "No expiry"}</td>
                    <td>{formatWhen(key.lastUsedAt)}</td>
                    <td className="actions">
                      {key.status === "active" ? (
                        <div className="row-actions">
                          <button className="ghost small" type="button" disabled={busy} onClick={() => void rotate(key)}>Rotate</button>
                          <button className="ghost small" type="button" onClick={() => setPending(key)}>Revoke</button>
                        </div>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pager page={page} onOffset={setOffset} />
      </section>

      <Modal open={issued !== ""} title="New key" lede="The previous secret no longer works. Copy this one now." onClose={() => setIssued("")}>
        <SecretReveal label="Rotated gateway key" value={issued} />
        <div className="modal-actions">
          <button className="primary" type="button" onClick={() => setIssued("")}>Done</button>
        </div>
      </Modal>

      <Confirm
        open={pending !== null}
        title="Revoke this key?"
        body={pending ? `Requests that use the key for ${pending.userEmail} will be rejected.` : ""}
        confirmLabel="Revoke"
        danger
        busy={busy}
        onClose={() => setPending(null)}
        onConfirm={() => {
          if (pending) void revoke(pending);
        }}
      />
    </Page>
  );
}

import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api";
import { EMPTY_PAGE, Pager, pageQuery, useDebounced, type PageMeta } from "../paging";
import { useSession } from "../session";
import { Banner, Confirm, Empty, Modal, Page, Pill, SecretReveal, avatarColor, formatMoney, initials, roleTone, statusTone } from "../ui";

interface UserRow {
  id: string;
  email: string;
  displayName: string;
  applicationId: string;
  role: string;
  status: string;
  workspaceName: string;
  monthlyUsd: number;
  spentUsd: number;
}

const EMPTY = { email: "", displayName: "", role: "inference", monthlyUsd: "25", externalSubject: "" };

const ROLE_HELP: Record<string, string> = {
  inference: "Can call chat and embeddings, and open the usage screen.",
  auditor: "Opens traces, audit, guardrails, and budget. Cannot change configuration.",
  admin: "Opens the full console, including people, keys, and providers.",
};

function todayInput(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

export function UsersPage() {
  const me = useSession();
  const [rows, setRows] = useState<UserRow[]>([]);
  const [page, setPage] = useState<PageMeta>(EMPTY_PAGE);
  const [offset, setOffset] = useState(0);
  const [role, setRole] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [defaultCap, setDefaultCap] = useState("25");
  const [issued, setIssued] = useState<{ email: string; plaintext: string; expiresOn: string } | null>(null);
  const [issuing, setIssuing] = useState<UserRow | null>(null);
  const [expiresOn, setExpiresOn] = useState("");
  const [pending, setPending] = useState<UserRow | null>(null);
  const [jwt, setJwt] = useState({ issuer: "", audience: "", jwksUrl: "" });
  const [oidc, setOidc] = useState({ clientId: "", clientSecret: "", redirectUrl: "" });
  const [clientCert, setClientCert] = useState(false);
  const [jwtSaved, setJwtSaved] = useState("");

  const debounced = useDebounced(query);

  async function load(nextOffset = offset, nextQuery = debounced, nextRole = role) {
    const body = await api<{ users: UserRow[]; page: PageMeta }>(`/api/users?${pageQuery(nextOffset, nextQuery, { role: nextRole })}`);
    setRows(body.users);
    setPage(body.page);
  }

  useEffect(() => {
    setOffset(0);
  }, [debounced, role]);

  useEffect(() => {
    load().catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load users."));
  }, [debounced, offset, role]);

  useEffect(() => {
    api<{ issuer: string; audience: string; jwksUrl: string }>("/api/settings/jwt")
      .then((body) => setJwt(body))
      .catch(() => undefined);
    api<{ clientId: string; redirectUrl: string }>("/api/settings/oidc")
      .then((body) => setOidc({ clientId: body.clientId, clientSecret: "", redirectUrl: body.redirectUrl }))
      .catch(() => undefined);
    api<{ required: boolean }>("/api/settings/client-cert")
      .then((body) => setClientCert(body.required))
      .catch(() => undefined);
    api<{ defaultMonthlyUsd: number }>("/api/budget")
      .then((body) => {
        const cap = String(body.defaultMonthlyUsd);
        setDefaultCap(cap);
        setForm((current) => ({ ...current, monthlyUsd: current.monthlyUsd === "25" ? cap : current.monthlyUsd }));
      })
      .catch(() => undefined);
  }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const monthlyUsd = Number(form.monthlyUsd);
    if (!Number.isFinite(monthlyUsd) || monthlyUsd < 0) {
      setError("Enter a monthly budget of zero or more.");
      setBusy(false);
      return;
    }
    try {
      await api("/api/users", { method: "POST", body: JSON.stringify({ ...form, monthlyUsd }) });
      setForm({ ...EMPTY, role: form.role, monthlyUsd: defaultCap });
      setOpen(false);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create the user.");
    } finally {
      setBusy(false);
    }
  }

  async function issue(event: FormEvent) {
    event.preventDefault();
    if (!issuing) return;
    if (!expiresOn) {
      setError("Choose the date the key is allowed until.");
      return;
    }
    setError("");
    setBusy(true);
    try {
      const body = await api<{ plaintext: string; userEmail: string }>("/api/keys", {
        method: "POST",
        body: JSON.stringify({ userId: issuing.id, expiresOn }),
      });
      setIssued({ email: body.userEmail, plaintext: body.plaintext, expiresOn });
      setIssuing(null);
      setExpiresOn("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not issue a key.");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(user: UserRow) {
    setError("");
    setBusy(true);
    try {
      await api(`/api/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: user.status === "active" ? "disabled" : "active" }),
      });
      setPending(null);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update the user.");
      setPending(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page
      title="Users"
      lede={`People in ${me.workspaceName}. A person belongs to this workspace from the moment they are created. Their keys and monthly spend stay with them here.`}
      actions={<button className="primary" type="button" onClick={() => { setError(""); setOpen(true); }}>Add user</button>}
    >
      {error && !open && !issuing ? <Banner tone="error">{error}</Banner> : null}
      <section className="card">
        <div className="toolbar">
          <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search name or email" aria-label="Search users" />
          <select aria-label="Role" value={role} onChange={(event) => setRole(event.target.value)}>
            <option value="">All roles</option>
            <option value="admin">Admin</option>
            <option value="auditor">Auditor</option>
            <option value="inference">Inference</option>
          </select>
        </div>
        {rows.length === 0 ? (
          <Empty title={query || role ? "No matches" : "No people yet"}>{query || role ? "Try a different name, email, or role." : "Add the first person, then issue a key for them."}</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Person</th>
                  <th>Workspace</th>
                  <th>Application</th>
                  <th>Role</th>
                  <th>Budget</th>
                  <th>Status</th>
                  <th className="actions">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((user) => (
                  <tr key={user.id}>
                    <td>
                      <div className="person">
                        <span className="avatar" style={{ background: avatarColor(user.displayName) }}>{initials(user.displayName)}</span>
                        <span>
                          <strong>{user.displayName}</strong>
                          <span className="muted">{user.email}</span>
                        </span>
                      </div>
                    </td>
                    <td><Pill tone="info">{user.workspaceName || me.workspaceName}</Pill></td>
                    <td>{user.applicationId}</td>
                    <td><Pill tone={roleTone(user.role)}>{user.role}</Pill></td>
                    <td>
                      {formatMoney(user.spentUsd)}
                      <span className="muted">of {formatMoney(user.monthlyUsd)}</span>
                    </td>
                    <td><Pill tone={statusTone(user.status)}>{user.status}</Pill></td>
                    <td className="actions">
                      <div className="row-actions">
                        <button className="primary small" type="button" disabled={user.status !== "active" || busy} onClick={() => { setError(""); setExpiresOn(""); setIssuing(user); }}>
                          Issue key
                        </button>
                        <button
                          className="ghost small"
                          type="button"
                          onClick={() => (user.status === "active" ? setPending(user) : void toggle(user))}
                        >
                          {user.status === "active" ? "Disable" : "Enable"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pager page={page} onOffset={setOffset} />
      </section>

      <form
        className="card"
        onSubmit={(event) => {
          event.preventDefault();
          setError("");
          setJwtSaved("");
          Promise.all([
            api("/api/settings/jwt", { method: "PUT", body: JSON.stringify(jwt) }),
            api("/api/settings/oidc", { method: "PUT", body: JSON.stringify(oidc) }),
          ])
            .then(() => setJwtSaved("Directory sign-in saved. A JWT email or subject must match a person already in this directory."))
            .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not save JWT settings."));
        }}
      >
        <h2>Directory sign-in</h2>
        <p className="hint">A bearer JWT is accepted beside gateway keys when these three values are set. The email or preferred_username claim must match a person here. This gateway does not issue the token.</p>
        {jwtSaved ? <Banner tone="ok">{jwtSaved}</Banner> : null}
        <div className="field">
          <label htmlFor="jwt-issuer">Issuer</label>
          <input id="jwt-issuer" value={jwt.issuer} onChange={(event) => setJwt({ ...jwt, issuer: event.target.value })} placeholder="https://idp.example" />
        </div>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="jwt-audience">Audience</label>
            <input id="jwt-audience" value={jwt.audience} onChange={(event) => setJwt({ ...jwt, audience: event.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="jwt-jwks">JWKS URL</label>
            <input id="jwt-jwks" value={jwt.jwksUrl} onChange={(event) => setJwt({ ...jwt, jwksUrl: event.target.value })} placeholder="https://idp.example/jwks" />
          </div>
        </div>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="oidc-client">OIDC client id</label>
            <input id="oidc-client" value={oidc.clientId} onChange={(event) => setOidc({ ...oidc, clientId: event.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="oidc-secret">OIDC client secret</label>
            <input id="oidc-secret" type="password" value={oidc.clientSecret} onChange={(event) => setOidc({ ...oidc, clientSecret: event.target.value })} placeholder="Leave blank to keep the saved secret" />
          </div>
        </div>
        <div className="field">
          <label htmlFor="oidc-redirect">Redirect URL</label>
          <input id="oidc-redirect" value={oidc.redirectUrl} onChange={(event) => setOidc({ ...oidc, redirectUrl: event.target.value })} placeholder="https://gateway.example/auth/callback" />
        </div>
        <label className="check">
          <input
            type="checkbox"
            checked={clientCert}
            onChange={(event) => {
              const required = event.target.checked;
              setClientCert(required);
              void api("/api/settings/client-cert", { method: "PUT", body: JSON.stringify({ required }) });
            }}
          />
          Require a client certificate (the TLS terminator must send x-client-verify: SUCCESS)
        </label>
        <div className="modal-actions">
          <button className="primary" type="submit">Save sign-in</button>
        </div>
      </form>

      <Modal open={open} title="Add user" lede={`Created in ${me.workspaceName}. This person stays in that workspace. They do not receive a key until you issue one.`} onClose={() => setOpen(false)}>
        <form onSubmit={(event) => void create(event)}>
          {error ? <Banner tone="error">{error}</Banner> : null}
          <div className="field">
            <label htmlFor="name">Name</label>
            <input id="name" required autoFocus value={form.displayName} onChange={(event) => setForm({ ...form, displayName: event.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="email">Email</label>
            <input id="email" type="email" required value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} />
            <p className="hint">This person stays in {me.workspaceName}. The same email can be added again in another workspace as a different person.</p>
          </div>
          <div className="field">
            <label htmlFor="subject">Directory subject</label>
            <input id="subject" value={form.externalSubject} onChange={(event) => setForm({ ...form, externalSubject: event.target.value })} placeholder="optional workload identity sub" />
            <p className="hint">When set, a JWT sub claim can match this person even if the email differs.</p>
          </div>
          <div className="field">
            <label htmlFor="budget">Monthly budget, USD</label>
            <input id="budget" inputMode="decimal" required value={form.monthlyUsd} onChange={(event) => setForm({ ...form, monthlyUsd: event.target.value })} />
            <p className="hint">Calls stop when this person’s spend reaches the cap.</p>
          </div>
          <div className="field">
            <label htmlFor="role">Role</label>
            <select id="role" value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })}>
              <option value="inference">inference</option>
              <option value="auditor">auditor</option>
              <option value="admin">admin</option>
            </select>
            <p className="hint">{ROLE_HELP[form.role]}</p>
          </div>
          <div className="modal-actions">
            <button className="ghost" type="button" onClick={() => setOpen(false)}>Cancel</button>
            <button className="primary" type="submit" disabled={busy}>{busy ? "Creating…" : "Create user"}</button>
          </div>
        </form>
      </Modal>

      <Modal
        open={issuing !== null}
        title="Issue a key"
        lede={issuing ? `This key for ${issuing.displayName} is allowed until the date you choose. After that date, calls with it are rejected.` : undefined}
        onClose={() => setIssuing(null)}
      >
        <form onSubmit={(event) => void issue(event)}>
          {error ? <Banner tone="error">{error}</Banner> : null}
          <div className="field">
            <label htmlFor="expires">Allowed until</label>
            <input id="expires" type="date" required min={todayInput()} value={expiresOn} onChange={(event) => setExpiresOn(event.target.value)} />
          </div>
          <div className="modal-actions">
            <button className="ghost" type="button" onClick={() => setIssuing(null)}>Cancel</button>
            <button className="primary" type="submit" disabled={busy}>{busy ? "Issuing…" : "Issue key"}</button>
          </div>
        </form>
      </Modal>

      <Modal
        open={issued !== null}
        title="Key issued"
        lede={issued ? `For ${issued.email}. Allowed until ${issued.expiresOn}. Copy it before you close this.` : undefined}
        onClose={() => setIssued(null)}
      >
        {issued ? <SecretReveal label="Gateway key" value={issued.plaintext} /> : null}
        <div className="modal-actions">
          <button className="primary" type="button" onClick={() => setIssued(null)}>Done</button>
        </div>
      </Modal>

      <Confirm
        open={pending !== null}
        title={pending ? `Disable ${pending.displayName}?` : "Disable user"}
        body="Every key for this person stops working until you enable them again."
        confirmLabel="Disable"
        danger
        busy={busy}
        onClose={() => setPending(null)}
        onConfirm={() => {
          if (pending) void toggle(pending);
        }}
      />
    </Page>
  );
}

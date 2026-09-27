import { useEffect, useState, type FormEvent } from "react";

import { api, setWorkspace, workspace } from "../api";
import { EMPTY_PAGE, Pager, pageQuery, useDebounced, type PageMeta } from "../paging";
import { useSession } from "../session";
import { Banner, Empty, Loading, Page, Pill } from "../ui";

interface Workspace {
  id: string;
  name: string;
  people: number;
}

export function WorkspacesPage() {
  const me = useSession();
  const [rows, setRows] = useState<Workspace[]>([]);
  const [page, setPage] = useState<PageMeta>(EMPTY_PAGE);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [name, setName] = useState("");
  const [current, setCurrent] = useState(workspace());
  const [homeName, setHomeName] = useState(me.homeName);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [loading, setLoading] = useState(true);
  const debounced = useDebounced(query);

  async function load(nextOffset = offset) {
    const body = await api<{ workspaces: Workspace[]; homeName: string; page: PageMeta }>(`/api/workspaces?${pageQuery(nextOffset, debounced)}`);
    setRows(body.workspaces);
    setPage(body.page);
    setHomeName(body.homeName);
  }

  useEffect(() => {
    setOffset(0);
  }, [debounced]);

  useEffect(() => {
    load()
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load workspaces."))
      .finally(() => setLoading(false));
  }, [debounced, offset]);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await api("/api/workspaces", { method: "POST", body: JSON.stringify({ name }) });
      setName("");
      setSaved("Workspace created. Switch into it before you add people. Each person stays in the workspace where they are created.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create the workspace.");
    }
  }

  function choose(id: string) {
    setWorkspace(id);
    setCurrent(id);
    setSaved(id ? "Later requests use this workspace." : "Later requests use your home workspace.");
  }

  if (loading) return <Loading label="Loading workspaces…" />;

  return (
    <Page title="Workspaces" lede="Each person belongs to one workspace. The count on a row is the people stored there, along with their keys and spend.">
      {error ? <Banner tone="error">{error}</Banner> : null}
      {saved ? <Banner tone="ok">{saved}</Banner> : null}
      <section className="card">
        <h2>What you are viewing</h2>
        <p>Your account belongs to <strong>{homeName}</strong>. The console is showing <strong>{rows.find((row) => row.id === current)?.name ?? me.workspaceName}</strong>. People you add from here are stored in the workspace you are viewing. An inference key cannot switch. It always calls inside the workspace that issued it.</p>
      </section>
      <form className="card" onSubmit={(event) => void create(event)}>
        <h2>Create a workspace</h2>
        <div className="field">
          <label htmlFor="ws-name">Name</label>
          <input id="ws-name" required value={name} onChange={(event) => setName(event.target.value)} />
        </div>
        <div className="modal-actions">
          <button className="primary" type="submit">Create workspace</button>
        </div>
      </form>
      <section className="card">
        <div className="toolbar">
          <h2>Switch</h2>
          <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search workspaces" aria-label="Search workspaces" />
        </div>
        {rows.length === 0 ? (
          <Empty title="No workspaces">The local workspace appears after setup.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Workspace</th>
                  <th>People</th>
                  <th className="actions"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const viewing = current ? row.id === current : row.id === me.homeId;
                  const home = row.id === me.homeId;
                  return (
                    <tr key={row.id}>
                      <td>
                        <strong>{row.name}</strong>
                        {home ? <Pill>Home</Pill> : null}
                        {viewing ? <Pill tone="info">Viewing</Pill> : null}
                      </td>
                      <td>{row.people.toLocaleString()} {row.people === 1 ? "person" : "people"}</td>
                      <td className="actions">
                        {viewing ? (
                          <span className="muted">Current</span>
                        ) : (
                          <button className="ghost small" type="button" onClick={() => choose(home ? "" : row.id)}>View</button>
                        )}
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

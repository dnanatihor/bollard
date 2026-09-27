import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api";
import { EMPTY_PAGE, Pager, pageQuery, useDebounced, type PageMeta } from "../paging";
import { useSession } from "../session";
import { Banner, Empty, Loading, Page } from "../ui";

interface PromptRow {
  id: string;
  name: string;
  version: number;
  body: string;
  createdAt: string;
}

export function PromptsPage() {
  const me = useSession();
  const [rows, setRows] = useState<PromptRow[]>([]);
  const [page, setPage] = useState<PageMeta>(EMPTY_PAGE);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [name, setName] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [loading, setLoading] = useState(true);

  const debounced = useDebounced(query);

  async function load(nextOffset = offset) {
    const next = await api<{ prompts: PromptRow[]; page: PageMeta }>(`/api/prompts?${pageQuery(nextOffset, debounced)}`);
    setRows(next.prompts);
    setPage(next.page);
  }

  useEffect(() => {
    setOffset(0);
  }, [debounced]);

  useEffect(() => {
    load()
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load prompts."))
      .finally(() => setLoading(false));
  }, [debounced, offset]);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSaved("");
    try {
      const created = await api<{ name: string; version: number }>("/api/prompts", {
        method: "POST",
        body: JSON.stringify({ name, body }),
      });
      setSaved(`${created.name} version ${created.version} saved. Pass its id as promptId on a chat request.`);
      setName("");
      setBody("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save the prompt.");
    }
  }

  if (loading) return <Loading label="Loading prompts…" />;

  return (
    <Page title="Prompts" lede={`Prompts saved in ${me.workspaceName}. A prompt is a system message. A client sends its id as promptId and Bollard prepends that version before the model sees the chat.`}>
      {error ? <Banner tone="error">{error}</Banner> : null}
      {saved ? <Banner tone="ok">{saved}</Banner> : null}
      <section className="card">
        <h2>Try one on the first call</h2>
        <p>Three samples are already saved. Copy an id into the chat body. Saving the same name again stores the next version and leaves the earlier one in place.</p>
        <pre className="sample">{`{"model":"general-fast","promptId":"${rows[0]?.id ?? "prompt_…"}","messages":[{"role":"user","content":"Summarize this ticket."}]}`}</pre>
      </section>
      <form className="card" onSubmit={(event) => void create(event)}>
        <h2>New version</h2>
        <div className="field">
          <label htmlFor="prompt-name">Name</label>
          <input id="prompt-name" required value={name} onChange={(event) => setName(event.target.value)} placeholder="support-tone" />
        </div>
        <div className="field">
          <label htmlFor="prompt-body">Body</label>
          <textarea id="prompt-body" required rows={5} value={body} onChange={(event) => setBody(event.target.value)} />
        </div>
        <div className="modal-actions">
          <button className="primary" type="submit">Save version</button>
        </div>
      </form>
      <section className="card">
        <div className="toolbar">
          <h2>Versions</h2>
          <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search name or text" aria-label="Search prompts" />
        </div>
        {rows.length === 0 ? (
          <Empty title="No prompts yet">Save a version, then pass its id as promptId on a chat request.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Version</th>
                  <th>Id</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <strong>{row.name}</strong>
                      <span className="muted">{row.body}</span>
                    </td>
                    <td>{row.version}</td>
                    <td className="mono">{row.id}</td>
                  </tr>
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

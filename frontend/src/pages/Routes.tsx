import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api";
import { EMPTY_PAGE, Pager, pageQuery, useDebounced, type PageMeta } from "../paging";
import { Banner, Empty, Modal, Page, Pill, Tabs, formatMoney } from "../ui";

interface Model {
  id: string;
  providerId: string;
  providerModel: string;
  displayName: string;
  inputPricePerMillion: number;
  outputPricePerMillion: number;
  kind: "chat" | "embedding";
}

interface RouteModel {
  id: string;
  displayName: string;
  providerId: string;
  modelId: string;
  modelKind?: string;
  fallbackModelId?: string | null;
  strategy?: string;
  targets?: { modelId?: string; weight?: number; header?: string; equals?: string }[];
}

interface Provider {
  id: string;
}

const EMPTY_MODEL = { id: "", providerId: "", providerModel: "", displayName: "", input: "0.15", output: "0.60", kind: "chat" };
const EMPTY_ROUTE = { id: "", displayName: "", providerId: "", modelId: "", fallbackModelId: "", strategy: "direct", targetsText: "" };

function parseTargets(strategy: string, raw: string) {
  const lines = raw.split("\n").map((line) => line.trim()).filter(Boolean);
  if (strategy === "weighted") {
    return lines.map((line) => {
      const [modelId, weight] = line.split(/\s+/);
      return { modelId, weight: Number(weight || 1) };
    });
  }
  if (strategy === "conditional") {
    return lines.map((line) => {
      const [rule, modelId] = line.split(/\s+/);
      if (!rule || rule === "default") return { modelId: modelId || rule };
      const [header, equals] = rule.split("=");
      return { header, equals, modelId };
    });
  }
  if (strategy === "canary") {
    return lines.map((line) => {
      const [modelId, percent] = line.split(/\s+/);
      return percent === undefined ? { modelId } : { modelId, percent: Number(percent) };
    });
  }
  if (strategy === "region") {
    return lines.map((line) => {
      const [region, modelId] = line.split(/\s+/);
      return { region, modelId: modelId || region };
    });
  }
  return lines.map((modelId) => ({ modelId }));
}

function targetHint(strategy: string) {
  if (strategy === "conditional") return "x-tier=fast openai/gpt-4o-mini\ndefault anthropic/claude-haiku";
  if (strategy === "weighted") return "openai/gpt-4o-mini 70\nanthropic/claude-haiku 30";
  if (strategy === "canary") return "openai/gpt-4o-mini 10\nanthropic/claude-haiku";
  if (strategy === "region") return "us-east openai/gpt-4o-mini\neu-west anthropic/claude-haiku";
  return "openai/gpt-4o-mini\ngemini/flash";
}

export function RoutesPage() {
  const [models, setModels] = useState<Model[]>([]);
  const [routes, setRoutes] = useState<RouteModel[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [modelPage, setModelPage] = useState<PageMeta>(EMPTY_PAGE);
  const [routePage, setRoutePage] = useState<PageMeta>(EMPTY_PAGE);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [modelOffset, setModelOffset] = useState(0);
  const [routeOffset, setRouteOffset] = useState(0);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("routes");
  const [modelOpen, setModelOpen] = useState(false);
  const [routeOpen, setRouteOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState(EMPTY_MODEL);
  const [route, setRoute] = useState(EMPTY_ROUTE);
  const [semanticThreshold, setSemanticThreshold] = useState("0");
  const [semanticModel, setSemanticModel] = useState("");
  const [semanticSaved, setSemanticSaved] = useState("");

  const debounced = useDebounced(query);

  async function load() {
    const [modelBody, routeBody, providerBody] = await Promise.all([
      api<{ models: Model[]; page: PageMeta }>(`/api/models?${pageQuery(modelOffset, debounced, { kind })}`),
      api<{ routes: RouteModel[]; page: PageMeta }>(`/api/routes?${pageQuery(routeOffset, debounced)}`),
      api<{ providers: Provider[] }>("/api/providers?limit=100"),
    ]);
    setModels(modelBody.models);
    setModelPage(modelBody.page);
    setRoutes(routeBody.routes);
    setRoutePage(routeBody.page);
    setProviders(providerBody.providers);
  }

  useEffect(() => {
    setModelOffset(0);
    setRouteOffset(0);
  }, [debounced, kind]);

  useEffect(() => {
    api<{ threshold: number; model: string }>("/api/settings/semantic")
      .then((body) => {
        setSemanticThreshold(String(body.threshold));
        setSemanticModel(body.model);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load().catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load routes."));
  }, [debounced, kind, modelOffset, routeOffset]);

  const catalog = new Map(models.map((item) => [item.id, item]));
  const modelsForProvider = models.filter((item) => item.providerId === route.providerId);

  async function saveSemantic(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSemanticSaved("");
    setBusy(true);
    try {
      await api("/api/settings/semantic", {
        method: "PUT",
        body: JSON.stringify({ threshold: Number(semanticThreshold), model: semanticModel }),
      });
      setSemanticSaved("Saved. Semantic cache uses this embedding model to compare prompts.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save the semantic cache.");
    } finally {
      setBusy(false);
    }
  }

  async function saveModel(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await api("/api/models", {
        method: "POST",
        body: JSON.stringify({
          id: model.id,
          providerId: model.providerId,
          providerModel: model.providerModel,
          displayName: model.displayName,
          inputPricePerMillion: Number(model.input),
          outputPricePerMillion: Number(model.output),
          kind: model.kind,
        }),
      });
      setModel(EMPTY_MODEL);
      setModelOpen(false);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save the model.");
    } finally {
      setBusy(false);
    }
  }

  async function saveRoute(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await api("/api/routes", {
        method: "POST",
        body: JSON.stringify({
          id: route.id,
          displayName: route.displayName,
          providerId: route.providerId,
          modelId: route.modelId,
          fallbackModelId: route.fallbackModelId || null,
          strategy: route.strategy,
          targets: route.strategy === "direct" ? [] : parseTargets(route.strategy, route.targetsText),
        }),
      });
      setRoute(EMPTY_ROUTE);
      setRouteOpen(false);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save the route.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page
      title="Models and routes"
      lede="A catalog model is either chat or embeddings. A route is the name a client sends, and it points at one catalog model."
      actions={
        tab === "routes" ? (
          <button className="primary" type="button" onClick={() => { setError(""); setRouteOpen(true); }}>Add route</button>
        ) : (
          <button className="primary" type="button" onClick={() => { setError(""); setModelOpen(true); }}>Add model</button>
        )
      }
    >
      <div className="compare">
        <article className="card">
          <h2>Catalog model</h2>
          <p>The provider’s model and its price. Choose chat for completions, or embeddings for vectors. This is what the gateway calls.</p>
        </article>
        <article className="card">
          <h2>Route</h2>
          <p>The name a client puts in <span className="mono">model</span>, such as <span className="mono">general-fast</span>. It selects one catalog model and can fall back to another if that call fails.</p>
        </article>
      </div>
      <form className="card" onSubmit={(event) => void saveSemantic(event)}>
        <h2>Semantic cache</h2>
        <p className="hint">This is not a budget. The gateway embeds a prompt with the model below and reuses a nearby reply when the cosine is at least the threshold. Zero turns it off.</p>
        {semanticSaved ? <Banner tone="ok">{semanticSaved}</Banner> : null}
        <div className="form-grid">
          <div className="field">
            <label htmlFor="semantic-threshold">Similarity threshold</label>
            <input id="semantic-threshold" inputMode="decimal" value={semanticThreshold} onChange={(event) => setSemanticThreshold(event.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="semantic-model">Embedding model id</label>
            <input id="semantic-model" value={semanticModel} onChange={(event) => setSemanticModel(event.target.value)} placeholder="openai/text-embedding-3-small" />
            <p className="hint">A catalog embedding model. It is only used to compare prompts.</p>
          </div>
        </div>
        <div className="modal-actions">
          <button className="primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Save semantic cache"}</button>
        </div>
      </form>
      <div className="toolbar">
        <Tabs
          value={tab}
          onChange={setTab}
          options={[
            ["routes", `Routes ${routePage.total}`],
            ["catalog", `Catalog ${modelPage.total}`],
          ]}
        />
        <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search id or name" aria-label="Search models and routes" />
        {tab === "catalog" ? (
          <select aria-label="Model kind" value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="">Chat and embeddings</option>
            <option value="chat">Chat</option>
            <option value="embedding">Embeddings</option>
          </select>
        ) : null}
      </div>
      {error && !modelOpen && !routeOpen ? <Banner tone="error">{error}</Banner> : null}

      {tab === "routes" ? (
        <section className="card">
          {routes.length === 0 ? (
            <Empty title="No routes yet">A route is the model name a client is allowed to send.</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Client model</th>
                    <th>Provider</th>
                    <th>Catalog model</th>
                    <th>Kind</th>
                    <th>Fallback</th>
                  </tr>
                </thead>
                <tbody>
                  {routes.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <strong>{item.displayName}</strong>
                        <span className="muted mono">{item.id}</span>
                      </td>
                      <td>{item.providerId}</td>
                      <td>
                        <span className="mono">{item.modelId}</span>
                        {catalog.get(item.modelId) ? <span className="muted">{catalog.get(item.modelId)?.displayName}</span> : null}
                      </td>
                      <td>{item.strategy || "direct"}</td>
                      <td><Pill tone={item.modelKind === "embedding" ? "info" : "ok"}>{item.modelKind || "chat"}</Pill></td>
                      <td className="mono">{item.fallbackModelId || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Pager page={routePage} onOffset={setRouteOffset} />
        </section>
      ) : (
        <section className="card">
          {models.length === 0 ? (
            <Empty title="Catalog is empty">Add a model before you can attach it to a route.</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Provider</th>
                    <th>Kind</th>
                    <th>Input</th>
                    <th>Output</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <strong>{item.displayName}</strong>
                        <span className="muted mono">{item.id}</span>
                      </td>
                      <td>{item.providerId}</td>
                      <td className="mono">{item.providerModel}</td>
                      <td><Pill tone={item.kind === "embedding" ? "info" : "ok"}>{item.kind}</Pill></td>
                      <td>{formatMoney(item.inputPricePerMillion)} / M</td>
                      <td>{formatMoney(item.outputPricePerMillion)} / M</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Pager page={modelPage} onOffset={setModelOffset} />
        </section>
      )}

      <Modal open={routeOpen} title="Add route" lede="The route id is the model name clients send." onClose={() => setRouteOpen(false)}>
        <form onSubmit={(event) => void saveRoute(event)}>
          {error ? <Banner tone="error">{error}</Banner> : null}
          <div className="field">
            <label htmlFor="rid">Route id</label>
            <input id="rid" required autoFocus value={route.id} onChange={(event) => setRoute({ ...route, id: event.target.value })} placeholder="general-fast" />
          </div>
          <div className="field">
            <label htmlFor="rname">Display name</label>
            <input id="rname" required value={route.displayName} onChange={(event) => setRoute({ ...route, displayName: event.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="rprovider">Provider</label>
            <select
              id="rprovider"
              required
              value={route.providerId}
              onChange={(event) => setRoute({ ...route, providerId: event.target.value, modelId: "", fallbackModelId: "" })}
            >
              <option value="">Choose a provider</option>
              {providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.id}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="rstrategy">Strategy</label>
            <select id="rstrategy" value={route.strategy} onChange={(event) => setRoute({ ...route, strategy: event.target.value })}>
              <option value="direct">direct</option>
              <option value="weighted">weighted</option>
              <option value="round_robin">round robin</option>
              <option value="least_cost">least cost</option>
              <option value="least_latency">least latency</option>
              <option value="conditional">conditional</option>
              <option value="canary">canary</option>
              <option value="region">region</option>
            </select>
          </div>
          {route.strategy === "direct" ? (
          <div className="field">
            <label htmlFor="rmodel">Catalog model</label>
            <select
              id="rmodel"
              required
              value={route.modelId}
              onChange={(event) => setRoute({ ...route, modelId: event.target.value })}
            >
              <option value="">Choose a model</option>
              {modelsForProvider.map((item) => <option key={item.id} value={item.id}>{item.displayName}</option>)}
            </select>
            {route.providerId && modelsForProvider.length === 0 ? <p className="hint">This provider has no catalog models yet.</p> : null}
          </div>
          ) : (
          <div className="field">
            <label htmlFor="rtargets">Targets</label>
            <textarea id="rtargets" required rows={4} value={route.targetsText} onChange={(event) => setRoute({ ...route, targetsText: event.target.value })} placeholder={targetHint(route.strategy)} />
            <p className="hint">One target per line. Weighted lines end with a weight. Canary lines end with a percent, and one line without a percent is the primary. Conditional lines are header=value then the model id. Region lines start with the region name.</p>
          </div>
          )}
          <div className="field">
            <label htmlFor="rfallback">Fallback model</label>
            <select id="rfallback" value={route.fallbackModelId} onChange={(event) => setRoute({ ...route, fallbackModelId: event.target.value })}>
              <option value="">None</option>
              {models.filter((item) => item.id !== route.modelId).map((item) => (
                <option key={item.id} value={item.id}>{item.displayName}</option>
              ))}
            </select>
            <p className="hint">Used once if the primary provider call fails.</p>
          </div>
          <div className="modal-actions">
            <button className="ghost" type="button" onClick={() => setRouteOpen(false)}>Cancel</button>
            <button className="primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Add route"}</button>
          </div>
        </form>
      </Modal>

      <Modal open={modelOpen} title="Add catalog model" lede="Prices are USD per million tokens and are used for the budget estimate." onClose={() => setModelOpen(false)}>
        <form onSubmit={(event) => void saveModel(event)}>
          {error ? <Banner tone="error">{error}</Banner> : null}
          <div className="field">
            <label htmlFor="mid">Model id</label>
            <input id="mid" required autoFocus value={model.id} onChange={(event) => setModel({ ...model, id: event.target.value })} placeholder="openai/gpt-4o-mini" />
          </div>
          <div className="field">
            <label htmlFor="dname">Display name</label>
            <input id="dname" required value={model.displayName} onChange={(event) => setModel({ ...model, displayName: event.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="mp">Provider</label>
            <select id="mp" required value={model.providerId} onChange={(event) => setModel({ ...model, providerId: event.target.value })}>
              <option value="">Choose a provider</option>
              {providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.id}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="pmodel">Provider model</label>
            <input id="pmodel" required value={model.providerModel} onChange={(event) => setModel({ ...model, providerModel: event.target.value })} placeholder="gpt-4o-mini" />
          </div>
          <div className="field">
            <label htmlFor="mkind">Kind</label>
            <select id="mkind" value={model.kind} onChange={(event) => setModel({ ...model, kind: event.target.value })}>
              <option value="chat">Chat</option>
              <option value="embedding">Embeddings</option>
            </select>
            <p className="hint">Chat models answer /v1/chat/completions. Embedding models answer /v1/embeddings.</p>
          </div>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="in">Input price / million</label>
              <input id="in" value={model.input} onChange={(event) => setModel({ ...model, input: event.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="out">Output price / million</label>
              <input id="out" value={model.output} onChange={(event) => setModel({ ...model, output: event.target.value })} />
            </div>
          </div>
          <div className="modal-actions">
            <button className="ghost" type="button" onClick={() => setModelOpen(false)}>Cancel</button>
            <button className="primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Add model"}</button>
          </div>
        </form>
      </Modal>
    </Page>
  );
}

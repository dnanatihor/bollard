import { useState, type FormEvent } from "react";

import { api } from "../api";
import { Banner, Page } from "../ui";

interface Step {
  traceId?: string;
  tool?: string;
  serverId?: string;
  error?: string;
  message?: { content?: string };
}

export function AgentsPage() {
  const [model, setModel] = useState("");
  const [task, setTask] = useState("");
  const [servers, setServers] = useState("");
  const [maxSteps, setMaxSteps] = useState("4");
  const [steps, setSteps] = useState<Step[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const body = await api<{ steps: Step[] }>("/api/agents/run", {
        method: "POST",
        body: JSON.stringify({
          model,
          task,
          serverIds: servers.split(",").map((item) => item.trim()).filter(Boolean),
          maxSteps: Number(maxSteps),
        }),
      });
      setSteps(body.steps);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The agent run failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page
      title="Agents"
      lede="A bounded runner. It lists tools from the MCP servers you name, calls the model, invokes each tool through the gateway, and stops when the model is done or the step cap is reached."
    >
      {error ? <Banner tone="error">{error}</Banner> : null}
      <form className="card" onSubmit={(event) => void run(event)}>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="agent-model">Model or route</label>
            <input id="agent-model" required value={model} onChange={(event) => setModel(event.target.value)} placeholder="general-fast" />
          </div>
          <div className="field">
            <label htmlFor="agent-steps">Max steps</label>
            <input id="agent-steps" inputMode="numeric" required value={maxSteps} onChange={(event) => setMaxSteps(event.target.value)} />
          </div>
        </div>
        <div className="field">
          <label htmlFor="agent-servers">MCP server ids</label>
          <input id="agent-servers" value={servers} onChange={(event) => setServers(event.target.value)} placeholder="orders, search" />
          <p className="hint">Comma-separated. The person running this must already have a grant on each server.</p>
        </div>
        <div className="field">
          <label htmlFor="agent-task">Task</label>
          <textarea id="agent-task" required rows={4} value={task} onChange={(event) => setTask(event.target.value)} />
        </div>
        <div className="modal-actions">
          <button className="primary" type="submit" disabled={busy}>{busy ? "Running…" : "Run agent"}</button>
        </div>
      </form>
      {steps.length > 0 ? (
        <section className="card">
          <h2>Steps</h2>
          <ol>
            {steps.map((step, index) => (
              <li key={`${step.traceId ?? "step"}-${index}`}>
                {step.tool ? `Tool ${step.serverId} / ${step.tool}` : step.error || step.message?.content || "Model reply"}
                {step.traceId ? <span className="muted mono"> {step.traceId}</span> : null}
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </Page>
  );
}

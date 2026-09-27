import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";

import { api, clearToken, setWorkspace, token, workspace } from "./api";
import { UsagePage } from "./pages/Usage";
import { SessionProvider, type Me } from "./session";
import { AgentsPage } from "./pages/Agents";
import { AuditPage } from "./pages/Audit";
import { BudgetPage } from "./pages/Budget";
import { GuardrailsPage } from "./pages/Guardrails";
import { McpPage } from "./pages/Mcp";
import { KeysPage } from "./pages/Keys";
import { OverviewPage } from "./pages/Overview";
import { PromptsPage } from "./pages/Prompts";
import { ProvidersPage } from "./pages/Providers";
import { RoutesPage } from "./pages/Routes";
import { SetupPage } from "./pages/Setup";
import { TracePage } from "./pages/Trace";
import { TracesPage } from "./pages/Traces";
import { UsersPage } from "./pages/Users";
import { WorkspacesPage } from "./pages/Workspaces";

const GROUPS = [
  {
    label: "Access",
    roles: ["admin"],
    links: [
      { to: "/workspaces", label: "Workspaces" },
      { to: "/users", label: "Users" },
      { to: "/keys", label: "API keys" },
    ],
  },
  {
    label: "Control",
    roles: ["admin"],
    links: [
      { to: "/providers", label: "Providers" },
      { to: "/routes", label: "Models & routes" },
      { to: "/guardrails", label: "Guardrails" },
      { to: "/mcp", label: "MCP" },
      { to: "/prompts", label: "Prompts" },
      { to: "/agents", label: "Agents" },
      { to: "/budget", label: "Budget" },
    ],
  },
  {
    label: "Operate",
    roles: ["admin", "auditor"],
    links: [
      { to: "/", label: "Overview", end: true },
      { to: "/traces", label: "Traces" },
      { to: "/audit", label: "Audit" },
    ],
  },
  {
    label: "Review",
    roles: ["auditor"],
    links: [
      { to: "/guardrails", label: "Guardrails" },
      { to: "/budget", label: "Budget" },
    ],
  },
  {
    label: "Use",
    roles: ["inference"],
    links: [{ to: "/", label: "My usage", end: true }],
  },
];

const ROLE_LABEL: Record<Me["role"], string> = {
  admin: "Admin",
  auditor: "Auditor",
  inference: "Inference",
};

export function App() {
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);

  useEffect(() => {
    api<{ needsSetup: boolean }>("/api/bootstrap")
      .then((body) => setNeedsSetup(body.needsSetup))
      .catch(() => setNeedsSetup(false));
  }, []);

  if (needsSetup === null) {
    return (
      <div className="boot">
        <div>
          <div className="mark-halo">
            <img className="mark" src="/bollard-mark.png" alt="" />
          </div>
          <p>Loading Bollard…</p>
        </div>
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/setup" element={<SetupPage needsSetup={needsSetup} onReady={() => setNeedsSetup(false)} />} />
      <Route path="/*" element={<Shell needsSetup={needsSetup} />} />
    </Routes>
  );
}

function Shell({ needsSetup }: { needsSetup: boolean }) {
  const navigate = useNavigate();
  const [me, setMe] = useState<Me | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (needsSetup || !token()) return;
    const load = () => {
      const finish = (body: Me) => setMe(body);
      api<Me>("/api/me")
        .then(finish)
        .catch(() => {
          if (!workspace()) {
            setFailed(true);
            return;
          }
          setWorkspace("");
          api<Me>("/api/me").then(finish).catch(() => setFailed(true));
        });
    };
    load();
    window.addEventListener("aigw-workspace", load);
    return () => window.removeEventListener("aigw-workspace", load);
  }, [needsSetup]);
  if (needsSetup || !token()) {
    return <Navigate to="/setup" replace />;
  }
  if (failed) {
    return <Navigate to="/setup?reason=expired" replace />;
  }
  if (!me) {
    return (
      <div className="boot">
        <div>
          <div className="mark-halo">
            <img className="mark" src="/bollard-mark.png" alt="" />
          </div>
          <p>Loading Bollard…</p>
        </div>
      </div>
    );
  }
  const allowed = new Set(
    GROUPS.filter((group) => group.roles.includes(me.role)).flatMap((group) => group.links.map((link) => link.to)),
  );
  return (
    <SessionProvider value={me}>
      <div className="shell">
        <aside className="nav">
          <div className="brand">
            <div className="mark-halo">
              <img className="mark" src="/bollard-mark.png" alt="" />
            </div>
            <span>
              <strong>Bollard</strong>
              <em>{ROLE_LABEL[me.role]} · {me.workspaceName}</em>
              {me.homeId !== me.workspaceId ? <em>Home {me.homeName}</em> : null}
            </span>
          </div>
          {GROUPS.filter((group) => group.roles.includes(me.role)).map((group) => (
            <div className="nav-group" key={group.label}>
              <div className="nav-label">{group.label}</div>
              {group.links.map((link) => (
                <NavLink key={link.to} to={link.to} end={link.end}>
                  {link.label}
                </NavLink>
              ))}
            </div>
          ))}
          <button
            className="signout"
            type="button"
            onClick={() => {
              void fetch("/auth/logout", { method: "POST" }).finally(() => {
                clearToken();
                setWorkspace("");
                navigate("/setup");
              });
            }}
          >
            Sign out
          </button>
        </aside>
        <div className="workspace">
          <main className="main">
            <Routes>
              <Route path="/" element={me.role === "inference" ? <UsagePage /> : <OverviewPage />} />
              <Route path="/providers" element={allowed.has("/providers") ? <ProvidersPage /> : <Navigate to="/" replace />} />
              <Route path="/routes" element={allowed.has("/routes") ? <RoutesPage /> : <Navigate to="/" replace />} />
              <Route path="/users" element={allowed.has("/users") ? <UsersPage /> : <Navigate to="/" replace />} />
              <Route path="/keys" element={allowed.has("/keys") ? <KeysPage /> : <Navigate to="/" replace />} />
              <Route path="/guardrails" element={allowed.has("/guardrails") ? <GuardrailsPage /> : <Navigate to="/" replace />} />
              <Route path="/audit" element={allowed.has("/audit") ? <AuditPage /> : <Navigate to="/" replace />} />
              <Route path="/traces" element={allowed.has("/traces") ? <TracesPage /> : <Navigate to="/" replace />} />
              <Route path="/traces/:traceId" element={allowed.has("/traces") ? <TracePage /> : <Navigate to="/" replace />} />
              <Route path="/mcp" element={allowed.has("/mcp") ? <McpPage /> : <Navigate to="/" replace />} />
              <Route path="/prompts" element={allowed.has("/prompts") ? <PromptsPage /> : <Navigate to="/" replace />} />
              <Route path="/agents" element={allowed.has("/agents") ? <AgentsPage /> : <Navigate to="/" replace />} />
              <Route path="/workspaces" element={allowed.has("/workspaces") ? <WorkspacesPage /> : <Navigate to="/" replace />} />
              <Route path="/budget" element={allowed.has("/budget") ? <BudgetPage /> : <Navigate to="/" replace />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
        </div>
      </div>
    </SessionProvider>
  );
}

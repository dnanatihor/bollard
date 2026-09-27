import { useEffect, useId, useRef, useState, type ReactNode } from "react";

export function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.replace("T", " ").slice(0, 16);
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
}

export function formatMoney(value: number, digits = 2): string {
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).slice(0, 2);
  const letters = parts.map((part) => part[0]?.toUpperCase() ?? "").join("");
  return letters || "?";
}

const AVATAR_COLORS = ["#0e6b5c", "#175cd3", "#9a6700", "#9f2d2d", "#3d4f66"];

export function avatarColor(name: string): string {
  const code = name.split("").reduce((sum, char) => sum + char.charCodeAt(0), 0);
  return AVATAR_COLORS[code % AVATAR_COLORS.length];
}

export function statusTone(status: string): "ok" | "bad" | "warn" | "neutral" {
  if (status === "active" || status === "ok" || status === "enabled") return "ok";
  if (status === "revoked" || status === "disabled" || status === "error" || status === "blocked") return "bad";
  if (status === "neutral") return "neutral";
  return "warn";
}

export function roleTone(role: string): "info" | "warn" | "neutral" {
  if (role === "admin") return "info";
  if (role === "auditor") return "warn";
  return "neutral";
}

export function Page({
  title,
  lede,
  actions,
  children,
}: {
  title: string;
  lede?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{title}</h1>
          {lede ? <p className="lede">{lede}</p> : null}
        </div>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </header>
      {children}
    </div>
  );
}

export function Banner({ tone, children }: { tone: "error" | "ok" | "info"; children: ReactNode }) {
  return (
    <p className={`banner ${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </p>
  );
}

export function Pill({
  tone = "neutral",
  children,
}: {
  tone?: "ok" | "bad" | "warn" | "neutral" | "info";
  children: ReactNode;
}) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children ? <p>{children}</p> : null}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <p className="loading">{label}</p>;
}

export function Exchange({
  prompt,
  sent,
  response,
}: {
  prompt: string;
  sent: string;
  response: string;
}) {
  return (
    <div className="exchange">
      <ExchangeBlock title="Prompt" text={prompt} empty="No prompt was recorded." />
      <ExchangeBlock title="Sent to the model" text={sent} empty="Nothing was sent to a provider." />
      <ExchangeBlock title="Model reply" text={response} empty="No reply was returned." />
    </div>
  );
}

function ExchangeBlock({ title, text, empty }: { title: string; text: string; empty: string }) {
  return (
    <section>
      <h3>{title}</h3>
      <pre>{text || empty}</pre>
    </section>
  );
}

export function Modal({
  open,
  title,
  lede,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  lede?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog || !open) return;
    if (!dialog.open) dialog.showModal();
    return () => {
      if (dialog.open) dialog.close();
    };
  }, [open]);

  if (!open) return null;

  return (
    <dialog
      ref={ref}
      className="modal"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <header className="modal-head">
        <div>
          <h2 id={titleId}>{title}</h2>
          {lede ? <p className="lede">{lede}</p> : null}
        </div>
        <button className="icon-btn" type="button" aria-label="Close" onClick={onClose}>
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M4 4l8 8M12 4L4 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </button>
      </header>
      <div className="modal-body">{children}</div>
    </dialog>
  );
}

export function Confirm({
  open,
  title,
  body,
  confirmLabel,
  danger,
  busy,
  onClose,
  onConfirm,
}: {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Modal open={open} title={title} onClose={onClose}>
      <p className="confirm-copy">{body}</p>
      <div className="modal-actions">
        <button className="ghost" type="button" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button className={danger ? "danger" : "primary"} type="button" disabled={busy} onClick={onConfirm}>
          {busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </Modal>
  );
}

export function SecretReveal({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setFailed(false);
    } catch {
      setFailed(true);
      setCopied(false);
    }
  }

  return (
    <div className="secret">
      <div className="secret-top">
        <span>{label}</span>
        <button className="ghost small" type="button" onClick={() => void copy()}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <input readOnly value={value} onFocus={(event) => event.currentTarget.select()} aria-label={label} />
      <p className="hint">
        {failed
          ? "Clipboard is unavailable. Select the key and copy it."
          : "Shown once. The gateway stores only a hash."}
      </p>
    </div>
  );
}

export function Switch({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: (value: boolean) => void;
}) {
  return (
    <button
      type="button"
      className={checked ? "switch on" : "switch"}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
    >
      <span />
    </button>
  );
}

export function Tabs({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (value: string) => void;
  options: Array<[string, string]>;
}) {
  return (
    <div className="tabs" role="tablist">
      {options.map(([id, label]) => (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={value === id}
          className={value === id ? "tab on" : "tab"}
          onClick={() => onChange(id)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

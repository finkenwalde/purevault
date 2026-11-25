import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  MEANINGFUL_ACTIVITY_EVENTS,
  advanceSessionGeneration,
  captureSessionGeneration,
  responseBelongsToCurrentSession,
  shouldAcknowledgeActivity,
} from "./sessionBoundary";

type EntryStatus = "locked" | "waiting" | "available";
type Entry = {
  id: number;
  title: string;
  unlock_delay_seconds: number;
  relock_policy: "automatic" | "manual";
  relock_after_seconds: number | null;
  status: EntryStatus;
  requested_at: string | null;
  eligible_at: string | null;
  access_until: string | null;
  created_at: string;
  updated_at: string;
  remaining_seconds: number | null;
  sync_performance_ms?: number;
};
type Session = {
  authenticated: boolean;
  username: string | null;
  setup_required: boolean;
  server_time: string;
  display_timezone: string;
  display_timezone_is_default: boolean;
  signed_out_reason: "inactivity" | null;
};
type Partner = { id: number; name: string; email: string };
type EmailConfiguration = {
  enabled: boolean;
  host: string;
  port: number;
  security: "starttls" | "ssl" | "none";
  username: string;
  password_set: boolean;
  from_email: string;
  from_name: string;
};
type QueueSummary = {
  pending: number;
  sending: number;
  action_needed: number;
  accepted: number;
  latest_error: string;
};
type AccountabilitySettings = {
  partners: Partner[];
  email: EmailConfiguration;
  queue: QueueSummary;
  warning: string;
  limits: { partners: number; reason_characters: number };
};

type SessionEndedDetail = { reason: "inactivity" | "explicit" | "authentication" };
const SESSION_EVENT_KEY = "purevault-session-ended";

function endClientSession(detail: SessionEndedDetail, broadcast = true) {
  advanceSessionGeneration();
  window.dispatchEvent(new CustomEvent<SessionEndedDetail>(SESSION_EVENT_KEY, { detail }));
  if (broadcast) {
    try {
      localStorage.setItem(SESSION_EVENT_KEY, JSON.stringify({ ...detail, nonce: crypto.randomUUID() }));
    } catch {
      // Server-side session validation remains authoritative if storage is unavailable.
    }
  }
}

function beginClientSession() {
  advanceSessionGeneration();
}

window.addEventListener("storage", (event) => {
  if (event.key !== SESSION_EVENT_KEY || !event.newValue) return;
  try {
    const value = JSON.parse(event.newValue) as SessionEndedDetail;
    endClientSession({ reason: value.reason }, false);
  } catch {
    endClientSession({ reason: "authentication" }, false);
  }
});

function cookie(name: string) {
  return (
    document.cookie
      .split("; ")
      .find((row) => row.startsWith(`${name}=`))
      ?.split("=")
      .slice(1)
      .join("=") || ""
  );
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const requestGeneration = captureSessionGeneration();
  const response = await fetch(`/api${path}`, {
    ...options,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": decodeURIComponent(cookie("csrftoken")),
      ...(options.headers || {}),
    },
  });
  const data = await response
    .json()
    .catch(() => ({ error: "The server returned an unreadable response." }));
  if (response.status === 401) {
    endClientSession({ reason: data.code === "inactivity_timeout" ? "inactivity" : "authentication" });
  }
  if (!response.ok)
    throw new Error(data.error || `Request failed (${response.status})`);
  if (!responseBelongsToCurrentSession(requestGeneration))
    throw new Error("The session ended before this response completed.");
  return data;
}

function duration(seconds: number | null) {
  if (!seconds) return "Manual";
  const units: [number, string][] = [
    [86400, "day"],
    [3600, "hour"],
    [60, "minute"],
    [1, "second"],
  ];
  const [factor, label] =
    units.find(([factor]) => seconds % factor === 0) || units[3];
  const amount = seconds / factor;
  return `${amount} ${label}${amount === 1 ? "" : "s"}`;
}

function remaining(entry: Entry, nowPerformance: number) {
  if (entry.remaining_seconds === null) return "Manual";
  const elapsed = Math.max(
    0,
    (nowPerformance - (entry.sync_performance_ms || nowPerformance)) / 1000,
  );
  const total = Math.max(0, Math.ceil(entry.remaining_seconds - elapsed));
  const days = Math.floor(total / 86400),
    hours = Math.floor((total % 86400) / 3600),
    mins = Math.floor((total % 3600) / 60),
    secs = total % 60;
  return [
    days && `${days}d`,
    (hours || days) && `${hours}h`,
    (mins || hours || days) && `${mins}m`,
    `${secs}s`,
  ]
    .filter(Boolean)
    .join(" ");
}

function localTime(value: string | null, zone: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone: zone,
  }).format(new Date(value));
}

function Secret({ value }: { value: string }) {
  return (
    <code className="secret" aria-label="Revealed password">
      {Array.from(value).map((char, i) => {
        const kind = /[A-Za-z]/.test(char)
          ? "letter"
          : /[0-9]/.test(char)
            ? "digit"
            : "special";
        return (
          <span className={kind} key={i}>
            {char}
          </span>
        );
      })}
    </code>
  );
}

const emptyForm = {
  title: "",
  password: "",
  unlock_delay: "1",
  unlock_unit: "hours",
  relock_policy: "automatic",
  relock_after: "10",
  relock_unit: "minutes",
};

function EntryForm({
  onCreated,
  editing,
  onCancel,
}: {
  onCreated: (entry: Entry) => void;
  editing?: Entry | null;
  onCancel?: () => void;
}) {
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (key: string, value: string) =>
    setForm((current) => ({ ...current, [key]: value }));
  useEffect(() => {
    if (editing)
      setForm((current) => ({
        ...current,
        title: editing.title,
        password: "",
        relock_policy: editing.relock_policy,
      }));
  }, [editing]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api<{ entry: Entry }>(
        editing ? `/entries/${editing.id}/` : "/entries/",
        { method: editing ? "PUT" : "POST", body: JSON.stringify(form) },
      );
      setForm(emptyForm);
      onCreated(result.entry);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="entry-form" onSubmit={submit} autoComplete="off">
      <div className="field full">
        <label htmlFor="entry-title">Entry title</label>
        <input
          id="entry-title"
          value={form.title}
          onChange={(e) => set("title", e.target.value)}
          maxLength={200}
          required
          placeholder="Example account"
        />
      </div>
      <div className="field full">
        <label htmlFor="entry-password">
          Password{" "}
          {editing && <span className="hint">(enter the new value)</span>}
        </label>
        <input
          id="entry-password"
          type="password"
          value={form.password}
          onChange={(e) => set("password", e.target.value)}
          maxLength={4096}
          required
          autoComplete="new-password"
        />
      </div>
      <div className="field">
        <label htmlFor="delay">Unlock delay</label>
        <div className="duration">
          <input
            id="delay"
            inputMode="numeric"
            value={form.unlock_delay}
            onChange={(e) => set("unlock_delay", e.target.value)}
            required
          />
          <select
            aria-label="Unlock delay unit"
            value={form.unlock_unit}
            onChange={(e) => set("unlock_unit", e.target.value)}
          >
            <option>seconds</option>
            <option>minutes</option>
            <option>hours</option>
            <option>days</option>
          </select>
        </div>
      </div>
      <fieldset className="policy full">
        <legend>Relock policy</legend>
        <label className="choice">
          <input
            type="radio"
            name="policy"
            checked={form.relock_policy === "automatic"}
            onChange={() => set("relock_policy", "automatic")}
          />
          <span>
            <strong>Automatic</strong>
            <small>
              The access window begins when the delay ends, whether or not you
              reveal the password.
            </small>
          </span>
        </label>
        <label className="choice">
          <input
            type="radio"
            name="policy"
            checked={form.relock_policy === "manual"}
            onChange={() => set("relock_policy", "manual")}
          />
          <span>
            <strong>Manual</strong>
            <small>Remains available until you choose Lock now.</small>
          </span>
        </label>
      </fieldset>
      {form.relock_policy === "automatic" && (
        <div className="field">
          <label htmlFor="relock">Access window</label>
          <div className="duration">
            <input
              id="relock"
              inputMode="numeric"
              value={form.relock_after}
              onChange={(e) => set("relock_after", e.target.value)}
              required
            />
            <select
              aria-label="Access window unit"
              value={form.relock_unit}
              onChange={(e) => set("relock_unit", e.target.value)}
            >
              <option>seconds</option>
              <option>minutes</option>
              <option>hours</option>
              <option>days</option>
            </select>
          </div>
        </div>
      )}
      {error && (
        <p className="error full" role="alert">
          {error}
        </p>
      )}
      <div className="form-actions full">
        {onCancel && (
          <button type="button" className="button ghost" onClick={onCancel}>
            Cancel
          </button>
        )}
        <button className="button primary" disabled={busy}>
          {busy
            ? "Saving…"
            : editing
              ? "Save and relock"
              : "Create locked entry"}
        </button>
      </div>
    </form>
  );
}

function Auth({ session, refresh }: { session: Session; refresh: () => void }) {
  const [mode, setMode] = useState<"login" | "setup">(
    session.setup_required ? "setup" : "login",
  );
  const [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    if (mode === "setup" && password !== confirmation) {
      setError("Master password and confirmation do not match.");
      setBusy(false);
      return;
    }
    try {
      await api(mode === "setup" ? "/setup/" : "/login/", {
        method: "POST",
        body: JSON.stringify(
          mode === "setup"
            ? { username, password, password_confirmation: confirmation }
            : { username, password },
        ),
      });
      beginClientSession();
      setPassword("");
      setConfirmation("");
      refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div className="brand">
          <span className="brand-mark">P</span>
          <span>Purevault</span>
        </div>
        <p className="eyebrow">DELAYED ACCESS VAULT</p>
        <h1>{mode === "setup" ? "Create your vault" : "Welcome back"}</h1>
        <p className="lede">
          {session.signed_out_reason === "inactivity" && mode === "login"
            ? "Signed out after 10 minutes of inactivity. Sign in to continue."
            : mode === "setup"
            ? "Choose the credentials you will use to open this vault."
            : "A deliberate pause between impulse and access."}
        </p>
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor="username">Username</label>
            <input
              id="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
            />
          </div>
          <div className="field">
            <label htmlFor="password">Master password</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={
                mode === "setup" ? "new-password" : "current-password"
              }
              required
              minLength={mode === "setup" ? 12 : undefined}
            />
          </div>
          {mode === "setup" && (
            <div className="field">
              <label htmlFor="password-confirmation">
                Confirm master password
              </label>
              <input
                id="password-confirmation"
                type="password"
                value={confirmation}
                onChange={(e) => setConfirmation(e.target.value)}
                autoComplete="new-password"
                required
                minLength={12}
              />
              <small className="hint">
                Use at least 12 characters. There is no automated password
                recovery.
              </small>
            </div>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="button primary wide" disabled={busy}>
            {busy
              ? "Please wait…"
              : mode === "setup"
                ? "Create vault"
                : "Sign in"}
          </button>
        </form>
        {!session.setup_required && mode === "setup" && (
          <button className="text-button" onClick={() => setMode("login")}>
            Return to sign in
          </button>
        )}
        <p className="privacy-note">
          Your vault is stored on this server. Waiting pauses while the service
          is stopped.
        </p>
      </section>
    </main>
  );
}

function SettingsPanel({
  session,
  onSessionSaved,
  onAccountability,
  onClose,
}: {
  session: Session;
  onSessionSaved: () => void;
  onAccountability: (value: AccountabilitySettings) => void;
  onClose: () => void;
}) {
  const [zone, setZone] = useState(session.display_timezone);
  const [settings, setSettings] = useState<AccountabilitySettings | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [partnerId, setPartnerId] = useState<number | null>(null);
  const [partnerName, setPartnerName] = useState("");
  const [partnerEmail, setPartnerEmail] = useState("");
  const [partnerPassword, setPartnerPassword] = useState("");
  const [testDestination, setTestDestination] = useState("");
  const [emailForm, setEmailForm] = useState({
    enabled: false,
    host: "",
    port: "587",
    security: "starttls" as EmailConfiguration["security"],
    username: "",
    password_action: "keep",
    password: "",
    from_email: "",
    from_name: "",
    current_password: "",
  });
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const supportedZones =
    (
      Intl as typeof Intl & { supportedValuesOf?: (key: string) => string[] }
    ).supportedValuesOf?.("timeZone") || [];
  const load = useCallback(async (resetForm = false) => {
    const value = await api<AccountabilitySettings>("/accountability/");
    setSettings(value);
    onAccountability(value);
    if (resetForm) {
      setEmailForm((current) => ({
        ...current,
        enabled: value.email.enabled,
        host: value.email.host,
        port: String(value.email.port),
        security: value.email.security,
        username: value.email.username,
        password_action: "keep",
        password: "",
        from_email: value.email.from_email,
        from_name: value.email.from_name,
        current_password: "",
      }));
    }
  }, [onAccountability]);
  useEffect(() => {
    load(true).catch((err) => setError((err as Error).message));
  }, [load]);
  async function saveTimezone(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api("/account/timezone/", {
        method: "POST",
        body: JSON.stringify({ display_timezone: zone }),
      });
      await onSessionSaved();
      setNotice("Timezone saved.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function savePartner(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(partnerId ? `/accountability/partners/${partnerId}/` : "/accountability/partners/", {
        method: partnerId ? "PUT" : "POST",
        body: JSON.stringify({ name: partnerName, email: partnerEmail, current_password: partnerPassword }),
      });
      setPartnerId(null);
      setPartnerName("");
      setPartnerEmail("");
      setPartnerPassword("");
      await load();
      setNotice(partnerId ? "Partner updated and notices queued." : "Partner added and introduction queued.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function removePartner(partner: Partner) {
    if (!partnerPassword) {
      setError("Enter your current master password before removing a partner.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/accountability/partners/${partner.id}/`, {
        method: "DELETE",
        body: JSON.stringify({ current_password: partnerPassword }),
      });
      setPartnerPassword("");
      await load();
      setNotice("Partner removed and a removal notice queued to the previous address.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function saveEmail(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api("/accountability/email/", { method: "PUT", body: JSON.stringify(emailForm) });
      await load(true);
      setNotice("Email settings saved. Pending messages will retry automatically.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function sendTest(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<{ message: string }>("/accountability/email/test/", {
        method: "POST",
        body: JSON.stringify({ destination: testDestination, current_password: emailForm.current_password }),
      });
      setNotice(result.message);
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const setEmail = (key: string, value: string | boolean) =>
    setEmailForm((current) => ({ ...current, [key]: value }));
  return (
    <section className="panel settings-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">SETTINGS</p>
          <h2>Vault settings</h2>
        </div>
        <button className="button ghost small" onClick={onClose}>Close</button>
      </div>
      {settings?.warning && <p className="warning" role="status">{settings.warning}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="success" role="status">{notice}</p>}

      <div className="settings-section">
        <h3>Display timezone</h3>
        <form onSubmit={saveTimezone} className="timezone-form">
        <div className="field">
          <label htmlFor="display-timezone">Timezone</label>
          <input
            id="display-timezone"
            list="timezone-options"
            value={zone}
            onChange={(e) => setZone(e.target.value)}
            placeholder="Search timezones"
            required
          />
          <datalist id="timezone-options">
            <option value="UTC" />
            {supportedZones.map((value) => (
              <option value={value} key={value} />
            ))}
          </datalist>
          <small className="hint">
            Changes displayed times only; waiting periods stay the same.
          </small>
        </div>
        <div className="suggestion">
          <span>
            Browser timezone: <strong>{browserZone}</strong>
          </span>
          <button
            type="button"
            className="button ghost small"
            onClick={() => setZone(browserZone)}
          >
            Use browser timezone
          </button>
        </div>
        <div className="form-actions">
          <button className="button primary" disabled={busy}>
            {busy ? "Saving…" : "Save timezone"}
          </button>
        </div>
      </form>
      </div>

      <div className="settings-section">
        <div className="subheading">
          <div>
            <h3>Accountability partners</h3>
            <p>Partners receive notifications only. They cannot access the vault or approve requests.</p>
          </div>
          <span>{settings?.partners.length || 0}/{settings?.limits.partners || 20}</span>
        </div>
        <div className="partner-list">
          {settings?.partners.map((partner) => (
            <div className="partner-row" key={partner.id}>
              <div><strong>{partner.name}</strong><small>{partner.email}</small></div>
              <div className="row-actions">
                <button type="button" className="button ghost small" onClick={() => { setPartnerId(partner.id); setPartnerName(partner.name); setPartnerEmail(partner.email); }}>Edit</button>
                <button type="button" className="button danger small" disabled={busy} onClick={() => removePartner(partner)}>Remove</button>
              </div>
            </div>
          ))}
          {!settings?.partners.length && <p className="hint">No accountability partners are configured. Unlocking does not require a reason.</p>}
        </div>
        <form className="settings-grid" onSubmit={savePartner}>
          <div className="field"><label htmlFor="partner-name">Partner name</label><input id="partner-name" value={partnerName} maxLength={100} onChange={(e) => setPartnerName(e.target.value)} required /></div>
          <div className="field"><label htmlFor="partner-email">Partner email</label><input id="partner-email" type="email" value={partnerEmail} maxLength={254} onChange={(e) => setPartnerEmail(e.target.value)} required /></div>
          <div className="field full"><label htmlFor="partner-master">Current master password</label><input id="partner-master" type="password" value={partnerPassword} onChange={(e) => setPartnerPassword(e.target.value)} autoComplete="current-password" required /><small className="hint">Required to add, edit, or remove a partner.</small></div>
          <div className="form-actions full">
            {partnerId && <button type="button" className="button ghost" onClick={() => { setPartnerId(null); setPartnerName(""); setPartnerEmail(""); }}>Cancel edit</button>}
            <button className="button primary" disabled={busy || (settings?.partners.length === settings?.limits.partners && !partnerId)}>{partnerId ? "Save partner" : "Add partner"}</button>
          </div>
        </form>
      </div>

      <div className="settings-section">
        <h3>Email notifications</h3>
        <p className="section-copy">Email is optional. If delivery fails, messages stay queued and retry; unlock timing never changes.</p>
        <form className="settings-grid" onSubmit={saveEmail}>
          <label className="toggle full"><input type="checkbox" checked={emailForm.enabled} onChange={(e) => setEmail("enabled", e.target.checked)} /><span><strong>Enable email delivery</strong><small>Disabling does not erase pending messages.</small></span></label>
          <div className="field"><label htmlFor="smtp-host">SMTP server</label><input id="smtp-host" value={emailForm.host} maxLength={255} onChange={(e) => setEmail("host", e.target.value)} placeholder="smtp.example.com" /></div>
          <div className="field"><label htmlFor="smtp-port">Port</label><input id="smtp-port" inputMode="numeric" value={emailForm.port} onChange={(e) => setEmail("port", e.target.value)} /><small className="hint">Usually 587 for STARTTLS or 465 for SSL/TLS.</small></div>
          <div className="field"><label htmlFor="smtp-security">Connection security</label><select id="smtp-security" value={emailForm.security} onChange={(e) => setEmail("security", e.target.value)}><option value="starttls">STARTTLS</option><option value="ssl">SSL/TLS</option><option value="none">Unencrypted (advanced/local relay)</option></select></div>
          <div className="field"><label htmlFor="smtp-user">SMTP username</label><input id="smtp-user" value={emailForm.username} maxLength={255} onChange={(e) => setEmail("username", e.target.value)} autoComplete="username" /><small className="hint">Leave username and password empty for a deliberate unauthenticated relay.</small></div>
          <div className="field"><label htmlFor="password-action">SMTP password</label><select id="password-action" value={emailForm.password_action} onChange={(e) => setEmail("password_action", e.target.value)}><option value="keep">Keep saved password{settings?.email.password_set ? " (masked)" : ""}</option><option value="replace">Replace password</option><option value="clear">Clear password</option></select></div>
          {emailForm.password_action === "replace" && <div className="field"><label htmlFor="smtp-password">New SMTP password</label><input id="smtp-password" type="password" value={emailForm.password} maxLength={1024} onChange={(e) => setEmail("password", e.target.value)} autoComplete="new-password" required /></div>}
          <div className="field"><label htmlFor="from-email">From email</label><input id="from-email" type="email" value={emailForm.from_email} maxLength={254} onChange={(e) => setEmail("from_email", e.target.value)} /></div>
          <div className="field"><label htmlFor="from-name">From name (optional)</label><input id="from-name" value={emailForm.from_name} maxLength={100} onChange={(e) => setEmail("from_name", e.target.value)} /></div>
          <div className="field full"><label htmlFor="email-master">Current master password</label><input id="email-master" type="password" value={emailForm.current_password} onChange={(e) => setEmail("current_password", e.target.value)} autoComplete="current-password" required /></div>
          <div className="form-actions full"><button className="button primary" disabled={busy}>Save email settings</button></div>
        </form>
        <form className="test-form" onSubmit={sendTest}>
          <div className="field"><label htmlFor="test-destination">Test destination</label><input id="test-destination" type="email" value={testDestination} onChange={(e) => setTestDestination(e.target.value)} placeholder="recipient@example.com" required /></div>
          <button className="button secondary" disabled={busy || !settings?.email.enabled}>Send test email</button>
          <small className="hint full">Uses the current master password above. “Accepted by mail server” does not guarantee inbox delivery.</small>
        </form>
        <div className="queue-status" aria-live="polite">
          <span><strong>{settings?.queue.pending || 0}</strong> pending</span>
          <span><strong>{settings?.queue.sending || 0}</strong> sending</span>
          <span><strong>{settings?.queue.action_needed || 0}</strong> need attention</span>
          <span><strong>{settings?.queue.accepted || 0}</strong> accepted</span>
          <button type="button" className="button ghost small" onClick={() => load()}>Refresh</button>
        </div>
        {settings?.queue.latest_error && <p className="warning">{settings.queue.latest_error}</p>}
      </div>
    </section>
  );
}

function App() {
  const [session, setSession] = useState<Session | null>(null),
    [entries, setEntries] = useState<Entry[]>([]);
  const [accountability, setAccountability] = useState<AccountabilitySettings | null>(null);
  const [error, setError] = useState(""),
    [showCreate, setShowCreate] = useState(false),
    [editing, setEditing] = useState<Entry | null>(null),
    [showSettings, setShowSettings] = useState(false),
    [unlocking, setUnlocking] = useState<Entry | null>(null),
    [unlockReason, setUnlockReason] = useState(""),
    [unlockError, setUnlockError] = useState("");
  const [revealed, setRevealed] = useState<Record<number, string>>({}),
    [nowPerformance, setNowPerformance] = useState(performance.now()),
    [serverSync, setServerSync] = useState({
      epochMs: Date.now(),
      performanceMs: performance.now(),
    });
  const syncServerTime = (value: string) =>
    setServerSync({
      epochMs: new Date(value).getTime(),
      performanceMs: performance.now(),
    });
  useEffect(() => {
    const handleSessionEnded = (event: Event) => {
      const detail = (event as CustomEvent<SessionEndedDetail>).detail;
      setEntries([]);
      setRevealed({});
      setAccountability(null);
      setShowCreate(false);
      setEditing(null);
      setShowSettings(false);
      setUnlocking(null);
      setUnlockReason("");
      setUnlockError("");
      setError("");
      setSession((current) => ({
        authenticated: false,
        username: null,
        setup_required: current?.setup_required ?? false,
        server_time: current?.server_time ?? new Date().toISOString(),
        display_timezone: current?.display_timezone ?? "UTC",
        display_timezone_is_default: current?.display_timezone_is_default ?? true,
        signed_out_reason: detail.reason === "inactivity" ? "inactivity" : null,
      }));
    };
    window.addEventListener(SESSION_EVENT_KEY, handleSessionEnded);
    return () => window.removeEventListener(SESSION_EVENT_KEY, handleSessionEnded);
  }, []);
  const loadSession = useCallback(async () => {
    try {
      const value = await api<Session>("/session/");
      setSession(value);
      if (value.authenticated) setError("");
      syncServerTime(value.server_time);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);
  const loadEntries = useCallback(async () => {
    try {
      const value = await api<{ entries: Entry[]; server_time: string }>(
        "/entries/",
      );
      const sync = performance.now();
      const synced = value.entries.map((entry) => ({
        ...entry,
        sync_performance_ms: sync,
      }));
      setEntries(synced);
      syncServerTime(value.server_time);
      setRevealed((current) =>
        Object.fromEntries(
          Object.entries(current).filter(([id]) =>
            synced.some((e) => e.id === Number(id) && e.status === "available"),
          ),
        ),
      );
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);
  const acceptAccountability = useCallback((value: AccountabilitySettings) => {
    setAccountability(value);
  }, []);
  const loadAccountability = useCallback(async () => {
    try {
      setAccountability(await api<AccountabilitySettings>("/accountability/"));
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);
  useEffect(() => {
    loadSession();
  }, [loadSession]);
  useEffect(() => {
    if (session?.authenticated) {
      loadEntries();
      loadAccountability();
    }
    else {
      setEntries([]);
      setRevealed({});
      setAccountability(null);
      setShowCreate(false);
      setEditing(null);
      setShowSettings(false);
      setUnlocking(null);
      setUnlockReason("");
      setUnlockError("");
    }
  }, [session?.authenticated, loadEntries, loadAccountability]);
  useEffect(() => {
    const tick = window.setInterval(
      () => setNowPerformance(performance.now()),
      250,
    );
    const sync = window.setInterval(
      () => session?.authenticated && loadEntries(),
      5000,
    );
    return () => {
      clearInterval(tick);
      clearInterval(sync);
    };
  }, [session?.authenticated, loadEntries]);
  useEffect(() => {
    if (!session?.authenticated) return;
    let lastAcknowledgedAt = Number.NEGATIVE_INFINITY;
    let acknowledgmentRunning = false;
    const acknowledgeMeaningfulActivity = () => {
      const now = performance.now();
      if (!shouldAcknowledgeActivity(lastAcknowledgedAt, now, acknowledgmentRunning)) return;
      lastAcknowledgedAt = now;
      acknowledgmentRunning = true;
      api("/session/activity/", { method: "POST", body: "{}" })
        .catch(() => undefined)
        .finally(() => { acknowledgmentRunning = false; });
    };
    for (const eventName of MEANINGFUL_ACTIVITY_EVENTS)
      window.addEventListener(eventName, acknowledgeMeaningfulActivity, true);
    return () => {
      for (const eventName of MEANINGFUL_ACTIVITY_EVENTS)
        window.removeEventListener(eventName, acknowledgeMeaningfulActivity, true);
    };
  }, [session?.authenticated]);
  const replace = (entry: Entry) => {
    const synced = { ...entry, sync_performance_ms: performance.now() };
    setEntries((current) =>
      current.some((e) => e.id === entry.id)
        ? current.map((e) => (e.id === entry.id ? synced : e))
        : [...current, synced].sort((a, b) => a.title.localeCompare(b.title)),
    );
    if (entry.status !== "available")
      setRevealed((current) => {
        const next = { ...current };
        delete next[entry.id];
        return next;
      });
  };
  async function action(
    entry: Entry,
    name: "unlock" | "cancel" | "relock" | "reveal",
    reason = "",
  ) {
    setError("");
    try {
      if (name === "reveal") {
        const result = await api<{ password: string }>(
          `/entries/${entry.id}/reveal/`,
          { method: "POST", body: "{}" },
        );
        setRevealed((current) => ({ ...current, [entry.id]: result.password }));
      } else {
        const result = await api<{ entry: Entry }>(
          `/entries/${entry.id}/${name}/`,
          { method: "POST", body: JSON.stringify(name === "unlock" ? { reason } : {}) },
        );
        replace(result.entry);
      }
      return true;
    } catch (err) {
      const message = (err as Error).message;
      if (name === "unlock" && unlocking) setUnlockError(message);
      else setError(message);
      loadEntries();
      return false;
    }
  }
  const available = useMemo(
    () => entries.filter((e) => e.status === "available").length,
    [entries],
  );
  if (!session) return <div className="loading">Opening Purevault…</div>;
  if (!session.authenticated)
    return <Auth session={session} refresh={loadSession} />;
  return (
    <>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">P</span>
          <span>Purevault</span>
        </div>
        <div className="account">
          <span>{session.username}</span>
          <button
            className="button ghost small"
            onClick={() => setShowSettings((value) => !value)}
          >
            Settings
          </button>
          <button
            className="button ghost small"
            onClick={async () => {
              try {
                await api("/logout/", { method: "POST", body: "{}" });
              } finally {
                endClientSession({ reason: "explicit" });
              }
            }}
          >
            Sign out
          </button>
        </div>
      </header>
      <main className="app-shell">
        <section className="hero">
          <div>
            <p className="eyebrow">YOUR PRIVATE VAULT</p>
            <h1>Make access intentional.</h1>
            <p>
              Passwords stay locked until their full waiting period has passed.
              Waiting pauses while Purevault is stopped.
            </p>
          </div>
          <button
            className="button primary"
            onClick={() => {
              setEditing(null);
              setShowCreate(true);
            }}
          >
            + New entry
          </button>
        </section>
        <section className="stats">
          <div>
            <strong>{entries.length}</strong>
            <span>Entries</span>
          </div>
          <div>
            <strong>
              {entries.filter((e) => e.status === "waiting").length}
            </strong>
            <span>Waiting</span>
          </div>
          <div>
            <strong>{available}</strong>
            <span>Available</span>
          </div>
          <div className="clock">
            <span>Server time · UTC</span>
            <strong>
              {new Date(
                serverSync.epochMs +
                  (nowPerformance - serverSync.performanceMs),
              )
                .toISOString()
                .replace("T", " ")
                .slice(0, 19)}
            </strong>
            <small>Display zone: {session.display_timezone}</small>
          </div>
        </section>
        {error && (
          <p className="error banner" role="alert">
            {error}
          </p>
        )}
        {showSettings && (
          <SettingsPanel
            session={session}
            onSessionSaved={loadSession}
            onAccountability={acceptAccountability}
            onClose={() => setShowSettings(false)}
          />
        )}
        {unlocking && (
          <div className="dialog-backdrop" role="presentation">
            <form
              className="dialog"
              onSubmit={async (event) => {
                event.preventDefault();
                if (await action(unlocking, "unlock", unlockReason)) {
                  setUnlocking(null);
                  setUnlockReason("");
                  setUnlockError("");
                  loadAccountability();
                }
              }}
            >
              <p className="eyebrow">ACCOUNTABILITY NOTICE</p>
              <h2>Why are you unlocking {unlocking.title}?</h2>
              <p>Your reason and this entry title will be emailed separately to each accountability partner. Do not include a password or other secret.</p>
              {unlockError && <p className="error" role="alert">{unlockError}</p>}
              <div className="field">
                <label htmlFor="unlock-reason">Reason for unlock</label>
                <textarea id="unlock-reason" value={unlockReason} onChange={(event) => setUnlockReason(event.target.value)} maxLength={accountability?.limits.reason_characters || 1000} rows={5} required autoFocus />
                <small className="hint">{unlockReason.length}/{accountability?.limits.reason_characters || 1000} characters</small>
              </div>
              <div className="form-actions">
                <button type="button" className="button ghost" onClick={() => { setUnlocking(null); setUnlockReason(""); setUnlockError(""); }}>Cancel</button>
                <button className="button primary">Start unlock and notify partners</button>
              </div>
            </form>
          </div>
        )}
        {(showCreate || editing) && (
          <section className="panel">
            <div className="section-heading">
              <div>
                <p className="eyebrow">
                  {editing ? "SECURITY CHANGE" : "NEW ENTRY"}
                </p>
                <h2>
                  {editing
                    ? `Update ${editing.title}`
                    : "Create a locked entry"}
                </h2>
              </div>
            </div>
            <EntryForm
              editing={editing}
              onCancel={() => {
                setShowCreate(false);
                setEditing(null);
              }}
              onCreated={(entry) => {
                replace(entry);
                setShowCreate(false);
                setEditing(null);
              }}
            />
          </section>
        )}
        <section>
          <div className="section-heading">
            <div>
              <p className="eyebrow">VAULT ENTRIES</p>
              <h2>
                {entries.length ? "Protected passwords" : "Your vault is ready"}
              </h2>
            </div>
          </div>
          {!entries.length ? (
            <div className="empty">
              <div className="empty-icon">⌛</div>
              <h3>No entries yet</h3>
              <p>Create an entry to start a delayed-access lock.</p>
              <button
                className="button primary"
                onClick={() => setShowCreate(true)}
              >
                Create first entry
              </button>
            </div>
          ) : (
            <div className="entry-grid">
              {entries.map((entry) => {
                const deadline =
                  entry.status === "waiting"
                    ? entry.eligible_at
                    : entry.access_until;
                return (
                  <article
                    className={`entry-card ${entry.status}`}
                    key={entry.id}
                  >
                    <div className="card-top">
                      <span className={`status ${entry.status}`}>
                        <i />
                        {entry.status}
                      </span>
                      <span className="policy-label">
                        {entry.relock_policy === "automatic"
                          ? "Auto relock"
                          : "Manual relock"}
                      </span>
                    </div>
                    <h3>{entry.title}</h3>
                    <dl>
                      <div>
                        <dt>Unlock delay</dt>
                        <dd>{duration(entry.unlock_delay_seconds)}</dd>
                      </div>
                      <div>
                        <dt>Access window</dt>
                        <dd>
                          {entry.relock_policy === "manual"
                            ? "Until locked"
                            : duration(entry.relock_after_seconds)}
                        </dd>
                      </div>
                    </dl>
                    {entry.status !== "locked" && (
                      <div className="timer">
                        <span>
                          {entry.status === "waiting"
                            ? "Time remaining"
                            : entry.access_until
                              ? "Relocks in"
                              : "Available until locked"}
                        </span>
                        <strong>{remaining(entry, nowPerformance)}</strong>
                        <small>
                          {entry.status === "waiting"
                            ? "Estimated local availability"
                            : "Estimated local deadline"}
                          : {localTime(deadline, session.display_timezone)}
                        </small>
                      </div>
                    )}
                    {revealed[entry.id] !== undefined && (
                      <div className="reveal">
                        <p>Revealed password</p>
                        <Secret value={revealed[entry.id]} />
                        <button
                          className="button secondary small"
                          onClick={async () => {
                            await navigator.clipboard.writeText(
                              revealed[entry.id],
                            );
                          }}
                        >
                          Copy exactly
                        </button>
                        <small>
                          Relocking cannot revoke a copy already obtained.
                        </small>
                      </div>
                    )}
                    <div className="card-actions">
                      {entry.status === "locked" && (
                        <button
                          className="button primary"
                          onClick={() => { if (accountability?.partners.length) { setUnlockError(""); setUnlocking(entry); } else action(entry, "unlock"); }}
                        >
                          Start unlock
                        </button>
                      )}
                      {entry.status === "waiting" && (
                        <button
                          className="button danger"
                          onClick={() => action(entry, "cancel")}
                        >
                          Cancel request
                        </button>
                      )}
                      {entry.status === "available" && (
                        <>
                          <button
                            className="button primary"
                            onClick={() => action(entry, "reveal")}
                          >
                            {revealed[entry.id] === undefined
                              ? "Reveal password"
                              : "Reveal again"}
                          </button>
                          <button
                            className="button danger"
                            onClick={() => action(entry, "relock")}
                          >
                            Lock now
                          </button>
                          <button
                            className="button ghost"
                            onClick={() => setEditing(entry)}
                          >
                            Edit & relock
                          </button>
                        </>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </section>
        <footer>
          <p>
            Waiting periods count only while Purevault is running. Automatic
            access locks again after an interrupted restart. Anyone controlling
            the host, storage, keys, or application code remains inside the
            trust boundary.
          </p>
        </footer>
      </main>
    </>
  );
}

export default App;

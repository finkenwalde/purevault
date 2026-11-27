import { FormEvent, useEffect, useRef, useState } from "react";

type BackupStatus = {
  configured: boolean;
  interval_days: number;
  retention: number;
  next_run_at: string | null;
  last_success_at: string | null;
  last_error: string;
  running: boolean;
  files: { name: string; bytes: number }[];
};

type Api = <T>(path: string, options?: RequestInit, read?: (response: Response) => Promise<T>) => Promise<T>;

export function BackupsPanel({ active, api, formatDate, onRestore }: {
  active: boolean;
  api: Api;
  formatDate: (date: string) => string;
  onRestore: () => void;
}) {
  const [status, setStatus] = useState<BackupStatus | null>(null);
  const [interval, setIntervalDays] = useState("0");
  const [retention, setRetention] = useState("7");
  const [rotating, setRotating] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const initialized = useRef(false);
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    async function refresh() {
      try {
        const value = await api<BackupStatus>("/backups/", { signal: controller.signal });
        if (controller.signal.aborted) return;
        if (!initialized.current) {
          setIntervalDays(String(value.interval_days));
          setRetention(String(value.retention));
          initialized.current = true;
        }
        setStatus(value);
      } catch (failure) {
        if (!controller.signal.aborted) setError((failure as Error).message);
      }
    }
    void refresh();
    const timer = window.setInterval(refresh, 15000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [active, api]);

  async function perform(operation: (signal: AbortSignal) => Promise<string>) {
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted) return;
    setPending(true);
    setError("");
    setNotice("");
    try {
      const message = await operation(signal);
      if (!signal.aborted) setNotice(message);
    } catch (failure) {
      if (!signal.aborted) setError((failure as Error).message);
    } finally {
      if (!signal.aborted) setPending(false);
    }
  }

  const editingPassphrase = status !== null && (!status.configured || rotating);

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    const recovery = editingPassphrase ? {
      current_password: fields.get("current_password"),
      passphrase: fields.get("passphrase"),
      confirmation: fields.get("confirmation"),
    } : {};
    form.reset();
    void perform(async (signal) => {
      const value = await api<BackupStatus>("/backups/", {
        method: "PUT",
        signal,
        body: JSON.stringify({ ...recovery, interval_days: Number(interval), retention: Number(retention) }),
      });
      setStatus(value);
      setRotating(false);
      return "Backup settings saved.";
    });
  }

  function restore(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    form.reset();
    void perform(async (signal) => {
      const value = await api<{ restored: number }>("/backups/restore/", {
        method: "POST", signal, body: fields,
      });
      onRestore();
      return `Restored ${value.restored} locked ${value.restored === 1 ? "entry" : "entries"}.`;
    });
  }

  function download(name: string) {
    void perform(async (signal) => {
      const blob = await api<Blob>(`/backups/files/${encodeURIComponent(name)}/`, { signal }, (response) => response.blob());
      if (signal.aborted) return "";
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      return "Encrypted download requested.";
    });
  }

  return (
    <div className="backup-sections">
      <div>
        <h2>Encrypted backups</h2>
        <p>Keep a downloaded copy off this host. Anyone with both the file and its recovery passphrase can decrypt its passwords independently, even during an unlock delay. Losing the passphrase makes that file unrecoverable.</p>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="success" role="status">{notice}</p>}
      {status && (
        <>
          <form className="backup-form" onSubmit={save}>
            {editingPassphrase && (
              <>
                <div className="field"><label htmlFor="backup-passphrase">New recovery passphrase</label><input id="backup-passphrase" name="passphrase" type="password" minLength={20} maxLength={256} autoComplete="new-password" required /></div>
                <div className="field"><label htmlFor="backup-confirmation">Confirm recovery passphrase</label><input id="backup-confirmation" name="confirmation" type="password" minLength={20} maxLength={256} autoComplete="new-password" required /></div>
                <div className="field"><label htmlFor="backup-master">Current master password</label><input id="backup-master" name="current_password" type="password" autoComplete="current-password" required /></div>
                <small className="hint full">Use several randomly chosen words or a password manager. Keep this passphrase separate from your vault and backups. Old copies still require their original passphrase.</small>
              </>
            )}
            <div className="field">
              <label htmlFor="backup-interval">Schedule</label>
              <select id="backup-interval" value={interval} onChange={(event) => setIntervalDays(event.target.value)}>
                <option value="0">Manual only</option>
                <option value="1">Every 24 hours</option>
                <option value="7">Every 7 days</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="backup-retention">Keep last successful backups</label>
              <input id="backup-retention" type="number" min={1} max={30} value={retention} onChange={(event) => setRetention(event.target.value)} required />
            </div>
            <div className="backup-actions full">
              <button className="button primary" disabled={pending}>Save backup settings</button>
              {status.configured && <button type="button" className="button ghost" disabled={pending} onClick={() => setRotating(!rotating)}>{rotating ? "Cancel passphrase change" : "Change recovery passphrase"}</button>}
            </div>
          </form>
          <section>
            <h3>Local copies</h3>
            <p>Last success: {status.last_success_at ? formatDate(status.last_success_at) : "None yet"}</p>
            <p>Next scheduled backup: {status.next_run_at ? formatDate(status.next_run_at) : "Off"}</p>
            {status.last_error && <p className="error" role="status">{status.last_error}</p>}
            <button className="button primary" disabled={pending || status.running || !status.configured} onClick={() => void perform(async (signal) => {
              try {
                await api("/backups/run/", { method: "POST", body: "{}", signal });
              } finally {
                if (!signal.aborted) setStatus(await api<BackupStatus>("/backups/", { signal }));
              }
              return "Encrypted backup saved.";
            })}>{status.running ? "Backup in progress" : "Back up now"}</button>
            {status.files.length > 0 && <ul className="backup-files">
              {status.files.map(({ name, bytes }) => (
                <li key={name}>
                  <span>{name}<small>{Math.ceil(bytes / 1024)} KiB</small></span>
                  <button type="button" className="button ghost small" disabled={pending} onClick={() => download(name)} aria-label={`Download ${name}`}>Download</button>
                </li>
              ))}
            </ul>}
          </section>
        </>
      )}
      <section>
        <h3>Restore into this vault</h3>
        <p>Restore requires an empty vault. Imported entries start locked and require their full configured delay.</p>
        <form className="backup-form" onSubmit={restore}>
          <div className="field full"><label htmlFor="restore-file">Encrypted backup file</label><input id="restore-file" name="backup" type="file" accept=".pvbackup" required /></div>
          <div className="field"><label htmlFor="restore-phrase">Backup recovery passphrase</label><input id="restore-phrase" name="passphrase" type="password" maxLength={256} autoComplete="off" required /></div>
          <div className="field"><label htmlFor="restore-master">Current vault master password</label><input id="restore-master" name="current_password" type="password" autoComplete="current-password" required /></div>
          <div className="backup-actions full"><button className="button secondary" disabled={pending}>Restore backup</button></div>
        </form>
      </section>
    </div>
  );
}

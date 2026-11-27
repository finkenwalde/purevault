import { FormEvent, useEffect, useRef, useState } from "react";

export function RestoreBackupPanel({ api, onRestore }: {
  api: <T>(path: string, options?: RequestInit) => Promise<T>;
  onRestore: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);

  async function restore(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted || pending) return;
    const form = event.currentTarget;
    const fields = new FormData(form);
    form.reset();
    setPending(true);
    setError("");
    setNotice("");
    try {
      const { restored } = await api<{ restored: number }>("/backups/restore/", {
        method: "POST", signal, body: fields,
      });
      if (signal.aborted) return;
      onRestore();
      setNotice(`Restored ${restored} locked ${restored === 1 ? "entry" : "entries"}.`);
    } catch (failure) {
      if (!signal.aborted) setError((failure as Error).message);
    } finally {
      if (!signal.aborted) setPending(false);
    }
  }

  return (
    <>
      <h2>Restore backup</h2>
      <p>Use a previously downloaded Purevault backup file and its original recovery passphrase. Restore requires an empty vault. Imported entries start locked and require their full configured delay.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="success" role="status">{notice}</p>}
      <form className="backup-form" onSubmit={restore}>
        <div className="field full">
          <label htmlFor="restore-file">Encrypted backup file</label>
          <input id="restore-file" name="backup" type="file" accept=".pvbackup" required />
        </div>
        <div className="field">
          <label htmlFor="restore-phrase">Backup recovery passphrase</label>
          <input id="restore-phrase" name="passphrase" type="password" maxLength={256} autoComplete="off" required />
        </div>
        <div className="field">
          <label htmlFor="restore-master">Current vault master password</label>
          <input id="restore-master" name="current_password" type="password" autoComplete="current-password" required />
        </div>
        <div className="form-actions full">
          <button className="button primary" disabled={pending}>{pending ? "Restoring…" : "Restore backup"}</button>
        </div>
      </form>
    </>
  );
}

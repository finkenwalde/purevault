# Purevault architecture and security boundary

Purevault v1 is a single-user, self-hosted delayed-access password vault. Django owns authentication, authorization, timing, persistence, and secret release. React provides the browser interface but never decides whether access is allowed. SQLite is appropriate for this single-instance use case and resides in a persistent Docker volume.

## Release model

Every entry belongs to an authenticated owner. A new entry starts locked. Starting an unlock creates one durable grant with a target on Purevault's accumulated application-runtime clock. Repeating the request preserves an active grant. Cancelling deletes it, so a later request begins the full delay again.

For automatic relocking, the access target is calculated from the unlock target, not from the first reveal. Eligibility is the half-open runtime interval `[eligible_runtime, access_until_runtime)`. At the exact access target the grant is expired. Manual grants remain eligible until explicitly relocked. Reveal and edit refresh the clock and check eligibility again inside their database transaction; metadata never contains plaintext. A release and cancellation that race are ordered by the database: a completed release may return plaintext, while a cancellation committed first makes release fail. Relocking cannot revoke plaintext already copied or observed.

Changing a secret or security policy requires currently eligible access. A plain save preserves the existing grant's original eligibility and finite access deadline exactly; changed delay and relock settings apply only to the next unlock. “Save changes and relock” updates the entry and deletes the grant in the same transaction. Grant expiry is stored on the grant rather than inferred from mutable entry policy, so changing an automatic policy to manual cannot turn the current window into indefinite access. A blank replacement-password field retains the encrypted secret, and plaintext is fetched only by an explicit eligibility-checked reveal.

## Durations and clocks

Durations are positive whole values in seconds, minutes, hours, or days. A day is exactly 86,400 seconds. Unlock delays are limited to 365 days. Automatic access windows are limited to 30 days. The backend rejects zero, negative, fractional, unknown-unit, and out-of-range values.

One container-level generation uses Python's monotonic clock and a durable SQLite accumulator. A supervised background timekeeper checkpoints every 250 milliseconds even with no browser open. Gunicorn workers share the same generation and use compare-and-swap updates, so concurrent readers cannot credit an interval twice. Stale workers cannot advance a newer generation. Unexpected negative readings or heartbeat gaps over two seconds receive no credit; checkpoints never move backward.

Restart initialization sets a new monotonic baseline without crediting downtime. Waiting therefore pauses while the service is stopped, and an abrupt stop can conservatively lose up to the last uncheckpointed fraction. Manual grants remain available across restart. An automatic grant that had reached its access window is invalidated when continuity is interrupted or the application restarts, preventing a frozen clock from extending access. Migration from the earlier wall-clock release model clears legacy unlock grants but preserves accounts, entries, encrypted secrets, and policies; users start a new full wait.

The API supplies authoritative remaining durations. Browser countdowns and the selected-timezone clock use a server timestamp plus `performance.now()` only for display and resynchronize every five seconds. Displayed dates are estimates for convenience, never release inputs. Each account can persist a valid IANA display timezone in Settings. UTC is the default. IANA rules provide DST-aware presentation. Browser clock and timezone changes do not alter policy.

This protects against ordinary wall-clock adjustments, browser clock changes, and unverified downtime. A person controlling the host can still modify the database, kernel, process, or executable code; Purevault does not provide a trusted external enforcement boundary.

## Secrets and authentication

Django's session authentication and password hashers protect the master password. Vault secrets are independently encrypted with Fernet authenticated encryption and a random application key in the `purevault_keys` volume. The master password authenticates the account; it is not the sole cryptographic barrier to a host operator. No bearer token is placed in browser storage.

The application key is not in the image or source tree. If encrypted rows exist and the key is missing, Purevault fails instead of silently replacing it. Recovery from volume loss requires both the database and its matching key. Keeping their copies separate where practical reduces accidental exposure.

There is no Django admin route, application administrator login, staff/superuser exception, emergency unlock, export endpoint, or maintenance command that bypasses delays. A person controlling Docker, storage, keys, clock, process memory, backups, or executable code remains inside the trust boundary. Purevault does not claim tamper resistance or zero knowledge against that person.

Purevault can import [legacy encrypted backups](BACKUPS.md) into an empty vault. Imported entries start locked. Anyone who holds a legacy file and its recovery passphrase can still decrypt that copy outside Purevault.

First-run account creation is a tokenless browser flow for the loopback-only local deployment. An atomic installation-state claim permits exactly one account, and the presence of any existing account independently keeps setup closed if the state flag is inconsistent. Complete first-run setup before changing the loopback binding or exposing the service through a LAN or public reverse proxy.

## Session inactivity lifecycle

An authenticated browser session expires after 10 minutes without meaningful keyboard, pointer, or touch activity. The server records the last acknowledged activity using the same trusted accumulated-runtime clock used elsewhere, but the session timestamp is separate from every vault grant and never participates in password eligibility. Background entry, countdown, and session polling validate the session without refreshing it.

The browser sends a throttled, CSRF-protected acknowledgment only after real interaction. The server ignores client timestamps and stamps its own current runtime value. Every authenticated API request validates the stored runtime value and container generation before its handler runs, and protected handlers revalidate it before sensitive work. At exactly 10 minutes, or when metadata is missing, invalid, ahead of the runtime clock, or from a previous container generation, the session is flushed. An activity request received after expiry cannot revive it.

Explicit and inactivity sign-out clear client-side revealed values and drafts. Tabs coordinate a session-ending event through same-origin browser storage, while deletion or expiry of the shared server session remains authoritative. A client generation guard rejects responses that began before sign-out, preventing a delayed reveal or settings response from rebuilding authenticated state. Container restart conservatively expires existing browser sessions, so an upgrade can require signing in again.

Session expiry does not delete or modify unlock grants, pause the runtime clock, relock manual entries, or change automatic deadlines. Signing in again loads the current server state and does not reveal a secret by itself.

## HTTP deployment

The included Compose file binds plain HTTP to loopback for local use. Do not expose it to a network. For remote use, place it behind a maintained HTTPS reverse proxy, use a trusted hostname, configure CSRF trusted origins, enable secure cookies and redirect, and set HSTS only after HTTPS is proven. Keep Django `DEBUG` disabled.

## Potential future features

- Multiple independent accounts with private, owner-scoped vaults.
- Independently hosted enforcement, which could strengthen the host-administrator boundary.
- Arbitrary notes and recovery-code bundles.

Purevault is distributed under the [MIT License](../LICENSE).


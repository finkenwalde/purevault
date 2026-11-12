# Purevault architecture and security boundary

Purevault v1 is a single-user, self-hosted delayed-access password vault. Django owns authentication, authorization, timing, persistence, and secret release. React provides the browser interface but never decides whether access is allowed. SQLite is appropriate for this single-instance use case and resides in a persistent Docker volume.

## Release model

Every entry belongs to an authenticated owner. A new entry starts locked. Starting an unlock creates one durable grant with a target on Purevault's accumulated application-runtime clock. Repeating the request preserves an active grant. Cancelling deletes it, so a later request begins the full delay again.

For automatic relocking, the access target is calculated from the unlock target, not from the first reveal. Eligibility is the half-open runtime interval `[eligible_runtime, access_until_runtime)`. At the exact access target the grant is expired. Manual grants remain eligible until explicitly relocked. Reveal and edit refresh the clock and check eligibility again inside their database transaction; metadata never contains plaintext. A release and cancellation that race are ordered by the database: a completed release may return plaintext, while a cancellation committed first makes release fail. Relocking cannot revoke plaintext already copied or observed.

Changing a secret or security policy requires currently eligible access and relocks the entry after the change. This simple rule prevents an edit from extending an existing grant.

## Durations and clocks

Durations are positive whole values in seconds, minutes, hours, or days. A day is exactly 86,400 seconds. Unlock delays are limited to 365 days. Automatic access windows are limited to 30 days. The backend rejects zero, negative, fractional, unknown-unit, and out-of-range values.

One container-level generation uses Python's monotonic clock and a durable SQLite accumulator. A supervised background timekeeper checkpoints every 250 milliseconds even with no browser open. Gunicorn workers share the same generation and use compare-and-swap updates, so concurrent readers cannot credit an interval twice. Stale workers cannot advance a newer generation. Unexpected negative readings or heartbeat gaps over two seconds receive no credit; checkpoints never move backward.

Restart initialization sets a new monotonic baseline without crediting downtime. Waiting therefore pauses while the service is stopped, and an abrupt stop can conservatively lose up to the last uncheckpointed fraction. Manual grants remain available across restart. An automatic grant that had reached its access window is invalidated when continuity is interrupted or the application restarts, preventing a frozen clock from extending access. Migration from the earlier wall-clock release model clears legacy unlock grants but preserves accounts, entries, encrypted secrets, and policies; users start a new full wait.

The API supplies authoritative remaining durations. Browser countdowns use `performance.now()` only for animation and resynchronize every five seconds. UTC and selected-timezone dates are estimates for convenience, never release inputs. Each account can persist a valid IANA display timezone; `PUREVAULT_DISPLAY_TIMEZONE` is the fallback. IANA rules provide DST-aware presentation. Browser clock and timezone changes do not alter policy.

This protects against ordinary wall-clock adjustments, browser clock changes, and unverified downtime. A person controlling the host can still modify the database, kernel, process, or executable code; Purevault does not provide a trusted external enforcement boundary.

## Secrets and authentication

Django's session authentication and password hashers protect the master password. Vault secrets are independently encrypted with Fernet authenticated encryption and a random application key in the `purevault_keys` volume. The master password authenticates the account; it is not the sole cryptographic barrier to a host operator. No bearer token is placed in browser storage.

The application key is not in the image or source tree. If encrypted rows exist and the key is missing, Purevault fails instead of silently replacing it. The database and key must both be backed up; losing the key makes entries unrecoverable. Keeping their backups separate where practical reduces accidental exposure.

There is no Django admin route, application administrator login, staff/superuser exception, emergency unlock, export endpoint, or maintenance command that bypasses delays. A person controlling Docker, storage, keys, clock, process memory, backups, or executable code remains inside the trust boundary. Purevault does not claim tamper resistance or zero knowledge against that person.

First-run account creation is a tokenless browser flow for the loopback-only local deployment. An atomic installation-state claim permits exactly one account, and the presence of any existing account independently keeps setup closed if the state flag is inconsistent. Complete first-run setup before changing the loopback binding or exposing the service through a LAN or public reverse proxy.

## HTTP deployment

The included Compose file binds plain HTTP to loopback for local use. Do not expose it to a network. For remote use, place it behind a maintained HTTPS reverse proxy, use a trusted hostname, configure CSRF trusted origins, enable secure cookies and redirect, and set HSTS only after HTTPS is proven. Keep Django `DEBUG` disabled.

## Potential future features

- Multiple independent accounts with private, owner-scoped vaults.
- Accountability-partner approval through an emailed link that opens a confirmation page. A GET or link visit must never approve because email security scanners may visit links automatically.
- Policies requiring both partner approval and elapsed delay.
- Rules preventing removal of a partner or disabling approval from becoming a shortcut.
- Approval metadata without disclosing secrets to partners.
- Independently hosted enforcement, which could strengthen the host-administrator boundary. Approval enforced only by the same local host does not.
- Arbitrary notes and recovery-code bundles.
- Encrypted export/import that preserves each entry's settings and imports every entry locked, with optional cloud destinations later. Key recovery and cross-installation portability remain unresolved design questions.

No open-source license has been selected. Repository owners should choose one before inviting redistribution or contributions.


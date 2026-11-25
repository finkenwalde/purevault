# Purevault deployment and maintenance

The recommended first installation is the [Docker Compose quick start](../README.md#quick-start-on-your-computer). It builds the application from the downloaded source, binds it to `127.0.0.1`, and stores the database and encryption key in persistent Docker volumes.

The source-build quick start remains the supported default until the first GHCR image has been successfully published, made public, and anonymously pull-tested. In `compose.yaml`, `build: .` means “build from this source folder,” while `image: purevault:local` names the resulting local image. A GitHub repository URL is not a Docker image name.

Maintainers can follow the [container image publication guide](PUBLISHING.md) to create `ghcr.io/finkenwalde/purevault`. After that image is confirmed public, the optional `compose.image.yaml` provides the same project, service, volume, environment, and security defaults without building locally. It initially supports only `linux/amd64`.

## Git checkout alternative

Git is optional. If it is already installed, you can use a checkout instead of downloading a ZIP:

```powershell
git clone https://github.com/finkenwalde/purevault.git
cd purevault
docker compose up --build -d
```

Open [http://localhost:8127](http://localhost:8127) after the service becomes healthy.

## Docker Run option

Run these commands from the source folder containing `Dockerfile`:

```powershell
docker volume create purevault_data
docker volume create purevault_keys
docker build -t purevault:local .
docker run -d --name purevault-web --restart unless-stopped -p 127.0.0.1:8127:8000 --mount source=purevault_data,target=/data --mount source=purevault_keys,target=/keys --security-opt no-new-privileges --cap-drop ALL purevault:local
```

Then open [http://localhost:8127](http://localhost:8127). Do not run the Compose and Docker Run examples at the same time because both use port `8127`.

Manage this container with:

```powershell
docker stop purevault-web
docker start purevault-web
docker logs purevault-web
```

The named volumes survive stopping or removing the container. Do not delete them unless you intentionally want to erase the installation.

The standalone Docker Run example uses volumes named `purevault_data` and `purevault_keys`. A Compose installation normally uses `purevault_purevault_data` and `purevault_purevault_keys`. Switching between these deployment methods does not automatically reuse the same vault; do not switch methods as an update procedure.

After the first public GHCR publication is verified, the equivalent image pull and Docker Run command is:

```powershell
docker pull ghcr.io/finkenwalde/purevault:latest
docker run -d --name purevault-web --restart unless-stopped -p 127.0.0.1:8127:8000 --mount source=purevault_data,target=/data --mount source=purevault_keys,target=/keys --security-opt no-new-privileges --cap-drop ALL ghcr.io/finkenwalde/purevault:latest
```

Do not use those commands before the package is public and an anonymous pull has succeeded. For an existing Compose installation, use the carefully documented `compose.image.yaml` procedure instead of changing to the standalone Docker Run volume names.

## Status and logs

For a Compose installation:

```powershell
docker compose ps
docker compose logs web
curl.exe http://localhost:8127/health/
```

The internal `/health/` endpoint reports status only. Docker Desktop's **Containers** view provides the same basic status and log access without terminal commands.

## Backup and restore

Stop Purevault before taking a backup. Back up its data and key volumes together, label them as one set, and store them securely. The data volume contains the SQLite database; the separate key volume contains the encryption key required to decrypt vault entries.

The standard Compose project creates `purevault_purevault_data` and `purevault_purevault_keys`. The standalone Docker Run example creates `purevault_data` and `purevault_keys`. Confirm which deployment you are backing up with `docker volume ls`; never mix a data volume from one installation with a key volume from another.

Key loss is unrecoverable. Never restore a database with a different key. Keeping copies of the database and key backup separate where practical reduces accidental exposure.

Purevault intentionally has no plaintext export or forgotten-master-password recovery in v1. An authenticated master-password change is supported by the backend; a dedicated settings screen is planned.

Encrypted export/import is a future feature, not a current backup method. Its intended behavior is to preserve each entry's timing and relock settings while importing every entry locked. Cloud destinations may be added later. Key recovery and cross-installation portability still require design work.

## Updating safely

Back up both volumes before updating. Do not overwrite an old extracted folder blindly, and do not delete volumes.

For a Git checkout, review the incoming changes, then update and rebuild from the same folder:

```powershell
git pull --ff-only
docker compose up --build -d
```

For a ZIP installation, stop the existing installation from its old folder and extract the new source into a separate folder. If you created a local `.env` file, copy it privately into the new folder; do not publish or commit it. Then run `docker compose up --build -d` from the new folder. The fixed Compose project name `purevault` keeps using the existing `purevault_purevault_data` and `purevault_purevault_keys` volumes. Keep the old source folder until the updated service is healthy and your data is present.

Do not change the Compose project or volume names during an update. Never use `docker compose down --volumes` as an update step.

## Local configuration

The defaults require no `.env` file. Optional variables and neutral examples are documented in [`.env.example`](../.env.example). After signing in, use **Settings** to choose a display timezone or accept the browser suggestion.

The included configuration publishes plain HTTP only on `127.0.0.1`. It is intended for use from the same computer.

## Remote and HTTPS deployment

Do not expose the default HTTP service directly to a LAN or the public internet. Complete first-run account creation while access is restricted, then use a maintained HTTPS reverse proxy. The relevant settings are:

```text
PUREVAULT_BIND_ADDRESS=127.0.0.1
PUREVAULT_ALLOWED_HOSTS=vault.example.com
PUREVAULT_CSRF_TRUSTED_ORIGINS=https://vault.example.com
PUREVAULT_SECURE_COOKIES=true
PUREVAULT_SECURE_SSL_REDIRECT=true
PUREVAULT_HSTS_SECONDS=31536000
PUREVAULT_TRUST_PROXY_HEADERS=true
```

These are neutral examples, not values to copy without understanding the deployment. Set HSTS only after HTTPS is working correctly. Enable `PUREVAULT_TRUST_PROXY_HEADERS` only when a trusted reverse proxy removes client-supplied forwarding headers and sets `X-Forwarded-Proto` itself.

A proxy running directly on the host can reach Purevault's loopback binding. A proxy in another container cannot use its own `localhost` to reach Purevault; attach both services to a deliberately scoped Docker network. If the proxy is on another machine, loopback is unreachable, so use a restricted interface and firewall rule instead of publishing the first-run setup broadly.

The internal `/health/` endpoint remains available over HTTP for Docker health checks when HTTPS redirect is enabled and exposes status only. Application routes still use the configured host, origin, secure-cookie, and proxy policy.

## Accountability email operation

Partners and SMTP settings are configured in the authenticated **Settings** screen. SMTP passwords are encrypted with the same installation key used for other protected application data, so the key volume is required to restore email settings and pending messages. SMTP is configured in the application rather than through environment variables or a setup token.

The container supervises a mail dispatcher alongside the web server and runtime timekeeper. Pending notifications retry automatically after restarts and after email settings are corrected. Mail failures do not stop the container and never change unlock eligibility. No external queue service is required.

Use a provider-supported secure mode. STARTTLS commonly uses port 587 and SSL/TLS commonly uses port 465, but provider-specific ports are accepted. Unencrypted delivery is intended only for an advanced, deliberately configured local relay. TLS certificate verification is always enabled for encrypted modes.

Purevault cannot guarantee delivery while the host is offline, SMTP is misconfigured, a provider rejects mail, or the host operator interferes. It also cannot prevent a rare duplicate if the process stops after SMTP acceptance but before that acceptance is recorded locally.

Remote deployment requires informed administration and is outside the beginner quick start. See [HTTP deployment and the security boundary](ARCHITECTURE.md#http-deployment) before changing the loopback binding.

## Development and tests

The production image builds React into static assets and runs Django with Gunicorn; Node.js is absent from the runtime image.

```powershell
docker build --target frontend-build -t purevault-frontend-check .
docker build -t purevault:local .
docker run --rm --user root --entrypoint python --mount type=tmpfs,destination=/data --mount type=tmpfs,destination=/keys -e PUREVAULT_DJANGO_SECRET_KEY=test-only-container -e PUREVAULT_DATA_DIR=/data -e PUREVAULT_KEY_FILE=/keys/app.key purevault:local manage.py test
```

The test command bypasses the production entrypoint and uses temporary storage. It cannot rotate a running installation's clock generation or touch its database and key volumes.

## Security boundary

Purevault enforces delays in its supported application and API paths. It cannot protect against someone who controls the host, containers, storage, encryption keys, server clock, backups, process memory, or application code. It is not tamper-proof or a zero-knowledge system. Read [Purevault architecture and security boundary](ARCHITECTURE.md) before relying on it for sensitive data.

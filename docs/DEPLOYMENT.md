# Purevault deployment and maintenance

The recommended first installation is the [Docker Compose quick start](../README.md#quick-start-on-your-computer). It builds the application from the downloaded source, binds it to `127.0.0.1`, and stores the database and encryption key in persistent Docker volumes.

Use the source-build quick start until the new port-8080 GHCR build is public and anonymously pullable.

## Prebuilt image quick start

Once the new port-8080 `linux/amd64` image is public and verified, open a terminal in the extracted source folder and run:

```powershell
docker compose -f compose.image.yaml up -d --pull always
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). Existing installations should follow the [port upgrade steps](#upgrading-to-port-8080) first. An image built with the old internal port 8000 needs its matching Compose file or a published mapping targeting 8000. See the [publication guide](PUBLISHING.md) to publish and verify the new build.

## Git checkout alternative

Git is optional. If it is already installed, you can use a checkout instead of downloading a ZIP:

```powershell
git clone https://github.com/finkenwalde/purevault.git
cd purevault
docker compose up --build -d
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080) after the service becomes healthy.

## Docker Run option

Run these commands from the source folder containing `Dockerfile`:

```powershell
docker volume create purevault_data
docker volume create purevault_keys
docker build -t purevault:local .
docker run -d --name purevault-web --restart unless-stopped -p 127.0.0.1:8080:8080 --mount source=purevault_data,target=/data --mount source=purevault_keys,target=/keys --security-opt no-new-privileges --cap-drop ALL purevault:local
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). Run one installation method at a time because both reserve host port `8080`. With standalone Docker, keep the explicit `127.0.0.1` in the mapping. Docker's standard bridge publishes a bare `-p 8080:8080` on all host interfaces, which can expose the tokenless first-run setup.

Manage this container with:

```powershell
docker stop purevault-web
docker start purevault-web
docker logs purevault-web
```

The named volumes survive stopping or removing the container. Do not delete them unless you intentionally want to erase the installation.

The standalone Docker Run example uses volumes named `purevault_data` and `purevault_keys`. A Compose installation normally uses `purevault_purevault_data` and `purevault_purevault_keys`. Switching between these deployment methods does not automatically reuse the same vault; do not switch methods as an update procedure.

After a public port-8080 GHCR build is verified, the equivalent image pull and Docker Run command is:

```powershell
docker pull ghcr.io/finkenwalde/purevault:latest
docker run -d --name purevault-web --restart unless-stopped -p 127.0.0.1:8080:8080 --mount source=purevault_data,target=/data --mount source=purevault_keys,target=/keys --security-opt no-new-privileges --cap-drop ALL ghcr.io/finkenwalde/purevault:latest
```

Do not use those commands before the package is public and an anonymous pull has succeeded. For an existing Compose installation, use the carefully documented `compose.image.yaml` procedure instead of changing to the standalone Docker Run volume names.

## Status and logs

For a Compose installation:

```powershell
docker compose ps
docker compose logs web
curl.exe http://127.0.0.1:8080/health/
```

The internal `/health/` endpoint reports status only. Docker Desktop's **Containers** view provides the same basic status and log access without terminal commands.

## Backup and restore

Stop Purevault before taking a backup. Back up its data and key volumes together, label them as one set, and store them securely. The data volume contains the SQLite database; the separate key volume contains the encryption key required to decrypt vault entries.

The standard Compose project creates `purevault_purevault_data` and `purevault_purevault_keys`. The standalone Docker Run example creates `purevault_data` and `purevault_keys`. Confirm which deployment you are backing up with `docker volume ls`; never mix a data volume from one installation with a key volume from another.

Key loss is unrecoverable. Never restore a database with a different key. Keeping copies of the database and key backup separate where practical reduces accidental exposure.

For an off-host copy that can restore into an empty installation without the original encryption key, use [portable encrypted backups](BACKUPS.md). Keep both the backup file and its independent recovery passphrase. The paired-volume backup and the portable backup serve different recovery needs: a full volume restore recovers the installation, while portable restore imports entries and settings into a new vault with every entry locked.

## Updating safely

Back up both volumes before updating. Do not overwrite an old extracted folder blindly, and do not delete volumes.

If your current container listens on port 8000, follow [Upgrading to port 8080](#upgrading-to-port-8080) before using the normal update commands. This applies to both source-build and prebuilt installations.

For a Git checkout, review the incoming changes, then update and rebuild from the same folder:

```powershell
git pull --ff-only
docker compose up --build -d
```

For a ZIP installation, stop the existing installation from its old folder and extract the new source into a separate folder. Then run `docker compose up --build -d` from the new folder. Keep the old folder until the updated service is healthy and your data is present.

Do not change the Compose project or volume names during an update. Never use `docker compose down --volumes` as an update step.

### Upgrading to port 8080

1. Stop Purevault and back up the data and key volumes together. Use `docker compose stop` or `docker compose -f compose.image.yaml stop`, matching the files used to start it.
2. Install the new files. Keep the same project name and named volumes. Move any HTTPS settings you still need from the old `.env` into `.env.https` and use the HTTPS override below. For a custom local host port, edit the mapping directly, for example `"127.0.0.1:8127:8080"`. The old `PUREVAULT_PORT` and `PUREVAULT_BIND_ADDRESS` variables are retired from Compose.
3. Rebuild and start with `docker compose up --build -d`, or use the prebuilt quick start once the matching image is available. Verify `docker compose ps` shows the expected loopback port and confirm your vault data is present. Update any proxy upstream from the old port to `8080`.

For a standalone Docker Run upgrade, stop and remove the old **container** after backing up both volumes. Recreate it with the new port mapping and the same two volume names.

## Upgrade from a version with accountability email

The database upgrade permanently removes accountability partners, SMTP settings and credentials, and all email history and queued messages. Existing vault entries, accounts, display timezones, and unlock grants are preserved by the migration. Normal restart behavior still applies to active automatic access windows. Older database copies, backups, and delivered emails may still contain removed data. Dropping the tables is not forensic secure erasure.

## Local configuration

The defaults require zero environment configuration. Choose a display timezone or accept the browser suggestion in **Settings**. UTC is the default.

Both Compose files publish `127.0.0.1:8080:8080`. Use `docker compose port web 8080` to check the binding.

If host port 8080 is in use, change the middle field of the mapping, for example `"127.0.0.1:8081:8080"` in Compose or `-p 127.0.0.1:8081:8080` with Docker Run. Use the new host port in the browser and in any host proxy upstream.

## Remote and HTTPS deployment

Complete first-run account creation via loopback before enabling remote access. For a Docker host you administer over SSH, run `ssh -L 8080:127.0.0.1:8080 user@docker-host` and open `http://127.0.0.1:8080` on your own computer while the tunnel is open.

For a controlled network where you intentionally want to publish the HTTP port on all interfaces after setup, replace the Compose mapping with `"8080:8080"` or use `-p 8080:8080` with Docker Run. Restrict access at the network level and configure the allowed hostname. For access over HTTPS, keep the HTTP port private and use the reverse proxy instructions below. See [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/).

Use a maintained HTTPS reverse proxy for remote access. Copy [`.env.example`](../.env.example) to `.env.https`, set the public hostname, and keep the file private. Have the proxy redirect HTTP to HTTPS or enable the app-level redirect in the example. Set additional trusted CSRF origins only for legitimate cross-origin form submissions. The optional override passes the file directly to the container:

```powershell
docker compose -f compose.yaml -f compose.https.yaml up --build -d
```

For a verified prebuilt image, use:

```powershell
docker compose -f compose.image.yaml -f compose.https.yaml up -d --pull always
```

Use the same file flags for subsequent Compose commands. The standard Compose `.env` file is used for interpolation. Container settings are loaded through the explicit override above. For Docker Run, use a copy with the trailing ` #optional` annotations removed from active lines, then pass that copy with `--env-file` before the image name. Set HSTS only after HTTPS is working. Enable `PUREVAULT_TRUST_PROXY_HEADERS` only when the trusted proxy strips client-supplied forwarding headers and sets `X-Forwarded-Proto` itself. Configure it to preserve the public `Host` header.

A proxy running directly on the Docker host can use `http://127.0.0.1:8080` as its upstream. A containerized proxy on the same Docker network can use `http://web:8080`; the Compose default network is `purevault_default`. Connect only trusted services to that network and reconnect the proxy if the network is recreated during an upgrade. A proxy on another machine needs a private tunnel or a deliberately restricted interface and firewall. Keep the raw HTTP service off the LAN and public internet.

The internal `/health/` endpoint remains available over HTTP for Docker health checks when HTTPS redirect is enabled and exposes status only. Application routes still use the configured host, origin, secure-cookie, and proxy policy.

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

# Purevault

Purevault is a self-hosted delayed-access password vault. It inserts a server-enforced waiting period between an unlock request and secret availability, with automatic or manual relocking.

## Start with Docker Compose

Requirements: Docker Desktop with Linux containers.

```powershell
docker compose up --build -d
```

Open [http://localhost:8127](http://localhost:8127) and choose a username and strong master password. Confirming the master password creates the installation's only account. There are no default credentials and account creation closes permanently after this first-run step.

Tokenless first-run creation is intended for the loopback-only local deployment shown here. Complete setup before changing the binding or placing Purevault behind a LAN or public reverse proxy.

The application binds to loopback only. Stop and restart it without losing waits or entries:

```powershell
docker compose stop
docker compose start
```

View health and status:

```powershell
docker compose ps
curl.exe http://localhost:8127/health/
```

After signing in, use **Settings** to choose a display timezone or accept the browser suggestion. The environment value is only the installation fallback.

## Start with `docker run`

Create named volumes and build the image:

```powershell
docker volume create purevault_data
docker volume create purevault_keys
docker build -t purevault:local .
docker run -d --name purevault-web --restart unless-stopped -p 127.0.0.1:8127:8000 --mount source=purevault_data,target=/data --mount source=purevault_keys,target=/keys --security-opt no-new-privileges --cap-drop ALL purevault:local
```

Then open [http://localhost:8127](http://localhost:8127). Do not run both the Compose and `docker run` examples at the same time because both use port 8127.

## Backup and restore

Back up the stopped `purevault_data` and `purevault_keys` volumes together, label them as one set, and store them securely. The database contains encrypted vault data; the separate key volume is required to decrypt it. Key loss is unrecoverable. Do not restore a database with a different key.

Purevault intentionally has no plaintext export or forgotten-master-password recovery in v1. An authenticated master-password change is supported by the backend; a dedicated settings screen is planned.

Encrypted export/import is a future feature, not a current backup method. The intended import behavior is to preserve entry timing and relock settings while importing every entry locked. Cloud destinations may be added later. Key recovery and cross-installation portability decisions remain unresolved and must be designed before that feature is implemented.

## Portainer on Docker Standalone

Purevault is a single-instance SQLite application for a Docker Standalone environment. This configuration does not claim Docker Swarm support.

### Preferred: deploy from Git source

This repository does not publish a container image. Commit and push the source changes you want Portainer to deploy before continuing; Portainer cannot see uncommitted files on another computer.

1. In the Docker Standalone environment, open **Stacks**, choose **Add stack**, and select **Git Repository**.
2. Configure the repository source, for example `https://example.com/organization/purevault.git`, select the intended branch, and set **Compose path** to `compose.yaml`.
3. Keep `PUREVAULT_BIND_ADDRESS=127.0.0.1` for the first deployment. Deploy the stack. The Compose `build: .` uses the checked-out repository as the Docker build context and creates `purevault:local` on that Docker endpoint.
4. Reach the remote host through restricted access, such as an SSH tunnel, and complete the one-time browser setup before widening network access. `localhost` in a Portainer stack refers to the Docker host, not the computer running the browser.

Portainer's documented stack inputs include the web editor, upload, and Git repository methods. A web-editor stack containing this repository's `build: .` cannot work by itself because the source files are not present beside the pasted Compose text. See Portainer's [Add a new stack](https://docs.portainer.io/user/docker/stacks/add) documentation.

### Alternative: build in Images, then deploy a stack

For a Docker Standalone endpoint, Portainer can build the image on that endpoint:

1. Open **Images** → **Build a new image**.
2. Name it `purevault:local`. Choose the URL/upload method that supplies the complete repository build context and use `Dockerfile` as its Dockerfile path. A Dockerfile pasted alone is insufficient because this build copies application and frontend files. See Portainer's [Build a new image](https://docs.portainer.io/user/docker/images/build) documentation.
3. Create a stack with `compose.portainer-image.yaml`, which pulls no registry image and contains no source-context build step. The locally built `purevault:local` image must exist on the same Docker endpoint.

The Git-source method is less error-prone because source and Compose revisions stay together. This Portainer UI procedure has been validated with equivalent Docker Compose builds and configuration checks, but the Portainer UI itself has not been exercised in this development environment.

### Persistence, updates, and remote HTTPS

`purevault_data` contains SQLite state and `purevault_keys` contains the encryption key. Keep the same Portainer stack name when updating so Compose resolves the same named volumes. Updating or redeploying is safe when both volumes remain attached. Do not select an option that removes volumes, and do not run `docker compose down --volumes`.

The safe default publishes only on `127.0.0.1` of the Docker endpoint. For remote use, finish first-run setup while access is restricted, then place Purevault behind a maintained HTTPS reverse proxy. Configure these stack environment variables with neutral values appropriate to that deployment:

```text
PUREVAULT_BIND_ADDRESS=127.0.0.1
PUREVAULT_ALLOWED_HOSTS=vault.example.com
PUREVAULT_CSRF_TRUSTED_ORIGINS=https://vault.example.com
PUREVAULT_SECURE_COOKIES=true
PUREVAULT_SECURE_SSL_REDIRECT=true
PUREVAULT_HSTS_SECONDS=31536000
PUREVAULT_TRUST_PROXY_HEADERS=true
```

Enable `PUREVAULT_TRUST_PROXY_HEADERS` only when the trusted reverse proxy removes client-supplied forwarding headers and sets `X-Forwarded-Proto` itself. A host-native proxy can reach the loopback binding. A proxy in another container cannot use its own `localhost` to reach Purevault; attach both services to a deliberately scoped Docker network instead of publishing the first-run wizard broadly. If the proxy runs on another machine, loopback is also unreachable, so use a restricted interface and firewall rule.

The internal `/health/` endpoint remains HTTP-accessible for Docker health checks even when HTTPS redirect is enabled; it exposes status only. Application routes continue to require the configured host, origin, secure-cookie, and proxy policy.

## Development and tests

The production image builds React into static assets and runs Django with Gunicorn; Node.js is absent from the runtime image.

```powershell
docker build --target frontend-build -t purevault-frontend-check .
docker build -t purevault:local .
docker run --rm --user root --entrypoint python --mount type=tmpfs,destination=/data --mount type=tmpfs,destination=/keys -e PUREVAULT_DJANGO_SECRET_KEY=test-only-container -e PUREVAULT_DATA_DIR=/data -e PUREVAULT_KEY_FILE=/keys/app.key purevault:local manage.py test
```

The test command bypasses the production entrypoint and uses temporary storage. It cannot rotate the running installation's clock generation or touch the real database and key volumes.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for timing semantics, duration limits, HTTPS guidance, the security boundary, and potential future features.

## Security boundary

Purevault enforces delays consistently in its supported application and API paths. It cannot protect against someone who controls the host, containers, storage, encryption keys, server clock, backups, process memory, or application code. It is not a tamper-proof or zero-knowledge system. The included HTTP configuration is for loopback use only; network deployment requires correctly configured HTTPS.

No license has been selected yet.

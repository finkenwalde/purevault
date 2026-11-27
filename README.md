# Purevault

Purevault is a self-hosted delayed-access password vault. It inserts a server-enforced waiting period between an unlock request and secret availability, with automatic or manual relocking.

Purevault is a heavily AI-assisted coding project. The author works in IT and has basic programming knowledge.

> **Use at your own risk.** Restarting or replacing the container preserves the vault as long as its original data and key volumes stay intact and are reattached. If you lose the vault database or its matching encryption key without a recovery copy, the saved passwords are unrecoverable. Keep a safe way to recover any critical credentials. See [Docker volume recovery](docs/DEPLOYMENT.md#backup-and-restore).

## Run with Docker

Save [`compose.image.yaml`](compose.image.yaml) and run this command in the same folder:

```sh
docker compose -f compose.image.yaml up -d --pull always
```

The image is `ghcr.io/finkenwalde/purevault:latest` for Intel and AMD 64-bit hosts. Open [http://127.0.0.1:8080](http://127.0.0.1:8080) on the computer running Docker to create your account. The default setup needs no environment variables.

- For a web interface, see [Deploy with Portainer](docs/DEPLOYMENT.md#deploy-with-portainer).
- To see what each Compose setting does, see [Compose settings](docs/DEPLOYMENT.md#compose-settings).
- For a build from source, follow the quick start below.

## Environment variables

All settings below are optional with the Docker image. Both Compose files show the most common settings as commented examples. To use one, uncomment `environment:` and the setting you want. For an HTTPS proxy, follow the [remote deployment guide](docs/DEPLOYMENT.md#remote-and-https-deployment).

| Variable | Docker default | What it does |
| --- | --- | --- |
| `PUREVAULT_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated IP addresses or hostnames people use to open Purevault. If opening `http://192.168.1.50:31417`, add `192.168.1.50` to the list. A host outside the list can cause **400 Bad Request**. |
| `PUREVAULT_DISPLAY_TIMEZONE` | `UTC` | Default display timezone, given as an IANA name such as `Europe/Paris`. A timezone chosen under **Settings** takes precedence. |
| `PUREVAULT_SECURE_COOKIES` | `false` | Set to `true` for HTTPS so sign-in and CSRF cookies are sent only over HTTPS. |
| `PUREVAULT_TRUST_PROXY_HEADERS` | `false` | Lets a trusted HTTPS reverse proxy tell Purevault whether the request arrived over HTTPS. Enable only if the proxy strips incoming `X-Forwarded-Proto` headers and sets the correct value itself. |
| `PUREVAULT_SECURE_SSL_REDIRECT` | `false` | Redirects HTTP requests to HTTPS, except for the container health check. Enable after HTTPS works if the proxy doesn't already redirect HTTP. |
| `PUREVAULT_CSRF_TRUSTED_ORIGINS` | Empty | Comma-separated additional origins allowed to submit forms. Include the scheme and port if applicable, for example `https://vault.example.com:8443`. A normal same-origin setup can leave this empty. |
| `PUREVAULT_HSTS_SECONDS` | `0` | Tells browsers to use HTTPS for this hostname and its subdomains for the given number of seconds. Enable only after HTTPS is working. |
| `PUREVAULT_LOGIN_ATTEMPTS` | `8` | Number of failed sign-in attempts allowed per browser session within the window below. |
| `PUREVAULT_LOGIN_WINDOW_SECONDS` | `300` | How far back, in seconds, Purevault counts failed attempts for the sign-in limit. |
| `PUREVAULT_DJANGO_SECRET_KEY` | Generated and stored at `/data/django-secret` | Optional override for Django's signing secret. Changing it can invalidate existing sessions. |
| `PUREVAULT_DATA_DIR` | `/data` | Directory for the SQLite database. Leave it at the default when using the supplied volume mapping. |
| `PUREVAULT_KEY_FILE` | `/keys/app.key` | File containing the vault's encryption key. Keep this path on persistent storage. Existing vault entries require their original key. |

The image also sets internal variables. `PUREVAULT_CLOCK_GENERATION` identifies the current runtime for unlock delays. `DJANGO_SETTINGS_MODULE` selects Purevault's Django settings. `PYTHONDONTWRITEBYTECODE` prevents Python from writing bytecode cache files, and `PYTHONUNBUFFERED` sends output to Docker logs immediately.

## Quick start on your computer

This quick start builds Purevault from source and keeps browser access on the computer running Docker.

### 1. Install and start Docker Desktop

Install [Docker Desktop](https://docs.docker.com/get-started/get-docker/) and open it. Wait until Docker Desktop says the engine is running. On Windows, use Linux containers (the normal default).

### 2. Download and extract Purevault

Download the [Purevault ZIP file](https://github.com/finkenwalde/purevault/archive/refs/heads/main.zip), then extract it somewhere you can find again. Open the extracted folder and confirm that it contains `compose.yaml`.

### 3. Open a terminal in that folder

On Windows, open the folder in File Explorer, click the address bar, type `powershell`, and press **Enter**. A PowerShell window will open in the correct folder.

On macOS or Linux, open a terminal and use `cd` to enter the extracted folder containing `compose.yaml`.

### 4. Start Purevault

For an existing installation on port 8127, start with the [port upgrade steps](docs/DEPLOYMENT.md#upgrading-to-port-8080).

Run this one command:

```powershell
docker compose up --build -d
```

The first build may take several minutes while Docker downloads and builds the required software. When the command finishes successfully, it returns to the prompt. You can check readiness with:

```powershell
docker compose ps
```

Look for the `web` service to say `Up` and then `healthy`. You can also open Docker Desktop, select **Containers**, expand **purevault**, and inspect the status or logs.

### 5. Create your account

Open [http://127.0.0.1:8080](http://127.0.0.1:8080) in your browser. Choose a username and a strong master password. This creates the installation's only account.

The default local setup uses Docker's loopback binding and works with zero environment configuration.

To change your master password, sign in and open **Settings → Security**. You’ll need your current password. Changing it keeps this session signed in and signs out other sessions. Keep a safe copy of your master password, as Purevault has no forgotten-password reset.

Purevault signs you out after 10 minutes without keyboard, pointer, or touch activity. Automatic background refreshes do not keep a session open. Signing out does not cancel unlock requests or change access windows.

## Stop and start later

Open a terminal in the same extracted folder and use:

```powershell
docker compose stop
docker compose start
```

Your account, entries, and encryption key are kept in Docker volumes and survive normal stops and starts. Unlock waits pause while Purevault is stopped and resume after it starts. Do not use `docker compose down --volumes`, because that deletes the stored data and encryption key.

If something does not start, open Docker Desktop's **Containers** view, expand **purevault**, and read the `web` logs. Check that Docker Desktop is running and port `8080` is free.

## How Purevault works

The backend uses **Django**, a Python web framework, for sign-in, data storage, and enforcement of unlock delays. Gunicorn runs the Django app, SQLite stores the vault data, and React provides the browser interface.

- **Sign-in and sessions.** Django hashes the master password, manages session cookies, and protects requests that change vault data with CSRF checks. Sessions expire after 10 minutes of inactivity.
- **Vault encryption.** Saved passwords are encrypted with Fernet. The database and its encryption key are kept in separate Docker volumes.
- **Unlock delays.** The server checks each request to reveal a password. It measures elapsed time while Purevault is running, so changing the browser clock cannot shorten the wait.

Anyone who controls the Docker host can bypass the app's controls. For details, see the [architecture and security boundary](docs/ARCHITECTURE.md).

## Other installation and maintenance options

See the [deployment and maintenance guide](docs/DEPLOYMENT.md) for:

- the complete `docker run` option;
- Git clone and update instructions;
- Docker volume backup and restore guidance;
- configuration and remote HTTPS considerations;
- development and test commands.

See the [sequential feature roadmap](docs/ROADMAP.md) for approved work that will be implemented and reviewed one feature at a time.

Purevault is available under the [MIT License](LICENSE).

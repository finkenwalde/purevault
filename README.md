# Purevault

Purevault is a self-hosted delayed-access password vault. It inserts a server-enforced waiting period between an unlock request and secret availability, with automatic or manual relocking.

Purevault is a heavily AI-assisted coding project. The author works in IT and has basic programming knowledge.

## Quick start on your computer

This quick start runs Purevault only on the same computer as Docker. It is not a guide for public or remote hosting.

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
- **Portable backups.** Backup files are encrypted and protected by a separate recovery passphrase. Restored entries start locked and require their full delay.

Anyone who controls the Docker host can bypass the app's controls. Anyone with both a backup file and its recovery passphrase can also decrypt it outside Purevault. For details, see the [architecture and security boundary](docs/ARCHITECTURE.md).

## Other installation and maintenance options

Use [portable encrypted backups](docs/BACKUPS.md) to download an off-host recovery copy or restore into an empty installation.

See the [deployment and maintenance guide](docs/DEPLOYMENT.md) for:

- the complete `docker run` option;
- Git clone and update instructions;
- backup and restore guidance;
- configuration and remote HTTPS considerations;
- development and test commands.

See the [sequential feature roadmap](docs/ROADMAP.md) for approved work that will be implemented and reviewed one feature at a time.

Use the source-build quick start above until a public 8080-compatible image is verified. After that, the [prebuilt image quick start](docs/DEPLOYMENT.md#prebuilt-image-quick-start) is the shortest installation path. Maintainers can publish it using the [container image publication guide](docs/PUBLISHING.md).

Purevault is available under the [MIT License](LICENSE).

# Purevault

Purevault is a self-hosted delayed-access password vault. It inserts a server-enforced waiting period between an unlock request and secret availability, with automatic or manual relocking.

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

Open [http://localhost:8127](http://localhost:8127) in your browser. Choose a username and a strong master password. This creates the installation's only account; there are no default credentials.

The default local setup does not require editing `.env`, entering a setup token, or installing Git.

Purevault signs you out after 10 minutes without keyboard, pointer, or touch activity. Automatic background refreshes do not keep a session open. Signing out does not cancel unlock requests, change access windows, or stop queued accountability emails.

## Stop and start later

Open a terminal in the same extracted folder and use:

```powershell
docker compose stop
docker compose start
```

Your account, entries, and encryption key are kept in Docker volumes and survive normal stops and starts. Unlock waits pause while Purevault is stopped and resume after it starts. Do not use `docker compose down --volumes`, because that deletes the stored data and encryption key.

If something does not start, open Docker Desktop's **Containers** view, expand **purevault**, and read the `web` logs. Also confirm that Docker Desktop is running and that no other program is using port `8127`.

## Optional accountability emails

After signing in, open **Settings** to add accountability partners and configure an SMTP email account. Purevault can notify each partner when an unlock is requested and requires a written reason whenever at least one partner is configured.

Email is optional. A missing or failing email configuration never changes an unlock timer: messages remain queued and retry after the configuration is corrected. Use **Send test email** before relying on notifications. “Accepted by mail server” means the SMTP server accepted the message, not that it is guaranteed to reach an inbox.

## Other installation and maintenance options

See the [deployment and maintenance guide](docs/DEPLOYMENT.md) for:

- the complete `docker run` option;
- Git clone and update instructions;
- backup and restore guidance;
- configuration and remote HTTPS considerations;
- accountability email operation and limitations; and
- development and test commands.

For implementation details, timing behavior, and the full security model, see [Purevault architecture and security boundary](docs/ARCHITECTURE.md).

See the [sequential feature roadmap](docs/ROADMAP.md) for approved work that will be implemented and reviewed one feature at a time.

Maintainers can follow the [container image publication guide](docs/PUBLISHING.md) to manually publish a reviewed `linux/amd64` image to GHCR. Until the first image is successfully published, made public, and anonymously pull-tested, continue using the source-build quick start above.

No open-source license has been selected yet.

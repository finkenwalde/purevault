# PureVault

PureVault is a small, single-user, self-hosted web app for storing credentials behind an enforced waiting period. Each secret is encrypted before it is written to SQLite. Asking to unlock an entry starts its countdown immediately; canceling clears the request, so a later request starts the full delay again.

The Docker image serves its collected CSS and other static assets through WhiteNoise, so no separate web server or static-file volume is needed.

## Run with Docker Compose

1. Install Docker Engine or Docker Desktop with the Compose plugin.
2. In this folder, create a `.env` file with a strong, random Django key. For example, generate one with Python:

   ```sh
   python -c "import secrets; print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(48))"
   ```

   Copy the output into `.env`. Optionally set `PUREVAULT_PORT=8000`, `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1`, and `TZ=UTC` (or an IANA timezone such as `America/New_York`).
3. Start the app:

   ```sh
   docker compose up -d --build
   ```

4. Visit [http://localhost:8000](http://localhost:8000) (or the port you set) and create the master password on first launch.

Compose stores the SQLite database and collected static files in the persistent `lockbox_data` Docker volume. Keep that volume: deleting it deletes the vault. The volume identifier is kept stable for existing installations.

## Run with `docker run`

Build the image from this directory:

```sh
docker build -t purevault:local .
```

Create a persistent volume, then start the app. Replace the example Django key with a generated random value and change the host port or timezone if needed.

```sh
docker volume create lockbox_data
docker run -d --name purevault --restart unless-stopped -p 8000:8000 -e "DJANGO_SECRET_KEY=replace-with-a-long-random-secret" -e "DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1" -e "TZ=America/New_York" -v lockbox_data:/data purevault:local
```

Open [http://localhost:8000](http://localhost:8000). To use another host port, change the left side of `-p`, for example `-p 8090:8000`, and browse to port 8090.

## Deploy from Portainer

This guide deploys PureVault directly from the public GitHub repository using a Portainer Stack. Portainer builds the included Dockerfile and keeps SQLite data in a named Docker volume.

### Before you start

- A Portainer Docker environment with permission to build and run containers.
- The PureVault GitHub repository URL: <https://github.com/finkenwalde/purevault>.

### Create the stack

1. In Portainer, select the Docker environment, open **Stacks**, then choose **Add stack**.
2. Enter `purevault` as the stack name and choose **Git Repository**.
3. Enter the repository URL above, select the `main` reference, and set the Compose path to `docker-compose.yml`.
4. Leave Git authentication disabled because this repository is public.
5. In **Environment variables**, set the variables below. Generate a fresh Django key on your computer with Python and paste the printed value into Portainer:

   ```sh
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   | Variable | Value |
   | --- | --- |
   | `DJANGO_SECRET_KEY` | The fresh random value generated above |
   | `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,<server-IP>`; replace `<server-IP>` with the Docker host's address, without a port |
   | `PUREVAULT_PORT` | `8000` or another unused host port |
   | `TZ` | An IANA timezone such as `America/New_York`; defaults to `UTC` |
   | `DJANGO_SECURE_COOKIES` | `0` for direct HTTP on a trusted private network; `1` when served through HTTPS |

6. Choose **Deploy the stack**. Portainer clones the repository and builds the app image from its Dockerfile.
7. Open `http://<server-IP>:8000` (replace `8000` if you chose another port) and create your master password.

For Portainer's Git stack fields, see [Add a stack](https://docs.portainer.io/user/docker/stacks/add).

### Updating and keeping your vault data

- To deploy a code change, push it to the selected branch, then open the `purevault` stack and use **Pull and redeploy** (the label may vary by Portainer version).
- After updating, confirm Portainer rebuilds the image from the Git repository. Restarting the old container alone will not install dependency or static-file changes.
- If the page appears unstyled, use **Pull and redeploy** to get the current code and rebuild; the container runs `collectstatic` at startup and WhiteNoise serves the collected files under `/static/`.
- The database is stored in the `lockbox_data` named volume. Keep this volume when updating or recreating the stack; removing it permanently deletes your vault entries. Do not change the volume name for an existing installation.
- Back up the Docker volume before upgrades or host maintenance. The secrets are encrypted, but entry titles and other metadata are not.
- Do not expose the HTTP port directly to the public internet. For remote access, use a private VPN or HTTPS reverse proxy and set `DJANGO_SECURE_COOKIES=1`.

## Use the vault

- On first visit, create a master password (at least 14 characters). It cannot be reset: forgetting it means stored secrets cannot be decrypted.
- Add entries with a title, secret, unlock delay, and unlock duration. Username, website, and notes are optional. After login, you do not need to enter the master password again to create or edit entries. Website values can be a bare domain, subdomain, or full HTTP(S) URL; bare domains open over HTTPS.
- Choose **Start unlock delay** to start the timer. The server records the unlock time, so the countdown continues if you close your browser or restart the container. Pending and unlocked times are shown in the configured timezone.
- The dashboard shows the server's current time in the configured timezone.
- Changing `TZ`, your computer's timezone, or your browser's timezone only changes how times are displayed. Unlock times are stored as absolute timestamps and checked against the server clock, so a timezone change does not shorten the wait. An administrator changing the Docker host's system clock is a separate matter and can affect server-side time checks.
- When the delay ends, the secret is available for the configured unlock duration (15 minutes by default), then returns to locked. You can also lock it early.
- Choose **Cancel request and reset timer** to lock the entry again. A future unlock request starts the full configured delay.
- After the wait ends, enter the master password to decrypt and reveal the secret. Lock the vault when done.
- Back up the `lockbox_data` volume and store the backup somewhere secure. A backup contains encrypted credentials but also metadata such as titles, usernames, websites, notes, and timing information.

## Security and deployment notes

- The master password is stored as a Django password hash. A separate key is derived from that password with scrypt and a random per-installation salt; Fernet authenticated encryption protects each secret. After login, the derived key is kept in the server-side session only as ciphertext wrapped with `DJANGO_SECRET_KEY`, so entry creation does not ask for the master password again. The master password itself is never stored in the session. If the master password is lost, the encrypted secrets are unrecoverable.
- This first version is intended for one trusted user on a private network. Anyone who can administer the Docker host or read the application process can ultimately access the vault. The app does not provide multi-user permissions, recovery, MFA, external identity, or protection against a compromised host/browser.
- There is no online login rate limiter in this initial version. Keep the app on a private network or behind a trusted HTTPS reverse proxy, and use a long, unique master passphrase.
- Do not expose the plain HTTP port directly to the public internet. For remote access, place the app behind a reverse proxy or private VPN that provides HTTPS, and set `DJANGO_SECURE_COOKIES=1` when requests reach Django over HTTPS. Set `DJANGO_ALLOWED_HOSTS` to the exact hostnames you use.
- Keep `DJANGO_SECRET_KEY` private and stable across restarts. Losing it invalidates sessions; changing it logs out users. It is distinct from the vault master password.
- Protect Docker volume backups and restrict host access. The app intentionally encrypts secret values; other entry fields are stored as ordinary database metadata.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PUREVAULT_PORT` | `8000` | Compose host port |
| `DJANGO_SECRET_KEY` | required | Django signing/session key |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated allowed hostnames |
| `DJANGO_SECURE_COOKIES` | `0` | Set to `1` behind HTTPS |
| `TZ` | `UTC` | IANA timezone used to display server times, e.g. `America/New_York` |
| `DATA_DIR` | `/data` in Docker | Persistent SQLite and static files directory |

## Data backup

For a consistent backup, stop the container first, archive the Docker volume, then restart it. One option is:

```sh
docker compose stop
docker run --rm -v purevault_lockbox_data:/data -v "$PWD":/backup alpine \\
  tar -czf /backup/purevault-data-backup.tar.gz -C /data .
docker compose start
```

Docker Compose prefixes the volume with the project or stack name. The command above assumes the Compose or Portainer stack is named `purevault`. Check the actual name with `docker volume ls` and substitute it if your installation uses another stack name.

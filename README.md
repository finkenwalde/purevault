# PureVault

PureVault is a small, single-user, self-hosted web app for storing credentials behind an enforced waiting period. Each secret is encrypted before it is written to SQLite. Asking to unlock an entry starts its countdown immediately; canceling clears the request, so a later request starts the full delay again.

The Docker image serves its collected CSS and other static assets through WhiteNoise, so no separate web server or static-file volume is needed.

For a quick deployment from the public GitHub repository, see [Portainer setup](PORTAINER.md).

## Run with Docker Compose

1. Install Docker Engine or Docker Desktop with the Compose plugin.
2. In this folder, create a `.env` file with a strong, random Django key. For example, generate one with Python:

   ```sh
   python -c "import secrets; print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(48))"
   ```

   Copy the output into `.env`. Optionally add `PUREVAULT_PORT=8000` and `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1`.
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

Create a persistent volume, then start the app. Replace the example Django key with a generated random value and change the host port if needed.

```sh
docker volume create lockbox_data
docker run -d --name purevault --restart unless-stopped -p 8000:8000 -e "DJANGO_SECRET_KEY=replace-with-a-long-random-secret" -e "DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1" -v lockbox_data:/data purevault:local
```

Open [http://localhost:8000](http://localhost:8000). To use another host port, change the left side of `-p`, for example `-p 8090:8000`, and browse to port 8090.

## Use the vault

- On first visit, create a master password (at least 14 characters). It cannot be reset: forgetting it means stored secrets cannot be decrypted.
- Add entries with a title, secret, unlock delay, and unlock duration. Username, website, and notes are optional. After login, you do not need to enter the master password again to create or edit entries. Website values can be a bare domain, subdomain, or full HTTP(S) URL; bare domains open over HTTPS.
- Choose **Start unlock delay** to start the timer. The server records the unlock time, so the countdown continues if you close your browser or restart the container.
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
| `DATA_DIR` | `/data` in Docker | Persistent SQLite and static files directory |

## Data backup

For a consistent backup, stop the container first, archive the Docker volume, then restart it. One option is:

```sh
docker compose stop
docker run --rm -v lockbox-selfhosted_lockbox_data:/data -v "$PWD":/backup alpine \
  tar -czf /backup/purevault-data-backup.tar.gz -C /data .
docker compose start
```

Docker Compose may prefix the volume name based on the directory. Check the actual name with `docker volume ls`; use that exact volume name in the backup command.

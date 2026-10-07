# Deploy PureVault with Portainer

This guide deploys PureVault directly from the public GitHub repository using a Portainer Stack. Portainer builds the included Dockerfile and keeps the SQLite data in a named Docker volume.

## Before you start

- A Portainer Docker environment with permission to build and run containers.
- The PureVault GitHub repository URL: `https://github.com/finkenwalde/purevault`.

## Create the stack

1. In Portainer, select the Docker environment, open **Stacks**, then choose **Add stack**.
2. Enter `purevault` as the stack name and choose **Git Repository**.
3. Enter the repository URL above, select the `main` reference, and set the Compose path to `docker-compose.yml`.
4. Leave Git authentication disabled because this repository is public.
5. In **Environment variables**, add the variables below. Generate a fresh Django key on your computer with Python and paste the printed value into Portainer:

   ```sh
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   | Variable | Value |
   | --- | --- |
   | `DJANGO_SECRET_KEY` | The fresh random value generated above |
   | `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,<server-IP>`; replace `<server-IP>` with the Docker host's address, without a port |
   | `PUREVAULT_PORT` | `8000` (or another unused host port) |
   | `DJANGO_SECURE_COOKIES` | `0` for direct HTTP on a trusted private network; `1` when served through HTTPS |

6. Choose **Deploy the stack**. Portainer clones the repo and builds the app image from its Dockerfile.
7. Open `http://<server-IP>:8000` (replace `8000` if you chose another port) and create your master password.

## Updating and keeping your vault data

- To deploy a code change, push it to the selected branch, then open the `purevault` stack and use **Pull and redeploy** (the label may vary by Portainer version).
- After updating, confirm Portainer rebuilds the image from the Git repository. Restarting the old container alone will not install dependency or static-file changes.
- If the page appears unstyled, use **Pull and redeploy** to get the current code and rebuild; the container runs `collectstatic` at startup and WhiteNoise serves the collected files under `/static/`.
- The database is stored in the `lockbox_data` named volume. Keep this volume when updating or recreating the stack; removing it permanently deletes your vault entries. Do not change the volume name for an existing installation.
- Back up the Docker volume before upgrades or host maintenance. The secrets are encrypted, but entry titles and other metadata are not.
- Do not expose the HTTP port directly to the public internet. For remote access, use a private VPN or HTTPS reverse proxy and set `DJANGO_SECURE_COOKIES=1`.

For Portainer's Git stack fields, see [Add a stack](https://docs.portainer.io/user/docker/stacks/add). GitHub fine-grained tokens should be limited to repository contents read access; see [Portainer's token scope guidance](https://docs.portainer.io/faqs/getting-started/what-scopes-are-required-for-github-gitlab-and-bitbucket-tokens).

# Publishing the Purevault container image

Purevault includes a manual GitHub Actions workflow that can publish a container image to GitHub Container Registry (GHCR):

```text
ghcr.io/finkenwalde/purevault
```

The workflow publishes only from the `main` branch. It builds a clean checkout of the repository; it never snapshots a running container and never reads the local vault volumes. Before anything is pushed, the production image build runs the frontend tests and the workflow runs the full backend test suite with temporary storage.

The initial release candidate is `0.1.0` for Linux on AMD64 (`linux/amd64`). The image's MIT label covers Purevault's own code. Bundled components retain their licenses. The image includes Purevault's [MIT License](../LICENSE) at `/app/LICENSE` and the React, React DOM, and Scheduler notices under `/app/licenses`.

Official references:

- [Manually running a GitHub Actions workflow](https://docs.github.com/actions/managing-workflow-runs/manually-running-a-workflow)
- [Working with the GitHub Container Registry](https://docs.github.com/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [Configuring package access control and visibility](https://docs.github.com/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)

## Before the first publication

1. Review all current repository changes. Make sure no `.env` file, database, encryption key, backup, real password, personal story, or local-only file is included.
2. Commit the reviewed project files and push that commit to the repository's `main` branch. The workflow can publish only files that are committed and present on GitHub; it cannot see uncommitted files on your computer.
3. Open [the Purevault repository](https://github.com/finkenwalde/purevault) on GitHub.
4. Select the **Actions** tab.
5. In the workflow list, select **Publish container image**.
6. Select **Run workflow**.
7. Confirm **Branch: main** and enter `0.1.0` for the first release. Select **Run workflow** only when you are ready to publish. The field accepts numeric `0.MINOR.PATCH` versions without a `v` prefix, leading zeros, or prerelease suffixes.
8. Open the new workflow run and wait for both **Verify clean source** and **Publish linux/amd64 image** to finish successfully.

The workflow uses GitHub's short-lived `GITHUB_TOKEN`. The publication job receives `packages: write` and tests the exact image before logging in or pushing. For version `0.1.0`, it publishes these tags:

- `ghcr.io/finkenwalde/purevault:<full-commit-SHA>`
- `ghcr.io/finkenwalde/purevault:0.1.0`
- `ghcr.io/finkenwalde/purevault:latest`

The workflow pushes the commit tag first, then the version tag, then `latest`. Use a new version for each release. Tags can be replaced by a rerun, so use the `ghcr.io/finkenwalde/purevault@sha256:...` reference in the run summary when you need the exact immutable image.

## Make the first package public

New GHCR packages are private by default. After the first successful workflow run:

1. On GitHub, open the profile for **finkenwalde** and select **Packages**.
2. Select the **purevault** container package.
3. Select **Package settings** on the package page.
4. Scroll to **Danger Zone**, select **Change visibility**, and choose **Public**.
5. Enter the requested package name to confirm the visibility change.

This changes only package download visibility; it does not expose a local vault or its Docker volumes. The image's OCI source label links it to [the source repository](https://github.com/finkenwalde/purevault).

## Verify an anonymous pull

After the package is public, test from a terminal that is not logged in to GHCR, or sign out first with `docker logout ghcr.io`:

```powershell
docker pull ghcr.io/finkenwalde/purevault:0.1.0
docker pull ghcr.io/finkenwalde/purevault:latest
```

You can also verify the exact image from the workflow run by replacing `<full-commit-SHA>` below with the complete 40-character commit ID shown on GitHub:

```powershell
docker pull ghcr.io/finkenwalde/purevault:<full-commit-SHA>
```

For an immutable verification, copy the digest from the workflow summary:

```powershell
docker pull ghcr.io/finkenwalde/purevault@sha256:<digest-from-workflow-summary>
```

Before using the current image commands, verify the new build is publicly pullable and listens on container port 8080. Check the pulled image with `docker image inspect ghcr.io/finkenwalde/purevault:latest` and confirm `8080/tcp` in `Config.ExposedPorts`. Images from before the port change expose `8000/tcp` and require their matching deployment configuration.

## Use the published image with Compose

The optional `compose.image.yaml` file uses the same project, service, volumes, and loopback port as `compose.yaml`. If you are upgrading an installation from the old port, follow the [port upgrade steps](DEPLOYMENT.md#upgrading-to-port-8080) before starting. After the port-8080 image is public and verified, run:

```powershell
docker compose -f compose.image.yaml up -d --pull always
```

Always include `-f compose.image.yaml` for image-based commands. Running plain `docker compose up` uses the source-build file instead. Do not combine the two files in one command.

Open [http://127.0.0.1:8080](http://127.0.0.1:8080) when the service is healthy. For a remote HTTPS proxy, add the [optional HTTPS override](DEPLOYMENT.md#remote-and-https-deployment).

Because both files declare `name: purevault` and the same volume keys, running either file from this repository reuses `purevault_purevault_data` and `purevault_purevault_keys`. Confirm those volume names with `docker volume ls` before changing installation methods. Never use `docker compose down --volumes` during an update.

## Publish later updates

For each reviewed release:

1. Commit and push the reviewed changes to `main`.
2. Open **Actions** → **Publish container image** → **Run workflow**.
3. Confirm `main`, enter the new version, start the run, and wait for both jobs to succeed.
4. Verify the version, full-SHA and `latest` tags with `docker pull`. Keep the published digest for recovery.
5. Back up the data and key volumes together before updating an installation.
6. Update an image-based installation:

   ```powershell
   docker compose -f compose.image.yaml pull
   docker compose -f compose.image.yaml up -d
   docker compose -f compose.image.yaml ps
   ```

## Failure and recovery

- If verification or the build fails, no image is pushed. Fix the source, commit the fix, push it to `main`, and manually run the workflow again.
- If a later tag fails to push, the successful earlier tags remain available. Check the run summary and use its digest to identify the exact image before retrying. A rerun can produce a different digest if upstream base images changed.
- Do not blindly run an older application image against a database that a newer version may have migrated. First confirm that the older application is schema-compatible with the current database.
- If an update is faulty and schema compatibility is confirmed, locate the last known-good workflow run, copy its `ghcr.io/finkenwalde/purevault@sha256:...` digest, and temporarily replace the image value in `compose.image.yaml` with that digest before running the two Compose update commands. A digest is the reliable image recovery point; a tag is not.
- If the older application is not schema-compatible, stop Purevault and restore the matched data-volume and key-volume backup taken before the update, then run the matching image digest. Restoring that backup discards writes made after the backup.
- When rolling back to a build that listens on port 8000, restore its matching Compose file and proxy upstream as well. The current Compose file targets container port 8080.
- Never recover by copying a live container or mixing data and key volumes from different installations. Restore the database and encryption key only as a matched backup pair.

The workflow does not create releases, choose a license, publish automatically on pull requests, or publish automatically when code is pushed. Publication remains a deliberate manual action.

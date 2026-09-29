# Existing-host application releases

The workflow builds standalone Next.js and `control-plane/Dockerfile`, exports
`convoy-control-plane:<commit SHA>` as a compressed Docker archive, and ships both
artifacts plus `remote-release.sh` and `compose-release.py` to the existing host.
It requires a trusted `DEPLOY_HOST_KEY`; there is no unverified-key fallback.
The host needs Docker Compose, Python 3 with PyYAML, GNU coreutils and `flock`.

Provision these outside the release workflow, owned and readable by root:

```text
/opt/convoy/portal/web.env
/opt/convoy/portal/control-plane.env
/opt/convoy/portal/data/
```

The data directory is mounted at `/data` and must be writable by the API image's
UID/GID 10001. Preserve the existing device identity and history when initially
provisioning it. The API is `control-plane:8080` on the default Compose network;
no host port is published. Host Caddy routing for agents is provisioned separately.

The workspace also uses the existing API: Compose supplies
`CONVOY_API_URL=http://control-plane:8080` and `CONVOY_API_INTERNAL_HTTP=1`.
This opt-in permits HTTP only to that exact private service origin. Management
mutations use `CONVOY_CONSOLE_ORIGIN`, falling back to `PORTAL_PUBLIC_ORIGIN`.
Management sign-in remains separate from restricted device-demo access.

`web.env` supplies the configuration documented in `src/lib/portal/README.md`.
Compose interpolation applies to unquoted and double-quoted dotenv values. Put
the complete scrypt hash in **single quotes** to preserve its dollar delimiters:

```dotenv
CONVOY_UPSTREAM_URL=http://control-plane:8080
PORTAL_PUBLIC_ORIGIN=https://deployconvoy.com
PORTAL_DEMO_PASSWORD_HASH='scrypt$<salt in lowercase hex>$<derived key in lowercase hex>'
```

Replace placeholders with the actual deployment values through the private
provisioning process. The application receives the literal hash without quote
characters. Never print the resolved Compose environment in CI or commit runtime
environment files. The release script uses `docker compose config --quiet`.

The API has a 768 MiB container memory cap, `no-new-privileges`, and Docker log
rotation at 10 MiB × 3 files for the shared 2 GiB host. Both services are restarted
with `--no-deps`; unrelated services stay running. The API inherits its image's
`/api/health` check. Release acceptance checks that endpoint and the web title
through Caddy's Compose network. New portal web services carry the explicit
`io.convoy.portal=true` label and must also serve the unauthenticated portal
session endpoint. An older landing page can therefore remain a valid rollback
target alongside a running API, without weakening new portal release checks.

Each change first saves Compose files, `.env` and Caddyfile under a private
`rollback/<timestamp>-<suffix>` directory. Start or health failures restore **both
application service definitions** and restart those services. Public verification
failure in CI restores the exact backup returned by that deployment. Manual
rollback also saves the current definitions and recovers them if the chosen
backup does not start successfully.
The default backup is selected by directory modification time, irrespective of
migration/release naming prefixes; incomplete directories without Compose files
and symlink directories are excluded.

Rollback does not restore, downgrade, delete or replace the database or runtime
env files. It retains current Caddy routing, overrides and unrelated services.
The configuration backups support separate operator recovery if necessary.
Rolling back to a deliberate pre-portal backup removes the newly introduced API
container while retaining its data. Application versions must be compatible with
the retained database schema; incompatible old code fails health and needs
operator attention, rather than causing an automatic database overwrite.

Historical standalone directories and tagged API images are retained for
rollback. The script requires 2 GiB free before deployment; manage retention
deliberately after identifying which backups remain required. Releases are locked
against concurrent deployment. Reusing a commit SHA reuses its existing immutable
standalone directory.

```sh
sudo bash remote-release.sh deploy <sha> standalone.tar.gz api-image.tar.gz
sudo bash remote-release.sh deploy <sha> standalone.tar.gz  # retain API version
sudo bash remote-release.sh rollback <backup-directory-name>
```

Local contract tests use fake Docker commands and temporary files; they never
contact a deployment host or a real database:

```sh
python -m unittest discover -s website/infra/deploy/tests -v
bash -n website/infra/deploy/remote-release.sh
```

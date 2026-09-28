# Run Convoy as separate local services

This development setup runs the actual Next.js console, management API,
PostgreSQL database, scheduler, inference worker and enrolled simulated robot in
separate containers. The reference policy is the **scripted MetaWorld expert**;
MuJoCo supplies physics. It does not download learned model weights or require a
GPU, cloud account, physical robot or host Python environment beyond Python 3.

## Start and verify

Requirements: Docker with Compose, OpenSSL, Python 3, network access for initial
image/package downloads and approximately 8 GiB allocated to Docker. Builds run
sequentially. Run these commands from the repository root:

```sh
python3 infra/development/services.py up
python3 infra/development/services.py verify
python3 infra/development/services.py status
```

Open `http://localhost:3300/console`. Use the generated email/password in
`infra/development/.services/connection.json`. This private, ignored directory
also holds local certificates, build logs and acceptance evidence. Do not commit
or share it. The console authenticates with the account's own session; its server
does not receive an operator API token or the admin password.

`up` builds the existing API and website images plus one CPU reference image,
starts PostgreSQL, explicitly migrates it with API/jobs/device stopped, then
starts the remaining services. Its one-time bootstrap logs in through the real
console proxy, creates a project/application/release, enrolls the simulated
device, binds a robot and requests a deployment. It never starts a mission.
Readiness appears only after the coordinator has checked the actual worker.

`verify` logs in through the same console proxy and:

1. Checks PostgreSQL health and the device's deployment acknowledgement.
2. Verifies the console rejects a mutation from an unrelated browser origin.
3. Starts seed 0 with an idempotency key and repeats that exact request, checking
   that it yields one mission.
4. Waits for the real coordinator, inference worker and MuJoCo rollout to finish.
5. Requires task success and matches all 500 reported steps to 500 applied action
   records in the device's durable journal.
6. Writes `infra/development/.services/evidence.json`, including the episode,
   database version/schema, release digest, source revision and built image IDs.

The console can also start/cancel subsequent missions normally. Completion and
task success are separate fields. Evidence describes an offline lockstep
scripted simulation; it is not evidence of learned-policy quality, 80 Hz remote
control, physical safety or production availability.

## Addresses and credentials

| Caller | Destination | Meaning |
| --- | --- | --- |
| Browser | `http://localhost:3300/console` | Loopback-only public console |
| Host API tools | `https://localhost:8443` | Loopback-only public management endpoint |
| Web server / device | `https://api:8443` | Container DNS name, verified TLS |
| Device | `https://inference:8443` | Private inference endpoint, verified TLS |
| API / scheduler | `postgres:5432` | Private Compose network; no published DB port |

Change host ports on the **first** run with `--web-port 3301 --api-port 8444`.
Use the printed `localhost` address consistently: the configured browser origin
is exact. Internal DNS names never appear in browser fetch URLs. The enrollment
configuration uses the internal API address; the server's public URL remains
the host address for human-facing commands.

The helper creates a private local CA and two distinct service certificates,
valid for 30 days. Only API/inference receive their own TLS private key. Web and
device receive the CA certificate; verification is never disabled. No root
certificate is installed into your operating system.

Certificates are copied over stdin into separate named volumes; no host bind
mounts or Docker Desktop access to the checkout directory are required. The CA
private key stays on the host and is never copied into a container.

For host API inspection:

```sh
curl --cacert infra/development/.services/tls/ca.crt https://localhost:8443/api/health
```

The console is HTTP only on loopback. The API's upstream cookie is secure, and
the console proxy reissues its own HttpOnly, SameSite=Strict session cookie for
the local browser origin. In a hosted setup the browser origin must be HTTPS.

The API and inference worker share a generated execution-signing secret. Only
the inference worker and device receive the separate readiness-probe token.
The device generates and retains its own enrollment credential; the web server
has neither credential. Bootstrap/verification receive the local login only for
their short-lived process. Docker operators can inspect container environment
variables: this is a local development configuration, not a secrets service.

## Stop, restart and inspect

```sh
python3 infra/development/services.py logs
python3 infra/development/services.py down
python3 infra/development/services.py up --skip-build
python3 infra/development/services.py verify
```

`down` removes only this setup's containers/network. PostgreSQL, API files,
device identity/journal and generated credentials survive. Existing unrelated
Docker projects are untouched. `up --skip-build` reuses installed image versions;
omit that option after source changes. Migrations are explicit on every `up`.

Stop while idle when preserving a usable demo. Interrupting an active mission
can leave an unknown result; restarting does not erase that uncertainty or
replay physical commands. Preserve the evidence and inspect the console.

To deliberately destroy **this development installation's** data and credentials:

```sh
python3 infra/development/services.py down --delete-data
```

The next `up` creates a fresh installation and fresh certificates. Do this after
the local certificates expire or when qualifying an incompatible reference
profile. This command is not a database migration or restore procedure.

If a build fails, inspect `.services/build-api.log`, `build-web.log` or
`build-inference.log`. If startup fails after migration, inspect `services.py
logs`; do not stamp a schema or disable TLS verification to force it to start.
The setup intentionally leaves failed services and volumes available for
diagnosis until `down` is requested.

## Packaging boundary

The API reuses `control-plane/Dockerfile`, including its pinned SQLite library
attestation for remaining node-local data. The website reuses its standalone
Next.js image. `infra/development/reference.Dockerfile` installs the existing
locked simulation `managed` extra; worker and simulator share the image bytes
but run as independent non-root processes with different credentials/volumes.
The current managed extra also brings server packages into that reference image;
they are not started there and it has no database credential. Removing those
unused packages is a later image-size optimization, not a new runtime boundary.

Python/Node/uv/PostgreSQL base images are pinned by multi-platform digest;
application dependencies use existing lockfiles. Debian package repositories
still supply the small OS library/compiler layer at build time. Image IDs in
evidence identify the tested output; this is not a byte-identical build claim.
No image is published by these commands. See the
[hosting decision](decisions/003-service-hosting.md) before deploying outside
the local machine.

The `service-images` CI job runs this same setup, real mission, teardown/restart
and second mission when packaging inputs change. It uploads only named evidence
JSON and build logs, then removes its disposable volumes and secrets. Existing
contract, API and console tests remain responsible for their respective logic;
there is no duplicate unit-test suite mirroring the Compose file.

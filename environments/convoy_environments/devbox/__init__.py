"""Browser components for sandboxed browser use.

Placement follows the sandboxes-never-hold-credentials rule (runtime DESIGN
§12), so the two components live on opposite sides of the trust boundary:

  egress_proxy — IN the sandbox image (holds no secrets): the browser's only
                 network path; enforces the environment's domain allowlist
                 (Chromium launches with --proxy-server pointed here).
  fill_sidecar — TRUSTED STACK SERVICE (holds the run token): drives the
                 sandbox browser's CDP endpoint from outside to fill logins
                 with credentials leased from the gateway.

Interface stability matters more here than anywhere else in the package:
the proxy ships inside someone else's image, so config is env-vars only
(CONVOY_GATEWAY_URL, CONVOY_RUN_TOKEN, CONVOY_ALLOWED_DOMAINS, CONVOY_CDP_URL)
and changes must be additive.
"""

from .egress_proxy import EgressPolicy, run_proxy  # noqa: F401
from .fill_sidecar import FillService, build_sidecar_app  # noqa: F401

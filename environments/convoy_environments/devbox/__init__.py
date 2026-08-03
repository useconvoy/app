"""Shipped devbox components — pip-installed into Aneesh's E2B image.

Two processes run beside the agent worker and Chromium:
  egress_proxy — the browser's only network path; enforces the environment's
                 domain allowlist (Chromium launches with --proxy-server
                 pointed here). The gateway URL itself is always allowed.
  fill_sidecar — localhost service the agent calls to log into a page; it
                 leases the credential from the gateway and fills via CDP so
                 values never enter model context.

Interface stability matters more here than anywhere else in the package:
these ship inside someone else's image, so config is env-vars only
(CONVOY_GATEWAY_URL, CONVOY_RUN_TOKEN, CONVOY_ALLOWED_DOMAINS, CONVOY_CDP_URL)
and changes must be additive.
"""

from .egress_proxy import EgressPolicy, run_proxy  # noqa: F401
from .fill_sidecar import FillService, build_sidecar_app  # noqa: F401

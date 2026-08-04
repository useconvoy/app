"""Constants shared between the e2e conftest and e2e tests."""

CONTROL_PLANE_PORT = 8700
CONTROL_PLANE_URL = f"http://localhost:{CONTROL_PLANE_PORT}"
# A second control plane + worker pair runs the Pydantic AI executor against
# the LiteLLM proxy -> mock-model, on its own task queue.
AI_CONTROL_PLANE_PORT = 8701
AI_CONTROL_PLANE_URL = f"http://localhost:{AI_CONTROL_PLANE_PORT}"
AI_TASK_QUEUE = "agent-runtime-ai"
DEV_TOKEN = "e2e-dev-token"
PG_APP_DSN = "postgresql://convoy_app:convoy_app@localhost:5433/convoy"
PG_ADMIN_DSN = "postgresql://convoy_admin:convoy_admin@localhost:5433/convoy"
LITELLM_URL = "http://localhost:4000"
LITELLM_MASTER_KEY = "sk-convoy-e2e-master-key"


def auth_headers(tenant: str = "tenant-e2e", actor: str = "e2e@convoy.test") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {DEV_TOKEN}",
        "X-Actor-Id": actor,
        "X-Tenant-Id": tenant,
    }

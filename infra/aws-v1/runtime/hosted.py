"""Expose the PG-backed lifecycle; filesystem-backed legacy APIs are unavailable.

This allowlist is specific to this synthetic staging image, not an alternate
business implementation. Core route authentication and authorization still run.
"""
import re

from starlette.responses import JSONResponse

LIFECYCLE = re.compile(
    r"/api/v1/(projects|robots|applications|deployments|missions|episodes|evaluations|evaluation-suites)(/[^/]+)*"
)
READ_DEVICE = re.compile(r"/api/v1/devices(/[A-Za-z0-9_-]{1,64})?")
AGENT = re.compile(r"/api/agent/v1/robots/[A-Za-z0-9_-]{1,64}/(desired|deployments/[^/]+/report|missions/[^/]+/(claim|report))")


def allowed(path, method):
    return bool(
        path == "/api/health" and method == "GET"
        or path in {"/api/v1/auth/me", "/api/v1/auth/login", "/api/v1/auth/logout"}
        or path == "/api/v1/enrollments" and method == "POST"
        or path == "/api/agent/v1/enroll" and method == "POST"
        or READ_DEVICE.fullmatch(path) and method == "GET"
        or LIFECYCLE.fullmatch(path) and method in {"GET", "POST"}
        or AGENT.fullmatch(path) and method in {"GET", "POST"}
    )


class LifecycleOnly:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not allowed(scope["path"], scope["method"]):
            await JSONResponse({"error": "This API is unavailable in the CPU lifecycle staging deployment"},
                               status_code=404)(scope, receive, send)
        else:
            await self.application(scope, receive, send)


def app():
    from convoy_server.app import create_app
    return LifecycleOnly(create_app())

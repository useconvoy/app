from .base import Connector, ConnectorError, get_connector, register  # noqa: F401
from . import github, notion, slack  # noqa: F401 — registration side effects
from .mcp_custom import McpCustomConnector  # noqa: F401

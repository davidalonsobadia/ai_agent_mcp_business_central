"""
Business Central MCP Client (knowall-ai)
=========================================

Python client for the knowall-ai MCP server for Microsoft Dynamics 365 Business Central.
Uses the standard MCP protocol (JSON-RPC over stdio).

Docs: https://github.com/knowall-ai/mcp-business-central
"""

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================


@dataclass
class BCMCPConfig:
    """Configuration for the Business Central MCP server."""

    # Business Central API base URL
    # Format: https://api.businesscentral.dynamics.com/v2.0/{tenant-id}/{environment}/api/v2.0
    bc_url_server: str

    # Company name in Business Central (use the "name" field, not displayName)
    bc_company: str

    # Authentication: "azure_cli" or "client_credentials"
    bc_auth_type: str = "azure_cli"

    # For client_credentials (optional)
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    tenant_id: Optional[str] = None

    # Optional path to local MCP server (e.g. ./mcp-business-central-local/build/index.js).
    # If unset and auth is client_credentials, auto-detected under project directory.
    local_server_path: Optional[str] = None


def load_bc_config_from_env() -> BCMCPConfig:
    """Build BCMCPConfig from environment variables. Use with .env or export."""
    bc_url = os.getenv("BC_URL_SERVER")
    bc_company = os.getenv("BC_COMPANY")
    auth_type = os.getenv("BC_AUTH_TYPE", "azure_cli")

    if not bc_url or not bc_company:
        raise ValueError(
            "BC_URL_SERVER and BC_COMPANY must be set (e.g. in .env or environment)"
        )

    config = BCMCPConfig(
        bc_url_server=bc_url,
        bc_company=bc_company,
        bc_auth_type=auth_type,
        client_id=os.getenv("BC_CLIENT_ID"),
        client_secret=os.getenv("BC_CLIENT_SECRET"),
        tenant_id=os.getenv("BC_TENANT_ID"),
        local_server_path=os.getenv("BC_LOCAL_SERVER_PATH"),
    )
    return config


# =============================================================================
# MCP CLIENT
# =============================================================================


class BusinessCentralMCPClient:
    """
    Client for the Business Central MCP server (knowall-ai).

    Communicates with the MCP server over stdio using JSON-RPC 2.0.
    """

    def __init__(self, config: BCMCPConfig) -> None:
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self.request_id = 0

    async def start(self) -> None:
        """Start the MCP server process and initialize the session."""
        logger.info("Starting Business Central MCP server...")

        env = {
            "BC_URL_SERVER": self.config.bc_url_server,
            "BC_COMPANY": self.config.bc_company,
            "BC_AUTH_TYPE": self.config.bc_auth_type,
        }

        if self.config.bc_auth_type == "client_credentials":
            if not all(
                [
                    self.config.client_id,
                    self.config.client_secret,
                    self.config.tenant_id,
                ]
            ):
                raise ValueError(
                    "client_credentials requires client_id, client_secret and tenant_id"
                )
            env.update(
                {
                    "BC_CLIENT_ID": self.config.client_id,
                    "BC_CLIENT_SECRET": self.config.client_secret,
                    "BC_TENANT_ID": self.config.tenant_id,
                }
            )

        server_command: List[str]
        if self.config.bc_auth_type == "client_credentials":
            if self.config.local_server_path:
                server_path = Path(self.config.local_server_path)
            else:
                current_dir = Path(__file__).parent
                server_path = (
                    current_dir / "mcp-business-central-local" / "build" / "index.js"
                )

            if server_path.exists():
                logger.info("Using local MCP server: %s", server_path)
                server_command = ["node", str(server_path)]
            else:
                logger.warning(
                    "Local server not found at %s; falling back to npx (azure_cli only).",
                    server_path,
                )
                server_command = ["npx", "-y", "@knowall-ai/mcp-business-central"]
        else:
            server_command = ["npx", "-y", "@knowall-ai/mcp-business-central"]

        try:
            self.process = subprocess.Popen(
                server_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, **env},
                text=True,
                bufsize=1,
            )
            logger.info("MCP server started.")
            await self._initialize()
        except Exception as e:
            logger.error("Failed to start MCP server: %s", e)
            raise

    async def stop(self) -> None:
        """Stop the MCP server process."""
        if self.process:
            self.process.terminate()
            self.process.wait()
            logger.info("MCP server stopped.")

    async def _send_request(self, method: str, params: Optional[Dict] = None) -> Dict:
        """Send a JSON-RPC request to the MCP server and return the result."""
        if not self.process:
            raise RuntimeError("MCP server not started. Call start() first.")

        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params or {},
        }
        request_str = json.dumps(request) + "\n"
        self.process.stdin.write(request_str)
        self.process.stdin.flush()

        response_str = self.process.stdout.readline()
        if not response_str:
            stderr = (
                self.process.stderr.read() if self.process.stderr else "No stderr"
            )
            raise RuntimeError(f"MCP server did not respond. stderr: {stderr}")

        response = json.loads(response_str)
        if "error" in response:
            raise RuntimeError(f"MCP server error: {response['error']}")

        return response.get("result", {})

    async def _initialize(self) -> None:
        """Initialize the MCP session (handshake)."""
        logger.info("Initializing MCP session...")
        result = await self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "bc-mcp-python-client", "version": "1.0.0"},
            },
        )
        logger.info("Session initialized. Capabilities: %s", result.get("capabilities"))

        try:
            await self._send_request("notifications/initialized")
        except RuntimeError:
            logger.debug("notifications/initialized not supported (optional).")

    async def list_tools(self) -> List[Dict]:
        """Return all tools exposed by the MCP server."""
        result = await self._send_request("tools/list")
        return result.get("tools", [])

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call an MCP tool by name with the given arguments."""
        result = await self._send_request(
            "tools/call", {"name": tool_name, "arguments": arguments}
        )
        return result

    # -------------------------------------------------------------------------
    # Convenience methods (map to MCP tools)
    # -------------------------------------------------------------------------

    async def get_schema(self, resource: str) -> Dict:
        """Get OData schema/metadata for a Business Central resource."""
        return await self.call_tool("get_schema", {"resource": resource})

    async def list_items(
        self,
        resource: str,
        filter: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
    ) -> Any:
        """List items from a resource with optional OData filter and pagination."""
        args: Dict[str, Any] = {"resource": resource}
        if filter is not None:
            args["filter"] = filter
        if top is not None:
            args["top"] = top
        if skip is not None:
            args["skip"] = skip
        return await self.call_tool("list_items", args)

    async def get_items_by_field(
        self, resource: str, field: str, value: str
    ) -> Any:
        """Get items matching a given field value."""
        return await self.call_tool(
            "get_items_by_field",
            {"resource": resource, "field": field, "value": value},
        )

    async def create_item(
        self, resource: str, item_data: Dict[str, Any]
    ) -> Any:
        """Create a new item in the given resource."""
        return await self.call_tool(
            "create_item", {"resource": resource, "item_data": item_data}
        )

    async def update_item(
        self, resource: str, item_id: str, item_data: Dict[str, Any]
    ) -> Any:
        """Update an existing item by ID."""
        return await self.call_tool(
            "update_item",
            {"resource": resource, "item_id": item_id, "item_data": item_data},
        )

    async def delete_item(self, resource: str, item_id: str) -> Any:
        """Delete an item by ID."""
        return await self.call_tool(
            "delete_item", {"resource": resource, "item_id": item_id}
        )


# =============================================================================
# DISCOVERY HELPER
# =============================================================================


class MCPDiscoveryHelper:
    """Helper to discover MCP server capabilities (tools and reachable resources)."""

    def __init__(self, client: BusinessCentralMCPClient) -> None:
        self.client = client

    async def discover_all(self) -> Dict[str, Any]:
        """Discover tools and which common resources are available."""
        logger.info("Discovering MCP capabilities...")
        tools = await self.client.list_tools()
        logger.info("Tools available: %d", len(tools))

        common_resources = [
            "companies",
            "customers",
            "contacts",
            "items",
            "vendors",
            "salesOpportunities",
            "salesQuotes",
            "salesOrders",
            "salesInvoices",
        ]
        available_resources: List[str] = []
        for resource in common_resources:
            try:
                await self.client.list_items(resource, top=1)
                available_resources.append(resource)
                logger.info("  %s: available", resource)
            except Exception as e:
                logger.debug("  %s: unavailable (%s)", resource, str(e)[:50])

        return {
            "tools": tools,
            "available_resources": available_resources,
            "total_tools": len(tools),
            "total_resources": len(available_resources),
        }

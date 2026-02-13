"""
FastAPI Agent for Business Central MCP (knowall-ai)
====================================================

AI agent that uses the knowall-ai MCP server for Microsoft Dynamics 365 Business Central.
Integrates OpenAI with MCP tool-calling for querying and acting on BC data.

Configuration via environment variables (see .env.example).
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

# Load .env from project root (same dir as this file) so it works regardless of cwd
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    _env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass

from fastapi import FastAPI, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel

from bc_mcp_client_knowall import (
    BCMCPConfig,
    BusinessCentralMCPClient,
    MCPDiscoveryHelper,
    load_bc_config_from_env,
)

# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================================================
# FASTAPI APP
# =============================================================================


# =============================================================================
# PYDANTIC MODELS
# =============================================================================


class ChatMessage(BaseModel):
    message: str
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    conversation_id: str


class MCPStatusResponse(BaseModel):
    status: str
    tools_available: int
    tools: List[Dict[str, str]]
    resources_available: int
    resources: List[str]

# =============================================================================
# CONFIGURATION (from environment)
# =============================================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY is not set. Chat endpoint will return 503.")

openai_client: Optional[AsyncOpenAI] = None
if OPENAI_API_KEY:
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# MCP client and config are initialized at startup from env
mcp_client: Optional[BusinessCentralMCPClient] = None
mcp_tools_cache: List[Dict] = []
conversations: Dict[str, List[Dict]] = {}

# =============================================================================
# LIFECYCLE
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize MCP client on startup, stop on shutdown."""
    global mcp_client, mcp_tools_cache
    logger.info("Starting FastAPI server...")
    try:
        bc_config = load_bc_config_from_env()
        mcp_client = BusinessCentralMCPClient(bc_config)
        await mcp_client.start()
        mcp_tools_cache = await mcp_client.list_tools()
        logger.info("MCP client ready. Tools available: %d", len(mcp_tools_cache))
    except Exception as e:
        logger.error("Failed to initialize MCP client: %s", e)
        raise
    yield
    if mcp_client:
        logger.info("Stopping MCP client...")
        await mcp_client.stop()


app = FastAPI(
    title="Business Central AI Agent (MCP)",
    description="AI agent with access to Business Central via MCP (knowall-ai)",
    version="1.0.0",
    lifespan=lifespan,
)

# =============================================================================
# AGENT
# =============================================================================


class BusinessCentralAgent:
    """AI agent with access to Business Central via MCP tools."""

    def __init__(
        self,
        openai_client: AsyncOpenAI,
        mcp_client: BusinessCentralMCPClient,
    ) -> None:
        self.openai_client = openai_client
        self.mcp_client = mcp_client

    @staticmethod
    def _mcp_tools_to_openai_format(mcp_tools: List[Dict]) -> List[Dict]:
        """Convert MCP tools to OpenAI function-calling format."""
        openai_tools = []
        for tool in mcp_tools:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", "Business Central tool"),
                        "parameters": tool.get(
                            "inputSchema", {"type": "object", "properties": {}}
                        ),
                    },
                }
            )
        return openai_tools

    async def process_message(
        self, user_message: str, conversation_history: List[Dict]
    ) -> Dict[str, Any]:
        """Process a user message with OpenAI and optional MCP tool calls."""
        messages = conversation_history + [{"role": "user", "content": user_message}]
        tools = self._mcp_tools_to_openai_format(mcp_tools_cache)
        model = os.getenv("OPENAI_MODEL", "gpt-4o")

        response = await self.openai_client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        assistant_message = response.choices[0].message

        if assistant_message.tool_calls:
            tool_results = await self._execute_mcp_tools(assistant_message.tool_calls)
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in assistant_message.tool_calls
                    ],
                }
            )
            for tool_call, result in zip(
                assistant_message.tool_calls, tool_results
            ):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result),
                    }
                )
            final_response = await self.openai_client.chat.completions.create(
                model=model,
                messages=messages,
            )
            return {
                "response": final_response.choices[0].message.content,
                "tool_calls": [
                    {
                        "name": tc.function.name,
                        "arguments": json.loads(tc.function.arguments),
                        "result": result,
                    }
                    for tc, result in zip(
                        assistant_message.tool_calls, tool_results
                    )
                ],
            }
        return {
            "response": assistant_message.content or "",
            "tool_calls": None,
        }

    async def _execute_mcp_tools(self, tool_calls: Any) -> List[Dict]:
        """Run the MCP tools requested by the LLM and return normalized results."""
        results: List[Dict] = []
        for tool_call in tool_calls:
            name = tool_call.function.name
            arguments = json.loads(tool_call.function.arguments)
            try:
                result = await self.mcp_client.call_tool(name, arguments)
                if not isinstance(result, dict):
                    results.append(result)
                    continue
                if result.get("isError"):
                    error_text = ""
                    if result.get("content") and len(result["content"]) > 0:
                        error_text = result["content"][0].get("text", str(result))
                    results.append(
                        {
                            "error": error_text or "Tool execution failed",
                            "tool": name,
                        }
                    )
                    continue
                if result.get("content") and len(result["content"]) > 0:
                    item = result["content"][0]
                    text_content = item.get("text")
                    if text_content:
                        try:
                            parsed = json.loads(text_content)
                            if isinstance(parsed, dict) and "value" in parsed:
                                results.append(
                                    {
                                        "data": parsed["value"],
                                        "context": parsed.get("@odata.context", ""),
                                    }
                                )
                            else:
                                results.append(parsed)
                        except json.JSONDecodeError:
                            results.append({"text": text_content})
                    else:
                        results.append(item)
                else:
                    results.append(result)
            except Exception as e:
                logger.exception("Error running tool %s: %s", name, e)
                results.append({"error": str(e), "tool": name})
        return results

# =============================================================================
# ENDPOINTS
# =============================================================================


@app.get("/")
async def root() -> Dict[str, Any]:
    """Root endpoint with service info and links."""
    return {
        "service": "Business Central AI Agent (MCP)",
        "status": "running",
        "mcp_server": "knowall-ai/mcp-business-central",
        "endpoints": {
            "chat": "/chat",
            "mcp_status": "/mcp/status",
            "mcp_tools": "/mcp/tools",
            "mcp_resources": "/mcp/resources",
        },
    }


SYSTEM_PROMPT = """You are an AI assistant with access to Business Central via MCP.

You can query and answer questions about any Business Central data, including:
- customers: customer records, addresses, contacts
- contacts: contact persons
- items: product catalog, inventory, prices
- vendors: vendor information
- salesOrders: sales orders
- salesQuotes: sales quotes
- salesInvoices: sales invoices
- purchaseOrders: purchase orders
- and any other resources exposed by the API

Available tools:
- list_items: list records from a resource (supports OData filters, top, skip)
- get_items_by_field: find records by a specific field value
- get_schema: get structure/metadata of a resource
- create_item: create new records
- update_item: update existing records
- delete_item: delete records

When the user asks a specific question, use the tools to fetch real data and answer accurately.
Respond in a clear, professional way. If a tool fails, explain what went wrong in a helpful way."""


@app.post("/chat", response_model=ChatResponse)
async def chat(message: ChatMessage) -> ChatResponse:
    """
    Main chat endpoint. Sends the user message to the agent; the agent may call
    MCP tools to query Business Central and then reply.
    """
    if not mcp_client:
        raise HTTPException(
            status_code=503,
            detail="MCP client not initialized",
        )
    if not openai_client:
        raise HTTPException(
            status_code=503,
            detail="OpenAI not configured. Set OPENAI_API_KEY.",
        )

    conv_id = message.conversation_id or f"conv_{len(conversations)}"
    if conv_id not in conversations:
        conversations[conv_id] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

    agent = BusinessCentralAgent(openai_client, mcp_client)
    result = await agent.process_message(message.message, conversations[conv_id])

    conversations[conv_id].append({"role": "user", "content": message.message})
    conversations[conv_id].append({"role": "assistant", "content": result["response"]})

    return ChatResponse(
        response=result["response"],
        tool_calls=result.get("tool_calls"),
        conversation_id=conv_id,
    )


@app.get("/mcp/status", response_model=MCPStatusResponse)
async def get_mcp_status() -> MCPStatusResponse:
    """Return MCP connection status and list of available tools and resources."""
    if not mcp_client:
        raise HTTPException(status_code=503, detail="MCP client not initialized")

    resources_to_check = [
        "companies",
        "customers",
        "contacts",
        "items",
        "vendors",
        "salesOpportunities",
        "salesOrders",
    ]
    available_resources: List[str] = []
    for resource in resources_to_check:
        try:
            await mcp_client.list_items(resource, top=1)
            available_resources.append(resource)
        except Exception:
            pass

    return MCPStatusResponse(
        status="connected",
        tools_available=len(mcp_tools_cache),
        tools=[
            {"name": t["name"], "description": t.get("description", "")}
            for t in mcp_tools_cache
        ],
        resources_available=len(available_resources),
        resources=available_resources,
    )


@app.get("/mcp/tools")
async def list_mcp_tools() -> Dict[str, Any]:
    """List all MCP tools with their schemas."""
    if not mcp_client:
        raise HTTPException(status_code=503, detail="MCP client not initialized")
    return {"tools": mcp_tools_cache}


@app.get("/mcp/resources")
async def list_mcp_resources() -> Dict[str, Any]:
    """List Business Central resources and whether they are reachable."""
    if not mcp_client:
        raise HTTPException(status_code=503, detail="MCP client not initialized")

    test_resources = [
        "companies",
        "customers",
        "contacts",
        "items",
        "vendors",
        "salesOpportunities",
        "salesQuotes",
        "salesOrders",
        "salesInvoices",
        "purchaseOrders",
    ]
    available = []
    for resource in test_resources:
        try:
            result = await mcp_client.list_items(resource, top=1)
            available.append({"name": resource, "status": "available"})
        except Exception as e:
            available.append(
                {"name": resource, "status": "unavailable", "error": str(e)[:100]}
            )
    return {"resources": available}


@app.post("/mcp/call")
async def call_mcp_tool(tool_name: str, arguments: Dict[str, Any]) -> Any:
    """Call an MCP tool by name with the given arguments (for debugging)."""
    if not mcp_client:
        raise HTTPException(status_code=503, detail="MCP client not initialized")
    try:
        return await mcp_client.call_tool(tool_name, arguments)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)

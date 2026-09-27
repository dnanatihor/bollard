import json

from sqlalchemy.orm import Session

from gateway.mcp_proxy import McpDenied, invoke
from gateway.pipeline import GatewayFailure, execute_chat, new_ids
from gateway.security import Identity


async def run_agent(
    db: Session,
    identity: Identity,
    *,
    model: str,
    task: str,
    server_ids: list[str],
    max_steps: int,
) -> dict[str, object]:
    tools: list[dict[str, object]] = []
    steps: list[dict[str, object]] = []
    for server_id in server_ids:
        try:
            listed = await invoke(
                db,
                identity,
                server_id,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                require_grant=True,
            )
        except McpDenied as denied:
            steps.append({"serverId": server_id, "error": denied.message})
            continue
        result = listed.get("result") if isinstance(listed.get("result"), dict) else {}
        raw_tools = result.get("tools") if isinstance(result.get("tools"), list) else []
        if not raw_tools:
            steps.append({"serverId": server_id, "error": "The MCP server returned no tools."})
        for tool in raw_tools:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": f"{server_id}__{tool['name']}",
                        "description": str(tool.get("description") or ""),
                        "parameters": tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object", "properties": {}},
                    },
                }
            )
    if server_ids and not tools:
        if not steps:
            steps.append({"error": "The MCP server returned no tools."})
        return {"steps": steps}
    messages: list[dict[str, object]] = [{"role": "user", "content": task}]
    for _step in range(max_steps):
        request_id, trace_id = new_ids()
        try:
            payload = await execute_chat(
                db,
                identity,
                model=model,
                messages=messages,
                temperature=None,
                max_tokens=None,
                request_id=request_id,
                trace_id=trace_id,
                tools=tools or None,
            )
        except GatewayFailure as error:
            steps.append({"traceId": trace_id, "error": error.message})
            break
        choice = {}
        choices = payload.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                choice = message
        steps.append({"traceId": trace_id, "message": choice})
        calls = choice.get("tool_calls") if isinstance(choice.get("tool_calls"), list) else []
        if not calls:
            break
        messages.append(choice)
        for call in calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            full_name = str(function.get("name") or "")
            server_id, _, tool_name = full_name.partition("__")
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            try:
                result = await invoke(
                    db,
                    identity,
                    server_id,
                    {"jsonrpc": "2.0", "id": call.get("id") or 1, "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}},
                    require_grant=True,
                    trace_id=trace_id,
                )
                content = json.dumps(result.get("result") or result.get("error") or {}, default=str)
            except McpDenied as denied:
                content = denied.message
            messages.append({"role": "tool", "tool_call_id": str(call.get("id") or ""), "content": content})
            steps.append({"traceId": trace_id, "serverId": server_id, "tool": tool_name})
    return {"steps": steps}

#!/usr/bin/env python3
"""Minimal TikHub MCP client for 恐龙哥社媒研究.

Uses TikHub's platform-specific Streamable HTTP MCP endpoints:
  https://mcp.tikhub.io/{platform}/mcp

Flow: initialize -> tools/list -> tools/call.
Only Python standard library is required.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE = "https://mcp.tikhub.io"
PROTOCOL_VERSION = "2024-11-05"
USER_AGENT = "konglong-research/1.0.0"


class McpError(RuntimeError):
    pass


def has_api_key() -> bool:
    return bool(os.getenv("TIKHUB_API_KEY", "").strip())


def _auth_key() -> str:
    key = os.getenv("TIKHUB_API_KEY", "").strip()
    if not key:
        raise McpError("TIKHUB_API_KEY is missing")
    return key


def _parse_sse_or_json(body: bytes, content_type: str) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    if "text/event-stream" not in (content_type or "").lower() and text.startswith(("{", "[")):
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
        return {"result": payload}

    data_lines: list[str] = []
    last_json: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("data:"):
            value = line[5:].strip()
            if value and value != "[DONE]":
                data_lines.append(value)
                try:
                    candidate = json.loads(value)
                    if isinstance(candidate, dict):
                        last_json = candidate
                except json.JSONDecodeError:
                    pass
    if last_json is not None:
        return last_json
    if data_lines:
        joined = "\n".join(data_lines)
        try:
            candidate = json.loads(joined)
            return candidate if isinstance(candidate, dict) else {"result": candidate}
        except json.JSONDecodeError:
            return {"raw": joined}
    raise McpError("Unable to parse MCP response")


def _post_json(url: str, payload: dict[str, Any], session_id: str | None = None) -> tuple[dict[str, Any], str | None]:
    headers = {
        "Authorization": f"Bearer {_auth_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": USER_AGENT,
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "")
            sid = resp.headers.get("Mcp-Session-Id") or session_id
            return _parse_sse_or_json(body, content_type), sid
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")[:1000]
        raise McpError(f"HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise McpError(f"Network error: {exc.reason}") from exc


def _get_json(url: str, authenticated: bool = False) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if authenticated:
        headers["Authorization"] = f"Bearer {_auth_key()}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")[:1000]
        raise McpError(f"HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise McpError(f"Network error: {exc.reason}") from exc


def base_url() -> str:
    return os.getenv("TIKHUB_MCP_BASE_URL", DEFAULT_BASE).rstrip("/")


def initialize(platform: str) -> tuple[str, dict[str, Any]]:
    url = f"{base_url()}/{platform}/mcp"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "konglong-research", "version": "1.0.0"},
        },
    }
    result, sid = _post_json(url, payload)
    if not sid:
        raise McpError("MCP server did not return Mcp-Session-Id")
    return sid, result


def list_tools(platform: str) -> list[dict[str, Any]]:
    sid, _ = initialize(platform)
    url = f"{base_url()}/{platform}/mcp"
    payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    result, _ = _post_json(url, payload, sid)
    if "error" in result:
        raise McpError(json.dumps(result["error"], ensure_ascii=False))
    tools = ((result.get("result") or {}).get("tools") or [])
    if not isinstance(tools, list):
        raise McpError("Unexpected tools/list response schema")
    return tools


def call_tool(platform: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    sid, _ = initialize(platform)
    url = f"{base_url()}/{platform}/mcp"
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    result, _ = _post_json(url, payload, sid)
    if "error" in result:
        raise McpError(json.dumps(result["error"], ensure_ascii=False))
    return result


def tool_search(tools: list[dict[str, Any]], query: str, limit: int = 20) -> list[dict[str, Any]]:
    tokens = [t.lower() for t in query.split() if t.strip()]
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for tool in tools:
        name = str(tool.get("name", ""))
        desc = str(tool.get("description", ""))
        schema = json.dumps(tool.get("inputSchema", {}), ensure_ascii=False)
        haystack = f"{name} {desc} {schema}".lower()
        score = sum(1 for token in tokens if token in haystack)
        if score:
            ranked.append((score, name, tool))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [tool for _, _, tool in ranked[:limit]]


def write_or_print(payload: Any, out: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        print(path)
    else:
        print(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="TikHub MCP helper for 恐龙哥社媒研究")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    sub.add_parser("health")
    sub.add_parser("platforms")

    p_list = sub.add_parser("list-tools")
    p_list.add_argument("--platform", required=True)
    p_list.add_argument("--out")

    p_discover = sub.add_parser("discover")
    p_discover.add_argument("--platform", required=True)
    p_discover.add_argument("--query", required=True)
    p_discover.add_argument("--limit", type=int, default=20)
    p_discover.add_argument("--out")

    p_call = sub.add_parser("call")
    p_call.add_argument("--platform", required=True)
    p_call.add_argument("--tool", required=True)
    p_call.add_argument("--args", default="{}", help="JSON object")
    p_call.add_argument("--args-file", help="Path to JSON object; overrides --args")
    p_call.add_argument("--out")

    args = parser.parse_args()
    try:
        if args.command == "status":
            write_or_print({"has_api_key": has_api_key()}, None)
        elif args.command == "health":
            write_or_print(_get_json(f"{base_url()}/health"), None)
        elif args.command == "platforms":
            write_or_print(_get_json(f"{base_url()}/platforms"), None)
        elif args.command == "list-tools":
            write_or_print(list_tools(args.platform), args.out)
        elif args.command == "discover":
            tools = list_tools(args.platform)
            write_or_print(tool_search(tools, args.query, args.limit), args.out)
        elif args.command == "call":
            raw_args = Path(args.args_file).read_text(encoding="utf-8") if args.args_file else args.args
            arguments = json.loads(raw_args)
            if not isinstance(arguments, dict):
                raise McpError("Tool arguments must be a JSON object")
            write_or_print(call_tool(args.platform, args.tool, arguments), args.out)
        return 0
    except (McpError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

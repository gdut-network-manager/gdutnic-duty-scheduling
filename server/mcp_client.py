from __future__ import annotations

import json
from typing import Any, AsyncIterator
from urllib.parse import urljoin, urlparse

import httpx

PROTOCOL_VERSION = "2025-03-26"


class McpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class OutlineMcpClient:
    def __init__(self, mcp_url: str, token: str, timeout: float = 60.0):
        self.mcp_url = mcp_url.rstrip("/")
        if not self.mcp_url.endswith("/mcp"):
            # Allow pasting the workspace origin or a doc URL.
            parsed = urlparse(mcp_url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            self.mcp_url = f"{origin}/mcp"
        self.token = token.strip()
        self.timeout = timeout
        self._id = 0
        self.session_id: str | None = None
        self.server_info: dict[str, Any] = {}
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "OutlineMcpClient":
        last: Exception | None = None
        for trust_env in (True, False):
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(90.0, connect=20.0),
                follow_redirects=False,
                trust_env=trust_env,
            )
            try:
                await self.initialize()
                return self
            except Exception as exc:
                last = exc
                print(f"[mcp] connect trust_env={trust_env}: {type(exc).__name__}: {exc}")
                await self._client.aclose()
                self._client = None
        raise last if last else RuntimeError("无法连接网协 Wiki")

    async def __aexit__(self, *args: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if extra:
            headers.update(extra)
        return headers

    async def _rpc(self, method: str, params: dict[str, Any] | None = None, notify: bool = False) -> Any:
        if self._client is None:
            raise McpError("MCP client is not started")
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            payload["id"] = self._next_id()
        if method == "initialize":
            payload["params"] = params or {}
        elif params:
            payload["params"] = params
        headers = self._headers()
        if self.server_info:
            negotiated = (self.server_info.get("protocolVersion") or PROTOCOL_VERSION)
            headers["MCP-Protocol-Version"] = negotiated
        response = await self._client.post(self.mcp_url, headers=headers, json=payload)
        session = response.headers.get("mcp-session-id") or response.headers.get("Mcp-Session-Id")
        if session:
            self.session_id = session
        if response.status_code == 401:
            raise McpError(
                "Outline MCP 需要登录：请重新点击企业登录",
                status=401,
                payload=_safe_json(response),
            )
        if response.status_code >= 300:
            raise McpError(
                "MCP 请求失败，请检查配置或重新登录",
                status=response.status_code,
                payload=_safe_json(response),
            )
        if notify:
            return None
        if not (response.content or b"").strip():
            raise McpError(f"MCP {method} 返回空响应 HTTP {response.status_code}")
        body = await _read_jsonrpc(response)
        if "error" in body:
            err = body["error"]
            raise McpError("MCP 请求失败", payload=err)
        return body.get("result")

    async def initialize(self) -> dict[str, Any]:
        last_error: McpError | None = None
        result: dict[str, Any] | None = None
        for version in (PROTOCOL_VERSION, "2024-11-05"):
            try:
                result = await self._rpc(
                    "initialize",
                    {
                        "protocolVersion": version,
                        "capabilities": {"tools": {}},
                        "clientInfo": {"name": "gdutnic-agent", "version": "0.8.0"},
                    },
                )
                break
            except McpError as exc:
                last_error = exc
                if exc.status in {401, 403}:
                    raise
        if result is None:
            raise last_error or McpError("MCP initialize 失败")
        self.server_info = result if isinstance(result, dict) else {}
        try:
            await self._rpc("notifications/initialized", notify=True)
        except McpError:
            pass
        return result

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._rpc("tools/list")
        return list((result or {}).get("tools") or [])

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        return result


async def _read_jsonrpc(response: httpx.Response) -> dict[str, Any]:
    ctype = (response.headers.get("content-type") or "").lower()
    if "text/event-stream" in ctype:
        last: dict[str, Any] | None = None
        async for event in _iter_sse(response.aiter_lines()):
            if event.get("event") == "endpoint":
                continue
            data = event.get("data")
            if not data or data == "[DONE]":
                continue
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
                last = parsed
        if last is None:
            raise McpError("MCP SSE 未返回 JSON-RPC 结果")
        return last
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise McpError("无法解析 MCP 响应") from exc


async def _iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, str]]:
    event: dict[str, str] = {}
    data_lines: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data_lines or event:
                event["data"] = "\n".join(data_lines)
                yield event
            event = {}
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event["event"] = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line.startswith("id:"):
            event["id"] = line[3:].strip()
    if data_lines or event:
        event["data"] = "\n".join(data_lines)
        yield event


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text[:400]


def origin_from_mcp_url(mcp_url: str) -> str:
    parsed = urlparse(mcp_url if "://" in mcp_url else f"https://{mcp_url}")
    return f"{parsed.scheme}://{parsed.netloc}"


def normalize_mcp_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("MCP 地址为空")
    parsed = urlparse(raw)
    if not parsed.scheme:
        raw = "https://" + raw
        parsed = urlparse(raw)
    if parsed.path.rstrip("/").endswith("/mcp"):
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
    return urljoin(f"{parsed.scheme}://{parsed.netloc}/", "mcp")

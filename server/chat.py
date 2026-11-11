from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator

import httpx

from server.mcp_client import OutlineMcpClient
from server import deepseek_vision

SYSTEM_PROMPT = """你是给网管提供协助的帮手，需要给网管回复用户提供意见和协助。
规则：
1. 回答用户问题前，必须先调用 MCP 工具检索或阅读相关文档，不要凭记忆编造。
2. 若检索不到，明确说明知识库中没有找到，并列出你尝试过的关键词。
3. 引用时写明文档标题；若工具返回了链接或 URL，一并给出。
4. 用用户使用的语言作答，默认简体中文。
5. 只基于工具返回的内容作答，不要扩写成未经证实的政策或数据。
6. 不要用相同参数反复调用同一工具；已经拿到相关文档后立即作答。
"""

VISION_DESCRIBE_PROMPT = """请客观描述图片中与用户问题相关的文字、界面和关键细节。
不要给操作建议，不要调用工具，用简体中文写一段话即可。"""


def normalize_openai_base(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return "https://api.openai.com/v1"
    if url.endswith("/chat/completions"):
        return url[: -len("/chat/completions")]
    return url


def mcp_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for tool in tools:
        schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description") or "",
                    "parameters": schema,
                },
            }
        )
    return converted


def _extract_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text") or "")
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        text = "\n".join(parts)
        if result.get("isError"):
            return f"[工具错误]\n{text}"
        return text
    return json.dumps(result, ensure_ascii=False)


async def stream_chat(
    *,
    openai_base: str,
    openai_key: str,
    model: str,
    messages: list[dict[str, Any]],
    mcp: OutlineMcpClient,
    tools: list[dict[str, Any]],
    extra_headers: dict[str, str] | None = None,
    provider: str = "",
    system_prompt: str = "",
    mcp_heat: int = 50,
) -> AsyncIterator[dict[str, Any]]:
    heat = max(0, min(100, int(mcp_heat)))
    openai_tools = mcp_tools_to_openai(tools) if heat > 0 else []
    prompt = (system_prompt or "").strip() or SYSTEM_PROMPT
    if "不要用相同参数反复调用" not in prompt:
        prompt += "\n不要用相同参数反复调用同一工具；已经拿到相关文档后立即作答。"
    prompt += "\n" + _mcp_heat_instruction(heat)
    outgoing = messages
    use_model = model
    has_images = deepseek_vision.has_images(messages)
    deepseek = deepseek_vision.is_deepseek_provider(provider, openai_base)
    if deepseek:
        use_model = deepseek_vision.resolve_model(model, messages)
        if has_images:
            outgoing = deepseek_vision.prepare_messages(messages)
    working = [{"role": "system", "content": prompt}, *outgoing]
    endpoint = f"{normalize_openai_base(openai_base)}/chat/completions"
    headers = {
        "Authorization": f"Bearer {openai_key}",
        "Content-Type": "application/json",
        **(extra_headers or {}),
    }
    max_tool_rounds = _max_tool_rounds(heat)

    async with httpx.AsyncClient(timeout=120.0) as client:
        if deepseek and has_images:
            describe: dict[str, Any] = {
                "role": "assistant",
                "content": "",
                "tool_calls": [],
                "reasoning_content": "",
            }
            vis_payload: dict[str, Any] = {
                "model": deepseek_vision.VISION_MODEL,
                "messages": [
                    {"role": "system", "content": VISION_DESCRIBE_PROMPT},
                    *outgoing,
                ],
                "stream": True,
                "temperature": 0.2,
            }
            yield {"type": "thinking"}
            think_open = False
            async for event in _stream_completion(client, endpoint, headers, vis_payload):
                kind = event["type"]
                if kind == "error":
                    yield event
                    return
                if kind == "thinking":
                    yield {"type": "thinking"}
                elif kind == "delta":
                    visible, think_open = _strip_think(event["text"], think_open)
                    if visible:
                        describe["content"] += visible
                    yield {"type": "thinking"}
            note = (describe["content"] or "").strip() or "未能从图片中识别出有效信息。"
            outgoing = deepseek_vision.replace_images_with_note(messages, note)
            use_model = deepseek_vision.TEXT_MODEL
            working = [{"role": "system", "content": prompt}, *outgoing]

        seen_calls: set[str] = set()
        for round_i in range(max_tool_rounds + 1):
            allow_tools = bool(openai_tools) and round_i < max_tool_rounds
            payload: dict[str, Any] = {
                "model": use_model,
                "messages": working,
                "stream": True,
            }
            if not _is_thinking_model(use_model):
                payload["temperature"] = 0.2
            else:
                payload["thinking"] = {"type": "enabled"}
                payload["reasoning_effort"] = "high"
            if allow_tools:
                payload["tools"] = openai_tools
                payload["tool_choice"] = "required" if heat >= 75 and round_i == 0 else "auto"
            elif round_i == max_tool_rounds:
                working.append(
                    {
                        "role": "user",
                        "content": "请根据已经检索到的内容直接作答，不要再调用工具。若资料不足，说明知识库中没有找到。",
                    }
                )
                payload["messages"] = working

            assistant: dict[str, Any] = {
                "role": "assistant",
                "content": "",
                "tool_calls": [],
                "reasoning_content": "",
            }
            think_open = False
            async for event in _stream_completion(client, endpoint, headers, payload):
                kind = event["type"]
                if kind == "thinking":
                    if event.get("text"):
                        assistant["reasoning_content"] += event["text"]
                    yield {"type": "thinking"}
                elif kind == "delta":
                    visible, think_open = _strip_think(event["text"], think_open)
                    if think_open:
                        yield {"type": "thinking"}
                    if visible:
                        assistant["content"] += visible
                        yield {"type": "delta", "text": visible}
                elif kind == "tool_call_delta":
                    _merge_tool_call(assistant["tool_calls"], event["index"], event["delta"])
                elif kind == "error":
                    yield event
                    return
                elif kind == "done":
                    pass

            if assistant["tool_calls"] and allow_tools:
                normalized_calls = []
                for call in assistant["tool_calls"]:
                    fn = call.get("function") or {}
                    normalized_calls.append(
                        {
                            "id": call.get("id") or f"call_{len(normalized_calls)}",
                            "type": "function",
                            "function": {
                                "name": fn.get("name") or "",
                                "arguments": fn.get("arguments") or "{}",
                            },
                        }
                    )
                working.append(
                    {
                        "role": "assistant",
                        "content": assistant["content"] or None,
                        "reasoning_content": assistant["reasoning_content"] or None,
                        "tool_calls": normalized_calls,
                    }
                )
                for call in normalized_calls:
                    name = call["function"]["name"]
                    raw_args = call["function"]["arguments"] or "{}"
                    try:
                        arguments = json.loads(raw_args) if raw_args.strip() else {}
                    except json.JSONDecodeError:
                        arguments = {"_raw": raw_args}
                    key = _tool_call_key(name, arguments, raw_args)
                    yield {"type": "tool_start", "name": name, "arguments": arguments}
                    if key in seen_calls:
                        text = "该工具已用相同参数调用过，请基于已有结果作答，不要重复调用。"
                    else:
                        seen_calls.add(key)
                        try:
                            result = await mcp.call_tool(name, arguments if isinstance(arguments, dict) else {})
                            text = _extract_text(result)
                        except Exception as exc:
                            text = f"工具调用失败: {exc}"
                    yield {"type": "tool_end", "name": name, "result": text[:8000]}
                    working.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": text[:24000],
                        }
                    )
                continue

            yield {"type": "done", "content": assistant["content"]}
            return

        yield {"type": "error", "message": "工具调用轮次过多，已停止"}


def _mcp_heat_instruction(heat: int) -> str:
    if heat <= 0:
        return "本次不要调用任何工具，直接作答。"
    if heat < 35:
        return f"MCP 检索热度为 {heat}：优先一次检索后作答，不要反复换关键词。"
    if heat < 75:
        return f"MCP 检索热度为 {heat}：检索到相关文档后即可作答。"
    return f"MCP 检索热度为 {heat}：请充分检索，必要时阅读多篇文档后再作答。"


def _max_tool_rounds(heat: int) -> int:
    if heat <= 0:
        return 0
    return max(1, min(8, round(heat / 100 * 8)))


def _tool_call_key(name: str, arguments: Any, raw_args: str) -> str:
    if isinstance(arguments, dict):
        packed = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    else:
        packed = raw_args or ""
    return f"{name}:{packed}"


def _is_thinking_model(model: str, provider: str = "") -> bool:
    name = (model or "").lower()
    if not name or deepseek_vision.is_vision_model(name):
        return False
    return any(token in name for token in ("reasoner", "-r1", "r1-", "thinking"))


def _reasoning_text(choice: dict[str, Any], delta: dict[str, Any]) -> str:
    message = choice.get("message") or {}
    for src in (delta, message, choice):
        if not isinstance(src, dict):
            continue
        for key in ("reasoning_content", "reasoning", "thinking", "reasoning_text"):
            value = src.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _strip_think(text: str, in_think: bool) -> tuple[str, bool]:
    visible: list[str] = []
    i = 0
    while i < len(text):
        if in_think:
            end = text.find("</think>", i)
            if end == -1:
                return "".join(visible), True
            i = end + len("</think>")
            in_think = False
            continue
        start = text.find("<think>", i)
        if start == -1:
            visible.append(text[i:])
            break
        visible.append(text[i:start])
        in_think = True
        i = start + len("<think>")
    return "".join(visible), in_think


def _merge_tool_call(calls: list[dict[str, Any]], index: int, delta: dict[str, Any]) -> None:
    while len(calls) <= index:
        calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
    target = calls[index]
    if delta.get("id"):
        target["id"] = delta["id"]
    fn = delta.get("function") or {}
    if fn.get("name"):
        target["function"]["name"] += fn["name"]
    if fn.get("arguments"):
        target["function"]["arguments"] += fn["arguments"]


async def _stream_completion(
    client: httpx.AsyncClient,
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
        if response.status_code >= 400:
            body = (await response.aread()).decode("utf-8", errors="replace")
            yield {"type": "error", "message": f"模型接口错误 HTTP {response.status_code}: {body[:800]}"}
            return
        async for line in response.aiter_lines():
            if not line:
                continue
            if line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                yield {"type": "done"}
                return
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                yield {"type": "error", "message": str(chunk["error"])}
                return
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            reasoning = _reasoning_text(choice, delta)
            if reasoning:
                yield {"type": "thinking", "text": reasoning}
            content = delta.get("content")
            if content:
                yield {"type": "delta", "text": content}
            for tc in delta.get("tool_calls") or []:
                yield {
                    "type": "tool_call_delta",
                    "index": tc.get("index") or 0,
                    "delta": tc,
                }
            if choice.get("finish_reason"):
                yield {"type": "done"}


TITLE_SYSTEM = """你是主题标签器。阅读助手回复，给出该次问答在谈什么。
只输出一行 JSON，格式严格为 {"t":"主题"}。
t 是 4 到 10 个汉字的名词短语，例如 宿舍网691、VPN连不上、打印机驱动。
禁止完整句子，禁止出现：标题、生成、拟定、根据、回复、我们。"""


def _plain_message(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(
            str(part.get("text") or "").strip()
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
    return ""


_TITLE_BAD = re.compile(
    r"(标题|拟定|生成|根据回复|回复内容|只需要|只输出|知识库中|我来帮|查询知识库|不要解释)"
)


def _clean_title(text: str) -> str:
    visible, _ = _strip_think(text or "", False)
    line = visible.strip().splitlines()[0] if visible.strip() else ""
    line = re.sub(r"^(标题|title|t)\s*[:：=]\s*", "", line, flags=re.I)
    line = line.strip(" \t\"'“”‘’《》【】[]{}。.．")
    line = re.sub(r"\s+", " ", line)
    return line[:12]


def _title_from_model(raw: str) -> str:
    visible, _ = _strip_think(raw or "", False)
    match = re.search(r"\{[^{}]+\}", visible)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                visible = str(obj.get("t") or obj.get("title") or "")
        except json.JSONDecodeError:
            pass
    return _clean_title(visible)


def _usable_title(title: str, source: str) -> str:
    title = _clean_title(title)
    if len(title) < 2:
        return ""
    if _TITLE_BAD.search(title):
        return ""
    compact_title = re.sub(r"\s+", "", title)
    compact_src = re.sub(r"\s+", "", source or "")
    if compact_src.startswith(compact_title) and len(compact_title) >= 4:
        return ""
    return title


_TITLE_SHOTS = [
    ("校园网认证提示 691，账号密码无误。", "认证691故障"),
    ("连上 VPN 后打不开内网系统。", "VPN内网不通"),
]


async def summarize_title(
    *,
    openai_base: str,
    openai_key: str,
    model: str,
    messages: list[dict[str, Any]],
    extra_headers: dict[str, str] | None = None,
    provider: str = "",
) -> str:
    replies: list[str] = []
    for item in messages:
        if item.get("role") != "assistant":
            continue
        text = _plain_message(item.get("content"))
        if not text:
            continue
        replies.append(text[:800])
        if len(replies) >= 2:
            break
    if not replies:
        return ""
    source = "\n".join(replies)
    use_model = deepseek_vision.title_model(model) if deepseek_vision.is_deepseek_provider(provider, openai_base) else model
    shot_messages: list[dict[str, Any]] = [{"role": "system", "content": TITLE_SYSTEM}]
    for sample, label in _TITLE_SHOTS:
        shot_messages.append({"role": "user", "content": sample})
        shot_messages.append({"role": "assistant", "content": json.dumps({"t": label}, ensure_ascii=False)})
    shot_messages.append({"role": "user", "content": source})
    payload: dict[str, Any] = {
        "model": use_model,
        "messages": shot_messages,
        "stream": False,
        "max_tokens": 24,
    }
    if not _is_thinking_model(use_model):
        payload["temperature"] = 0.1
    endpoint = f"{normalize_openai_base(openai_base)}/chat/completions"
    headers = {
        "Authorization": f"Bearer {openai_key}",
        "Content-Type": "application/json",
        **(extra_headers or {}),
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
        if response.status_code >= 400:
            print(f"[title] HTTP {response.status_code}: {response.text[:300]}")
            response.raise_for_status()
        data = response.json()
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    raw = str(message.get("content") or "")
    title = _usable_title(_title_from_model(raw), source)
    if title:
        return title
    heading = re.search(r"^#{1,6}\s+(.+)$", source, re.M)
    if heading:
        return _usable_title(heading.group(1), source)
    return ""


from __future__ import annotations

import base64
from typing import Any

from urllib.parse import urlparse


VISION_MODEL = "deepseek-v4-flash-vision-exp"
TEXT_MODEL = "deepseek-v4-flash"
MAX_IMAGE_BYTES = 32 * 1024 * 1024
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_DEEPSEEK_HOSTS = {"api.deepseek.com"}


def is_deepseek_provider(provider: str, openai_base: str) -> bool:
    if (provider or "").strip().lower() == "deepseek":
        return True
    raw = (openai_base or "").strip()
    if not raw:
        return False
    try:
        host = (urlparse(raw if "://" in raw else f"https://{raw}").hostname or "").lower()
    except ValueError:
        return False
    return host in _DEEPSEEK_HOSTS


def sniff_image_type(data: bytes) -> str:
    if len(data) >= 2 and data[0] == 0xFF and data[1] == 0xD8:
        return "image/jpeg"
    if len(data) >= 8 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 6 and (data.startswith(b"GIF87a") or data.startswith(b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _decode_data_url(url: str) -> bytes | None:
    raw = (url or "").strip()
    if not raw.startswith("data:"):
        return None
    _, _, payload = raw.partition(",")
    if not payload:
        return None
    try:
        data = base64.b64decode(payload, validate=False)
    except Exception:
        return None
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    return data


def _to_data_url(data: bytes, mime: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def has_images(messages: list[dict[str, Any]]) -> bool:
    for item in messages:
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                return True
    return False


def _plain(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()


def _user_blocks(content: Any) -> list[dict[str, Any]]:
    texts: list[str] = []
    images: list[dict[str, Any]] = []
    if isinstance(content, str):
        if content.strip():
            texts.append(content.strip())
    elif isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = str(part.get("text") or "").strip()
                if text:
                    texts.append(text)
            elif part.get("type") == "image_url":
                src = part.get("image_url")
                url = src.get("url") if isinstance(src, dict) else part.get("url")
                data = _decode_data_url(str(url or ""))
                if not data:
                    continue
                mime = sniff_image_type(data)
                if mime not in ALLOWED_TYPES:
                    continue
                images.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _to_data_url(data, mime),
                            "detail": "auto",
                        },
                    }
                )
    blocks: list[dict[str, Any]] = []
    text = "\n".join(texts).strip()
    if not text and images:
        text = "请结合图片说明。"
    if text:
        blocks.append({"type": "text", "text": text})
    blocks.extend(images)
    return blocks


def prepare_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rewrite history for DeepSeek vision: images only on the latest user turn."""
    last_user = -1
    for i, item in enumerate(messages):
        if str(item.get("role") or "") == "user":
            last_user = i
    prepared: list[dict[str, Any]] = []
    for i, item in enumerate(messages):
        role = str(item.get("role") or "")
        content = item.get("content")
        if role == "user":
            if i != last_user:
                text = _plain(content)
                if not text and _content_has_image(content):
                    text = "（用户发送了图片）"
                if text:
                    prepared.append({"role": "user", "content": text})
                continue
            blocks = _user_blocks(content)
            if not blocks:
                continue
            prepared.append({"role": "user", "content": blocks})
            continue
        if role == "assistant":
            text = _plain(content)
            prepared.append({"role": "assistant", "content": text})
    return prepared


def _content_has_image(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(part, dict) and part.get("type") == "image_url" for part in content)


def is_vision_model(model: str) -> bool:
    return "vision" in (model or "").lower()


def resolve_model(model: str, messages: list[dict[str, Any]]) -> str:
    if has_images(messages):
        return VISION_MODEL
    name = (model or "").strip()
    if not name or is_vision_model(name):
        return TEXT_MODEL
    return name


def title_model(model: str) -> str:
    name = (model or "").strip()
    if not name or is_vision_model(name):
        return TEXT_MODEL
    return name


def replace_images_with_note(messages: list[dict[str, Any]], note: str) -> list[dict[str, Any]]:
    """Turn image turns into text so the text model can search the wiki."""
    caption = (note or "").strip() or "未能从图片中识别出有效信息。"
    prepared: list[dict[str, Any]] = []
    for item in messages:
        role = str(item.get("role") or "")
        content = item.get("content")
        if role == "user":
            text = _plain(content)
            if _content_has_image(content):
                text = "\n".join(part for part in (text, f"【图片识别】{caption}") if part)
            if text:
                prepared.append({"role": "user", "content": text})
            continue
        if role == "assistant":
            prepared.append({"role": "assistant", "content": _plain(content)})
    return prepared

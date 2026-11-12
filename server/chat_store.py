from __future__ import annotations

import json
import time
from typing import Any

from server import db

MAX_SESSIONS = 80
MAX_MESSAGES = 240
MAX_CONTENT = 20000
MAX_IMAGE_URL = 2_000_000


class ChatQuotaExceeded(ValueError):
    pass

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def _plain_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _trim_image_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw.startswith("data:"):
        return ""
    header, _, data = raw.partition(",")
    mime = header[5:].split(";")[0].strip().lower()
    if mime not in ALLOWED_IMAGE_TYPES or not data or len(raw) > MAX_IMAGE_URL:
        return ""
    return raw


def _trim_content(content: Any) -> Any:
    if isinstance(content, list):
        parts: list[dict[str, Any]] = []
        for part in content[:32]:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = str(part.get("text") or "")[:MAX_CONTENT]
                if text:
                    parts.append({"type": "text", "text": text})
            elif part.get("type") == "image_url":
                src = part.get("image_url")
                url = src.get("url") if isinstance(src, dict) else part.get("url")
                cleaned = _trim_image_url(str(url or ""))
                if cleaned:
                    parts.append({"type": "image_url", "image_url": {"url": cleaned}})
        return parts
    return str(content or "")[:MAX_CONTENT]


def _trim_session(raw: dict[str, Any]) -> dict[str, Any] | None:
    sid = str(raw.get("id") or "").strip()
    if not sid:
        return None
    messages = []
    for item in raw.get("messages") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = _trim_content(item.get("content"))
        if content == []:
            continue
        messages.append({"role": role, "content": content})
        if len(messages) >= MAX_MESSAGES:
            break
    theme = raw.get("userTheme") if isinstance(raw.get("userTheme"), dict) else {}
    return {
        "id": sid,
        "title": str(raw.get("title") or "新会话")[:80],
        "userTheme": {
            "bg": str(theme.get("bg") or ""),
            "ink": str(theme.get("ink") or ""),
        },
        "messages": messages,
        "updatedAt": int(time.time() * 1000),
    }


def _max_mb(conn) -> int:
    try:
        value = int(db.get_setting(conn, "chat_max_mb", 32) or 32)
    except (TypeError, ValueError):
        value = 32
    return max(1, min(value, 4096))


def _usage_bytes(conn) -> int:
    row = conn.execute(
        """
        SELECT
          COALESCE((SELECT SUM(LENGTH(content)) FROM messages), 0)
          + COALESCE((SELECT SUM(LENGTH(title) + LENGTH(id) + LENGTH(visitor_id)) FROM chats), 0)
          AS n
        """
    ).fetchone()
    return int(row["n"] if row else 0)


def _evict_oldest(conn, max_mb: int) -> None:
    limit = max_mb * 1024 * 1024
    # Called inside a write transaction: subtract each deletion rather than
    # rescanning all remaining message content after every evicted session.
    used = _usage_bytes(conn)
    while used > limit:
        row = conn.execute(
            "SELECT visitor_id, id, LENGTH(title) + LENGTH(id) + LENGTH(visitor_id) AS size "
            "FROM chats ORDER BY updated_at ASC, id ASC LIMIT 1"
        ).fetchone()
        if not row:
            break
        messages = conn.execute(
            "SELECT COALESCE(SUM(LENGTH(content)), 0) AS size FROM messages "
            "WHERE visitor_id = ? AND chat_id = ?", (row["visitor_id"], row["id"])
        ).fetchone()
        conn.execute(
            "DELETE FROM chats WHERE visitor_id = ? AND id = ?",
            (row["visitor_id"], row["id"]),
        )
        conn.execute(
            """
            DELETE FROM visitors
            WHERE id = ?
              AND NOT EXISTS (SELECT 1 FROM chats WHERE visitor_id = visitors.id)
            """,
            (row["visitor_id"],),
        )
        used -= int(row["size"]) + int(messages["size"])


def _session_from_row(conn, row) -> dict[str, Any]:
    messages = []
    for item in conn.execute(
        """
        SELECT role, content FROM messages
        WHERE visitor_id = ? AND chat_id = ?
        ORDER BY seq ASC
        """,
        (row["visitor_id"], row["id"]),
    ):
        try:
            content = json.loads(item["content"])
        except json.JSONDecodeError:
            content = item["content"]
        messages.append({"role": item["role"], "content": content})
    return {
        "id": row["id"],
        "title": row["title"] or "新会话",
        "userTheme": {"bg": row["theme_bg"] or "", "ink": row["theme_ink"] or ""},
        "messages": messages,
        "updatedAt": int(row["updated_at"] or 0),
    }


def stats() -> dict[str, Any]:
    with db.read() as conn:
        used = _usage_bytes(conn)
        max_mb = _max_mb(conn)
    return {
        "max_mb": max_mb,
        "used_bytes": used,
        "used_mb": round(used / (1024 * 1024), 2),
    }


def set_max_mb(max_mb: int) -> dict[str, Any]:
    value = max(1, min(int(max_mb), 4096))
    with db.write() as conn:
        db.set_setting(conn, "chat_max_mb", value)
        _evict_oldest(conn, value)
    return stats()


def save_visitor_chats(visitor_id: str, sessions: list[Any], current_id: str | None) -> None:
    visitor_id = (visitor_id or "").strip()
    if not visitor_id:
        return
    cleaned: list[dict[str, Any]] = []
    for item in sessions[:MAX_SESSIONS]:
        if not isinstance(item, dict):
            continue
        row = _trim_session(item)
        if row and row.get("messages"):
            cleaned.append(row)
    for index, row in enumerate(cleaned):
        row["updatedAt"] -= index  # Server ordering; client timestamps never affect eviction.
    cleaned = cleaned[:MAX_SESSIONS]
    with db.write() as conn:
        if not cleaned:
            conn.execute("DELETE FROM visitors WHERE id = ?", (visitor_id,))
            return
        db.upsert(conn, "visitors", {"id": visitor_id, "current_id": str(current_id or "")}, ["id"])
        conn.execute("DELETE FROM chats WHERE visitor_id = ?", (visitor_id,))
        for session in cleaned:
            conn.execute(
                """
                INSERT INTO chats(visitor_id, id, title, updated_at, theme_bg, theme_ink)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    visitor_id,
                    session["id"],
                    session["title"],
                    int(session.get("updatedAt") or 0),
                    session["userTheme"]["bg"],
                    session["userTheme"]["ink"],
                ),
            )
            conn.executemany(
                "INSERT INTO messages(visitor_id, chat_id, seq, role, content) VALUES(?, ?, ?, ?, ?)",
                (
                    (
                        visitor_id,
                        session["id"],
                        seq,
                        message["role"],
                        json.dumps(message.get("content"), ensure_ascii=False),
                    )
                    for seq, message in enumerate(session["messages"])
                ),
            )
        if _usage_bytes(conn) > _max_mb(conn) * 1024 * 1024:
            # Roll back this write instead of deleting another account's history.
            raise ChatQuotaExceeded("chat storage quota exceeded")


def load_visitor_chats(visitor_id: str) -> dict[str, Any]:
    visitor_id = (visitor_id or "").strip()
    with db.read() as conn:
        rec = conn.execute("SELECT current_id FROM visitors WHERE id = ?", (visitor_id,)).fetchone()
        rows = conn.execute(
            """
            SELECT visitor_id, id, title, updated_at, theme_bg, theme_ink
            FROM chats
            WHERE visitor_id = ?
            ORDER BY updated_at DESC
            """,
            (visitor_id,),
        ).fetchall()
        sessions = [_session_from_row(conn, row) for row in rows]
    return {
        "currentId": str(rec["current_id"] or "") if rec else "",
        "sessions": sessions,
    }


def list_all() -> list[dict[str, Any]]:
    with db.read() as conn:
        rows = conn.execute(
            """
            SELECT
              c.visitor_id,
              c.id,
              c.title,
              c.updated_at,
              c.theme_bg,
              c.theme_ink,
              (SELECT COUNT(*) FROM messages m WHERE m.visitor_id = c.visitor_id AND m.chat_id = c.id) AS message_count,
              (
                SELECT m.content FROM messages m
                WHERE m.visitor_id = c.visitor_id AND m.chat_id = c.id AND m.role = 'user'
                ORDER BY m.seq ASC LIMIT 1
              ) AS preview_json
            FROM chats c
            WHERE EXISTS (
              SELECT 1 FROM messages m WHERE m.visitor_id = c.visitor_id AND m.chat_id = c.id
            )
            ORDER BY c.updated_at DESC
            """
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            preview = ""
            if row["preview_json"]:
                try:
                    preview = _plain_content(json.loads(row["preview_json"]))[:80]
                except json.JSONDecodeError:
                    preview = str(row["preview_json"])[:80]
            items.append(
                {
                    "visitor_id": row["visitor_id"],
                    "id": row["id"],
                    "title": row["title"] or "新会话",
                    "updatedAt": int(row["updated_at"] or 0),
                    "userTheme": {"bg": row["theme_bg"] or "", "ink": row["theme_ink"] or ""},
                    "message_count": int(row["message_count"] or 0),
                    "preview": preview,
                }
            )
    return items


def get_session(visitor_id: str, chat_id: str) -> dict[str, Any] | None:
    visitor_id = (visitor_id or "").strip()
    with db.read() as conn:
        row = conn.execute(
            "SELECT visitor_id, id, title, updated_at, theme_bg, theme_ink "
            "FROM chats WHERE visitor_id = ? AND id = ?",
            (visitor_id, chat_id),
        ).fetchone()
        return _session_from_row(conn, row) if row else None

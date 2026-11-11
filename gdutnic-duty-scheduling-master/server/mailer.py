"""Outgoing mail over SMTP, configured from 其他设置 → SMTP.

Only the standard library is used, so no extra dependency is needed. Every send opens a
fresh connection: mail is infrequent and a stale pooled socket would surface as a silent
failure. The plaintext password never leaves the server.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from server import admin_store

TIMEOUT = 20


def _ssl_context():
    """A CA bundle that works on every platform.

    Some Python builds ship with an empty default store (macOS framework builds in
    particular), which turns every connection into CERTIFICATE_VERIFY_FAILED. `certifi`
    is already a dependency of the HTTP stack, so prefer its bundle and fall back to the
    interpreter default when it is unavailable.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _connect(config):
    """Open and authenticate a connection; raises RuntimeError with a readable message."""
    host = str(config.get("host") or "").strip()
    if not host:
        raise RuntimeError("尚未配置 SMTP 服务器地址")
    port = int(config.get("port") or 0)
    security = str(config.get("security") or "ssl").lower()
    context = _ssl_context()
    try:
        if security == "ssl":
            client = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=context)
        else:
            client = smtplib.SMTP(host, port, timeout=TIMEOUT)
            client.ehlo()
            if security == "starttls":
                client.starttls(context=context)
                client.ehlo()
    except (OSError, smtplib.SMTPException) as exc:
        raise RuntimeError(f"无法连接 {host}:{port}（{security}）：{exc}") from exc
    username = str(config.get("username") or "").strip()
    password = str(config.get("password") or "")
    if username:
        try:
            client.login(username, password)
        except smtplib.SMTPException as exc:
            client.close()
            raise RuntimeError(f"SMTP 登录失败：{exc}") from exc
    return client


def sender(config):
    """`formataddr` of the configured name and address, validated by the save endpoint."""
    address = str(config.get("from_address") or "").strip()
    if not address:
        raise RuntimeError("尚未配置发件邮箱")
    name = str(config.get("from_name") or "").strip()
    return formataddr((name, address)) if name else address


def send(to, subject, body, html=None):
    """Send plaintext with an optional HTML alternative using the configured SMTP."""
    config = admin_store.smtp_config()
    if not config.get("enabled"):
        raise RuntimeError("SMTP 尚未启用")
    if not str(to or "").strip():
        raise ValueError("请填写收件邮箱")
    message = EmailMessage()
    message["Subject"] = str(subject or "通知")
    message["From"] = sender(config)
    message["To"] = str(to).strip()
    message.set_content(str(body or ""))
    if html is not None:
        message.add_alternative(str(html), subtype="html")
    client = _connect(config)
    try:
        client.send_message(message)
    except smtplib.SMTPException as exc:
        raise RuntimeError(f"发送失败：{exc}") from exc
    finally:
        try:
            client.quit()
        except smtplib.SMTPException:
            client.close()


def send_test(to):
    """A fixed test message, so an operator can verify the configuration end to end."""
    config = admin_store.smtp_config()
    name = str(config.get("from_name") or "").strip() or "网络服务协会"
    send(to, "SMTP 测试邮件",
         f"这是一封来自「{name}」值班系统的测试邮件。\n"
         "如果你收到了它，说明 SMTP 配置可用。\n")
    return {"ok": True, "to": str(to).strip()}

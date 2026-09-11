"""Telegram wire boundary. No SDK implicit retries; tokens never enter diagnostics."""

import re
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Command:
    user_id: int
    chat_id: int
    thread_id: int
    action: str
    text: str = ""


def parse_update(update: dict, bot_id: int, username: str) -> Command | None:
    """Only original messages by real users; ignore edits, channels and callbacks for S1."""
    m = update.get("message")
    if not isinstance(m, dict) or m.get("sender_chat") or m.get("forward_origin"):
        return None
    user, chat = m.get("from", {}), m.get("chat", {})
    if user.get("is_bot") or type(user.get("id")) is not int or type(chat.get("id")) is not int:
        return None
    text = m.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 8000:
        return None
    thread = m.get("message_thread_id", 0)
    if type(thread) is not int or thread < 0:
        return None
    # Telegram entity offsets/lengths are UTF-16 code units, NOT Python indices.
    encoded = text.encode("utf-16-le")
    mention = False
    for entity in m.get("entities", []):
        if entity.get("type") not in ("mention", "bot_command"):
            continue
        start, length = entity.get("offset"), entity.get("length")
        if type(start) is not int or type(length) is not int or start < 0 or length < 0:
            continue
        value = encoded[2 * start : 2 * (start + length)].decode("utf-16-le", errors="replace")
        mention |= value.lower() == f"@{username.lower()}" or (
            entity["type"] == "bot_command" and value.lower().endswith(f"@{username.lower()}")
        )
    replied_to_bot = m.get("reply_to_message", {}).get("from", {}).get("id") == bot_id
    if chat.get("type") in ("group", "supergroup"):
        if not (mention or replied_to_bot):
            return None
    elif chat.get("type") != "private":
        return None
    command = re.match(r"^/(\w+)(?:@([A-Za-z0-9_]+))?(?:\s|$)", text)
    if command:
        action, target = command.groups()
        if target and target.lower() != username.lower():
            return None
        if action in {"start", "help", "status", "cancel", "new", "mem"}:
            return Command(user["id"], chat["id"], thread, action)
        return Command(user["id"], chat["id"], thread, "help")
    text = re.sub(rf"(?i)(?<!\w)@{re.escape(username)}\b", "", text).strip()
    return Command(user["id"], chat["id"], thread, "chat", text) if text else None


def split_text(text: str, limit: int = 3500) -> list[str]:
    """Keep every segment within a conservative Telegram UTF-16 budget."""
    if limit < 2:
        raise ValueError("limit too small")
    parts, current, size = [], [], 0
    for char in text:
        units = len(char.encode("utf-16-le")) // 2
        if size + units > limit:
            parts.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += units
    if current:
        parts.append("".join(current))
    return parts or ["(empty result)"]


async def identify(client):
    """Operator-only onboarding: metadata only, no offset acknowledgement/admission.

    Use on a new bot while its gateway is stopped. Never authorize these IDs
    automatically; the operator must confirm the intended person/chat.
    """
    me = await client.call("getMe", {})
    updates = await client.call(
        "getUpdates", {"timeout": 0, "limit": 100, "allowed_updates": ["message"]}
    )
    if (
        not isinstance(me, dict)
        or type(me.get("id")) is not int
        or not me.get("is_bot")
        or not isinstance(updates, list)
    ):
        raise ValueError("Invalid bot identity/update response")
    candidates = set()
    for update in updates:
        m = update.get("message", {})
        user, chat = m.get("from", {}), m.get("chat", {})
        if type(user.get("id")) is int and type(chat.get("id")) is int and not user.get("is_bot"):
            candidates.add((user["id"], chat["id"]))
    return {
        "bot_id": me["id"],
        "candidates": [{"user_id": user, "chat_id": chat} for user, chat in sorted(candidates)],
        "authorized": False,
    }


class TelegramError(Exception):
    def __init__(self, disposition: str, error_class: str, retry_after: int = 0):
        super().__init__(error_class)  # Never include HTTP URL (contains token) or response body.
        self.disposition = disposition
        self.error_class = error_class
        self.retry_after = retry_after


class TelegramClient:
    def __init__(self, token: str, *, transport=None):
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
            raise ValueError("Invalid Telegram token format")
        self.client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}/",
            timeout=httpx.Timeout(45, connect=10),
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        )

    async def call(self, method: str, payload: dict):
        if method not in {
            "getMe",
            "getUpdates",
            "sendMessage",
            "sendMessageDraft",
            "sendChatAction",
        }:
            raise ValueError("Unsupported Telegram method")
        try:
            response = await self.client.post(method, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise TelegramError("retry", "connect", 3) from None
        except httpx.HTTPError:
            raise TelegramError("uncertain", "transport") from None
        try:
            data = response.json()
        except ValueError:
            raise TelegramError("uncertain", "invalid_response") from None
        if not isinstance(data, dict):
            raise TelegramError("uncertain", "invalid_response")
        if response.status_code == 429 or data.get("error_code") == 429:
            delay = data.get("parameters", {}).get("retry_after", 5)
            delay = delay if type(delay) is int and delay >= 1 else 5
            raise TelegramError("retry", "rate_limit", min(delay, 86400))
        if response.status_code >= 500:
            raise TelegramError("uncertain", "server_error")
        if response.status_code >= 300 or data.get("ok") is not True:
            raise TelegramError("failed", "api_rejected")
        if "result" not in data:
            raise TelegramError("uncertain", "missing_result")
        return data["result"]

    async def close(self):
        await self.client.aclose()

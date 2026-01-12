from http.server import BaseHTTPRequestHandler
import os
import json
import time
import secrets
import urllib.parse
import urllib.request

from slack_sdk import WebClient

from api._slack_sig import verify_slack_signature
from api._redis import get_redis
from api._blocks import build_broadcast_blocks, draft_modal_view, review_modal_view

SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"].encode("utf-8")
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]

# Optional authorization list (comma-separated user IDs)
ALLOWED_BROADCASTERS = {
    uid.strip()
    for uid in (os.environ.get("ALLOWED_BROADCASTERS") or "").split(",")
    if uid.strip()
}

MAX_BROADCAST_CHANNELS = int(os.environ.get("MAX_BROADCAST_CHANNELS", "500"))
BROADCAST_COOLDOWN_SECONDS = int(os.environ.get("BROADCAST_COOLDOWN_SECONDS", "0"))

# Needed to trigger the on-demand worker
WORKER_SECRET = os.environ["WORKER_SECRET"]
PUBLIC_BASE_URL = os.environ["PUBLIC_BASE_URL"].rstrip("/")  # e.g. https://slack-broadcast-bot.vercel.app

redis = get_redis()
client = WebClient(token=SLACK_BOT_TOKEN)

CHANNEL_SET_KEY = "partner_alert_bot:channels"
JOB_LIST_KEY = "partner_alert_bot:jobs"


def user_allowed(user_id: str) -> bool:
    # If no allowlist is set, allow anyone (for now)
    return True if not ALLOWED_BROADCASTERS else user_id in ALLOWED_BROADCASTERS


def cooldown_key(user_id: str) -> str:
    return f"partner_alert_bot:cooldown:{user_id}"


def in_cooldown(user_id: str) -> bool:
    if BROADCAST_COOLDOWN_SECONDS <= 0:
        return False
    return redis.get(cooldown_key(user_id)) is not None


def set_cooldown(user_id: str):
    if BROADCAST_COOLDOWN_SECONDS <= 0:
        return
    redis.set(cooldown_key(user_id), str(int(time.time())), ex=BROADCAST_COOLDOWN_SECONDS)


def get_channel_count() -> int:
    return len(redis.smembers(CHANNEL_SET_KEY) or [])


def extract_draft(view_state: dict) -> dict:
    """
    Defensive parser for Slack modal state.
    Prevents KeyErrors that cause Slack's "trouble connecting" banner.
    """
    values = (view_state or {}).get("values") or {}

    title = (
        values.get("title_block", {})
        .get("title_input", {})
        .get("value", "")
        or ""
    )

    category = (
        values.get("category_block", {})
        .get("category_select", {})
        .get("selected_option", {})
        .get("value", "Release")
    )

    body = (
        values.get("body_block", {})
        .get("body_input", {})
        .get("value", "")
        or ""
    )

    link = (
        values.get("link_block", {})
        .get("link_input", {})
        .get("value", "")
        or ""
    ).strip() or None

    return {"title": title.strip(), "category": category, "body": body.strip(), "link": link}


def trigger_worker_async():
    """
    Fire-and-forget: trigger the worker endpoint once.
    Uses a short timeout so /api/interactions responds quickly to Slack.
    """
    url = f"{PUBLIC_BASE_URL}/api/worker?secret={urllib.parse.quote(WORKER_SECRET)}"
    try:
        req = urllib.request.Request(url, method="GET")
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        # Queue is source of truth; worker can be triggered manually if needed
        pass


class handler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status: int = 200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)

        # Verify Slack signature
        if not verify_slack_signature(SLACK_SIGNING_SECRET, self.headers, body):
            self._send_json({"error": "invalid signature"}, status=401)
            return

        # Slack sends form-encoded payload=<json>
        form = urllib.parse.parse_qs(body.decode("utf-8"))
        payload = json.loads(form.get("payload", ["{}"])[0])

        ptype = payload.get("type")
        user_id = payload.get("user", {}).get("id", "")

        # Ignore Slack retries to avoid double sends
        if self.headers.get("X-Slack-Retry-Num"):
            self._send_json({})
            return

        if not user_allowed(user_id):
            # For interactions, return an empty response so Slack closes silently
            self._send_json({})
            return

        # --- Draft submitted -> show review modal ---
        if ptype == "view_submission" and payload.get("view", {}).get("callback_id") == "broadcast_draft_submit":
            if in_cooldown(user_id):
                self._send_json({
                    "response_action": "errors",
                    "errors": {"body_block": "Cooldown active. Try again shortly."}
                })
                return

            channel_count = get_channel_count()
            if channel_count == 0:
                self._send_json({
                    "response_action": "errors",
                    "errors": {"body_block": "No tracked channels yet. Invite the bot to at least one channel."}
                })
                return

            if channel_count > MAX_BROADCAST_CHANNELS:
                self._send_json({
                    "response_action": "errors",
                    "errors": {"body_block": f"Safety cap triggered: {channel_count} > {MAX_BROADCAST_CHANNELS}."}
                })
                return

            draft = extract_draft(payload.get("view", {}).get("state") or {})
            title = draft["title"] or "Partner Update"

            preview = build_broadcast_blocks(
                title=title,
                body=draft["body"],
                category=draft["category"],
                sender_name=f"<@{user_id}>",
                link=draft["link"],
            )

            draft_id = secrets.token_urlsafe(12)
            redis.set(f"partner_alert_bot:draft:{draft_id}", json.dumps(draft), ex=60 * 60)

            private_metadata = json.dumps({"user_id": user_id, "draft_id": draft_id})

            review_view = review_modal_view(
                private_metadata=private_metadata,
                preview_blocks=preview,
                channel_count=channel_count,
            )

            self._send_json({"response_action": "update", "view": review_view})
            return

        # --- Button clicks on review modal (Edit / Send) ---
        if ptype == "block_actions":
            actions = payload.get("actions") or []
            action_id = actions[0].get("action_id") if actions else ""
            view = payload.get("view") or {}
            meta = json.loads(view.get("private_metadata") or "{}")
            draft_id = meta.get("draft_id")

            if action_id == "edit_draft":
                # Show draft modal again
                private_metadata = json.dumps({"user_id": user_id, "ts": int(time.time())})
                client.views_update(
                    view_id=view["id"],
                    hash=view.get("hash"),
                    view=draft_modal_view(private_metadata),
                )
                self._send_json({})
                return

            if action_id == "send_broadcast":
                if in_cooldown(user_id):
                    client.views_update(
                        view_id=view["id"],
                        hash=view.get("hash"),
                        view={
                            "type": "modal",
                            "title": {"type": "plain_text", "text": "Review Broadcast"},
                            "close": {"type": "plain_text", "text": "Close"},
                            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "Cooldown active. Try again shortly."}}],
                        },
                    )
                    self._send_json({})
                    return

                raw = redis.get(f"partner_alert_bot:draft:{draft_id}") if draft_id else None
                if not raw:
                    client.views_update(
                        view_id=view["id"],
                        hash=view.get("hash"),
                        view={
                            "type": "modal",
                            "title": {"type": "plain_text", "text": "Review Broadcast"},
                            "close": {"type": "plain_text", "text": "Close"},
                            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "Draft expired. Run `/partner_broadcast` again."}}],
                        },
                    )
                    self._send_json({})
                    return

                draft = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))

                # Queue job
                job = {
                    "queued_at": int(time.time()),
                    "queued_by": user_id,
                    "title": draft.get("title") or "Partner Update",
                    "category": draft.get("category") or "Release",
                    "body": draft.get("body") or "",
                    "link": draft.get("link"),
                }
                redis.lpush(JOB_LIST_KEY, json.dumps(job))
                set_cooldown(user_id)

                # Update modal instantly (Slack-native)
                client.views_update(
                    view_id=view["id"],
                    hash=view.get("hash"),
                    view={
                        "type": "modal",
                        "title": {"type": "plain_text", "text": "Sending ✅"},
                        "close": {"type": "plain_text", "text": "Close"},
                        "blocks": [
                            {"type": "section", "text": {"type": "mrkdwn", "text": "Broadcast started. I’ll DM you when it finishes."}},
                            {"type": "context", "elements": [{"type": "mrkdwn", "text": "If you don’t receive a DM, check Vercel logs for /api/worker."}]},
                        ],
                    },
                )

                # Trigger worker (no cron / no polling)
                trigger_worker_async()

                self._send_json({})
                return

        # For unknown interaction payloads: return empty JSON
        self._send_json({})

    def do_GET(self):
        self._send_json({"ok": True, "message": "Interactions endpoint is up."})

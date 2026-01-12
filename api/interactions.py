from http.server import BaseHTTPRequestHandler
import os
import json
import time
import urllib.parse
import urllib.request

from slack_sdk import WebClient

from api._slack_sig import verify_slack_signature
from api._redis import get_redis
from api._blocks import build_broadcast_blocks, draft_modal_view, review_modal_view

SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"].encode("utf-8")
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]

ALLOWED_BROADCASTERS = {
    uid.strip()
    for uid in (os.environ.get("ALLOWED_BROADCASTERS") or "").split(",")
    if uid.strip()
}

MAX_BROADCAST_CHANNELS = int(os.environ.get("MAX_BROADCAST_CHANNELS", "500"))
BROADCAST_COOLDOWN_SECONDS = int(os.environ.get("BROADCAST_COOLDOWN_SECONDS", "0"))

WORKER_SECRET = os.environ["WORKER_SECRET"]
PUBLIC_BASE_URL = os.environ["PUBLIC_BASE_URL"].rstrip("/")

redis = get_redis()
client = WebClient(token=SLACK_BOT_TOKEN)

CHANNEL_SET_KEY = "partner_alert_bot:channels"
JOB_LIST_KEY = "partner_alert_bot:jobs"


def user_allowed(user_id: str) -> bool:
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
    values = (view_state or {}).get("values") or {}

    title = (values.get("title_block", {}).get("title_input", {}).get("value") or "").strip()
    category = (
        values.get("category_block", {})
        .get("category_select", {})
        .get("selected_option", {})
        .get("value", "Release")
    )
    body = (values.get("body_block", {}).get("body_input", {}).get("value") or "").strip()
    link = (values.get("link_block", {}).get("link_input", {}).get("value") or "").strip() or None

    return {"title": title, "category": category, "body": body, "link": link}


def trigger_worker_async():
    url = f"{PUBLIC_BASE_URL}/api/worker?secret={urllib.parse.quote(WORKER_SECRET)}"
    try:
        req = urllib.request.Request(url, method="GET")
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
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

        if not verify_slack_signature(SLACK_SIGNING_SECRET, self.headers, body):
            self._send_json({"error": "invalid signature"}, status=401)
            return

        # Avoid double-processing Slack retries
        if self.headers.get("X-Slack-Retry-Num"):
            self._send_json({})
            return

        form = urllib.parse.parse_qs(body.decode("utf-8"))
        payload = json.loads(form.get("payload", ["{}"])[0])

        ptype = payload.get("type")
        user_id = (payload.get("user") or {}).get("id", "")

        print("INTERACTIONS type=", ptype, "callback_id=", (payload.get("view") or {}).get("callback_id"))

        if not user_allowed(user_id):
            self._send_json({})
            return

        # ---- Draft submitted -> show review modal (NO REDIS HERE) ----
        if ptype == "view_submission" and (payload.get("view") or {}).get("callback_id") == "broadcast_draft_submit":
            # Channel count check can stay (fast), but if you want max speed you can remove it.
            channel_count = get_channel_count()
            if channel_count == 0:
                self._send_json({"response_action": "errors", "errors": {"body_block": "No tracked channels yet. Invite the bot to a channel first."}})
                return
            if channel_count > MAX_BROADCAST_CHANNELS:
                self._send_json({"response_action": "errors", "errors": {"body_block": f"Safety cap: {channel_count} > {MAX_BROADCAST_CHANNELS}."}})
                return

            draft = extract_draft((payload.get("view") or {}).get("state") or {})
            if not draft["body"]:
                self._send_json({"response_action": "errors", "errors": {"body_block": "Message is required."}})
                return

            title = draft["title"] or "Partner Update"

            preview = build_broadcast_blocks(
                title=title,
                body=draft["body"],
                category=draft["category"],
                sender_name=f"<@{user_id}>",
                link=draft["link"],
            )

            # Put draft directly into private_metadata so we don't depend on KV at review time
            private_metadata = json.dumps({"user_id": user_id, "draft": draft})

            self._send_json({
                "response_action": "update",
                "view": review_modal_view(private_metadata=private_metadata, preview_blocks=preview, channel_count=channel_count),
            })
            return

        # ---- Buttons on review modal ----
        if ptype == "block_actions":
            actions = payload.get("actions") or []
            action_id = actions[0].get("action_id") if actions else ""
            view = payload.get("view") or {}

            meta = json.loads(view.get("private_metadata") or "{}")
            draft = meta.get("draft") or {}
            meta_user_id = meta.get("user_id") or user_id

            if action_id == "edit_draft":
                private_metadata = json.dumps({"user_id": meta_user_id, "ts": int(time.time())})
                client.views_update(view_id=view["id"], hash=view.get("hash"), view=draft_modal_view(private_metadata))
                self._send_json({})
                return

            if action_id == "send_broadcast":
                if in_cooldown(meta_user_id):
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

                job = {
                    "queued_at": int(time.time()),
                    "queued_by": meta_user_id,
                    "title": (draft.get("title") or "Partner Update"),
                    "category": (draft.get("category") or "Release"),
                    "body": (draft.get("body") or ""),
                    "link": draft.get("link"),
                }

                if not job["body"]:
                    client.views_update(
                        view_id=view["id"],
                        hash=view.get("hash"),
                        view={
                            "type": "modal",
                            "title": {"type": "plain_text", "text": "Review Broadcast"},
                            "close": {"type": "plain_text", "text": "Close"},
                            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "Draft is missing a message. Run `/partner_broadcast` again."}}],
                        },
                    )
                    self._send_json({})
                    return

                redis.lpush(JOB_LIST_KEY, json.dumps(job))
                set_cooldown(meta_user_id)

                client.views_update(
                    view_id=view["id"],
                    hash=view.get("hash"),
                    view={
                        "type": "modal",
                        "title": {"type": "plain_text", "text": "Sending ✅"},
                        "close": {"type": "plain_text", "text": "Close"},
                        "blocks": [
                            {"type": "section", "text": {"type": "mrkdwn", "text": "Broadcast started. I’ll DM you when it finishes."}},
                        ],
                    },
                )

                trigger_worker_async()
                self._send_json({})
                return

        self._send_json({})

    def do_GET(self):
        self._send_json({"ok": True, "message": "Interactions endpoint is up."})

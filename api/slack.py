from http.server import BaseHTTPRequestHandler
import os
import json
import urllib.parse
import time

from slack_sdk import WebClient

from api._slack_sig import verify_slack_signature
from api._redis import get_redis
from api._blocks import draft_modal_view

SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"].encode("utf-8")
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]

# Optional authorization list (comma-separated user IDs)
ALLOWED_BROADCASTERS = {
    uid.strip() for uid in (os.environ.get("ALLOWED_BROADCASTERS") or "").split(",") if uid.strip()
}

redis = get_redis()
client = WebClient(token=SLACK_BOT_TOKEN)

CHANNEL_SET_KEY = "partner_alert_bot:channels"


def user_allowed(user_id: str) -> bool:
    # If no allowlist is set, allow anyone (for now)
    return True if not ALLOWED_BROADCASTERS else user_id in ALLOWED_BROADCASTERS


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
            self._send_json({"response_type": "ephemeral", "text": "Invalid signature."}, status=401)
            return

        form = urllib.parse.parse_qs(body.decode("utf-8"))
        trigger_id = form.get("trigger_id", [""])[0]
        user_id = form.get("user_id", [""])[0]
        text = (form.get("text", [""])[0] or "").strip()

        if not user_allowed(user_id):
            self._send_json({"response_type": "ephemeral", "text": "You are not allowed to use this command."})
            return

        # Optional status shortcut: /partner_broadcast status
        if text.lower() == "status":
            count = len(redis.smembers(CHANNEL_SET_KEY) or [])
            self._send_json({"response_type": "ephemeral", "text": f"Tracked channels: {count}"})
            return

        # Open the Draft modal
        private_metadata = json.dumps({"user_id": user_id, "ts": int(time.time())})
        client.views_open(trigger_id=trigger_id, view=draft_modal_view(private_metadata=private_metadata))

        # Respond quickly to Slack (prevents timeout)
        self._send_json({"response_type": "ephemeral", "text": "Opening draft… ✅"})

    def do_GET(self):
        self._send_json({"ok": True, "message": "Slash endpoint is up."})

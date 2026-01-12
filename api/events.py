from http.server import BaseHTTPRequestHandler
import os
import json

from api._redis import get_redis
from api._slack_sig import verify_slack_signature

SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"].encode("utf-8")
SLACK_BOT_USER_ID = os.environ["SLACK_BOT_USER_ID"]

CHANNEL_SET_KEY = "partner_alert_bot:channels"
redis = get_redis()


class handler(BaseHTTPRequestHandler):
    def _send_text(self, text: str, status: int = 200):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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

        payload = json.loads(body.decode("utf-8"))

        # Slack URL verification handshake
        if payload.get("type") == "url_verification":
            self._send_text(payload.get("challenge", ""))
            return

        # Avoid double-processing Slack retries
        if self.headers.get("X-Slack-Retry-Num"):
            self._send_json({"ok": True})
            return

        # Verify signature
        if not verify_slack_signature(SLACK_SIGNING_SECRET, self.headers, body):
            self._send_text("invalid signature", status=401)
            return

        event = payload.get("event") or {}

        # Only track events where the user affected is the bot itself
        if event.get("user") != SLACK_BOT_USER_ID:
            self._send_json({"ok": True})
            return

        channel = event.get("channel")
        if not channel:
            self._send_json({"ok": True})
            return

        if event.get("type") == "member_joined_channel":
            redis.sadd(CHANNEL_SET_KEY, channel)
        elif event.get("type") == "member_left_channel":
            redis.srem(CHANNEL_SET_KEY, channel)

        self._send_json({"ok": True})

    def do_GET(self):
        self._send_json({"ok": True, "message": "Events endpoint is up."})

from http.server import BaseHTTPRequestHandler
import os
import json
import logging

from api._redis import get_redis
from api._slack_sig import verify_slack_signature

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
        logger.info("Received POST request to events endpoint")

        length = int(self.headers.get("Content-Length", "0"))
        logger.debug(f"Content-Length: {length}")

        body = self.rfile.read(length)
        logger.debug(f"Request body length: {len(body)} bytes")

        try:
            payload = json.loads(body.decode("utf-8"))
            logger.debug(f"Parsed payload type: {payload.get('type')}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON payload: {e}")
            self._send_text("Invalid JSON", status=400)
            return

        # Slack URL verification handshake
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge", "")
            logger.info(f"Handling URL verification challenge: {challenge}")
            self._send_text(challenge)
            return

        # Avoid double-processing Slack retries
        retry_num = self.headers.get("X-Slack-Retry-Num")
        if retry_num:
            logger.warning(f"Ignoring Slack retry #{retry_num} to prevent double-processing")
            self._send_json({"ok": True})
            return

        # Verify signature
        logger.debug("Verifying Slack signature")
        if not verify_slack_signature(SLACK_SIGNING_SECRET, self.headers, body):
            logger.error("Signature verification failed - rejecting request")
            self._send_text("invalid signature", status=401)
            return
        logger.debug("Signature verification successful")

        event = payload.get("event") or {}
        event_type = event.get("type")
        event_user = event.get("user")
        event_channel = event.get("channel")

        logger.debug(f"Processing event - type: {event_type}, user: {event_user}, channel: {event_channel}")

        # Only track events where the user affected is the bot itself
        if event_user != SLACK_BOT_USER_ID:
            logger.debug(f"Ignoring event for user {event_user} (not bot user {SLACK_BOT_USER_ID})")
            self._send_json({"ok": True})
            return

        logger.info(f"Processing bot event: {event_type} in channel {event_channel}")

        if not event_channel:
            logger.warning(f"Event {event_type} missing channel information - ignoring")
            self._send_json({"ok": True})
            return

        if event_type == "member_joined_channel":
            logger.info(f"Bot joined channel {event_channel} - adding to tracked channels")
            try:
                redis.sadd(CHANNEL_SET_KEY, event_channel)
                logger.debug(f"Successfully added channel {event_channel} to Redis set {CHANNEL_SET_KEY}")
            except Exception as e:
                logger.error(f"Failed to add channel {event_channel} to Redis: {e}")
        elif event_type == "member_left_channel":
            logger.info(f"Bot left channel {event_channel} - removing from tracked channels")
            try:
                redis.srem(CHANNEL_SET_KEY, event_channel)
                logger.debug(f"Successfully removed channel {event_channel} from Redis set {CHANNEL_SET_KEY}")
            except Exception as e:
                logger.error(f"Failed to remove channel {event_channel} from Redis: {e}")
        else:
            logger.debug(f"Unhandled event type: {event_type} - no action taken")

        logger.debug("Request processed successfully - sending OK response")
        self._send_json({"ok": True})

    def do_GET(self):
        logger.info("Received GET request - returning health check response")
        self._send_json({"ok": True, "message": "Events endpoint is up."})

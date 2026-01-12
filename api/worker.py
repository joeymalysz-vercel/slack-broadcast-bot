from http.server import BaseHTTPRequestHandler
import os
import json
import time
import urllib.parse

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from api._redis import get_redis
from api._blocks import build_broadcast_blocks

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
WORKER_SECRET = os.environ["WORKER_SECRET"]

POST_THROTTLE_SECONDS = float(os.environ.get("POST_THROTTLE_SECONDS", "0.2"))
MAX_BROADCAST_CHANNELS = int(os.environ.get("MAX_BROADCAST_CHANNELS", "500"))

CHANNEL_SET_KEY = "partner_alert_bot:channels"
JOB_LIST_KEY = "partner_alert_bot:jobs"

redis = get_redis()
client = WebClient(token=SLACK_BOT_TOKEN)


def _normalize_members(raw) -> list[str]:
    items = raw or []
    out = []
    for c in items:
        out.append(c.decode("utf-8") if isinstance(c, (bytes, bytearray)) else str(c))
    out.sort()
    return out


def _post_with_retry(channel: str, text: str, blocks):
    try:
        client.chat_postMessage(channel=channel, text=text, blocks=blocks)
        return True, None
    except SlackApiError as e:
        err = e.response.get("error")
        if err == "ratelimited":
            retry_after = 1
            try:
                retry_after = int(e.response.headers.get("Retry-After", "1"))
            except Exception:
                retry_after = 1
            time.sleep(retry_after + 1)
            try:
                client.chat_postMessage(channel=channel, text=text, blocks=blocks)
                return True, None
            except SlackApiError as e2:
                return False, e2.response.get("error") or "ratelimited"
        return False, err or "SlackApiError"
    except Exception as e:
        return False, str(e)


class handler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status: int = 200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # Auth via querystring secret
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        provided = (params.get("secret") or [""])[0]
        if not provided or provided != WORKER_SECRET:
            self._send_json({"error": "unauthorized"}, status=401)
            return

        # Pop exactly one job per invocation (keeps runtime bounded)
        job_raw = redis.rpop(JOB_LIST_KEY)
        if not job_raw:
            self._send_json({"ok": True, "message": "No queued jobs."})
            return

        job = json.loads(job_raw if isinstance(job_raw, str) else job_raw.decode("utf-8"))

        channels = _normalize_members(redis.smembers(CHANNEL_SET_KEY))
        if not channels:
            self._send_json({"ok": True, "message": "No channels tracked; job dropped."})
            return

        if len(channels) > MAX_BROADCAST_CHANNELS:
            self._send_json({"ok": False, "error": f"cap_exceeded {len(channels)}>{MAX_BROADCAST_CHANNELS}"}, status=400)
            return

        title = job.get("title") or "Partner Update"
        category = job.get("category") or "Release"
        body = job.get("body") or ""
        link = job.get("link")
        queued_by = job.get("queued_by") or ""

        blocks = build_broadcast_blocks(
            title=title,
            body=body,
            category=category,
            sender_name=f"<@{queued_by}>" if queued_by else "Partner Alert Bot",
            link=link,
        )
        fallback_text = f"{category}: {title}"

        sent = 0
        failed = []

        for ch in channels:
            ok, err = _post_with_retry(ch, fallback_text, blocks)
            if ok:
                sent += 1
            else:
                failed.append(f"{ch} ({err})")
            time.sleep(POST_THROTTLE_SECONDS)

        # DM sender summary (best effort)
        if queued_by:
            try:
                dm = client.conversations_open(users=queued_by)
                dm_channel = dm["channel"]["id"]
                msg = f"✅ Broadcast complete. Sent to {sent}/{len(channels)} channels."
                if failed:
                    msg += f" Failed: {len(failed)} (first 10): " + ", ".join(failed[:10])
                client.chat_postMessage(channel=dm_channel, text=msg)
            except Exception:
                pass

        self._send_json({"ok": True, "sent": sent, "failed": len(failed), "channels": len(channels)})
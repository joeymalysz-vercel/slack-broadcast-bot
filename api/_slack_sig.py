import time
import hmac
import hashlib

def verify_slack_signature(signing_secret: bytes, headers, body: bytes) -> bool:
    timestamp = headers.get("X-Slack-Request-Timestamp", "")
    signature = headers.get("X-Slack-Signature", "")

    if not timestamp or not signature:
        return False

    try:
        ts_int = int(timestamp)
    except ValueError:
        return False

    # 5-minute replay window
    if abs(time.time() - ts_int) > 60 * 5:
        return False

    basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    my_sig = "v0=" + hmac.new(
        signing_secret,
        basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(my_sig, signature)
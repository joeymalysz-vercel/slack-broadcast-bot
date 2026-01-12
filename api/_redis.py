import os
from upstash_redis import Redis


def get_redis() -> Redis:
    """
    Uses Vercel KV env vars (KV_*) or older STORAGE_KV_* aliases.
    """
    url = os.environ.get("KV_REST_API_URL") or os.environ.get("STORAGE_KV_REST_API_URL")
    token = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("STORAGE_KV_REST_API_TOKEN")

    if not url or not token:
        raise RuntimeError("Missing KV_REST_API_URL / KV_REST_API_TOKEN in env.")

    return Redis(url=url, token=token)
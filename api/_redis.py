import os
from upstash_redis import Redis

def get_redis() -> Redis:
    """
    Supports Vercel KV (KV_*) and legacy STORAGE_KV_* env vars.
    """
    url = (
        os.environ.get("KV_REST_API_URL")
        or os.environ.get("STORAGE_KV_REST_API_URL")
    )
    token = (
        os.environ.get("KV_REST_API_TOKEN")
        or os.environ.get("STORAGE_KV_REST_API_TOKEN")
    )

    if not url or not token:
        raise RuntimeError(
            "Missing KV env vars. Expected KV_REST_API_URL / KV_REST_API_TOKEN."
        )

    return Redis(url=url, token=token)
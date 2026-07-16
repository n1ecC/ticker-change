"""S3-backed JSON cache for option chains.

Objects live under s3://{S3_CACHE_BUCKET}/{S3_CACHE_PREFIX}/... and are plain
JSON documents. Freshness is judged from the object's LastModified timestamp,
so payloads need no embedded bookkeeping. Every failure (missing bucket, bad
credentials, network) degrades to a cache miss — the dashboard must always
render, with the SQLite api_cache as the local fallback.

Configuration (environment, all optional — the cache is disabled without a bucket):
    S3_CACHE_BUCKET        bucket name; unset disables the S3 layer entirely
    S3_CACHE_PREFIX        key prefix, default "optchains"
    S3_CACHE_REGION        overrides AWS_REGION / AWS_DEFAULT_REGION
    S3_CACHE_ENDPOINT_URL  custom endpoint for S3-compatible stores (R2, MinIO)

Credentials resolve through the standard boto3 chain (env vars, shared
credentials file, instance/task role).
"""
from __future__ import annotations
import json
import os
import threading
from datetime import datetime, timezone

_client = None
_client_lock = threading.Lock()
_client_failed = False


def bucket() -> str:
    return os.environ.get("S3_CACHE_BUCKET", "").strip()


def _prefix() -> str:
    return os.environ.get("S3_CACHE_PREFIX", "optchains").strip().strip("/")


def enabled() -> bool:
    """True when a bucket is configured and the client could be constructed."""
    return bool(bucket()) and _get_client() is not None


def _get_client():
    global _client, _client_failed
    if _client is not None or _client_failed or not bucket():
        return _client
    with _client_lock:
        if _client is not None or _client_failed:
            return _client
        try:
            import boto3
            kwargs = {}
            region = os.environ.get("S3_CACHE_REGION", "").strip()
            if region:
                kwargs["region_name"] = region
            endpoint = os.environ.get("S3_CACHE_ENDPOINT_URL", "").strip()
            if endpoint:
                kwargs["endpoint_url"] = endpoint
            _client = boto3.client("s3", **kwargs)
        except Exception as e:
            print(f"[s3-cache] disabled — client init failed: {e}")
            _client_failed = True
    return _client


def _full_key(key: str) -> str:
    return f"{_prefix()}/{key}"


def get_json(key: str) -> tuple[dict, float] | None:
    """Return (payload, age_hours) for a cached object, or None on miss/error.

    The caller decides freshness from age_hours, so a single GET can serve both
    the fresh-hit path and the serve-stale-on-fetch-failure path.
    """
    client = _get_client()
    if client is None:
        return None
    try:
        resp = client.get_object(Bucket=bucket(), Key=_full_key(key))
        payload = json.loads(resp["Body"].read())
        age = datetime.now(timezone.utc) - resp["LastModified"]
        return payload, age.total_seconds() / 3600.0
    except Exception as e:
        if type(e).__name__ not in ("NoSuchKey", "NoSuchBucket"):
            print(f"[s3-cache] get {key} failed: {e}")
        return None


def put_json(key: str, payload) -> bool:
    """Upload a JSON-serialisable payload; returns False (and logs) on any error."""
    client = _get_client()
    if client is None:
        return False
    try:
        client.put_object(
            Bucket=bucket(),
            Key=_full_key(key),
            Body=json.dumps(payload).encode(),
            ContentType="application/json",
        )
        return True
    except Exception as e:
        print(f"[s3-cache] put {key} failed: {e}")
        return False

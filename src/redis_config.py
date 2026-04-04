"""
Configuration for Redis pooling and multi-worker coordination.
"""

import os
from config import settings


def get_redis_config() -> dict:
    """Get Redis configuration"""
    # Build Redis URL from components or use explicit URL if provided
    # Format: redis://[:password@]host:port/db
    # Use REDIS_PASSWORD if explicitly set; do not fall back to API_TOKEN to
    # keep authentication credentials independent.
    redis_password = settings.REDIS_PASSWORD or None

    if redis_password:
        redis_url = os.getenv(
            "REDIS_URL",
            f"redis://:{redis_password}@{settings.REDIS_HOST}:{settings.REDIS_SERVER_PORT}/{settings.REDIS_DB}",
        )
    else:
        redis_url = os.getenv(
            "REDIS_URL",
            f"redis://{settings.REDIS_HOST}:{settings.REDIS_SERVER_PORT}/{settings.REDIS_DB}",
        )

    return {
        "host": settings.REDIS_HOST,
        "port": settings.REDIS_SERVER_PORT,
        "password": redis_password,
        "db": settings.REDIS_DB,
        "redis_url": redis_url,
        "enabled": settings.REDIS_ENABLED,
        "pooling_enabled": settings.ENABLE_TRANSCODING_POOLING,
        "max_clients_per_stream": settings.MAX_CLIENTS_PER_SHARED_STREAM,
        "stream_timeout": settings.SHARED_STREAM_TIMEOUT,
        "worker_id": settings.WORKER_ID,
        "heartbeat_interval": settings.HEARTBEAT_INTERVAL,
        "cleanup_interval": settings.CLEANUP_INTERVAL,
        "sharing_strategy": settings.STREAM_SHARING_STRATEGY,
    }


def should_use_pooling() -> bool:
    """Check if pooling should be used"""
    return settings.REDIS_ENABLED and settings.ENABLE_TRANSCODING_POOLING

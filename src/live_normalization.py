"""Compose the existing direct proxy and pooled FFmpeg, never rewrite handoff.

The inner HTTP body stays open while sources switch. Only the outer stream is
publicly counted; ownership is server state, not client-supplied metadata.
"""

import hashlib
from urllib.parse import urlparse
from string import Formatter

from fastapi import HTTPException

from config import settings
from redis_config import should_use_pooling
from transcoding import get_profile_manager


async def create_normalized_stream(manager, payload, request):
    urls = [payload.url, *(payload.failover_urls or [])]
    if any(
        not manager._detect_stream_type(url)[2]
        or urlparse(url).path.lower().endswith((".mpd", ".m3u8", ".m3u"))
        for url in urls
    ):
        raise HTTPException(400, "Live normalization supports direct live MPEG-TS only")
    try:
        available = (
            not settings.REDIS_ENABLED
            and not manager._redis_requested
            and settings.ENABLE_SINGLE_PROCESS_TRANSCODING
            and should_use_pooling()
            and manager.pooled_manager is not None
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not available:
        raise HTTPException(
            400,
            "Live normalization requires single-process transcoding pooling without Redis",
        )

    # Do not trust Host/forwarded headers. The socket's server port is authoritative.
    server = request.scope.get("server") if request is not None else None
    if not server or not isinstance(server[1], int) or not 0 < server[1] < 65536:
        raise HTTPException(400, "Live normalization requires the ASGI listening port")
    try:
        fields = {
            name
            for _, name, _, _ in Formatter().parse(settings.LIVE_NORMALIZE_PROFILE)
            if name is not None
        }
        if not {"input_url", "output_args"}.issubset(fields) or fields - {
            "input_url",
            "output_args",
            "format",
        }:
            raise ValueError(
                "expected input_url/output_args and optional format placeholders"
            )
    except ValueError as exc:
        raise HTTPException(400, f"Invalid LIVE_NORMALIZE_PROFILE: {exc}") from exc
    profile = get_profile_manager().create_profile_from_template(
        name="live-normalize",
        parameters=settings.LIVE_NORMALIZE_PROFILE,
        description="Persistent direct-live output normalization",
    )
    # Include the effective normalizer profile in both private identities.
    salt = (
        "live-normalize:"
        + hashlib.sha256(settings.LIVE_NORMALIZE_PROFILE.encode()).hexdigest()
    )
    options = payload.model_dump(exclude={"url", "normalize_live_output"})
    options.update(
        strict_live_ts=True,
        use_sticky_session=True,
        end_session_on_reconnect=False,
        single_recovery_per_exit=True,
    )
    async with manager._normalization_lock:
        before = set(manager.streams)
        try:
            inner_id = await manager.get_or_create_stream(
                payload.url, **options, identity_salt=salt
            )
            inner = manager.streams[inner_id]
            # No recycling/restarting an existing owned encoder or selected B.
            if inner.normalization_parent_id in manager.streams:
                outer = manager.streams[inner.normalization_parent_id]
                outer.last_access = inner.last_access
                return outer.stream_id
            loopback = f"http://127.0.0.1:{server[1]}/stream/{inner_id}"
            args = profile.render(
                {"input_url": loopback, "output_args": "pipe:1", "format": "mpegts"}
            )
            outer_id = await manager.get_or_create_stream(
                loopback,
                metadata={**(payload.metadata or {}), "normalize_live_output": "true"},
                is_transcoded=True,
                transcode_profile=salt,
                transcode_ffmpeg_args=args,
                identity_salt=salt,
            )
            outer = manager.streams[outer_id]
            outer.normalization_input_id = inner_id
            inner.normalization_parent_id = outer_id
            return outer_id
        except BaseException:
            # Also roll back cancellation, including failure after an allocation.
            # No provider connections/encoder can exist before the initial GET.
            for stream_id in set(manager.streams) - before:
                manager.streams.pop(stream_id, None)
                manager.stream_clients.pop(stream_id, None)
                manager._stats.active_streams = max(
                    0, manager._stats.active_streams - 1
                )
            raise

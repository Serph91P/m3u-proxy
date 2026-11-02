"""Keep capability validation real when transport/stats methods are doubled."""

from datetime import datetime, timezone
from stream_manager import StreamInfo, StreamManager


def bind_media_context(manager, *stream_ids):
    real = StreamManager()
    now = datetime.now(timezone.utc)
    for stream_id in stream_ids:
        real.streams[stream_id] = StreamInfo(
            stream_id=stream_id,
            original_url="https://upstream.example.com/live/",
            created_at=now,
            last_access=now,
        )
    manager.streams = real.streams
    manager.media_context = real.media_context
    manager.media_headers = real.media_headers

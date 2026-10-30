import asyncio
import os
import sys
import threading
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import api  # noqa: E402


@pytest.fixture(autouse=True)
def clear_version_cache():
    api._binary_versions.clear()
    yield
    api._binary_versions.clear()


def test_ffmpeg_version_is_read_once_and_cached():
    with patch.object(
        api, "_read_ffmpeg_version", return_value="ffmpeg version 7.1"
    ) as reader:
        assert asyncio.run(api.get_ffmpeg_version()) == "ffmpeg version 7.1"
        assert asyncio.run(api.get_ffmpeg_version()) == "ffmpeg version 7.1"

    assert reader.call_count == 1


def test_missing_resolver_is_not_cached():
    with patch.object(api, "_read_resolver_version", return_value=None) as reader:
        assert asyncio.run(api.get_resolver_version("streamlink")) is None
        assert asyncio.run(api.get_resolver_version("streamlink")) is None

    assert reader.call_count == 2


def test_version_lookup_runs_off_the_event_loop():
    loop_thread = threading.get_ident()
    reader_threads = []

    def slow_reader(binary):
        reader_threads.append(threading.get_ident())
        return f"{binary} 1.0"

    with patch.object(api, "_read_resolver_version", side_effect=slow_reader):
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(3):
                await asyncio.sleep(0)
                ticks += 1

        async def lookup():
            return await asyncio.gather(api.get_resolver_version("yt-dlp"), ticker())

        version, _ = asyncio.run(lookup())

    assert version == "yt-dlp 1.0"
    assert reader_threads and reader_threads[0] != loop_thread
    assert ticks == 3

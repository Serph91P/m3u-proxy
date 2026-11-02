"""Native TCP/HTTP and CPU-FFmpeg acceptance for signed local HLS output."""

import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import shutil
import socket
import subprocess
import threading

import httpx
import pytest
import uvicorn

import api
from stream_manager import StreamManager


@pytest.mark.asyncio
async def test_cpu_transcoder_capabilities_over_real_http(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("CPU media acceptance requires ffmpeg and ffprobe")
    source_path = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10",
            "-t",
            "3",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(source_path),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )

    class Source(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    source = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(Source, directory=str(tmp_path))
    )
    thread = threading.Thread(target=source.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(api.settings, "API_TOKEN", "synthetic-cpu-management-token")
    monkeypatch.setattr(api.settings, "ROOT_PATH", "")
    monkeypatch.setattr(api.settings, "HLS_TEMP_DIR", str(tmp_path))
    manager = StreamManager(enable_pooling=True)
    monkeypatch.setattr(api, "stream_manager", manager)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    server = uvicorn.Server(
        uvicorn.Config(
            api.app,
            lifespan="off",
            access_log=False,
            log_level="error",
            timeout_graceful_shutdown=2,
        )
    )
    serving = None
    try:
        await manager.start()
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        async with asyncio.timeout(5):
            while not server.started:
                if serving.done():
                    await serving
                    pytest.fail("Proxy did not start")
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=20
        ) as player:
            response = await player.post(
                "/transcode",
                headers={"X-API-Token": "synthetic-cpu-management-token"},
                json={
                    "url": f"http://127.0.0.1:{source.server_port}/source.mp4",
                    "profile": "-i {input_url} -an -c:v libx264 -preset ultrafast -g 10 -hls_time 1 -hls_list_size 0 -f hls index.m3u8",
                },
            )
            assert response.status_code == 200, response.text
            stream_id = response.json()["stream_id"]
            playlist = await player.get(f"/hls/{stream_id}/playlist.m3u8")
            assert playlist.status_code == 200, playlist.text
            links = [line for line in playlist.text.splitlines() if "/segment" in line]
            assert links
            segment = await player.get(links[0])
            assert segment.status_code == 200 and len(segment.content) > 188
            assert "api_token" not in links[0]
            local = tmp_path / "replayed.ts"
            local.write_bytes(segment.content)
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_name",
                    "-of",
                    "csv=p=0",
                    str(local),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert "h264" in probe.stdout
            assert (
                await player.get(links[0].replace(stream_id, "other-stream"))
            ).status_code == 403
    finally:
        server.should_exit = True
        if serving:
            await asyncio.wait_for(serving, timeout=5)
        sock.close()
        await manager.stop()
        source.shutdown()
        source.server_close()
        thread.join(timeout=2)

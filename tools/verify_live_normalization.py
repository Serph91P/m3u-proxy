#!/usr/bin/env python3
"""Offline acceptance: one /streams POST + one player GET survives A -> B.

Real loopback uvicorn/HTTP/FFmpeg; no provider access. Compatible H.264/AAC
sources with different content, PIDs and clocks; NOT arbitrary codec changes.
Run with the installed project dependencies and ffmpeg/ffprobe on PATH.
"""

import array
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run(args, check=True, capture_output=True).stdout


def fixture(path, color, tone, pid, offset):
    run(
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=160x120:r=25",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={tone}:sample_rate=48000",
        "-t",
        "35",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-threads",
        "1",
        "-g",
        "25",
        "-bf",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-mpegts_start_pid",
        str(pid),
        "-output_ts_offset",
        str(offset),
        "-f",
        "mpegts",
        str(path),
    )


def inspect(path):
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "warning",
            "-i",
            str(path),
            "-an",
            "-vf",
            "scale=1:1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    colors = Counter()
    for i in range(0, len(decoded.stdout), 3):
        r, g, b = decoded.stdout[i : i + 3]
        colors[
            "red"
            if r > 180 and g < 70 and b < 70
            else "blue"
            if b > 180 and r < 70 and g < 70
            else "other"
        ] += 1
    assert colors["red"] > 25 and colors["blue"] > 75, colors
    assert colors["other"] == 0, colors
    pcm = array.array(
        "h",
        run(
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "s16le",
            "pipe:1",
        ),
    )
    if sys.byteorder != "little":
        pcm.byteswap()
    assert len(pcm) > 480000
    tones = []
    for samples in (pcm[48000:96000], pcm[-96000:-48000]):
        tones.append(sum(a <= 0 < b for a, b in zip(samples, samples[1:])))
    assert abs(tones[0] - 440) < 8 and abs(tones[1] - 880) < 8, tones
    probe = json.loads(
        run("ffprobe", "-v", "error", "-show_packets", "-of", "json", str(path))
    )
    last = {}
    last_time = {}
    max_gaps = {}
    backwards = 0
    for packet in probe["packets"]:
        if "dts" not in packet:
            continue
        index, dts = packet["stream_index"], int(packet["dts"])
        backwards += index in last and dts < last[index]
        last[index] = dts
        if "dts_time" in packet:
            timestamp = float(packet["dts_time"])
            if index in last_time:
                max_gaps[index] = max(
                    max_gaps.get(index, 0), timestamp - last_time[index]
                )
            last_time[index] = timestamp
    assert backwards == 0, backwards
    assert len(max_gaps) == 2 and all(gap < 0.1 for gap in max_gaps.values()), max_gaps
    warnings = decoded.stderr.decode(errors="replace")
    assert "non-monoton" not in warnings.lower(), warnings
    return {
        "colors": dict(colors),
        "audio_seconds": len(pcm) / 48000,
        "head_tail_hz": tones,
        "backwards_dts": backwards,
        "max_packet_gaps_seconds": max_gaps,
        "decode_warnings": warnings,
        "bytes": path.stat().st_size,
    }


def main():
    with tempfile.TemporaryDirectory(prefix="live-normalize-") as directory:
        temp = Path(directory)
        a, b = temp / "a.ts", temp / "b.ts"
        fixture(a, "red", 440, 256, 20)
        fixture(b, "blue", 880, 512, 0)
        data = {"/live/a.ts": a.read_bytes(), "/live/b.ts": b.read_bytes()}
        requests = []
        readers = set()
        lock = threading.Lock()

        class Source(BaseHTTPRequestHandler):
            def do_GET(self):
                content = data.get(self.path)
                if content is None:
                    self.send_error(404)
                    return
                with lock:
                    requests.append(self.path)
                    readers.add(id(self))
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                start = time.monotonic()
                try:
                    for offset in range(0, len(content), 1316):
                        elapsed = time.monotonic() - start
                        # Abrupt incomplete-body fault, not an operator failover.
                        if self.path == "/live/a.ts" and elapsed >= 9:
                            return
                        self.wfile.write(content[offset : offset + 1316])
                        self.wfile.flush()
                        time.sleep(1316 / len(content) * 35)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with lock:
                        readers.discard(id(self))

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Source)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        base = f"http://127.0.0.1:{listener.getsockname()[1]}"
        source = f"http://127.0.0.1:{server.server_port}"
        env = {
            k: os.environ[k]
            for k in ("PATH", "HOME", "LANG", "LD_LIBRARY_PATH", "TMPDIR")
            if k in os.environ
        }
        env.update(
            PYTHONPATH=str(ROOT / "src"),
            ROOT_PATH="",
            REDIS_ENABLED="false",
            ENABLE_TRANSCODING_POOLING="true",
            ENABLE_SINGLE_PROCESS_TRANSCODING="true",
            LIVE_NORMALIZE_OUTPUT="false",
            WORKER_ID="",
            API_TOKEN="",
            LIVE_END_SESSION_ON_RECONNECT="false",
            LIVE_SINGLE_RECOVERY_PER_EXIT="false",
            STRICT_LIVE_TS_PREBUFFER_SIZE="0",
            LIVE_SILENT_RECONNECT_MIN_CHUNKS="100000",
            DEFAULT_MAX_RETRIES="0",
            STREAM_RETRY_ATTEMPTS="0",
            LOG_LEVEL="info",
            TEMP_DIR=str(temp / "streams"),
            LOG_DIR=str(temp / "logs"),
            HLS_TEMP_DIR=str(temp / "hls"),
            HLS_BROADCAST_DIR=str(temp / "broadcast"),
            DVR_RECORDING_DIR=str(temp / "dvr"),
        )
        with (temp / "proxy.log").open("wb") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "api:app",
                    "--fd",
                    str(listener.fileno()),
                    "--log-level",
                    "warning",
                ],
                pass_fds=(listener.fileno(),),
                cwd=temp,
                env=env,
                stdout=log,
                stderr=log,
            )
            listener.close()
            try:
                with httpx.Client(base_url=base, timeout=20, trust_env=False) as client:
                    for _ in range(150):
                        if process.poll() is not None:
                            raise RuntimeError((temp / "proxy.log").read_text())
                        try:
                            if client.get("/health").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.1)
                    else:
                        raise RuntimeError("API not ready")
                    options = {
                        "url": source + "/live/a.ts",
                        "failover_urls": [source + "/live/b.ts"],
                        "use_sticky_session": True,
                        "silent_close_retry_window": 0,
                        "end_session_on_reconnect": True,  # must be overridden internally
                        "normalize_live_output": True,
                        "metadata": {
                            "id": "fixture",
                            "playlist_uuid": "fixture-provider",
                        },
                    }
                    created = client.post("/streams", json=options)
                    created.raise_for_status()
                    outer_id = created.json()["stream_id"]
                    started = time.monotonic()
                    capture = temp / "normalized.ts"
                    # Exactly one player GET. No second operator/player request.
                    with (
                        client.stream(
                            "GET", created.json()["stream_endpoint"]
                        ) as response,
                        capture.open("wb") as output,
                    ):
                        response.raise_for_status()
                        for chunk in response.iter_bytes():
                            output.write(chunk)
                            if time.monotonic() - started >= 24:
                                break
                        else:
                            raise AssertionError(
                                "normalized body ended before capture deadline"
                            )
                    assert requests == ["/live/a.ts", "/live/b.ts"], requests
                    info = client.get(f"/streams/{outer_id}").json()["stream"]
                    assert info["current_url"] == source + "/live/b.ts", info
                    assert info["failover_attempts"] == 1, info
                    listed = client.get("/streams").json()
                    assert len(listed["streams"]) == 1, listed
                    count = client.get(
                        "/streams/counts-by-metadata",
                        params={
                            "field": "playlist_uuid",
                            "values": "fixture-provider",
                            "active_only": "false",
                        },
                    ).json()
                    assert count["counts"] == {"fixture-provider": 1}, count
                    result = inspect(capture)
                    result.update(
                        player_gets=1,
                        initial_posts=1,
                        source_requests=list(requests),
                        failover_attempts=info["failover_attempts"],
                    )
                    client.delete(f"/streams/{outer_id}").raise_for_status()
                    for _ in range(100):
                        if not readers:
                            break
                        time.sleep(0.1)
                    assert not readers, "DELETE outer leaked its provider reader"
                    assert client.get("/streams").json()["streams"] == []
                    # Default-off control: unmodified bytes, ordinary direct ID;
                    # no private input or encoder, even with local pooling enabled.
                    options.pop("normalize_live_output")
                    options["end_session_on_reconnect"] = False
                    legacy = client.post("/streams", json=options)
                    legacy.raise_for_status()
                    legacy_id = legacy.json()["stream_id"]
                    with client.stream(
                        "GET", legacy.json()["stream_endpoint"]
                    ) as response:
                        response.raise_for_status()
                        prefix = next(response.iter_bytes(chunk_size=1316))
                    assert prefix == data["/live/a.ts"][:1316]
                    state = client.get(f"/streams/{legacy_id}").json()["stream"]
                    assert "normalization_input_stream_id" not in state
                    assert state["current_url"] == options["url"]
                    client.delete(f"/streams/{legacy_id}").raise_for_status()
                    result["default_off_byte_exact_prefix"] = True
                    print(json.dumps(result, indent=2))
            except BaseException:
                print((temp / "proxy.log").read_text(), file=sys.stderr)
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    main()

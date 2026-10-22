"""Finite, fully drained hardware handoff gate (not proxy API evidence).

RUN_QSV_CADENCE=1 requires a disposable network-none render-node sandbox and
QSV_CADENCE_HELPER names an external transport-worker helper directory only
until ts_canonicalizer is integrated into product sources. No helper is copied
into this branch. QSV_CADENCE_CONTROL optionally names the retained 1077-frame
original handoff fixture. Three truncated-video cases intentionally remain red
until the transport layer can gate incomplete zero-length video PES correctly.
"""
import array
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

import pytest

from live_normalization import QSV_PROFILE


def run(*args):
    return subprocess.run([str(a) for a in args], capture_output=True, check=True, timeout=90)


def frames(path):
    return json.loads(run("ffprobe", "-v", "error", "-select_streams", "v", "-show_frames", "-of", "json", path).stdout)["frames"]


def normalize(source, target, *, discard=True):
    args = shlex.split(QSV_PROFILE.format(input_url=source, output_args=target))
    # Only network reconnect options are inapplicable to this finite local file.
    for option in ("-reconnect", "-reconnect_at_eof", "-reconnect_streamed",
                   "-reconnect_delay_max", "-reconnect_max_retries"):
        at = args.index(option)
        del args[at:at + 2]
    if not discard:
        args[args.index("+genpts+discardcorrupt")] = "+genpts"
    command = ["ffmpeg", "-hide_banner", "-debug_ts", "-y", *args]
    result = subprocess.run(command, capture_output=True, timeout=90)
    Path(str(target) + ".log").write_bytes(result.stderr)
    result.check_returncode()
    return frames(target)


def test_qsv_cadence_uses_existing_corruption_policy():
    args = shlex.split(QSV_PROFILE)
    assert args[args.index("-fflags") + 1] == "+genpts+discardcorrupt"
    assert args[args.index("-fps_mode") + 1] == "passthrough"
    assert "-r" not in args
    assert "setpts" not in args[args.index("-vf") + 1]


def test_qsv_discardcorrupt_retained_control(tmp_path):
    if os.environ.get("RUN_QSV_CADENCE") != "1":
        pytest.skip("explicit disposable QSV hardware sandbox required")
    if not os.environ.get("QSV_CADENCE_CONTROL"):
        pytest.skip("retained 1077-frame control fixture not supplied")
    report = {}
    for discard in (False, True):
        target = tmp_path / f"control-{discard}.ts"
        output = normalize(os.environ["QSV_CADENCE_CONTROL"], target, discard=discard)
        pts = [int(frame["pts"]) for frame in output]
        log = Path(str(target) + ".log").read_text()
        report[str(discard)] = {
            "frames": len(output),
            "bad_deltas": [b - a for a, b in zip(pts, pts[1:]) if b - a != 3600],
            "mux_clamps": [line for line in log.splitlines() if "Non-monotonic" in line],
            "discontinuities": [line for line in log.splitlines() if "timestamp discontinuity" in line],
        }
    (tmp_path / "control-evidence.json").write_text(json.dumps(report, indent=2))
    assert report["False"]["frames"] == report["True"]["frames"] == 1077
    assert report["False"]["bad_deltas"] == [1, 1, 3598]
    assert report["False"]["mux_clamps"]
    assert report["True"]["bad_deltas"] == report["True"]["mux_clamps"] == []
    assert len(report["False"]["discontinuities"]) == 3
    assert len(report["True"]["discontinuities"]) == 1


@pytest.mark.parametrize("kind", ["p25", "p50", "i25"])
@pytest.mark.parametrize("cut_mode", ["complete-video", "truncated-audio", "truncated-video"])
@pytest.mark.parametrize("bounded_video_pes", [False, True])
def test_qsv_moving_handoff_cadence(tmp_path, kind, cut_mode, bounded_video_pes):
    if os.environ.get("RUN_QSV_CADENCE") != "1":
        pytest.skip("explicit disposable QSV hardware sandbox required")
    try:
        import ts_canonicalizer as module
    except ModuleNotFoundError as error:
        if error.name != "ts_canonicalizer":
            raise
        helper = Path(os.environ["QSV_CADENCE_HELPER"]) / "ts_canonicalizer.py"
        spec = importlib.util.spec_from_file_location("cadence_transport_helper", helper)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    inputs = []
    for generation, tone in enumerate((440, 880)):
        source = tmp_path / f"{kind}-{generation}.ts"
        rate = 25 if kind == "p25" else 50
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
               f"testsrc2=size=320x180:rate={rate}:duration=3",
               "-f", "lavfi", "-i", f"sine=frequency={tone}:sample_rate=48000:duration=3"]
        vf = []
        if generation:
            vf.append("hue=h=90")
        if kind == "i25":
            vf.extend(("tinterlace=mode=interleave_top", "setfield=tff"))
            cmd += ["-flags", "+ilme+ildct", "-x264-params", "tff=1"]
        if vf:
            cmd += ["-vf", ",".join(vf)]
        if bounded_video_pes:
            # One-variable control: default zero-length video PES cannot be
            # flagged as truncated by the demuxer's PES-length check.
            cmd += ["-omit_video_pes_length", "0"]
        run(*cmd, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-threads", "1", "-bf", "0", "-g", "25",
            "-c:a", "aac", "-mpegts_pmt_start_pid", 1256 + generation * 256,
            "-mpegts_start_pid", 256 + generation * 256,
            "-output_ts_offset", 20 if not generation else 0, "-f", "mpegts", source)
        flags = frames(source)
        assert len(flags) == (75 if kind != "p50" else 150)
        assert {f["interlaced_frame"] for f in flags} == {int(kind == "i25")}
        inputs.append(source)
    # End immediately before a video PES start: every selected video access
    # unit is complete, while an in-flight interleaved audio PES may be cut.
    data = inputs[0].read_bytes()
    starts = [at for at in range(0, len(data), 188)
              if data[at + 1] & 64
              and ((data[at + 1] & 31) << 8 | data[at + 2]) == 256]
    first_packet = data[starts[0]:starts[0] + 188]
    payload_at = 5 + first_packet[4] if first_packet[3] & 32 else 4
    video_pes_length = int.from_bytes(first_packet[payload_at + 4:payload_at + 6], "big")
    assert bool(video_pes_length) == bounded_video_pes
    complete_cut = min(starts, key=lambda at: abs(at - len(data) * 2 / 3))
    if cut_mode == "truncated-audio":
        audio_starts = [at for at in range(0, len(data), 188)
                        if data[at + 1] & 64
                        and ((data[at + 1] & 31) << 8 | data[at + 2]) == 257]
        complete_cut = min(audio_starts, key=lambda at: abs(at - len(data) * 2 / 3)) + 188
        # FFmpeg writes these PES contiguously. All preceding video PES are
        # complete; the final audio PES deliberately has only one TS packet.
        next_packet = data[complete_cut:complete_cut + 188]
        assert ((next_packet[1] & 31) << 8 | next_packet[2]) == 257
        assert not next_packet[1] & 64
    complete_frames_a = sum(at < complete_cut for at in starts)
    assert len(starts) == (150 if kind == "p50" else 75)
    cut = complete_cut
    if cut_mode == "truncated-video":
        # Keep just the first TS packet of the next video PES. It cannot be
        # decoded intact and MUST be discarded, not counted as a saved frame.
        cut += 188
        assert cut < starts[complete_frames_a + 1]
    inputs[0].write_bytes(data[:cut])
    expected = (complete_frames_a + (150 if kind == "p50" else 75)) * (2 if kind == "i25" else 1)
    # Independent clean-video cut provides a non-corrupt visual reference.
    reference_a = tmp_path / "reference-a.ts"
    reference_a.write_bytes(data[:complete_cut])
    clean_adapter = module.TSCanonicalizer()
    clean = clean_adapter.feed(reference_a.read_bytes())
    clean_adapter.start_generation()
    clean += clean_adapter.feed(inputs[1].read_bytes()) + clean_adapter.finish()
    reference = tmp_path / "reference.ts"
    reference.write_bytes(clean)
    adapter = module.TSCanonicalizer()
    canonical = bytearray()
    for n, source in enumerate(inputs):
        if n:
            adapter.start_generation()
        data = source.read_bytes()
        for at in range(0, len(data), 997):
            canonical.extend(adapter.feed(data[at:at + 997]))
    canonical.extend(adapter.finish())
    joined = tmp_path / "canonical.ts"
    joined.write_bytes(canonical)
    target = tmp_path / "qsv.ts"
    output = normalize(joined, target)
    (tmp_path / "video-gate-evidence.json").write_text(json.dumps({
        "kind": kind, "cut_mode": cut_mode, "bounded_video_pes": bounded_video_pes,
        "first_video_pes_length": video_pes_length,
        "complete_source_a_frames": complete_frames_a,
        "expected_frames": expected, "actual_frames": len(output),
        "scope": "finite hardware gate, not product API",
    }, indent=2))
    assert len(output) == expected
    assert all(not f["interlaced_frame"] for f in output)
    step = 3600 if kind == "p25" else 1800
    pts = [int(f["pts"]) for f in output]
    assert {b - a for a, b in zip(pts, pts[1:])} == {step}
    # No muxer-clamped timestamps may hide a decoder/filter regression.
    log = Path(str(target) + ".log").read_text()
    assert "Non-monotonic" not in log and "non monotonically" not in log
    assert len([line for line in log.splitlines() if "timestamp discontinuity" in line]) == 1
    if cut_mode == "truncated-audio":
        assert re.search(r"Packet corrupt \(stream = 1,.*dropping it", log)
    hashes = run("ffmpeg", "-v", "error", "-i", target, "-an", "-fps_mode", "passthrough", "-f", "framemd5", "-").stdout.decode()
    hashes = [line.rsplit(",", 1)[-1].strip() for line in hashes.splitlines() if not line.startswith("#")]
    assert len(hashes) == len(set(hashes)) == expected
    # Independent SW decode/bwdif reference. Its timestamps are not used as the
    # cadence oracle: reset each stream to sequence indexes ONLY for comparison.
    comparison = run("ffmpeg", "-v", "info", "-fflags", "+discardcorrupt", "-i", reference,
                     "-i", target, "-lavfi",
                     "[0:v]bwdif=mode=send_field:parity=auto:deint=interlaced,settb=1/90000,setpts=N[a];[1:v]settb=1/90000,setpts=N[b];[a][b]ssim=stats_file=" + str(tmp_path / "ssim.log"),
                     "-an", "-f", "null", "-")
    scores = [float(re.search(r"All:([0-9.]+)", line).group(1)) for line in (tmp_path / "ssim.log").read_text().splitlines()]
    assert len(scores) == expected
    assert min(scores) > .95
    assert float(re.search(rb"All:([0-9.]+)", comparison.stderr).group(1)) > .97
    # Every encoded frame must match its own input position more closely than
    # either neighbour: detects repeats/drops that aggregate SSIM can conceal.
    raws = []
    for path, vf in ((reference, "bwdif=mode=send_field:parity=auto:deint=interlaced,"), (target, "")):
        raw = run("ffmpeg", "-v", "error", "-fflags", "+discardcorrupt", "-i", path, "-an", "-vf", vf + "scale=80:44", "-pix_fmt", "gray", "-fps_mode", "passthrough", "-f", "rawvideo", "-").stdout
        raws.append([raw[i:i + 3520] for i in range(0, len(raw), 3520)])
    assert len(raws[0]) == len(raws[1]) == expected
    for i, actual in enumerate(raws[1]):
        distances = {j: sum(abs(a - b) for a, b in zip(actual, raws[0][j])) for j in range(max(0, i - 2), min(expected, i + 3))}
        assert min(distances, key=distances.get) == i, (i, distances)
    pcm = array.array("h", run("ffmpeg", "-v", "error", "-i", target, "-vn", "-ac", "1", "-ar", "48000", "-f", "s16le", "-").stdout)
    tones = [sum(a <= 0 < b for a, b in zip(s, s[1:])) for s in (pcm[24000:72000], pcm[-72000:-24000])]
    assert abs(tones[0] - 440) < 8 and abs(tones[1] - 880) < 8
    packets = json.loads(run("ffprobe", "-v", "error", "-show_packets", "-of", "json", target).stdout)["packets"]
    audio = [p for p in packets if p["codec_type"] == "audio"]
    audio_gaps = [float(b["pts_time"]) - float(a["pts_time"]) - float(a["duration_time"])
                  for a, b in zip(audio, audio[1:])]
    assert max(abs(gap) for gap in audio_gaps) < .00002
    av_head = float(audio[0]["pts_time"]) - pts[0] / 90000
    assert abs(av_head) < .12
    # Bound inserted silence anywhere, including the handoff, rather than
    # proving only the two steady-state tones. AAC padding is permitted.
    quiet_blocks = [max(abs(v) for v in pcm[i:i + 480]) < 100
                    for i in range(0, len(pcm) - 479, 480)]
    longest_quiet = quiet_run = 0
    for quiet in quiet_blocks:
        quiet_run = quiet_run + 1 if quiet else 0
        longest_quiet = max(longest_quiet, quiet_run)
    # TS muxing batches AAC into PES (~0.3 s). Cutting before the next
    # batch leaves a source-side audio gap, filled by the existing aresample.
    # This bound is not a claim of inaudible/seamless audio.
    assert longest_quiet <= 35
    av_tail = float(audio[-1]["pts_time"]) + float(audio[-1]["duration_time"]) - (pts[-1] + step) / 90000
    assert abs(av_tail) < .12
    evidence = dict(kind=kind, cut_mode=cut_mode, bounded_video_pes=bounded_video_pes,
                    complete_source_a_frames=complete_frames_a,
                    intentionally_discarded_incomplete_video_pes=int(cut_mode == "truncated-video"),
                    frames=expected, unique_frames=len(set(hashes)), pts_step=step,
                    max_audio_gap_seconds=max(abs(gap) for gap in audio_gaps),
                    max_quiet_seconds=longest_quiet * .01, av_head_seconds=av_head,
                    min_ssim=min(scores), tones=tones, av_tail_seconds=av_tail,
                    source_sha256=[hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs],
                    scope="finite real QSV profile + external adapter, NOT proxy API")
    (tmp_path / "evidence.json").write_text(json.dumps(evidence, indent=2))
    print("QSV_CADENCE", json.dumps(evidence))

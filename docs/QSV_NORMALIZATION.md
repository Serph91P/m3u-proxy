# Explicit QSV live normalization

## Accepted FFmpeg standard presentation policy

Max explicitly accepted the existing FFmpeg bwdif presentation policy for the
single **unbounded truncated-video TFF-i25** source cut. Decode is statically
software (`-hwaccel none -err_detect:v explode -ec:v 0`); bwdif is CPU and only
encoding is QSV. This is not a runtime fallback or a custom FFmpeg build/patch.

The source-field vector remains unchanged in `source-timeline-oracle.json`
(`expected_pts`). The separate `standard_bwdif_policy_pts` vector replaces only
the second field of the last valid A picture with the midpoint of independently
known valid A and B input PTS. In the moving fixture this is index 101:
**311640 → 313440 ticks (+1800 ticks / 20 ms)**, while B stays **317040**.
This is an accepted presentation-time tradeoff, **not preservation of all source
field timestamps**. The expectation is never calculated from actual output.
All other source-cut cases require the identical original source vector.

No permission is implied for an extra corrupt/concealed image, missing/repeated
valid field, hidden TS continuity loss, shifted B epoch, altered audio, or relaxed
SSIM/A/V limits. The original source-position mismatches remain visible separately
from policy mismatches; exact count, PTS-policy equality, unique hashes, independent
image-position matches and every existing audio/quality gate remain mandatory.
The P1 continuity fix is integrated: media CC rebasing preserves source deltas
and retransmissions rather than concealing transport loss.

Fresh acceptance artifacts, runner, flags, source hashes, JUnit and per-case
video/audio evidence are retained under:

```text
/opt/data/profiles/infra/workspace/m3u-live-failover/optin-acceptance/qsv-check/accepted-standard-policy/
```

Historical failures below remain historical failures under their then-required
source-PTS contract; this explicit owner decision, not a deinterlacer correction,
changes acceptance of precisely the single field above.

## Historical rejected source-position contract (superseded by explicit decision)

The previous strict source-position gate returned 588 passed / 1 failed: all
valid pictures survived, but the last valid A second-field timestamp differed
by 20 ms. Unbounded truncated progressive p25/p50 also have a real missing-picture
clock slot (80/40 ms transition interval), not arbitrary gap allowance.

FFmpeg 8.1.2 `estdif` and `w3fdif` were source-inspected and replayed; both use the
same adjacent-picture midpoint timing as bwdif and did not restore source-field
PTS. No replacement filter or patched FFmpeg is included. Earlier raw evidence
is retained under `/opt/data/profiles/infra/cache/scratch/qsv-field-fix/`.

The earlier full-tree Ruff findings were introduced within this QSV workstream,
not inherited upstream exceptions. They are resolved in the accepted candidate:
semicolon statements split without semantic changes, and the shared integration
fixture registered without an imported-name/parameter collision. AST inspection
confirmed the four F811 findings were **not duplicate test definitions**; no test
was removed or skipped. The integration test file still collects all 17 cases.

Fresh all-flags verification after P1 integration: **616 passed, 0 failed,
0 skipped**, with 105 framework/datetime deprecation warnings. Separate QSV
Full-HD p25/p50/i25 verification: **3 passed**; exact finite counts 50/100/100,
40/20/20-ms cadence, progressive distinct pictures and SSIM
0.998968/0.998896/0.998530 respectively. All 18 moving source-cut cases passed
against the accepted policy; only the documented source-field mismatch exists.
Both real API cases (AAC and DVB AC-3) passed, including automatic A EOF to B
within one POST/player GET, changed PMT/ES PIDs and actual reset epochs.
Full-tree `ruff check src tests` and `git diff --check` pass.

## Contract and implementation

`POST /streams` accepts `normalization_encoder: "cpu" | "vaapi" | "qsv"`.
CPU is the default, including older requests without the field. Normalization
remains off by default; a QSV selector with normalization off does not probe the
GPU or change the direct-stream ID. QSV is not auto-selected and has no silent
CPU/VAAPI runtime fallback.

The QSV preset is server-owned. Its encoder and full effective template salt
both the public output ID and owned private input ID. CPU custom templates do
not affect QSV. Server metadata overwrites attempted encoder-marker spoofing.
The input/ownership/source-control transport contracts are unchanged, including
409 on deleting the owned input and cascading output deletion. API enum, ID,
profile-change, reuse, cleanup, admission failure/cancellation and controls have
regression tests.

Pipeline:

```text
-init_hw_device vaapi=va:/dev/dri/renderD128
-init_hw_device qsv=qs@va -filter_hw_device qs
-hwaccel none -err_detect:v explode -ec:v 0
-vf bwdif=mode=send_field:parity=auto:deint=interlaced,format=nv12,hwupload=extra_hw_frames=64
-fps_mode passthrough -enc_time_base 1/90000
-c:v h264_qsv -global_quality 18 -g 25 -bf 0
```

This is explicit **CPU decode and bwdif, GPU QSV encode**, not full-GPU processing
and not a runtime fallback. Strict software decode rejects truncated unbounded
H.264 PES that hardware decode conceals even with strict error flags. Conditional
bwdif preserves progressive cadence and produces two temporal pictures per
interlaced input frame. Unconditional QSV VPP field doubling was rejected because
it also doubles progressive sources and loses a finite-clip tail frame.

The original editor profile 3 was read-only inspected in RAM, printing only an
allowlisted rate-control summary: hardware QSV decode/encode, preset `faster`,
GOP 50, bitrate default 2000k/maxrate 2500k/bufsize 5000k, no explicit B-frame,
output timebase, frame-mode or global-quality controls. The new preset deliberately
does **not** inherit these bitrate caps or encoder defaults: fixed quality 18,
no B-frame reordering, passthrough timestamps, explicit 90 kHz encoder timebase.
Neither the original profile nor any provider settings were edited.

Admission checks an Intel character render node, then generates four compatible
8-bit H.264 TFF frames in RAM and exercises the actual software-decode/bwdif/
upload/QSV-encode chain. Each subprocess has a 10-second timeout and is killed and
reaped on timeout/cancellation. Nonzero exit, missing executable/device, fixture
failure and initialization errors produce 503 before any stream allocation.
The fixture never contains provider inputs or secrets. This does not guarantee
that every future source codec, resolution or device state is supported.

## Historical hardware-decoder evidence (not current candidate acceptance)

The tables and recorded suite totals in this section and the historical handoff
recheck below were obtained with the previous hardware-decoder preset. They do
not validate the current software-decoder candidate. Earlier evidence is retained
under `qsv-check/final-acceptance/`; the then-required original source-position
gate failed even when all valid frame counts matched. No historical failure is
reclassified as a pass; current acceptance uses the explicitly separate policy
and fresh evidence documented above.

Disposable image `m3u-proxy:quality-d23fd81`, FFmpeg 8.1.2, Intel renderD128,
`--rm --network none --device /dev/dri --cpus 2 --memory 1g --pids-limit 128`,
600 MiB scratch tmpfs. Driver environment: `LIBVA_DRIVER_NAME=iHD`,
`LIBVA_DRIVERS_PATH=/usr/local/lib/x86_64-linux-gnu/dri`,
`LD_LIBRARY_PATH=/usr/local/lib`. Serial short moving clips only.

Two-second compatible H.264 CRF18/yuv420p inputs; i25 generated by interleaving
50p `testsrc2` with TFF signaling. Decoded input flags independently checked.
The reference is lossless bwdif on the **same decoded H.264 input**. SSIM is
computed only after exact count/PTS/progressive/hash checks pass.

| Size | Source | Output frames / distinct hashes | PTS step | SSIM |
|---|---|---:|---:|---:|
| 320x180 | TFF i25 | 100 / 100 | 20 ms | 0.994654 |
| 320x180 | p25 | 50 / 50 | 40 ms | 0.996547 |
| 320x180 | p50 | 100 / 100 | 20 ms | 0.996243 |
| 1920x1080 | TFF i25 | 100 / 100 | 20 ms | 0.998530 |
| 1920x1080 | p25 | 50 / 50 | 40 ms | 0.998968 |
| 1920x1080 | p50 | 100 / 100 | 20 ms | 0.998896 |

All output frames are progressive. The real admission function also passed in
this sandbox. A separate short loopback API→owned input→FFmpegStreamer→QSV
capture passed for all three source types, with one player GET each. The first
recorded run decoded 215/112/234 distinct progressive frames respectively, exact
20/40/20 ms intervals, and SSIM 0.994108/0.996313/0.995839 against the corresponding
bwdif reference segment. Counts here vary with the intentional five-second wall
capture boundary; exact finite-clip counts are the independent test above.
Owned encoder PIDs 445/970/1495 each held an actual `/dev/dri/renderD128` FD
(container namespace; ephemeral). Input DELETE returned 409 and output DELETE
left the public stream list empty.

Raw output and the self-contained sandbox runner live outside the repository:

```text
/opt/data/profiles/infra/workspace/m3u-live-failover/optin-acceptance/qsv-check/
  qsv-cadence-probe.py
  run-proxy-qsv-validation.py
  validation-320x180.txt
  validation-1920x1080.txt
  validation-api.txt
  validation-all.txt
```

The complete sandbox suite passed: **513 passed, no skips**, with 105 existing
framework/datetime deprecation warnings. This includes CPU, VAAPI and QSV quality
cases plus the three real QSV API captures. Host-side `ruff check src tests` and
`git diff --check` also passed (the sandbox image does not contain Ruff; no
package was installed). All owned validation containers were removed.

## Historical hardware-decode handoff recheck (not current acceptance)

`329463e` already contains `-fflags +genpts+discardcorrupt`. No QSV source
profile/filter change or timestamp rewrite was needed for the retained original
1077-frame handoff fixture. Changing **only** that flag to `+genpts` reproduced
three bad 90 kHz video deltas, `[1, 1, 3598]`, and two mux DTS clamps. Keeping the
existing flag produced all 1077 frames at 3600 ticks, no clamps, and one clock
recovery rather than three audio/video discontinuity corrections. This proves
the missing-flag mechanism; it does not establish which flags were in an earlier
RAM-only profile invocation whose complete arguments were not retained.

The new opt-in `tests/test_qsv_cadence_handoff.py` exercises moving 320x180 p25,
p50 and TFF-i25, changed PMT and elementary PIDs, A at offset 20 s and B at 0 s,
997-byte feed chunks, finite A EOF and fully drained B. It imports the product
`ts_canonicalizer` directly. The older run below used an external helper; no
external helper is required by the current test.

The complete-video cut has **126 / 251 / 252** expected and distinct output
frames (p25 / p50 / i25), with exact 40 / 20 / 20 ms intervals. A second cut
inside an audio PES has **122 / 259 / 260** expected/distinct frames: FFmpeg
explicitly reports dropping corrupt audio, with only one clock recovery, no mux
clamps and unchanged video cadence. Both 440/880 Hz tones were verified; audio
packet continuity residual was at most 1 microsecond, initial audio lead 42.667 ms,
and final A/V difference below 27 ms. **PCM silence at the handoff is real:**
140–160 ms for the clean-video cuts and 300–320 ms when an incomplete AAC PES is
dropped. Do not label these cuts seamless/inaudible. Per-frame minimum SSIM was
at least 0.993540 across these passing cases; every output frame matched its
own independent reference position better than either of its two neighbours.

A deliberate truncated-video cut exposes a separate transport limitation:
with the muxer's default zero video PES length, `discardcorrupt` cannot infer
that the final PES is incomplete. QSV conceals it, giving **127 vs 126**, **252 vs
251**, and **254 vs 252** frames. These three strict tests remain **red**, not
xfail and not relaxed. A one-variable source control with
`-omit_video_pes_length 0` gives explicit nonzero PES lengths and passes all
nine moving combinations, including exact **126 / 251 / 252** truncated-video
outputs. Those cases intentionally discard one damaged source video PES, not
"zero source-frame drops". Source PES-length selection is a diagnostic fixture
control, **not** a proposed live-source workaround.

The final all-flags sandbox run returned **530 passed, 3 failed, 0 skipped**
(105 existing warnings); the failures are exactly the unbounded truncated-video
PES gates above. CPU/VAAPI/QSV finite-quality and real fixed-source QSV API tests
were included and passed. `ruff check src tests` passed. This is not a green
handoff release gate: complete-PES gating for unbounded video belongs to the
transport workstream and remains unresolved. No `setpts`, fixed output fps,
encoder frame-dropping policy or source-profile mutation was introduced.

Retained fixtures, generated sources/outputs, debug logs, JUnit XML, per-case
metrics and control results are outside the repository under:

```text
/opt/data/profiles/infra/workspace/m3u-live-failover/optin-acceptance/qsv-check/cadence-fix/resumed-evidence/
```

Reproduce only in an owned network-none Intel render-node sandbox, serially:

```sh
PYTHONPATH=src RUN_QSV_CADENCE=1 \
QSV_CADENCE_CONTROL=/lab/canonical.ts RUN_QSV_QUALITY=1 RUN_VAAPI_QUALITY=1 \
NORMALIZATION_QUALITY_SIZE=320x180 python -m pytest tests -q --tb=short
```

`QSV_CADENCE_CONTROL` is optional and specifically names the retained original
1077-frame synthetic fixture; the moving cases regenerate their own inputs.

## Limits

These are synthetic correctness/regression checks, not provider/client visual
acceptance, throughput or GPU stress tests. Current acceptance includes the
product TS canonicalizer and real loopback API changed-PMT automatic A→B tests
with AAC and DVB AC-3; it does not establish arbitrary codec/source compatibility.
The only accepted field-time deviation is the 20-ms special cut documented above.
No provider traffic, user-stream stop, deployed configuration edit, push or
deployment was performed. GPU/codec failure after creation can terminate playback.
No automatic fallback hides a decoder/encoder failure.

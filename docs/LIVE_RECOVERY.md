# Opt-in direct-live recovery

These independent options are **disabled by default**. They apply to direct live
HTTP streams, not VOD/timeshift, transcoding, HLS playlists or DASH manifests.
Existing TS discontinuity-marker and overlap-trimming behavior is unchanged with
both options off. They do not convert media, normalize PTS/PIDs/codecs, or offer
seamless playback across arbitrary upstream sources.

| ENV (default) | Nullable `POST /streams` field | Effect |
| --- | --- | --- |
| `LIVE_END_SESSION_ON_RECONNECT=false` | `end_session_on_reconnect` | Finish the old downstream HTTP bodies before opening a replacement upstream generation. |
| `LIVE_SINGLE_RECOVERY_PER_EXIT=false` | `single_recovery_per_exit` | Do not reinterpret an already handled timeout/retry/failover exit as EOF or a circuit-breaker failure. |

For each field, omitted or `null` inherits ENV; explicit `false` overrides a global
`true`. Effective policies are frozen at stream creation. Different effective
policy combinations use different stream IDs, so incompatible clients cannot
silently share the wrong recovery policy. Both-off IDs retain their old format.
Changing ENV does not mutate already-created streams.

## Scoped activation

Start with per-stream activation, rather than switching every channel globally:

```http
POST /streams
Content-Type: application/json

{
  "url": "http://source.example/live/channel.ts",
  "failover_urls": ["http://backup.example/live/channel.ts"],
  "end_session_on_reconnect": true,
  "single_recovery_per_exit": true
}
```

Consume the returned `stream_endpoint`. At an upstream reconnect boundary, that
HTTP body ends. Make a **new GET to the same endpoint** to consume the chosen
source. The old primary, all subscribers (including backlogged queues), and any
unclaimed upstream handoff are detached from the old generation. Already-sent
bytes cannot be retracted. Subscriber backlog is discarded, not drained into a
new session. A healthy connection can still be handed to a subscriber when only
the primary viewer disconnects: this does not create a new upstream generation.
If a promoted subscriber cannot reuse the handed-off upstream, its existing body
ends rather than splicing a fresh connection into it.
In session-end mode a still-active primary owns stall recovery; subscribers do
not start competing source selections merely because it is slow. In-flight
source selection is serialized with generation reset before a new primary is
exposed. This does not make selection durable across process restarts.

The selected `current_url` survives the reset, including a subsequent identical
`POST /streams`. This also covers reconnecting to the **same URL**, because a new
connection to the same URL may still reset timestamps or codec configuration.
No replacement-generation bytes are sent in the old bodies. A reconnecting live
body has no inherited upstream `Content-Length`/`Content-Range` and uses HTTP 200,
so early session-end does not falsely promise the remainder of an upstream body.
Normal stream expiry, explicit deletion or a process restart still loses this
in-memory selection; this is not durable source pinning.

The reset option does not change the existing source-selection/retry algorithm.
The single-recovery option is separate on purpose: with it off, legacy timeout
post-loop classification remains enabled, even if session-end is enabled.
Enabling both is recommended when testing a timeout-sensitive channel. The
existing same-URL silent-close retry window is another independent option, not
implicitly enabled by either switch.

## Supported topology and limits

Use **one proxy process/instance**, without Redis or `WORKER_ID`. Policy-enabled
live stream creation returns **HTTP 400 before allocating a stream or clients**
if Redis is enabled, a Redis URL was supplied to the manager, or `WORKER_ID` is
set. This deliberately rejects distributed/pool-coordinated operation rather
than pretending that process-local generation barriers coordinate workers.
Local, non-Redis transcoding-pool infrastructure may remain enabled; these
policies only affect the direct-live path, never pooled transcoding output.
External multi-process launchers/load balancers without Redis/worker configuration
cannot be auto-detected: do not activate these options there. Sticky routing is
not a substitute for generation coordination. Redis/device acceptance is not
claimed; distributed support would need a separate implementation and tests.

This is **session-end**, not a player reconnect instruction. Emby, Kodi, TVs and
other clients may stop playback instead of issuing a new request. Test actual
client versions and reverse-proxy buffering separately. In particular, the
[Emby M3U tuner documentation](https://emby.media/support/articles/M3U-Tuners.html)
does not guarantee automatic reconnection after the HTTP body ends.

## Why not just add a discontinuity flag?

- [FFmpeg MPEG-TS muxer](https://ffmpeg.org/ffmpeg-formats.html#mpegts):
  `initial_discontinuity` marks the initial packet of each stream. It does not
  normalize arbitrary codec/track/timestamp changes or guarantee player recovery.
  Generating missing timestamps (`genpts`) is not equivalent to rewriting all
  existing timestamps into a continuous timeline either.
- [RFC 8216 §4.3.2.3](https://www.rfc-editor.org/rfc/rfc8216#section-4.3.2.3):
  HLS requires `EXT-X-DISCONTINUITY` for changes in format, track identifiers or
  timestamp sequence. That is a **playlist/segment boundary**, not an in-band tag
  that can be attached to a direct continuous HTTP TS response. Implementing that
  honestly would require a separate segmented-output architecture.

## Rollback

Set both per-stream fields to `false`, or remove them with both ENV values still
`false`, and request the newly returned endpoint. When rolling back global
activation, set both ENV variables to `false` and restart the single process, then
recreate affected streams. Existing policy-enabled IDs remain policy-enabled
until deleted/expired/restarted; do not expect editing ENV or posting an override
to change an already-open body. No release/deploy step is implied by this guide.

## Reproducible local verification

With project requirements installed and FFmpeg/ffprobe on PATH:

```sh
python -m pytest tests/test_live_recovery.py tests/test_live_recovery_headers.py -q
python -m pytest --tb=short -q -m 'not integration and not slow'
python tools/verify_live_recovery.py
```

The executable acceptance script uses real loopback source HTTP requests and a
real uvicorn proxy API. It generates red/440 Hz MPEG-2/MP2 TS at PID 0x100/0x101,
then blue/880 Hz H.264/AAC TS at PID 0x200/0x201 with a timestamp reset. It asserts
byte-exact first-body=A, same-endpoint reconnect=B (also after POST reuse), decodes
**all frames and audio** of each response separately, checks track/timestamp
changes, and checks that feature-disabled legacy still emits A+B in one body.
Both compared runs explicitly enable existing Strict Live TS mode (zero fixture
prebuffer), so only the two new policy switches differ.
It does not claim that legacy mixed-codec bytes decode seamlessly. No providers,
production streams or external network are used by the acceptance script.

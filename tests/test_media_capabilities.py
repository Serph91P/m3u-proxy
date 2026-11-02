"""Player replays with real processors/managers and synthetic upstream headers."""

from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import httpx
import pytest
import pytest_asyncio

import api
from stream_manager import StreamManager


@pytest_asyncio.fixture
async def playback(monkeypatch):
    monkeypatch.setattr(api.settings, "API_TOKEN", "synthetic-management-token")
    monkeypatch.setattr(api.settings, "ROOT_PATH", "")
    manager = StreamManager()
    captured = []

    def upstream(request):
        captured.append((str(request.url), dict(request.headers)))
        path = request.url.path
        if path == "/redirect.m3u8":
            return httpx.Response(
                302, headers={"Location": "http://cdn.test/index.m3u8"}
            )
        if path.endswith("master.m3u8"):
            body = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\nchild.m3u8\n"
        elif path.endswith("index.m3u8") or path.endswith("child.m3u8"):
            body = (
                "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nseg.ts\n"
                "#EXTINF:4,\nhttp://cdn.test/seg.ts\n"
            )
        elif path.endswith("manifest.mpd"):
            body = (
                '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>'
                '<SegmentList><SegmentURL media="http://a.test/seg.m4s"/>'
                '</SegmentList><SegmentTemplate media="chunk-$Number$.m4s"/>'
                "</Period></MPD>"
            )
        elif path == "/redirect.ts":
            return httpx.Response(302, headers={"Location": "http://cdn.test/end.ts"})
        else:
            return httpx.Response(200, content=b"synthetic-media")
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as source:
        await manager.http_client.aclose()
        manager.http_client = source
        monkeypatch.setattr(api, "stream_manager", manager)
        ids = []
        for host in ("a", "b"):
            ids.append(
                await manager.get_or_create_stream(
                    f"http://{host}.test/index.m3u8",
                    user_agent=f"synthetic-provider-agent-{host}",
                    headers={
                        "Authorization": f"Bearer synthetic-{host}",
                        "X-Provider-Key": f"synthetic-{host}",
                    },
                )
            )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://proxy.test"
        ) as client:
            yield client, manager, ids, captured
        await manager.stop()


async def hls_links(client, stream_id):
    response = await client.get(f"/hls/{stream_id}/playlist.m3u8")
    assert response.status_code == 200
    return [line for line in response.text.splitlines() if line.startswith("/hls/")]


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["other-stream", "dash-file"])
async def test_transplanted_capability_cannot_select_credentials(playback, destination):
    client, manager, (a, b), captured = playback
    link = (await hls_links(client, a))[0]
    allowed = await client.get(link)
    assert allowed.status_code == 200 and allowed.content == b"synthetic-media"
    assert captured[-1][1]["authorization"] == "Bearer synthetic-a"
    assert captured[-1][1]["x-provider-key"] == "synthetic-a"
    captured.clear()
    forged = (
        link.replace(a, b)
        if destination == "other-stream"
        else link.replace(f"/hls/{a}/segment.ts", f"/dash/{a}/file")
    )
    response = await client.get(forged)
    assert captured == [], "rejected capabilities must not perform an upstream fetch"
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_cross_origin_media_and_redirects_do_not_forward_provider_headers(
    playback,
):
    client, manager, (a, _), captured = playback
    links = await hls_links(client, a)
    response = await client.get(links[1])
    assert response.status_code == 200
    assert "authorization" not in captured[-1][1]
    assert "x-provider-key" not in captured[-1][1]
    manager.streams[a].original_url = "http://a.test/redirect-playlist.m3u8"
    # Issue a real processor link to a same-origin redirecting segment.
    from stream_manager import M3U8Processor

    info = manager.streams[a]
    processor = M3U8Processor(
        f"/hls/{a}",
        "player",
        original_url=info.original_url,
        signing_context=manager.media_context(a),
    )
    link = processor._rewrite_url("http://a.test/redirect.ts", f"/hls/{a}")
    response = await client.get(link)
    assert response.status_code == 200
    assert captured[-2][1]["x-provider-key"] == "synthetic-a"
    assert "authorization" not in captured[-1][1]
    assert "x-provider-key" not in captured[-1][1]
    assert captured[-1][1]["user-agent"] == api.settings.DEFAULT_USER_AGENT


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["remove", "recreate", "headers", "user-agent"])
async def test_capabilities_are_revoked_with_issuing_context(playback, change):
    client, manager, (a, _), captured = playback
    link = (await hls_links(client, a))[0]
    if change == "remove":
        del manager.streams[a]
    elif change == "recreate":
        del manager.streams[a]
        await manager.get_or_create_stream("http://a.test/index.m3u8")
    elif change == "headers":
        manager.streams[a].headers["X-Provider-Key"] = "synthetic-replacement"
    else:
        manager.streams[a].user_agent = "synthetic-replacement"
    captured.clear()
    response = await client.get(link)
    assert captured == []
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_real_dash_urls_replay_but_not_under_other_stream(playback):
    client, manager, (_, b), captured = playback
    a = await manager.get_or_create_stream(
        "http://a.test/manifest.mpd", headers={"X-Provider-Key": "synthetic-dash"}
    )
    response = await client.get(f"/dash/{a}/manifest.mpd")
    assert response.status_code == 200
    root = ET.fromstring(response.text)
    base = root.find("{*}BaseURL").text
    file = root.find(".//{*}SegmentURL").get("media")
    for link in (urljoin(base, "chunk-1.m4s"), file):
        assert (await client.get(link)).status_code == 200
        assert captured[-1][1]["x-provider-key"] == "synthetic-dash"
        captured.clear()
        assert (await client.get(link.replace(a, b))).status_code == 403
        assert captured == []


@pytest.mark.asyncio
async def test_variant_parent_and_resource_family_are_bound(playback):
    client, manager, (_, b), captured = playback
    a = await manager.get_or_create_stream(
        "http://a.test/master.m3u8", headers={"X-Provider-Key": "synthetic-parent"}
    )
    link = (await hls_links(client, a))[0]
    captured.clear()
    for forged in (
        link.replace(f"parent={a}", f"parent={b}"),
        link.replace(f"/hls/{a}/playlist.m3u8", f"/dash/{a}/manifest.mpd"),
    ):
        assert (await client.get(forged)).status_code == 403
        assert captured == []
    response = await client.get(link)
    assert response.status_code == 200
    child = next(line for line in response.text.splitlines() if "/segment" in line)
    assert captured[-1][1]["x-provider-key"] == "synthetic-parent"
    assert (await client.get(child)).status_code == 200
    assert captured[-1][1]["x-provider-key"] == "synthetic-parent"
    del manager.streams[a]
    captured.clear()
    assert (await client.get(child)).status_code == 403
    assert captured == []
    child_id = child.split("/")[2]
    for path in (f"/hls/{child_id}/playlist.m3u8", f"/dash/{child_id}/manifest.mpd"):
        assert (await client.get(path)).status_code == 403
        assert captured == []


@pytest.mark.asyncio
async def test_variant_refresh_and_credential_change(playback):
    client, manager, _, captured = playback
    a = await manager.get_or_create_stream(
        "http://a.test/master.m3u8", headers={"X-Provider-Key": "synthetic-parent"}
    )
    link = (await hls_links(client, a))[0]
    response = await client.get(link)
    child = next(line for line in response.text.splitlines() if "/segment" in line)
    assert (await client.get(link)).status_code == 200
    assert (await client.get(child)).status_code == 200
    manager.streams[a].headers["X-Provider-Key"] = "synthetic-new-parent"
    new_link = (await hls_links(client, a))[0]
    response = await client.get(new_link)
    new_child = next(line for line in response.text.splitlines() if "/segment" in line)
    assert (await client.get(new_child)).status_code == 200
    assert captured[-1][1]["x-provider-key"] == "synthetic-new-parent"


@pytest.mark.asyncio
async def test_sticky_cross_origin_playlist_never_regains_credentials(playback):
    client, manager, _, captured = playback
    a = await manager.get_or_create_stream(
        "http://a.test/redirect.m3u8",
        headers={"X-Provider-Key": "synthetic-parent"},
        use_sticky_session=True,
    )
    for _ in range(2):
        link = (await hls_links(client, a))[0]
        assert (await client.get(link)).status_code == 200
        assert all(
            "x-provider-key" not in headers
            for url, headers in captured
            if url.startswith("http://cdn.test/")
        )


@pytest.mark.asyncio
async def test_local_scope_is_the_issuing_transcoder_directory(
    playback, tmp_path, monkeypatch
):
    from url_signing import sign_url

    client, manager, (a, b), captured = playback
    left = tmp_path / "m3u_proxy_hls_left"
    right = tmp_path / "m3u_proxy_hls_right"
    left.mkdir()
    right.mkdir()
    (left / "s.ts").write_bytes(b"left")
    (right / "s.ts").write_bytes(b"right")
    monkeypatch.setattr(api.settings, "HLS_TEMP_DIR", str(tmp_path))
    manager.streams[a].media_local_dir = str(left)
    manager.streams[b].media_local_dir = str(right)
    url = f"file://{left}/s.ts"
    params = {
        "client_id": "player",
        "url": url,
        "sig": sign_url(url, context=manager.media_context(a), role="hls-segment"),
    }
    assert (await client.get(f"/hls/{a}/segment.ts", params=params)).content == b"left"
    assert (await client.get(f"/hls/{b}/segment.ts", params=params)).status_code == 403
    params["url"] = f"file://{right}/s.ts"
    params["sig"] = sign_url(
        params["url"], context=manager.media_context(a), role="hls-segment"
    )
    assert (await client.get(f"/hls/{a}/segment.ts", params=params)).status_code == 403


@pytest.mark.asyncio
async def test_migration_tampering_and_token_rotation_fail_before_fetch(
    playback, monkeypatch
):
    import base64
    import hashlib
    import hmac
    from urllib.parse import parse_qs, urlparse
    from url_signing import _signing_key, sign_url

    client, manager, (a, _), captured = playback
    link = (await hls_links(client, a))[0]
    params = {k: v[0] for k, v in parse_qs(urlparse(link).query).items()}
    original = params["sig"]
    legacy = (
        base64.urlsafe_b64encode(
            hmac.new(_signing_key(), params["url"].encode(), hashlib.sha256).digest()[
                :16
            ]
        )
        .decode()
        .rstrip("=")
    )
    for signature in (
        legacy,
        sign_url(params["url"]),
        "v2.!.invalid",
        "v2.é.invalid",
        original.replace("aHR0", "bHR0"),
    ):
        captured.clear()
        params["sig"] = signature
        assert (
            await client.get(f"/hls/{a}/segment.ts", params=params)
        ).status_code == 403
        assert captured == []
    params["sig"] = original
    params["url"] = "http://b.test/seg.ts"
    assert (await client.get(f"/hls/{a}/segment.ts", params=params)).status_code == 403
    assert captured == []
    monkeypatch.setattr(api.settings, "API_TOKEN", "synthetic-rotated-token")
    assert (await client.get(link)).status_code == 403
    assert captured == []


@pytest.mark.asyncio
async def test_independent_workers_need_the_issuing_stream_context(playback):
    from url_signing import sign_url, verify_url

    client, left, (a, _), captured = playback
    right = StreamManager(enable_pooling=False)
    b = await right.get_or_create_stream(
        "http://a.test/index.m3u8",
        headers=dict(left.streams[a].headers),
        user_agent=left.streams[a].user_agent,
    )
    assert a == b
    signature = sign_url(
        "http://a.test/seg.ts", context=left.media_context(a), role="hls-segment"
    )
    assert verify_url(
        "http://a.test/seg.ts",
        signature,
        context=left.media_context(a),
        role="hls-segment",
    )
    assert not verify_url(
        "http://a.test/seg.ts",
        signature,
        context=right.media_context(b),
        role="hls-segment",
    )
    assert (await client.get((await hls_links(client, a))[0])).status_code == 200
    await right.stop()


@pytest.mark.asyncio
async def test_failover_issues_new_links_without_changing_old_header_scope(playback):
    client, manager, (a, _), captured = playback
    manager.streams[a].failover_urls = ["http://b.test/index.m3u8"]
    old_link = (await hls_links(client, a))[0]
    assert await manager._try_update_failover_url(a, "synthetic-acceptance")
    new_link = (await hls_links(client, a))[0]
    assert (await client.get(new_link)).status_code == 200
    assert captured[-1][0] == "http://b.test/seg.ts"
    assert captured[-1][1]["x-provider-key"] == "synthetic-a"
    assert (await client.get(old_link)).status_code == 200
    assert captured[-1][0] == "http://a.test/seg.ts"
    assert captured[-1][1]["x-provider-key"] == "synthetic-a"

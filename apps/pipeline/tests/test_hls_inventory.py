from __future__ import annotations

import pytest

from temnia_pipeline.media.hls_inventory import playlist_references, validate_inventory


@pytest.mark.parametrize(
    "reference", ["../outside.mp4", "/outside.mp4", "https://other/x", "%2e%2e/x", "x?token=y"]
)
def test_hls_references_cannot_escape_the_ladder(reference: str) -> None:
    with pytest.raises(ValueError, match="unsafe HLS"):
        playlist_references("master.m3u8", f"#EXTM3U\n{reference}\n", {})


def test_iframe_init_and_media_ranges_must_fit_the_named_object() -> None:
    playlist = (
        '#EXTM3U\n#EXT-X-MAP:URI="iframes.mp4",BYTERANGE="20@0"\n'
        "#EXTINF:2,\n#EXT-X-BYTERANGE:30@20\niframes.mp4\n"
        "#EXTINF:2,\n#EXT-X-BYTERANGE:30\niframes.mp4\n#EXT-X-ENDLIST\n"
    )
    assert playlist_references("iframes/index.m3u8", playlist, {"iframes/iframes.mp4": 80}) == {
        "iframes/iframes.mp4"
    }
    with pytest.raises(ValueError, match="byte range"):
        playlist_references("iframes/index.m3u8", playlist, {"iframes/iframes.mp4": 79})


def test_a_master_must_reference_every_rendition_even_if_the_file_exists() -> None:
    playlists = {"master.m3u8": "#EXTM3U\na/index.m3u8\n", "a/index.m3u8": "a", "b/index.m3u8": "b"}
    sizes = {name: len(text) for name, text in playlists.items()}
    with pytest.raises(ValueError, match="expected renditions"):
        validate_inventory(sizes, playlists, {"a", "b"})

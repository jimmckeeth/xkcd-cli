import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch, call
import pytest
from typer.testing import CliRunner

from .xkcd import (
    app,
    Cache,
    XkcdComicMeta,
    XkcdComic,
    _invert_image,
    fetch_xkcd_archive,
    fetch_xkcd_comic,
    _update_cache,
    _update_cache_if_outdated,
)

ARCHIVE_HTML = """
<html><body>
<div id="middleContainer">
  <a href="/3/">Forgot to Hit Send</a>
  <a href="/2/">Game AIs</a>
  <a href="/1/">Barrel - Part 1</a>
</div>
</body></html>
"""

COMIC_HTML = """
<html><body>
<div id="comic">
  <img src="//imgs.xkcd.com/comics/barrel_cropped_(1).jpg"
       title="Don't we all." />
</div>
</body></html>
"""

IMG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8  # minimal PNG-like bytes


def _make_response(text: str = "", content: bytes = b"", status: int = 200) -> Mock:
    r = Mock()
    r.text = text
    r.raise_for_status = Mock()
    r.__iter__ = Mock(return_value=iter([content] if content else []))
    return r


def _make_cache(tmp_path: Path, age: timedelta = timedelta(hours=1)) -> Path:
    cache_file = tmp_path / "cache.json"
    cache = Cache(
        last_updated=datetime.now(timezone.utc) - age,
        comics=[
            XkcdComicMeta(id=3, href="/3/", title="Forgot to Hit Send"),
            XkcdComicMeta(id=2, href="/2/", title="Game AIs"),
            XkcdComicMeta(id=1, href="/1/", title="Barrel - Part 1"),
        ],
    )
    cache.write(cache_file)
    return cache_file


# ---------------------------------------------------------------------------
# fetch_xkcd_archive
# ---------------------------------------------------------------------------


def test_fetch_xkcd_archive_parses_comics():
    with patch("xkcd_cli.xkcd.requests.get") as mock_get:
        mock_get.return_value = _make_response(text=ARCHIVE_HTML)
        comics = fetch_xkcd_archive()

    assert len(comics) == 3
    assert comics[0] == XkcdComicMeta(id=3, href="/3/", title="Forgot to Hit Send")
    assert comics[2] == XkcdComicMeta(id=1, href="/1/", title="Barrel - Part 1")


# ---------------------------------------------------------------------------
# fetch_xkcd_comic
# ---------------------------------------------------------------------------


def test_fetch_xkcd_comic_parses_img_and_subtext():
    meta = XkcdComicMeta(id=1, href="/1/", title="Barrel - Part 1")
    with patch("xkcd_cli.xkcd.requests.get") as mock_get:
        mock_get.return_value = _make_response(text=COMIC_HTML)
        comic = fetch_xkcd_comic(meta)

    assert comic.id == 1
    assert comic.title == "Barrel - Part 1"
    assert comic.img_src == "https://imgs.xkcd.com/comics/barrel_cropped_(1).jpg"
    assert comic.subtext == "Don't we all."


# ---------------------------------------------------------------------------
# Cache read / write
# ---------------------------------------------------------------------------


def test_cache_roundtrip(tmp_path: Path):
    cache_file = tmp_path / "cache.json"
    original = Cache(
        last_updated=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        comics=[XkcdComicMeta(id=42, href="/42/", title="Geeks and Nerds")],
    )
    original.write(cache_file)
    loaded = Cache.read(cache_file)

    assert loaded.last_updated == original.last_updated
    assert len(loaded.comics) == 1
    assert loaded.comics[0] == original.comics[0]


# ---------------------------------------------------------------------------
# _update_cache_if_outdated
# ---------------------------------------------------------------------------


def test_update_cache_if_outdated_skips_when_fresh(tmp_path: Path):
    cache_file = _make_cache(tmp_path, age=timedelta(hours=1))
    with patch("xkcd_cli.xkcd.requests.get") as mock_get:
        result = _update_cache_if_outdated(cache_file)

    mock_get.assert_not_called()
    assert len(result.comics) == 3


def test_update_cache_if_outdated_refreshes_when_stale(tmp_path: Path):
    cache_file = _make_cache(tmp_path, age=timedelta(hours=25))
    with patch("xkcd_cli.xkcd.requests.get") as mock_get:
        mock_get.return_value = _make_response(text=ARCHIVE_HTML)
        result = _update_cache_if_outdated(cache_file)

    mock_get.assert_called_once()
    assert len(result.comics) == 3


# ---------------------------------------------------------------------------
# CLI: show
# ---------------------------------------------------------------------------

runner = CliRunner()


def _show_mocks(tmp_path: Path):
    """Return context managers that mock network + file-open for show tests."""
    cache_file = _make_cache(tmp_path)
    return cache_file


@pytest.fixture()
def mock_network_and_open():
    """Patch requests.get for comic + image, and the platform file opener."""
    with (
        patch("xkcd_cli.xkcd.requests.get") as mock_get,
        patch("os.startfile", create=True) as mock_startfile,
        patch("xkcd_cli.xkcd.subprocess.run") as mock_subp,
    ):
        mock_get.side_effect = [
            _make_response(text=COMIC_HTML),   # comic page
            _make_response(content=IMG_BYTES), # image download
        ]
        yield mock_get, mock_startfile, mock_subp


def test_show_latest(tmp_path: Path, mock_network_and_open):
    cache_file = _show_mocks(tmp_path)
    mock_get, mock_startfile, mock_subp = mock_network_and_open

    result = runner.invoke(app, [
        "show", "--latest", "--no-terminal-graphics",
        "--cache-filename", str(cache_file),
    ])

    assert result.exit_code == 0, result.output
    assert "Forgot to Hit Send" in result.output
    assert "Don't we all." in result.output


def test_show_by_id(tmp_path: Path, mock_network_and_open):
    cache_file = _show_mocks(tmp_path)
    mock_get, _, _ = mock_network_and_open

    result = runner.invoke(app, [
        "show", "--comic-id", "1", "--no-terminal-graphics",
        "--cache-filename", str(cache_file),
    ])

    assert result.exit_code == 0, result.output
    assert "Barrel - Part 1" in result.output


def test_show_unknown_id(tmp_path: Path):
    cache_file = _show_mocks(tmp_path)

    result = runner.invoke(app, [
        "show", "--comic-id", "9999", "--no-terminal-graphics",
        "--cache-filename", str(cache_file),
    ])

    assert result.exit_code != 0
    assert "9999" in result.output


def test_show_no_cache(tmp_path: Path, mock_network_and_open):
    mock_get, _, _ = mock_network_and_open
    # no-cache: first get is the archive, second is the comic, third is the image
    mock_get.side_effect = [
        _make_response(text=ARCHIVE_HTML),
        _make_response(text=COMIC_HTML),
        _make_response(content=IMG_BYTES),
    ]
    cache_file = tmp_path / "nonexistent.json"

    result = runner.invoke(app, [
        "show", "--latest", "--no-terminal-graphics", "--no-cache",
        "--cache-filename", str(cache_file),
    ])

    assert result.exit_code == 0, result.output
    assert "Forgot to Hit Send" in result.output


# ---------------------------------------------------------------------------
# CLI: update-cache
# ---------------------------------------------------------------------------


def test_show_no_fzf_gives_helpful_error(tmp_path: Path):
    cache_file = _show_mocks(tmp_path)

    result = runner.invoke(app, [
        "show", "--no-terminal-graphics",
        "--cache-filename", str(cache_file),
        "--fzf-cmd", "__fzf_not_installed__",  # simulate fzf not found
    ])

    assert result.exit_code != 0
    assert "fzf" in result.output.lower()


def test_update_cache_command(tmp_path: Path):
    cache_file = tmp_path / "cache.json"

    with patch("xkcd_cli.xkcd.requests.get") as mock_get:
        mock_get.return_value = _make_response(text=ARCHIVE_HTML)
        result = runner.invoke(app, ["update-cache", "--cache-filename", str(cache_file)])

    assert result.exit_code == 0, result.output
    assert cache_file.exists()
    loaded = Cache.read(cache_file)
    assert len(loaded.comics) == 3


# ---------------------------------------------------------------------------
# _invert_image
# ---------------------------------------------------------------------------

def test_invert_image_roundtrip():
    from pathlib import Path as _Path
    png_bytes = _Path("tests/assets/1x1.png").read_bytes()
    inverted = _invert_image(png_bytes)
    # Inverting twice should return to original pixel values.
    double_inverted = _invert_image(inverted)

    from PIL import Image
    import io
    orig = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    result = Image.open(io.BytesIO(double_inverted)).convert("RGBA")
    assert list(orig.getdata()) == list(result.getdata())


def test_invert_image_changes_pixels():
    from pathlib import Path as _Path
    import io
    from PIL import Image

    png_bytes = _Path("tests/assets/1x1.png").read_bytes()
    inverted = _invert_image(png_bytes)

    orig_pixel = Image.open(io.BytesIO(png_bytes)).convert("RGBA").getpixel((0, 0))
    inv_pixel = Image.open(io.BytesIO(inverted)).convert("RGBA").getpixel((0, 0))

    # RGB channels should be inverted (255 - original); alpha unchanged.
    assert inv_pixel[0] == 255 - orig_pixel[0]
    assert inv_pixel[1] == 255 - orig_pixel[1]
    assert inv_pixel[2] == 255 - orig_pixel[2]
    assert inv_pixel[3] == orig_pixel[3]


# ---------------------------------------------------------------------------
# CLI: --invert / --no-invert / auto-detect
# ---------------------------------------------------------------------------

def test_show_explicit_invert(tmp_path: Path, mock_network_and_open):
    cache_file = _show_mocks(tmp_path)
    mock_get, _, _ = mock_network_and_open
    from pathlib import Path as _Path
    real_png = _Path("tests/assets/1x1.png").read_bytes()
    mock_get.side_effect = [
        _make_response(text=COMIC_HTML),
        _make_response(content=real_png),
    ]

    with patch("xkcd_cli.xkcd._invert_image", wraps=_invert_image) as mock_invert:
        result = runner.invoke(app, [
            "show", "--latest", "--no-terminal-graphics", "--invert",
            "--cache-filename", str(cache_file),
        ])

    assert result.exit_code == 0, result.output
    mock_invert.assert_called_once()


def test_show_no_invert_skips_inversion(tmp_path: Path, mock_network_and_open):
    cache_file = _show_mocks(tmp_path)

    with patch("xkcd_cli.xkcd._invert_image") as mock_invert:
        result = runner.invoke(app, [
            "show", "--latest", "--no-terminal-graphics", "--no-invert",
            "--cache-filename", str(cache_file),
        ])

    assert result.exit_code == 0, result.output
    mock_invert.assert_not_called()


def test_show_auto_inverts_on_dark_background(tmp_path: Path, mock_network_and_open):
    cache_file = _show_mocks(tmp_path)

    with patch("xkcd_cli.xkcd.IV") as MockIV, \
         patch("xkcd_cli.xkcd._invert_image") as mock_invert:
        instance = MockIV.return_value
        instance.protocol = None        # no terminal graphics
        instance.is_dark_background.return_value = True
        mock_invert.return_value = IMG_BYTES

        result = runner.invoke(app, [
            "show", "--latest", "--no-terminal-graphics",
            "--cache-filename", str(cache_file),
        ])

    assert result.exit_code == 0, result.output
    mock_invert.assert_called_once()


def test_show_auto_skips_invert_on_light_background(tmp_path: Path, mock_network_and_open):
    cache_file = _show_mocks(tmp_path)

    with patch("xkcd_cli.xkcd.IV") as MockIV, \
         patch("xkcd_cli.xkcd._invert_image") as mock_invert:
        instance = MockIV.return_value
        instance.protocol = None
        instance.is_dark_background.return_value = False

        result = runner.invoke(app, [
            "show", "--latest", "--no-terminal-graphics",
            "--cache-filename", str(cache_file),
        ])

    assert result.exit_code == 0, result.output
    mock_invert.assert_not_called()

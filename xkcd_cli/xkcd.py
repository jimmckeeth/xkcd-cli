from bs4 import BeautifulSoup, Tag
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import randint
from subprocess import Popen, PIPE
from textwrap import wrap
from typing import List, Tuple, Optional
import json
import os
import requests
import shutil
import subprocess
import sys
import tempfile
import typer
from .iv import IV

BASE_URL = "https://xkcd.com"
ARCHIVE_ENDPOINT = "/archive/"
CACHE_DIR = Path("~", ".cache", "xkcd-cli").expanduser()
CACHE_PATH = Path(CACHE_DIR, "cache.json")
TERM_MAX_WIDTH_CHARS = 80

app = typer.Typer()


@app.callback()
def callback():
    """
    xkcd terminal viewer

    This tool fetches xkcd comics from upstream and allows users to grep a comic
    by selecting it in the terminal.

    It relies on fzf for fuzzy searching the comic titles and a terminal with support
    for graphics to render the images in the terminal.
    """
    pass


def setup():
    os.makedirs(CACHE_DIR, exist_ok=True)


@dataclass
class XkcdComicMeta:
    """
    Meta information of a xkcd comic as described in the archive.
    """

    id: int
    href: str
    title: str


@dataclass
class XkcdComic(XkcdComicMeta):
    """
    Full information of a xkcd comic.
    """

    img_src: str
    subtext: str


@dataclass
class Cache:
    """
    Internal representation of all xkcd comics metadata from the official archive.
    """

    last_updated: datetime
    comics: List[XkcdComicMeta]

    @classmethod
    def read(cls, path: Path):
        content = json.load(open(path))
        last_updated = datetime.fromisoformat(content["last_updated"])
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)
        comics = [XkcdComicMeta(**c) for c in content["comics"]]
        return Cache(last_updated=last_updated, comics=comics)

    def write(self, path: Path):
        with open(path, "w") as f:
            f.write(json.dumps(asdict(self), default=str))


def _invert_image(data: bytes) -> bytes:
    """Invert image pixel colors, preserving the alpha channel."""
    from PIL import Image, ImageOps
    import io as _io

    image = Image.open(_io.BytesIO(data)).convert("RGBA")
    r, g, b, a = image.split()
    inverted = ImageOps.invert(Image.merge("RGB", (r, g, b)))
    result = Image.merge("RGBA", (*inverted.split(), a))
    buf = _io.BytesIO()
    result.save(buf, format="PNG")
    return buf.getvalue()


def fetch_xkcd_archive() -> List[XkcdComicMeta]:
    """
    Fetches all xkcd comic meta information from the archive
    page: https://xkcd.com/archive/
    Since a single page contains all historic xkcds, no pagination logic is needed.
    """
    r = requests.get(BASE_URL + ARCHIVE_ENDPOINT)
    r.raise_for_status()
    text = r.text

    soup = BeautifulSoup(text, "html.parser")
    comics: List[XkcdComicMeta] = []

    container = soup.find(id="middleContainer")
    assert isinstance(container, Tag)

    for el in container.find_all("a"):
        href = el.get("href")
        title = el.get_text()
        cid = int(href.replace("/", ""))

        meta = XkcdComicMeta(id=cid, href=href, title=title)
        comics.append(meta)

    return comics


def fetch_xkcd_comic(comic: XkcdComicMeta) -> XkcdComic:
    """
    Fetch an individual xkcd comic from upstream.
    """
    r = requests.get(BASE_URL + comic.href)
    r.raise_for_status()
    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    comic_soup = soup.find(id="comic")
    assert isinstance(comic_soup, Tag)
    img = comic_soup.find("img")
    assert isinstance(img, Tag)
    img_src = img.get("src")
    assert isinstance(img_src, str)
    img_src = "https:" + img_src
    subtext = img.get("title")
    assert isinstance(subtext, str)
    return XkcdComic(
        title=comic.title,
        id=comic.id,
        href=comic.href,
        img_src=img_src,
        subtext=subtext,
    )


def choice_fzf(fzf_cmd: Path, comics: List[XkcdComicMeta]) -> Tuple[bytes, bytes]:
    """
    Uses fzf to select a comic by its title.
    """
    titles = [str(c.id) + ": " + c.title for c in comics]
    content = "\n".join(titles)

    p = Popen([fzf_cmd], stdin=PIPE, stdout=PIPE)
    assert p.stdin
    p.stdin.write(content.encode())
    p.stdin.flush()
    stdout, stderr = p.communicate()
    return stdout, stderr


def _update_cache_if_outdated(
    cache_filename: Path,
    cache_timeout: timedelta = timedelta(hours=24),
) -> Cache:
    """
    Updates the cache if it is older than the last updated timestamp plus the
    cache_timeout.
    """
    cache = Cache.read(cache_filename)
    if datetime.now(timezone.utc) > (cache.last_updated + cache_timeout):
        # Transparently create cache for the user if the cache is older than the
        # cache timeout.
        cache = _update_cache(cache_filename)
    return cache


def _update_cache(cache_filename: Path) -> Cache:
    """
    Updates the cache by fetching all comics from the xkcd archive page and
    updating the cache file on disk.
    """
    dt = datetime.now(timezone.utc)
    comics = fetch_xkcd_archive()

    cache = Cache(last_updated=dt, comics=comics)
    cache.write(cache_filename)
    return cache


@app.command()
def update_cache(
    cache_filename: Path = typer.Option(
        CACHE_PATH,
        writable=True,
        help="Path to the cache file.",
    )
):
    """
    Updates the cache file by fetching the latest comics from the xkcd archive website.
    """
    _update_cache(cache_filename)
    typer.echo("Cache updated 👍")


@app.command()
def show(
    terminal_graphics: bool = typer.Option(
        True,
        help=(
            "Defines if the output will be displayed in terminal by "
            "rendering the image in the terminal window if supported."
        ),
    ),
    fzf_cmd: Path = typer.Option(
        shutil.which("fzf") or os.getenv("FZF_CMD"),
        help="Path to the fzf tool",
    ),
    width: int = typer.Option(
        -1,
        help="Defines the target width of the comic when rendered in the terminal.",
    ),
    terminal_scale_up: bool = typer.Option(
        True,
        help=(
            "Scales the image up to max possible width if terminal graphics is being "
            "used. Does not have any effect if 'width' is set to an explicit value."
        ),
    ),
    latest: bool = typer.Option(
        False,
        help=(
            "Fetches and renders the latest xkcd without going through a selection first."
        ),
    ),
    random: bool = typer.Option(False, help="Fetches and renders a random xkcd comic."),
    comic_id: Optional[int] = typer.Option(
        None,
        help="Renders a comic with a certain ID.",
    ),
    cache: bool = typer.Option(
        True,
        help=(
            "Defines if a cache should be used for listing all available xkcd "
            "comics. Otherwise calls the xkcd archive endpoint to gather the list "
            "of comics (slower)."
        ),
    ),
    cache_filename: Path = typer.Option(
        CACHE_PATH,
        exists=False,
        readable=True,
        help="Path to the cache file.",
    ),
    invert: Optional[bool] = typer.Option(
        None,
        "--invert/--no-invert",
        help=(
            "Invert image colors. When omitted, auto-detected: inverts if the "
            "terminal background is dark."
        ),
    ),
):
    """
    Show an individual xkcd comic.
    """
    iv: Optional[IV] = None
    if terminal_graphics:
        iv = IV("auto")
        if not iv.protocol:
            terminal_graphics = False

    # Resolve invert: auto-detect from terminal background when not explicitly set.
    should_invert: bool
    if invert is None:
        try:
            _iv = iv if iv is not None else IV(None)
            should_invert = _iv.is_dark_background()
        except Exception:
            should_invert = False
    else:
        should_invert = invert
    if cache:
        if not cache_filename.exists():
            # Transparently create cache for the user if cache does not yet exist.
            _update_cache(cache_filename)

        content = _update_cache_if_outdated(cache_filename)
        comics = content.comics
    else:
        # Bypass cache and read fetched results directly
        comics = fetch_xkcd_archive()

    if random:
        meta = comics[randint(0, len(comics))]
    elif latest:
        meta = comics[0]
    elif comic_id is not None:
        try:
            meta = next(c for c in comics if c.id == comic_id)
        except StopIteration:
            typer.echo(
                f"""\
Comic with ID {comic_id} is unknown. Sometimes this happens because the cache is \
outdated. Please use the 'update-cache' command to fetch most recent comics from \
xkcd upstream.""",
                err=True,
            )
            sys.exit(1)
    else:
        if fzf_cmd is None or shutil.which(str(fzf_cmd)) is None:
            typer.echo(
                "fzf not found. Install fzf or set the FZF_CMD environment variable, "
                "or use --latest, --random, or --comic-id to select a comic directly.",
                err=True,
            )
            sys.exit(1)
        stdout, _ = choice_fzf(fzf_cmd, comics)
        choice = stdout.decode("UTF-8").strip()
        try:
            choice_title = choice.split(":")[1].strip()
        except IndexError:
            # Happens if user uses ctrl+c to exit selection
            typer.echo("Unknown index. Abort.", err=True)
            sys.exit(1)

        meta = next(c for c in comics if c.title == choice_title)
    comic = fetch_xkcd_comic(meta)

    wrapped_title = "\n".join(wrap(comic.title, width=TERM_MAX_WIDTH_CHARS))
    typer.echo(typer.style(wrapped_title, bold=True) + f" ({comic.id})")

    r = requests.get(comic.img_src, stream=True)
    r.raise_for_status()
    img_data = b"".join(r)
    if should_invert:
        img_data = _invert_image(img_data)

    if terminal_graphics:
        # show_image reads the file synchronously, so a TemporaryDirectory is safe.
        with tempfile.TemporaryDirectory() as tempdir:
            tmp_img_path = Path(tempdir, str(comic.id) + ".png")
            tmp_img_path.write_bytes(img_data)
            assert iv is not None  # safe since iv has been initialized above
            fitscreen = width <= 0
            iv.show_image(
                str(tmp_img_path),
                w=width,
                newline=True,
                fitwidth=fitscreen,
                fitheight=fitscreen,
                upscale=terminal_scale_up,
            )
    else:
        # os.startfile / xdg-open are non-blocking, so the file must outlive this
        # process's cleanup. Use mkstemp so the file persists until the viewer opens it.
        fd, viewer_path = tempfile.mkstemp(suffix=f"_{comic.id}.png")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(img_data)
            if sys.platform == "win32":
                os.startfile(viewer_path)
                # Leave file for the viewer; Windows temp dir is cleaned up by the OS.
            else:
                subprocess.run(["xdg-open", viewer_path], stdout=None, stderr=None)
                Path(viewer_path).unlink(missing_ok=True)
        except Exception:
            Path(viewer_path).unlink(missing_ok=True)
            raise
    typer.echo("\n".join(wrap(comic.subtext, width=TERM_MAX_WIDTH_CHARS)))


def main():
    setup()
    app()


if __name__ == "__main__":
    main()

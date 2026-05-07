# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`dcs-xkcd-cli` is a Python CLI tool that fetches and displays xkcd comics in the terminal. It uses `fzf` for fuzzy searching and supports rendering images directly in terminals that support Kitty, iTerm, or sixel graphics protocols.

The CLI entry point is `xkcd` (mapped to `xkcd_cli.xkcd:main` in pyproject.toml). The two subcommands are `show` and `update-cache`.

## Setup

This project uses [Poetry](https://python-poetry.org/) for dependency management. `poetry.toml` configures Poetry to use an in-project `.venv`.

```powershell
# Install poetry if not present (Windows)
pip install poetry --user

poetry install
```

## Common Commands

```powershell
# Format code (auto-fix)
poetry run python -m black .

# Check formatting without fixing
poetry run python -m black --check --diff .

# Static type checking
poetry run python -m pyright

# Run tests with coverage
poetry run python -m pytest --cov="xkcd_cli/" --cov-report term --cov-report html

# Run a single test file
poetry run python -m pytest xkcd_cli/iv_test.py

# Run a single test by name
poetry run python -m pytest -k "test_name"
```

## Code Architecture

The package lives in `xkcd_cli/` with two main modules:

- **`xkcd.py`** — The CLI application (Typer-based). Handles archive scraping via BeautifulSoup, a JSON cache at `~/.cache/xkcd-cli/cache.json` (auto-refreshed every 24h), comic fetching, fzf integration for selection, and image download/display. Dataclasses: `XkcdComicMeta`, `XkcdComic`, `Cache`.

- **`iv.py`** — Image viewer abstraction (`IV` class). Auto-detects terminal graphics protocol (kitty, kitty+, iterm, sixel) and renders images in-terminal. Used by `xkcd.py` to display comics. Tests are co-located in `iv_test.py`.

Tests live in `xkcd_cli/iv_test.py` (co-located with the module) and the `tests/` directory holds assets only.

## Code Style

- Formatter: `black` (line length 88, target Python 3.8)
- Type checker: `pyright` (Python 3.8, Linux platform target)
- CI runs lint, type check, and tests on push/PR to `main` and `develop`
- Minimum Python version: 3.8

## Publishing

The package is published to PyPI as `dcs-xkcd-cli`. The `publish.yaml` workflow triggers only on semver-matching tags.

#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "mcp>=2.3.0",
#     "playwright>=1.45",
#     "html2text>=2025.4.15",
# ]
# ///
"""Compare saved X and LinkedIn page screenshots with their current versions."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent  # ../brepl.py
PROFILE_DIR = SCRIPT_DIR / ".brepl-profile"
THRESHOLDS = {"xcom": 80, "linkedin": 90}  # min matching-pixel %

sys.path.insert(0, str(PROJECT_ROOT))

from brepl import Brepl


def read_entries(path: Path) -> list[str]:
    try:
        entries = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    except OSError as e:
        raise RuntimeError(f"cannot read {path}: {e}") from e
    entries = [entry for entry in entries if entry and not entry.startswith("#")]
    for entry in entries:
        if entry in {".", ".."} or "/" in entry or "\\" in entry or any(c.isspace() for c in entry):
            raise RuntimeError(f"invalid account identifier in {path}: {entry!r}")
    return entries


def navigate(browser: Brepl, target: str) -> None:
    result = browser.go(target).strip()
    if result.startswith(("error:", "no current site", "usage:")):
        raise RuntimeError(result)
    current_url = browser.url()
    if urlparse(current_url).scheme not in {"http", "https"}:
        raise RuntimeError(f"navigation did not reach a web page: {current_url or result}")


def matching_percent(result: str) -> int:
    match = re.fullmatch(r"(\d{1,3})%", result.strip())
    if not match or int(match.group(1)) > 100:
        raise RuntimeError(f"unexpected screenshot comparison result: {result.strip()!r}")
    return int(match.group(1))


def check_site(
    browser: Brepl,
    site: str,
    base_target: str,
    entries: list[str],
    page_url,
    encode=lambda entry: entry,
) -> int:
    if not entries:
        return 0
    try:
        navigate(browser, base_target)
    except Exception as e:
        print(f"ERROR {site}: cannot open {base_target}: {e}", file=sys.stderr)
        return 1

    failures = 0
    for entry in map(encode, entries):
        url = page_url(entry)
        image = f"{site}/{entry}.png"
        try:
            target = f"/{entry}" if site == "xcom" else url
            navigate(browser, target)
            result = browser.run(f"p 1 ?{image}")
            score = matching_percent(result)
            print(f"{site}/{entry}: {score}% match")
            if score < THRESHOLDS[site]:
                print(browser.run(f"n changed {url}").strip())
        except Exception as e:
            print(f"ERROR {site}/{entry}: {e}", file=sys.stderr)
            failures += 1
    return failures


def main() -> int:
    try:
        x_accounts = read_entries(SCRIPT_DIR / "xcom" / "000.txt")
        linkedin_accounts = read_entries(SCRIPT_DIR / "linkedin" / "000.txt")
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    os.chdir(SCRIPT_DIR)
    try:
        with Brepl(profile=str(PROFILE_DIR)) as browser:
            failures = check_site(
                browser,
                "xcom",
                "x.com",
                x_accounts,
                lambda entry: f"https://x.com/{entry}",
            )
            failures += check_site(
                browser,
                "linkedin",
                "linkedin.com",
                linkedin_accounts,
                lambda entry: f"https://linkedin.com/in/{entry}/recent-activity/all",
                lambda entry: quote(unquote(entry), safe=""),  # é -> %C3%A9, no double-encoding
            )
    except Exception as e:
        print(f"ERROR: cannot start brepl: {e}", file=sys.stderr)
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

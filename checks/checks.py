#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.3.0"]
# ///
"""Notify when the latest X / LinkedIn post of the listed accounts changes (see checks.md).

Usage: ./checks.py [path/to/.mcp.json]   (default ../.mcp.json)
Uses the brepl already running; never starts one.
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DIR = Path(__file__).resolve().parent
PAUSE = 30  # seconds between checks
SITES = {  # site -> (host, url path prefix -> saved name prefix, extractor)
    "xcom": ("x.com", {"": ""}, DIR / "xcom-latest.py"),
    "linkedin": ("linkedin.com", {"in/": "", "company/": "company-"}, DIR / "li-latest.py"),
}


def page_url(site: str, name: str) -> str:
    if site == "xcom":
        return f"https://x.com/{name}"
    if name.startswith("company-"):
        return f"https://www.linkedin.com/company/{name.removeprefix('company-')}/posts/?feedView=all"
    return f"https://linkedin.com/in/{name}/recent-activity/all"


def read_names(site: str) -> list[str]:
    host, prefixes, _ = SITES[site]
    path = DIR / site / "000.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        print(f"ERROR cannot read {path}: {e}", file=sys.stderr)
        return []
    names = []
    for line in map(str.strip, lines):
        if not line or line.startswith("#"):
            continue
        name = line
        if "/" in line:
            u, name = urlparse(line), ""
            part = u.path.lstrip("/")
            if u.scheme in ("http", "https") and u.netloc.removeprefix("www.") == host:
                for prefix, tag in prefixes.items():
                    if part.startswith(prefix) and part[len(prefix):].split("/")[0]:
                        name = tag + part[len(prefix):].split("/")[0]
                        break
        name = unquote(name)
        if not name or name in (".", "..") or any(c.isspace() or c == "\\" for c in name):
            print(f"ERROR {path}: invalid line {line!r}", file=sys.stderr)
            continue
        names.append(name)
    return names


def server_url(mcp_json: Path) -> str:
    return json.loads(mcp_json.read_text())["mcpServers"]["brepl"]["url"]


async def repl(session: ClientSession, line: str) -> str:
    result = await session.call_tool("repl", {"command": line})
    return "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text").strip()


async def check(session: ClientSession, site: str, name: str) -> None:
    url = page_url(site, name)
    out = await repl(session, f"g {url}")
    if out.startswith(("error:", "usage:")):
        raise RuntimeError(out)
    saved = DIR / site / f"{name}.html"
    verdict = await repl(session, f"d ?{saved} {SITES[site][2]}")
    if verdict not in ("true", "false"):
        raise RuntimeError(f"cannot compare the latest post: {verdict!r}")
    print(f"{site}/{name}: {'same' if verdict == 'true' else 'NEW POST'}", flush=True)
    if verdict == "false":
        print(await repl(session, f"n changed {url}"), flush=True)


async def main() -> int:
    mcp_json = Path(sys.argv[1]) if len(sys.argv) > 1 else DIR.parent / ".mcp.json"
    try:
        url = server_url(mcp_json)
    except (OSError, KeyError, ValueError) as e:
        print(f"ERROR no brepl server in {mcp_json}: {e}", file=sys.stderr)
        return 1
    async with streamable_http_client(url) as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            while True:
                todo = [(s, n) for s in SITES for n in read_names(s)]
                random.shuffle(todo)
                if not todo:
                    print("nothing to check", file=sys.stderr)
                for site, name in todo:
                    try:
                        await check(session, site, name)
                    except Exception as e:
                        print(f"ERROR {site}/{name}: {e}", file=sys.stderr, flush=True)
                    await asyncio.sleep(PAUSE)
                if not todo:
                    await asyncio.sleep(PAUSE)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
    except Exception as e:  # e.g. brepl not running
        print(f"ERROR brepl not reachable (start it first): {e!r}", file=sys.stderr)
        sys.exit(1)

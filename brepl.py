#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "mcp>=2.3.0",
#     "playwright>=1.45",
#     "html2text>=2025.4.15",
# ]
# ///
# Copyright 2026 Michele Sciabarrà
# SPDX-License-Identifier: Apache-2.0
"""brepl: a REPL-driven headless Chrome browser."""

import asyncio
import base64
import builtins
import io
import os
import queue
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from urllib.parse import urlparse

import json
import random

import html2text
from playwright.sync_api import Error as PWError
from playwright.sync_api import sync_playwright

HELP = """\
  ?                    show the commands
  g <url>|<num>        go to url (https:// and .com/.net/.org tried automatically)
                       or to link <num> from the last 'l'
  g /<path>            go to path on the current origin (g /login)
  g /<substr>/         go to the first link whose 'l' line ([text] url) contains
                       substr, ignoring case
  G <like g>           g, then p (show the page)
  u                    print current url
  p                    mark buttons (red, numbered), fields (blue, named),
                       and links (green, numbered as in 'l', saved to link.txt).
                       Show the full-page
                       screenshot inline (blank parts skipped; saved as
                       page1.png ... pageN.png) + list in page.txt
  p [n] / pp [n]       n = parts to show and save (0 = all, default 1)
  p <n> <file>         make all the parts, show part n and copy it (png) to file
  p <n> ?<file>        whole-number percentage of pixels matching the png in file,
                       which is then replaced by part n
                       (missing file: create it from part n, print 100%)
  pp                   like p, with the page's images replaced by white space
  l [substr|/regex/]   list internal urls as [text] url, numbered, saved to link.txt
                       (only those whose path or anchor text contains substr,
                       or matches the regex, ignoring case)
  c <num>|<text>|/regex/ [field=value ...]
                       fill fields (name prefix ok), then click button <num>
                       from the last p/pp, the button containing <text>, or the
                       first one whose text matches the regex (ignoring case)
  s field=value ...    fill fields, then submit (Enter in the last one)
  d [file]             the page as markdown (no images): printed, or written to file;
                       always saved as page.html (rendered DOM) and page.md
  d <file> <command>   pipe the page html to the shell command, write its output to
                       file ('-' prints it), e.g. d out.txt grep -c href | tr -d ' '
  d ?<file> [command]  true if the output (or markdown) equals file, else false;
                       file is then replaced by the new output
  h [n]                no n: numbered history for the current site
                       n > 0: go to that entry; n < 0: step back n pages (h -1 = back)
  @                    switch between headless and headed (relaunches Chrome; the
                       profile, open tabs, current page and history are kept)
  n <message>          notify (osascript) or print; a link in it makes it clickable
  q                    quit"""

MCP_PORT = 12549
MCP_NAME = "brepl"
PROMPT = "brepl> "  # "brepl:<port>> " while the MCP server runs
PROFILE_DIR = ".brepl-profile"  # --profile without =DIR
LOCAL_PORTS = (12000, 14000)  # --local picks a free port at random in this range
# MCP screenshots: the full page as several images, top to bottom, so each stays legible
# after a client downscales it; parts overlap so a badge on a boundary is whole in one
MCP_TILE = 1600
# after p/pp: drop the hidden-media style, and the marks too unless headed (keep=true)
UNMARK_JS = """keep => document.querySelectorAll(keep ? '#brepl-noimg' : '.brepl-ov, #brepl-noimg')
  .forEach(e => e.remove())"""
MCP_OVERLAP = 40
MCP_MAX_TILES = 20

# Output capture: commands print as usual; while an MCP call runs, the worker thread
# sets _tls.buf / _tls.images and print()/iterm2_image() write there instead of the terminal.
_tls = threading.local()


def print(*args, **kw):  # noqa: A001  (shadows the builtin on purpose)
    buf = getattr(_tls, "buf", None)
    if buf is not None and "file" not in kw:
        kw["file"] = buf
    builtins.print(*args, **kw)


def capturing() -> bool:
    return getattr(_tls, "buf", None) is not None


TIMEOUT = 5000
SUFFIXES = (".com", ".net", ".org")
RATE, WINDOW = 4, 60.0  # at most RATE requests (page loads) per WINDOW seconds
_requests: list[float] = []


def throttle() -> None:
    """Block until a request fits the rate limit, then record it."""
    now = time.monotonic()
    _requests[:] = [t for t in _requests if now - t < WINDOW]
    if len(_requests) >= RATE:
        wait = _requests[0] + WINDOW - now
        print(f"(waiting {wait:.0f}s: max {RATE} requests/min)", flush=True)
        time.sleep(wait)
        _requests.pop(0)
    _requests.append(time.monotonic())


def goto(page, url: str) -> None:
    throttle()
    page.goto(url, wait_until="domcontentloaded")


LINK_FILE = "link.txt"  # last 'l' listing, in the current directory
PAGE_FILE = "page.txt"  # last 'p'/'pp' buttons and fields
HTML_FILE = "page.html"  # last 'd': the rendered DOM
MD_FILE = "page.md"  # last 'd': the same as markdown
SHOT_FILE = re.compile(r"page\d+\.png$")  # last 'p'/'pp' parts: page1.png ... pageN.png

# Scans visible buttons and input fields, tags them with data-brepl-* attributes and,
# when draw is true, overlays red numbered boxes (buttons) / blue named boxes (fields).
SCAN_JS = """
({draw, buttons, links}) => {
  // querySelectorAll that also searches open shadow roots (web components), at any depth
  const roots = [document];
  for (let i = 0; i < roots.length; i++)
    for (const e of roots[i].querySelectorAll('*')) if (e.shadowRoot) roots.push(e.shadowRoot);
  const all = sel => roots.flatMap(r => [...r.querySelectorAll(sel)]);
  // parent, crossing from a shadow root to its host
  const up = e => e.parentElement || (e.getRootNode() instanceof ShadowRoot ? e.getRootNode().host : null);
  document.querySelectorAll('.brepl-ov').forEach(e => e.remove());
  all('[data-brepl-field]').forEach(e => e.removeAttribute('data-brepl-field'));
  if (buttons) all('[data-brepl-id]').forEach(e => e.removeAttribute('data-brepl-id'));
  // laid out at all (fillable / clickable by playwright)
  const visible = el => {
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  // the part of el actually visible in a full-page screenshot, or null: clipped by every
  // overflow-hiding/scrolling ancestor, by the viewport if inside a fixed element, and
  // hidden if (effectively) transparent or tiny
  const shown = el => {
    const r = el.getBoundingClientRect();
    let x1 = r.left, y1 = r.top, x2 = r.right, y2 = r.bottom, fixed = false, opacity = 1;
    for (let a = el; a && a !== document.documentElement; a = up(a)) {
      const cs = getComputedStyle(a);
      opacity *= parseFloat(cs.opacity);
      // a fixed element escapes the overflow of the ancestors above it (modals)
      if (!fixed && a !== el && a !== document.body && /hidden|auto|scroll|clip/.test(cs.overflowX + cs.overflowY)) {
        const q = a.getBoundingClientRect();
        x1 = Math.max(x1, q.left); y1 = Math.max(y1, q.top);
        x2 = Math.min(x2, q.right); y2 = Math.min(y2, q.bottom);
      }
      if (cs.position === 'fixed') fixed = true;
    }
    if (fixed) {
      x1 = Math.max(x1, 0); y1 = Math.max(y1, 0);
      x2 = Math.min(x2, innerWidth); y2 = Math.min(y2, innerHeight);
    }
    if (opacity < 0.05 || x2 - x1 < 3 || y2 - y1 < 3) return null;
    // fixed elements are captured at their viewport position, the rest at page position
    const dx = fixed ? 0 : scrollX, dy = fixed ? 0 : scrollY;
    return {x: x1 + dx, y: y1 + dy, w: x2 - x1, h: y2 - y1};
  };
  // after: badge right after the box (links, so dense text stays readable), 'inside':
  // in the box's top-left corner (images, often packed edge to edge), else above it
  const overlay = (rect, color, label, bw = 2, after = false, ink = 'white') => {
    if (!draw || !rect) return;
    const x = rect.x, y = rect.y, r = {width: rect.w, height: rect.h};
    const base = {position: 'absolute', pointerEvents: 'none', zIndex: '2147483647'};
    const box = document.createElement('div');
    box.className = 'brepl-ov';
    Object.assign(box.style, base, {left: x + 'px', top: y + 'px', width: r.width + 'px',
      height: r.height + 'px', border: bw + 'px solid ' + color, boxSizing: 'border-box'});
    const tag = document.createElement('div');
    tag.className = 'brepl-ov';
    tag.textContent = label;
    Object.assign(tag.style, base, after === 'inside'
      ? {left: x + 'px', top: y + 'px', font: 'bold 14px/18px sans-serif', padding: '0 5px'}
      : after
      ? {left: (x + r.width) + 'px', top: y + 'px', font: 'bold 10px/12px sans-serif', padding: '0 2px'}
      : {left: x + 'px', top: Math.max(0, y - 16) + 'px', font: 'bold 12px/16px sans-serif', padding: '0 4px'},
      {background: color, color: ink, whiteSpace: 'nowrap'});
    document.body.append(box, tag);
  };
  const clip = s => [...(s || '').replace(/\\s+/g, ' ').trim()].slice(0, 60).join('');  // by code point: no split surrogates

  const out = {buttons: [], fields: []};
  const bsel = 'button, [role=button], input[type=button], input[type=submit], input[type=reset]';
  let n = 0;
  for (const el of all(bsel)) {
    if (!visible(el)) continue;
    const rect = shown(el);
    if (buttons) {  // number only buttons that can be seen, so list and image agree
      if (!rect) continue;
      el.setAttribute('data-brepl-id', String(++n));
    }
    const id = el.getAttribute('data-brepl-id');
    if (!id) continue;
    out.buttons.push({id, label: clip(el.innerText || el.value || el.getAttribute('aria-label') || el.title)});
    overlay(rect, 'red', id);
  }
  const fsel = 'input:not([type=hidden]):not([type=button]):not([type=submit]):not([type=reset]):not([type=image]), textarea, select, ' +
    '[contenteditable=""], [contenteditable=true], [contenteditable=plaintext-only]';
  let k = 0;
  for (const el of all(fsel)) {
    if (!visible(el)) continue;
    const editable = el.isContentEditable && !/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName);
    if (editable && up(el) && up(el).isContentEditable) continue;  // only the editor's root
    k++;
    const name = el.name || el.id || el.getAttribute('aria-label') || el.placeholder ||
      el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || ('field' + k);
    el.setAttribute('data-brepl-field', String(k));
    // fields are registered even when not shown (e.g. custom-styled radios) so they can be filled
    const rect = shown(el);
    out.fields.push({key: String(k), name, form: el.form ? [...document.forms].indexOf(el.form) : -1,
                     type: editable ? 'editable' : el.tagName === 'INPUT' ? (el.type || 'text') : el.tagName.toLowerCase(),
                     value: clip(editable ? el.innerText : el.value), shown: !!rect});
    overlay(rect, 'blue', name);
  }
  // links: green, numbered like the 'l' listing (every anchor of a url gets its number)
  if (links) {
    const num = new Map(links.map((u, i) => [u, i + 1]));
    for (const a of all('a[href]')) {
      const n = num.get(a.href.split('#')[0]);
      if (n) overlay(shown(a), '#080', String(n), 1, true);
    }
  }
  return out;
}
"""

SCROLL_JS = """
async () => {
  const step = innerHeight;
  for (let y = 0; y < document.documentElement.scrollHeight && y < 50000; y += step) {
    scrollTo(0, y);
    await new Promise(r => setTimeout(r, 120));
  }
  scrollTo(0, 0);
}
"""


NOIMG_JS = """
() => {
  const st = document.createElement('style');
  st.id = 'brepl-noimg';
  // media keeps its box but turns solid white; background images are dropped
  st.textContent = 'img, picture, video, canvas, svg, iframe, embed, object' +
    ' { filter: brightness(0) invert(1) !important; } * { background-image: none !important; }';
  document.head.append(st);
}
"""


# Headed: stamp a tab when the user is on it (shown, focused, clicked, typed, loaded),
# so State.sync can follow the tab they switched to or navigated by hand.
ACTIVE_JS = """
(() => {
  const mark = () => { window.__breplActive = Date.now(); };
  if (document.visibilityState === 'visible' && document.hasFocus()) mark();
  addEventListener('focus', mark, true);
  addEventListener('pointerdown', mark, true);
  addEventListener('keydown', mark, true);
  addEventListener('load', () => { if (document.hasFocus()) mark(); });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') mark();
  });
})();
"""


# Does the viewport area {top, height} show any content? True when a box of text, of
# media/controls, or of an element with a background image overlaps it, ignoring fixed
# or sticky bars (repeated in every part), hidden elements and brepl's own overlays.
HAS_CONTENT_JS = """
({top, height}) => {
  const bottom = top + height, w = innerWidth;
  const fixed = el => {
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const p = getComputedStyle(e).position;
      if (p === 'fixed' || p === 'sticky') return true;
    }
    return false;
  };
  const hit = r => r.width > 0 && r.height > 0 && r.bottom > top && r.top < bottom &&
    r.right > 0 && r.left < w;
  const shown = el => { const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const ok = el => el && !el.closest('.brepl-ov') && shown(el) && !fixed(el);
  // text: the boxes of the text itself, not of its (maybe page-wide) container
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT,
    {acceptNode: n => n.textContent.trim() ? 1 : 2});
  const range = document.createRange();
  for (let n; (n = tw.nextNode());) {
    range.selectNodeContents(n);
    if ([...range.getClientRects()].some(hit) && ok(n.parentElement)) return true;
  }
  for (const el of document.querySelectorAll(
      'img,svg,video,canvas,picture,iframe,input,textarea,select,button,[style*="background"]')) {
    if (hit(el.getBoundingClientRect()) && ok(el)) return true;
  }
  for (const el of document.body.querySelectorAll('*')) {  // css background images
    if (getComputedStyle(el).backgroundImage !== 'none' && hit(el.getBoundingClientRect()) && ok(el))
      return true;
  }
  return false;
}
"""

PIXEL_DIFF_JS = """
async ({current, reference}) => {
  const decode = async data => {
    const image = new Image();
    image.src = 'data:image/png;base64,' + data;
    await image.decode();
    return image;
  };
  const [a, b] = await Promise.all([decode(current), decode(reference)]);
  const width = Math.max(a.naturalWidth, b.naturalWidth);
  const height = Math.max(a.naturalHeight, b.naturalHeight);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d', {willReadFrequently: true});
  context.drawImage(a, 0, 0);
  const pixelsA = context.getImageData(0, 0, width, height).data;
  context.clearRect(0, 0, width, height);
  context.drawImage(b, 0, 0);
  const pixelsB = context.getImageData(0, 0, width, height).data;
  let total = 0, changed = 0;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const inA = x < a.naturalWidth && y < a.naturalHeight;
      const inB = x < b.naturalWidth && y < b.naturalHeight;
      if (!inA && !inB) continue;
      total++;
      const i = (y * width + x) * 4;
      if (!inA || !inB || pixelsA[i] !== pixelsB[i] ||
          pixelsA[i + 1] !== pixelsB[i + 1] || pixelsA[i + 2] !== pixelsB[i + 2] ||
          pixelsA[i + 3] !== pixelsB[i + 3]) changed++;
    }
  }
  return total ? Math.round(100 * (total - changed) / total) + '%' : '0%';
}
"""


class State:
    def __init__(self, page, headed: bool = False) -> None:
        self.page = page
        self.ctx = page.context
        self.headed = headed
        self.touched: dict = {}  # page -> ms of brepl's last command on it
        self.links: list[str] = []
        self.new_pages: list = []
        self.navs = 0  # main-frame navigations since the last click
        self.history: list[str] = []  # every page visited, never truncated
        self.pos = -1  # index of the current page in history

    def sync(self) -> None:
        """Follow the page the user actually has in front of them, then record its url
        (so pages reached by hand land in history). Runs before every command.
        Headed only: the tab with the latest activity wins, user activity (ACTIVE_JS:
        shown, focused, clicked, typed, loaded) or brepl's own last command on it;
        a closed current tab falls back to the latest one. Headless keeps its page
        (pop-ups opened by sites are not followed) unless it was closed."""
        pages = [p for p in self.ctx.pages if not p.is_closed()]
        if not pages:
            pages = [self.ctx.new_page()]
        new = self.page if self.page in pages else pages[-1]
        if self.headed:
            def active(p) -> float:
                try:
                    js = p.evaluate("window.__breplActive || 0")
                except PWError:
                    js = 0
                return max(js or 0, self.touched.get(p, 0))
            new = max(pages, key=lambda p: (active(p), p is new))
        if new is not self.page:
            self.page = new
            print(f"(switched to the tab at {new.url})")
        self.record()

    def touch(self) -> None:
        """brepl just used self.page: it's now the most recently active tab."""
        self.touched[self.page] = time.time() * 1000

    def front(self) -> None:
        """Headed: show the current page (after g/c/h navigated it)."""
        if self.headed:
            try:
                # bring_to_front activates Chrome and steals the keyboard focus from
                # the app the user is typing in: only do it to switch tabs
                if self.page.evaluate("() => document.visibilityState") != "visible":
                    self.page.bring_to_front()
            except PWError:
                pass

    def record(self) -> None:
        """Append the current url unless it is where history already points (h moves pos)."""
        url = self.page.url
        if url.startswith(("about:", "chrome-error:")):
            return
        if 0 <= self.pos < len(self.history) and self.history[self.pos] == url:
            return
        self.history.append(url)
        self.pos = len(self.history) - 1


def iterm2_image(png: bytes) -> None:
    if capturing():
        _tls.images.append(png)
        return
    data = base64.b64encode(png).decode()
    sys.stdout.flush()
    os.set_blocking(sys.stdout.fileno(), True)  # playwright's driver can leave it non-blocking
    sys.stdout.write(
        f"\033]1337;File=inline=1;size={len(png)};width=100%;preserveAspectRatio=1:{data}\a\n"
    )
    sys.stdout.flush()


def settle(page) -> None:
    """Wait until the page has really rendered, not just parsed its HTML."""
    for state, ms in (("load", 15000), ("networkidle", 3000)):
        try:
            page.wait_for_load_state(state, timeout=ms)
        except PWError:
            pass


LINKS_JS = """
els => els.map(e => [e.getAttribute('href'), e.href,
  [...(e.innerText || e.getAttribute('aria-label') || e.title ||
   (e.querySelector('img[alt]') || {}).alt ||
   ((e.querySelector('[title], [aria-label]') || {getAttribute: () => ''}).getAttribute('title') ||
    (e.querySelector('[aria-label]') || {getAttribute: () => ''}).getAttribute('aria-label')) ||
   '').replace(/\\s+/g, ' ').trim()].slice(0, 60).join('')])
"""


def internal_urls(page) -> list[tuple[str, list[str]]]:
    """Same-host links as (url, [anchor texts]), deduped by url, non-empty texts in page order."""
    base = page.url
    host = urlparse(base).netloc
    seen: dict[str, list[str]] = {}
    for h, href, text in page.eval_on_selector_all("a[href]", LINKS_JS):
        if not h or h.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        url = href.split("#")[0]
        u = urlparse(url)
        if u.scheme in ("http", "https") and u.netloc == host and url.rstrip("/") != base.rstrip("/"):
            texts = seen.setdefault(url, [])
            if text and text not in texts:
                texts.append(text)
    return list(seen.items())


def show(page) -> None:
    print(f"{page.url}  —  {page.title()}")


def resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443)
        return True
    except (socket.gaierror, UnicodeError):
        return False


def candidates(arg: str) -> list[str]:
    url = arg if "://" in arg else "https://" + arg
    u = urlparse(url)
    host = u.hostname or ""
    if host == "localhost" or u.port or host.endswith(SUFFIXES):
        return [url]
    return [url] + [u._replace(netloc=u.netloc + s).geturl() for s in SUFFIXES]


def cmd_go(st: State, arg: str) -> bool:
    """g: navigate; True if a page was opened."""
    page = st.page
    if not arg:
        print("usage: g <url>|<num>|/<path>|/<substr>/")
        return False
    if len(arg) > 2 and arg.startswith("/") and arg.endswith("/"):
        return go_match(st, arg[1:-1])
    if arg.startswith("/"):  # a path on the current origin
        u = urlparse(page.url)
        if u.scheme not in ("http", "https"):
            print(f"no current site for {arg}")
            return False
        goto(page, f"{u.scheme}://{u.netloc}{arg}")
        settle(page)
        show(page)
        return True
    if arg.isdigit():
        i = int(arg)
        if not 1 <= i <= len(st.links):
            print(f"no link {i} (run l or p first; {len(st.links)} links listed)")
            return False
        goto(page, st.links[i - 1])
        settle(page)
        show(page)
        return True
    errors = []
    for url in candidates(arg):
        host = urlparse(url).hostname or ""
        if not resolves(host):  # check DNS here: a failed goto leaves a chrome-error page behind
            errors.append(f"{host}: does not resolve")
            continue
        try:
            goto(page, url)
        except PWError as e:
            msg = str(e).splitlines()[0]
            if "interrupted by another navigation" not in msg:
                errors.append(f"{host}: {msg.removeprefix('Page.goto: ')}")
                continue
            # a JS/meta redirect took over: fine, as long as it landed on a real page
        settle(page)
        if page.url.startswith("chrome-error:"):
            errors.append(f"{host}: failed to load")
            continue
        show(page)
        return True
    print("error: could not open " + arg)
    for e in errors:
        print(f"  {e}")
    return False


def go_match(st: State, sub: str) -> bool:
    """Follow the first internal link whose listing line ([text] url) contains sub,
    case-insensitive. Ranked: whole word in an anchor text, then anywhere in an
    anchor text, then in the url, so /next/ picks 'Next page' over 'girlnextdoor'."""
    low = sub.lower()
    word = re.compile(r"(?<!\w)" + re.escape(low) + r"(?!\w)")

    def rank(u: str, ts: list[str]) -> int | None:
        texts = [t.lower() for t in ts]
        if any(word.search(t) for t in texts):
            return 0
        if any(low in t for t in texts):
            return 1
        return 2 if low in u.lower() else None

    ranked = [(r, i, u) for i, (u, ts) in enumerate(internal_urls(st.page))
              if (r := rank(u, ts)) is not None]
    hits = [u for _, _, u in sorted(ranked)]
    if not hits:
        print(f"no link containing {sub!r}")
        return False
    if len(hits) > 1:
        print(f"({len(hits)} links contain {sub!r}, following the first)")
    goto(st.page, hits[0])
    settle(st.page)
    show(st.page)
    return True


def page_markdown(page, html: str | None = None) -> str:
    """The rendered page (current DOM, not the original HTML) as markdown, without images."""
    conv = html2text.HTML2Text(baseurl=page.url)
    conv.ignore_images = True
    conv.body_width = 0  # no hard wrapping
    conv.unicode_snob = True
    return conv.handle(page.content() if html is None else html).strip() + "\n"


def cmd_dump(st: State, path: str) -> None:
    settle(st.page)
    html = st.page.content()
    md = page_markdown(st.page, html)
    for name, text in ((HTML_FILE, html), (MD_FILE, md)):  # always: the last d
        try:
            with open(name, "w", encoding="utf-8", errors="replace") as fh:
                fh.write(text)
        except OSError as e:
            print(f"warning: cannot write {name}: {e}")
    if not path:
        print(md.rstrip("\n"))
        print(f"(saved {HTML_FILE}, {MD_FILE})")
        return
    path, _, command = path.strip().partition(" ")
    command = command.strip()
    check = path.startswith("?")
    if check:
        path = path[1:]
        if not path:
            print("usage: d ?<file> [command]")
            return
    if command:  # page html | sh -c command, as typed (quotes, pipes ...)
        try:
            r = subprocess.run(command, shell=True, input=html.encode("utf-8", "replace"),
                               capture_output=True)
        except OSError as e:
            print(f"error: cannot run {command!r}: {e}")
            return
        if r.stderr:
            print(r.stderr.decode("utf-8", "replace").rstrip("\n"))
        if r.returncode:
            print(f"error: command exited with {r.returncode}")
            if check:  # don't replace the reference with a failed run
                return
        md = r.stdout.decode("utf-8", "replace")
        if path == "-":
            print(md.rstrip("\n"))
            return
    path = os.path.expanduser(path)
    if check:  # same as the last output? then this output becomes the reference
        new = r.stdout if command else md.encode("utf-8", "replace")
        try:
            with open(path, "rb") as fh:
                same = fh.read() == new
        except FileNotFoundError:
            same = True  # no reference yet: it matches itself
        except OSError as e:
            print(f"error: cannot read {path}: {e}")
            return
        try:
            if os.path.dirname(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(new)
        except OSError as e:
            print(f"error: cannot write {path}: {e}")
            return
        print("true" if same else "false")
        return
    try:
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(md)
    except OSError as e:
        print(f"error: cannot write {path}: {e}")
        return
    via = f" | {command}" if command else ""
    print(f"{st.page.url}{via} -> {path} ({len(md.splitlines())} lines)")


def save(path: str, url: str, lines: list[str]) -> None:
    """Overwrite path with the listing just printed, headed by the page url."""
    try:
        with open(path, "w", encoding="utf-8", errors="replace") as fh:  # pages may hold lone surrogates
            fh.write("\n".join([url, *lines]) + "\n")
    except OSError as e:
        print(f"warning: cannot write {path}: {e}")


P_USAGE = "usage: p [n] | p <n> <file> | p <n> ?<file>   (also pp; n parts, 0 = all, default 1)"


def cmd_print(st: State, images: bool, arg: str = "", header: bool = True) -> None:
    """p/pp [n]: show and save n (non-blank) parts, 0 = all, default 1.
    p <n> <file>: make parts up to n, show part n and write it to file (no pageN.png).
    p <n> ?<file>: make parts up to n and print the percentage of matching pixels."""
    num, _, rest = arg.partition(" ")
    rest = rest.strip()
    if (num and not num.isdigit()) or (rest and int(num or 0) < 1):
        print(P_USAGE)
        return
    target, check, path = None, False, ""
    if rest:
        target = int(num)
        check = rest.startswith("?")
        path = os.path.expanduser(rest[1:].strip() if check else rest)
        if not path:
            print(P_USAGE)
            return
    limit = target or (int(num) if num else 1)
    page = st.page
    settle(page)
    page.evaluate(SCROLL_JS)  # trigger lazy-loaded / on-scroll content
    settle(page)
    if not images:  # hide media but keep its space, so the layout is unchanged
        page.evaluate(NOIMG_JS)
    found = internal_urls(page)  # numbered exactly like an unfiltered 'l'
    st.links = [u for u, _ in found]
    save(LINK_FILE, page.url, link_lines(found))
    info = page.evaluate(SCAN_JS, {"draw": True, "buttons": True, "links": st.links})
    if not check and header:  # G: g already printed it
        show(page)
    # the full page as jpeg parts 1600px high, top to bottom: MCP clients downscale
    # big images, and terminals drop them (VS Code/xterm.js: ~16M pixels, ~20MB).
    # Each part is scrolled into view and shot as rendered, not cut from one
    # full-page shot: virtualized lists (x.com timelines) only render what is near
    # the viewport, so a full-page shot is blank below the first screen or two.
    mcp = capturing()
    # in the terminal the parts just stack up; p <n> <file> parts never overlap, so
    # part n is the same from the REPL and over MCP
    overlap = MCP_OVERLAP if mcp and not target else 0
    height = page.evaluate("() => document.documentElement.scrollHeight")
    view = page.viewport_size or page.evaluate(  # None when headed (no_viewport)
        "() => ({width: innerWidth, height: innerHeight})")
    width = view["width"]
    # headed: resizing the viewport resizes (undocks) the visible window, so keep it
    tile = view["height"] if st.headed else MCP_TILE
    tops = list(range(0, max(height - overlap, 1), tile - overlap))
    if mcp and not target and not 0 < limit <= MCP_MAX_TILES:  # images go into the client's context
        limit = MCP_MAX_TILES
    for f in [] if target else os.listdir("."):  # the previous page's parts
        if SHOT_FILE.fullmatch(f):
            try:
                os.remove(f)
            except OSError:
                pass
    saved: list[str] = []
    blank = shown = 0
    more = False  # stopped at the limit with parts left
    if not st.headed:
        page.set_viewport_size({"width": width, "height": tile})
    try:
        for i, y in enumerate(tops):
            if limit and shown == limit:
                more = True
                break
            page.evaluate("y => window.scrollTo(0, y)", y)
            page.wait_for_timeout(250)  # let the list render what scrolled in
            sy = page.evaluate("() => window.scrollY")  # clamped near the bottom
            top = max(0, min(y - sy, tile - 1))
            clip = {"x": 0, "y": top, "width": width, "height": max(1, min(tile - top, height - y))}
            if not page.evaluate(HAS_CONTENT_JS, {"top": clip["y"], "height": clip["height"]}):
                blank += 1  # nothing rendered there: don't show or save it
                continue
            name = f"page{len(saved) + 1}.png"  # lossless copy on disk
            try:
                if target:  # only part n is kept, in memory: no pageN.png
                    png = page.screenshot(clip=clip) if shown + 1 == target else None
                    saved.append(png)
                else:
                    page.screenshot(clip=clip, path=name)
                    saved.append(name)
            except (PWError, OSError) as e:
                print(f"warning: cannot write {name}: {e}")
            # shown/sent as jpeg: far smaller than png for the terminal and MCP
            if target is None or (not check and len(saved) == target):
                iterm2_image(page.screenshot(clip=clip, type="jpeg", quality=75 if mcp else 85))
            shown += 1
    finally:
        if not st.headed:
            page.set_viewport_size(view)
        page.evaluate("() => window.scrollTo(0, 0)")
    if target:
        page.evaluate(UNMARK_JS, st.headed)
        save(PAGE_FILE, page.url, [])  # stale lists must not survive
        part_file(page, target, saved, check, path)
        return
    if mcp and shown > 1:
        print(f"(page {height}px high, in {shown} images of {tile}px, top to bottom,"
              f" overlapping {MCP_OVERLAP}px)")
    if blank:
        print(f"({blank} blank part{'s' if blank > 1 else ''} of {tile}px not shown)")
    if more:
        left = len(tops) - i
        print(f"(page {height}px high: {shown} part{'s' if shown > 1 else ''} shown, up to {left}"
              f" more below; " + ("pages=N or 0 for more)" if mcp else "p <n> or p 0 for more)"))
    if saved:
        print(f"saved {saved[0]}" + (f" ... {saved[-1]}" if len(saved) > 1 else ""))
    page.evaluate(UNMARK_JS, st.headed)
    lines = [f"buttons ({len(info['buttons'])}):"]
    lines += [f"  {b['id']:>3}  {b['label']}" for b in info["buttons"]]
    lines.append(f"fields ({len(info['fields'])}):")
    for f in info["fields"]:
        val = f"  = {f['value']!r}" if f["value"] else ""
        hidden = "" if f["shown"] else "  (not shown)"
        lines.append(f"       {f['name']}  [{f['type']}]{val}{hidden}")
    print("\n".join(lines))
    print(f"links ({len(found)}): numbered in green, listed in {LINK_FILE} (g <num> to follow)")
    save(PAGE_FILE, page.url, lines)


def part_file(page, n: int, saved: list[bytes | None], check: bool, path: str) -> None:
    """Write part n, or compare its changed-pixel percentage with a reference image."""
    if n > len(saved):
        if check:
            print("0%")
            return
        print(f"no page {n}: the page has {len(saved)} non-blank part{'s' if len(saved) != 1 else ''}")
        return
    png = saved[n - 1]
    if png is None:
        if check:
            print("0%")
            return
        print(f"no page {n}: the page has {len(saved)} non-blank part{'s' if len(saved) != 1 else ''}")
        return
    if check and not os.path.exists(path):
        check = False  # no reference yet: write part n there, it matches itself
        created = True
    else:
        created = False
    if check:
        try:
            with open(path, "rb") as fh:
                reference = base64.b64encode(fh.read()).decode("ascii")
            current = base64.b64encode(png).decode("ascii")
            percentage = page.evaluate(PIXEL_DIFF_JS, {"current": current, "reference": reference})
        except OSError as e:
            print(f"error: cannot read comparison image {path}: {e}")
            return
        except Exception as e:
            print(f"error: cannot compare image {path}: {str(e).splitlines()[0]}")
            return
        try:  # part n becomes the reference for the next check
            with open(path, "wb") as fh:
                fh.write(png)
        except OSError as e:
            print(f"warning: cannot update {path}: {e}")
        print(percentage)
        return
    try:
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(png)
    except OSError as e:
        print(f"error: cannot write {path}: {e}")
        return
    print("100%" if created else f"page {n} -> {path} ({len(png) // 1024} KB png)")


def link_lines(found: list[tuple[str, list[str]]], match=None) -> list[str]:
    lines = []
    for i, (u, ts) in enumerate(found, 1):
        # show the text that matched the filter, else the first one
        t = next((t for t in ts if match and match(t)), ts[0] if ts else "")
        lines.append(f"{i:3}  [{t}] {u}")
    return lines


def cmd_list(st: State, sub: str) -> None:
    # match on path+query or anchor text
    # (the host is the same for every internal url, so it isn't matched)
    if len(sub) > 2 and sub.startswith("/") and sub.endswith("/"):  # /regex/, ignoring case
        try:
            rx = re.compile(sub[1:-1], re.IGNORECASE)
        except re.error as e:
            print(f"bad regex {sub}: {e}")
            return
        match_text = match_path = lambda t: rx.search(t) is not None  # noqa: E731
    else:  # plain substring: exact on the path, ignoring case on the text
        low = sub.lower()
        match_path = lambda p: sub in p  # noqa: E731
        match_text = lambda t: low in t.lower()  # noqa: E731
    found = [(u, ts) for u, ts in internal_urls(st.page)
             if not sub or match_path(u.split(urlparse(u).netloc, 1)[1])
             or any(match_text(t) for t in ts)]
    st.links = [u for u, _ in found]
    if not found:
        print("(no internal urls)" + (f" containing {sub!r}" if sub else ""))
    lines = link_lines(found, match_text if sub else None)
    if lines:
        print("\n".join(lines))
    save(LINK_FILE, st.page.url, lines)


def fill_fields(page, assigns: list[tuple[str, str]], form: int) -> list | None:
    """Fill fields matched by exact name or unique prefix. Returns the filled fields,
    None if any failed.
    Same-named fields (e.g. login vs signup form) resolve to the one in `form`."""
    fields = page.evaluate(SCAN_JS, {"draw": False, "buttons": False})["fields"]
    plan = []
    for name, value in assigns:
        hits = [f for f in fields if f["name"] == name] or [f for f in fields if f["name"].startswith(name)]
        if not hits:
            print(f"no field matching {name!r}")
            return None
        if len({f["name"] for f in hits}) > 1:
            print(f"warning: {name!r} is ambiguous: {', '.join(sorted({f['name'] for f in hits}))}")
            return None
        radios = [f for f in hits if f["type"] == "radio"]
        if radios:  # radio group: pick the option by value, e.g. amount=10
            opt = next((f for f in radios if f["value"].lower() == value.lower()), None)
            if opt is None:
                print(f"no {hits[0]['name']!r} option {value!r}: {', '.join(f['value'] for f in radios)}")
                return None
            plan.append((opt, value))
            continue
        plan.append((next((f for f in hits if f["form"] == form), hits[0]), value))
    for f, value in plan:
        loc = page.locator(f'[data-brepl-field="{f["key"]}"]')
        if f["type"] == "select":
            loc.select_option(value, timeout=TIMEOUT)
        elif f["type"] == "radio":
            loc.check(timeout=TIMEOUT, force=not f["shown"])  # custom radios hide the real input
        elif f["type"] == "checkbox":
            loc.set_checked(value.lower() not in ("", "0", "no", "false", "off"), timeout=TIMEOUT,
                            force=not f["shown"])
        else:
            loc.fill(value, timeout=TIMEOUT)
        print(f"  {f['name']} = {value!r}")
    return [f for f, _ in plan]


def cmd_click(st: State, arg: str) -> None:
    page = st.page
    # c /regex/ [field=value ...]: the regex is taken raw (before shlex, which
    # would eat its backslashes)
    rx = None
    m = re.match(r"/((?:\\.|[^/\\])+)/(?=\s|$)", arg.strip())
    if m:
        try:
            rx = re.compile(m.group(1), re.IGNORECASE)
        except re.error as e:
            print(f"bad regex /{m.group(1)}/: {e}")
            return
        arg = arg.strip()[m.end():]
    try:
        toks = shlex.split(arg)
    except ValueError as e:
        print(f"parse error: {e}")
        return
    assigns = [tuple(t.split("=", 1)) for t in toks if "=" in t]
    target = " ".join(t for t in toks if "=" not in t)
    if rx is not None:
        if target:
            print(f"unexpected {target!r} after the regex (fields are name=value)")
            return
        loc = None
        for c in (page.get_by_role("button", name=rx), page.get_by_text(rx)):
            c = c.filter(visible=True)
            if c.count() > 0:
                loc = c
                break
        if loc is None:
            print(f"no button matching /{rx.pattern}/")
            return
    elif not target:
        print("usage: c <num>|<text>|/regex/ [field=value ...]")
        return
    elif target.isdigit():
        loc = page.locator(f'[data-brepl-id="{target}"]')
        if loc.count() == 0:
            print(f"no button {target} (run p or pp again)")
            return
    else:
        loc = None
        for c in (page.get_by_role("button", name=target), page.get_by_text(target)):
            c = c.filter(visible=True)
            if c.count() > 0:
                loc = c
                break
        if loc is None:
            print(f"no button containing {target!r}")
            return
    if assigns:
        form = loc.first.evaluate("el => el.form ? [...document.forms].indexOf(el.form) : -1")
        if fill_fields(page, assigns, form) is None:
            return
    act(st, lambda: loc.first.click(timeout=TIMEOUT))


def cmd_submit(st: State, arg: str) -> None:
    """s field=value ...: fill the fields, then press Enter in the last one."""
    try:
        toks = shlex.split(arg)
    except ValueError as e:
        print(f"parse error: {e}")
        return
    if not toks or any("=" not in t for t in toks):
        print("usage: s <field>=<value> [...]")
        return
    filled = fill_fields(st.page, [tuple(t.split("=", 1)) for t in toks], -1)
    if filled is None:
        return
    loc = st.page.locator(f'[data-brepl-field="{filled[-1]["key"]}"]')
    act(st, lambda: loc.press("Enter", timeout=TIMEOUT))


def act(st: State, action) -> None:
    """Run a click/submit, follow a new tab, wait, print the new url or (same page)."""
    page = st.page
    before = page.url
    st.new_pages.clear()
    st.navs = 0
    throttle()
    action()
    page.wait_for_timeout(500)  # give a navigation the chance to start
    if st.new_pages:  # click opened a new tab: follow it
        st.page = st.new_pages[-1]
        print("(new tab)")
    settle(st.page)
    if st.page.url != before or st.navs:
        show(st.page)
    else:
        print("(same page)")


def cmd_history(st: State, arg: str) -> None:
    host = urlparse(st.page.url).netloc
    site = [i for i, u in enumerate(st.history) if urlparse(u).netloc == host]
    if not arg:
        if not site:
            print("(no history for this site)")
        for n, i in enumerate(site, 1):
            mark = "*" if i == st.pos else " "
            print(f"{n:3}{mark} {st.history[i]}")
        return
    try:
        n = int(arg)
    except ValueError:
        print("usage: h [n]")
        return
    if n > 0:
        if n > len(site):
            print(f"no entry {n} ({len(site)} for this site)")
            return
        target = site[n - 1]
    elif n < 0:
        target = st.pos + n
        if target < 0:
            print(f"only {st.pos} pages back")
            return
    else:
        print("usage: h [n]  (n != 0)")
        return
    goto(st.page, st.history[target])
    settle(st.page)
    st.pos = target
    show(st.page)


NO_WEBDRIVER_JS = """
Object.defineProperty(Navigator.prototype, 'webdriver', {get: () => false, configurable: true});
"""


def launch(pw, profile: str | None = None, headed: bool = False):
    """Start Chrome (or Playwright's Chromium) and return (closer, context). With a
    profile dir, the context is persistent: cookies, localStorage etc. are kept there
    across runs; without, everything is thrown away on quit."""
    # don't announce automation (--enable-automation): sites like x.com refuse to log
    # in such a browser. No extra flags: Chrome shows an "unsupported command-line
    # flag" bar for --disable-blink-features=AutomationControlled too
    opts = {"headless": not headed, "ignore_default_args": ["--enable-automation"],
            # Playwright passes --no-sandbox by default: Chrome then shows an
            # "unsupported command-line flag" bar (and runs less safely)
            "chromium_sandbox": True}
    # headed: no fixed viewport, the page fills the window (else an empty band
    # shows when the window is bigger than 1280x800)
    view = {"no_viewport": True} if headed else {"viewport": {"width": 1280, "height": 800}}
    for channel in ({"channel": "chrome"}, {}):
        try:
            if profile:
                ctx = pw.chromium.launch_persistent_context(profile, **channel, **opts, **view)
                closer = ctx
            else:
                closer = pw.chromium.launch(**channel, **opts)
                ctx = closer.new_context(**view)
            # navigator.webdriver is still true under Playwright: hide it in every page
            ctx.add_init_script(NO_WEBDRIVER_JS)
            return closer, ctx
        except PWError as e:
            if profile and ("SingletonLock" in str(e) or "ProcessSingleton" in str(e)):
                sys.exit(f"profile {profile} is in use by another brepl/Chrome")
            last = e
    sys.exit(f"no Chrome found ({str(last).splitlines()[0]}); run: uvx playwright install chromium")


URL_RE = re.compile(r"https?://[^\s<>\"']+")


def notify_link(msg: str, url: str) -> bool:
    """A clickable notification for url: an osascript Open/Cancel dialog, detached
    (it outlives brepl); Open opens the url in a new instance of the default browser."""
    osa = shutil.which("osascript")
    if not osa:
        return False
    # open -n: a plain open hands the url to a running Chrome, maybe brepl's headless one
    script = ['use framework "AppKit"', "use scripting additions", "on run argv",
              'set r to display dialog (item 1 of argv) with title "brepl" '
              'buttons {"Cancel", "Open"} default button "Open" giving up after 600',
              'if button returned of r is not "Open" then return',
              "set u to current application's NSURL's URLWithString:(item 2 of argv)",
              "set a to ((current application's NSWorkspace's sharedWorkspace()'s "
              "URLForApplicationToOpenURL:u)'s |path|()) as text",
              'do shell script "/usr/bin/open -n -a " & quoted form of a & " " '
              "& quoted form of (item 2 of argv)",
              "end run"]
    try:
        subprocess.Popen([osa, *sum((["-e", ln] for ln in script), []), msg, url],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def cmd_notify(msg: str) -> None:
    """n <message>: a macOS notification via osascript, else print it. With a link,
    the notification is clickable and opens it."""
    if not msg:
        print("usage: n <message>")
        return
    link = URL_RE.search(msg)
    if link:
        if notify_link(msg, link.group(0).rstrip(".,;:!?)]")):
            print("notified")
            return
        print(f"brepl: {msg}")
        return
    osa = shutil.which("osascript")
    if osa:
        try:
            r = subprocess.run([osa, "-e", "on run argv", "-e",
                                'display notification (item 1 of argv) with title "brepl"',
                                "-e", "end run", msg],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                print("notified")
                return
        except (subprocess.TimeoutExpired, OSError):
            pass
    print(f"brepl: {msg}")


def dispatch(st: State, line: str) -> None:
    """Run one REPL command line against the browser (worker thread only)."""
    cmd, _, arg = line.strip().partition(" ")
    arg = arg.strip()
    try:
        st.sync()
        if cmd == "?":
            print(HELP)
        elif cmd == "g":
            cmd_go(st, arg)
        elif cmd == "G":
            if cmd_go(st, arg):
                cmd_print(st, images=True, header=False)
        elif cmd == "u":
            print(st.page.url)
        elif cmd == "p":
            cmd_print(st, images=True, arg=arg)
        elif cmd == "pp":
            cmd_print(st, images=False, arg=arg)
        elif cmd == "l":
            cmd_list(st, arg)
        elif cmd == "c":
            cmd_click(st, arg)
        elif cmd == "s":
            cmd_submit(st, arg)
        elif cmd == "h":
            cmd_history(st, arg)
        elif cmd == "d":
            cmd_dump(st, arg)
        elif cmd == "n":
            cmd_notify(arg)
        else:
            print("unknown command, type ? for help")
        if cmd in ("g", "G", "c", "s", "h") and (cmd != "h" or arg):
            st.front()  # headed: see where g/c/h went
        st.touch()
    except PWError as e:
        print(f"error: {str(e).splitlines()[0]}")
    except Exception as e:  # never let one command kill the shared browser
        print(f"error: {type(e).__name__}: {e}")
    st.record()


class Worker(threading.Thread):
    """Owns the browser. Playwright's sync API is bound to the thread that started it,
    so the REPL and the MCP server both send command lines here and wait for them;
    commands run one at a time on the same browser."""

    def __init__(self, profile: str | None = None, headed: bool = False) -> None:
        super().__init__(daemon=True, name="brepl-browser")
        self.profile, self.headed = profile, headed
        self.jobs: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.error: str | None = None

    def open(self, pw):
        """Launch Chrome in the current mode; return (closer, context, State)."""
        browser, ctx = launch(pw, self.profile, self.headed)
        headed = self.headed
        ua: list = []  # headless: the user agent without "Headless"

        def on_nav(frame):
            if frame == frame.page.main_frame:
                st.navs += 1

        def prep(p):
            """Per tab: headed, undo Playwright's fake focus so document.hasFocus()
            and focus events tell the tab the user is on; headless, drop the
            'HeadlessChrome' user agent that sites like x.com refuse."""
            p.on("framenavigated", on_nav)
            try:
                cdp = ctx.new_cdp_session(p)
                if headed:
                    cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": False})
                else:
                    if not ua:
                        ua.append(p.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome"))
                    cdp.send("Emulation.setUserAgentOverride", {"userAgent": ua[0]})
            except PWError:
                pass

        def on_page(p):
            prep(p)
            st.new_pages.append(p)

        ctx.add_init_script(ACTIVE_JS)
        # a persistent context starts with a (blank) tab open: reuse it
        st = State(ctx.pages[0] if ctx.pages else ctx.new_page(), headed)
        prep(st.page)
        ctx.on("page", on_page)  # tabs opened later, e.g. by target=_blank clicks
        return browser, ctx, st

    def toggle(self, pw, browser, st: State):
        """'@': relaunch Chrome in the other mode (headless <-> headed); a running
        Chrome can't switch. The profile keeps cookies/logins; the open tabs, the
        current page, history and link numbers are carried over."""
        st.sync()
        tabs = [p.url for p in st.ctx.pages if not p.is_closed()]
        cur = next((i for i, p in enumerate(st.ctx.pages) if p is st.page), 0)
        keep = (st.history, st.pos, st.links)
        browser.close()
        self.headed = not self.headed
        try:
            browser, ctx, new = self.open(pw)
        except BaseException:
            self.headed = not self.headed  # e.g. no display: back to the old mode
            print("could not relaunch, staying as before")
            browser, ctx, new = self.open(pw)
        new.history, new.pos, new.links = keep
        pages = []
        for i, url in enumerate(tabs):
            p = new.page if i == 0 else ctx.new_page()
            pages.append(p)
            if url and not url.startswith("about:"):
                try:
                    p.goto(url, timeout=30000)
                except PWError as e:
                    print(f"error reopening {url}: {str(e).splitlines()[0]}")
        new.page = pages[cur] if cur < len(pages) else new.page
        new.new_pages.clear()
        settle(new.page)
        new.touch()
        new.front()
        print(f"{'headed' if self.headed else 'headless'}: {new.page.url}"
              + (f" ({len(tabs)} tabs)" if len(tabs) > 1 else ""))
        return browser, new

    def run(self) -> None:
        try:
            with sync_playwright() as pw:
                browser, _, st = self.open(pw)
                self.ready.set()
                while (job := self.jobs.get()) is not None:
                    line, capture, fut = job
                    if capture:
                        _tls.buf, _tls.images = io.StringIO(), []
                    try:
                        if line.strip() == "@":
                            browser, st = self.toggle(pw, browser, st)
                        else:
                            dispatch(st, line)
                        fut.set_result((_tls.buf.getvalue(), _tls.images) if capture else None)
                    except BaseException as e:
                        fut.set_exception(e)
                    finally:
                        _tls.buf = None
                browser.close()
        except BaseException as e:  # includes launch()'s SystemExit
            self.error = str(e) or type(e).__name__
            self.ready.set()

    def call(self, line: str, capture: bool = False):
        """Run a command line; with capture, return (printed text, [images])."""
        fut: Future = Future()
        self.jobs.put((line, capture, fut))
        return fut.result()

    def stop(self) -> None:
        self.jobs.put(None)
        self.join(timeout=10)


def port_free(port: int) -> bool:
    with socket.socket() as s:
        # like uvicorn, ignore connections of a previous run still in TIME_WAIT
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def log_mcp(text: str, images: int) -> None:
    """Verbose MCP: echo what a tool call returned in the terminal (indented, so it
    stands apart from REPL output), then redraw the prompt and any half-typed line."""
    out = "\n".join("  " + ln for ln in text.splitlines())
    if images:
        out += f"\n  ({images} image{'s' if images > 1 else ''} sent to the client)"
    sys.stderr.write(out + "\n")
    sys.stderr.flush()
    readline = sys.modules.get("readline")  # loaded by the REPL only, not as a module
    if readline:
        sys.stdout.write(PROMPT + readline.get_line_buffer())
        sys.stdout.flush()


def start_mcp(worker: Worker, port: int) -> bool:
    """Serve the commands as MCP tools on http://localhost:<port>/mcp (background thread)."""
    import uvicorn
    from mcp.server.mcpserver import Image, MCPServer

    import logging

    for name in ("mcp", "uvicorn"):  # keep library chatter out of the REPL
        logging.getLogger(name).setLevel(logging.WARNING)
    if not port_free(port):
        print(f"mcp: port {port} is busy (another brepl?), not serving MCP")
        return False

    server = MCPServer(
        MCP_NAME,
        instructions=(
            "A headless Chrome browser shared with the brepl REPL. Typical flow: go(url), "
            "page() to see the page with numbered buttons (red), named fields (blue) and "
            "numbered links (green), then click(button, fields) or go(link number)."
        ),
    )

    async def run(line: str):
        sys.stderr.write(f"\r[mcp] {line}\n")
        text, images = await asyncio.get_running_loop().run_in_executor(None, worker.call, line, True)
        text = text.rstrip().lstrip("\n") or "(ok)"
        log_mcp(text, len(images))
        return [text, *(Image(data=d, format="png" if d[:4] == b"\x89PNG" else "jpeg") for d in images)] if images else text

    @server.tool()
    async def go(target: str) -> str:
        """Open a url (https:// is added, and .com/.net/.org are tried for bare names),
        or follow link number N from the last links()/page() listing,
        or "/path" (no final /): that path on the current site's origin,
        or "/substr/": follow the first internal link whose text or url contains
        substr (case-insensitive)."""
        return await run(f"g {target}")

    @server.tool()
    async def current_url() -> str:
        """Return the url of the current page."""
        return await run("u")

    @server.tool()
    async def page(hide_images: bool = False, pages: int = 1):
        """Full-page screenshot of the current page, as several images top to bottom
        for tall pages (1600px parts overlapping 40px), with buttons outlined in red and
        numbered, input fields outlined in blue with their name, and internal links
        outlined in green and numbered (same numbers as links()). Also returns the list
        of buttons and fields.
        hide_images replaces the page's images with white.
        pages: how many (non-blank) 1600px parts to return, from the top; 0 = all
        (at most 20). Default 1: ask for more only when the lower page matters."""
        return await run(("pp" if hide_images else "p") + f" {max(0, pages)}")

    @server.tool()
    async def save_page(n: int, file: str, hide_images: bool = False):
        """Make all the 1600px parts of the current page (as page() does, blank ones
        skipped), return part n (1 = top) as an image and copy it, lossless png, to
        file (relative to brepl's directory)."""
        return await run(f"{'pp' if hide_images else 'p'} {n} {file}")

    @server.tool()
    async def same_page(n: int, file: str, hide_images: bool = False) -> str:
        """Return the whole-number percentage of pixels in part n that match the png in file.
        Pixels present in only one image count as changed. Part n then replaces
        file (so the next call compares with this one). If file does not exist,
        part n is saved there and 100% is returned."""
        return await run(f"{'pp' if hide_images else 'p'} {n} ?{file}")

    @server.tool()
    async def links(filter: str = "") -> str:
        """List the internal (same-site) links as numbered '[text] url' lines;
        filter keeps those whose path or text contains it, or, written as /regex/,
        matches that regex (case-insensitive). go(N) follows link N."""
        return await run(f"l {filter}".strip())

    @server.tool()
    async def click(target: str, fields: dict[str, str] | None = None) -> str:
        """Optionally fill fields ({name or unique name prefix: value}; radios take the
        option value, checkboxes on/off), then click a button: its number from the last
        page() call, text contained in the button, or /regex/ matching its text
        (case-insensitive). Returns the new url if the page
        changed, else '(same page)'."""
        # a /regex/ target goes in raw: quoting it would hide the leading '/' from 'c'
        is_rx = len(target) > 2 and target.startswith("/") and target.endswith("/")
        args = [target if is_rx else shlex.quote(target)]
        args += [shlex.quote(f"{k}={v}") for k, v in (fields or {}).items()]
        return await run("c " + " ".join(args))

    @server.tool()
    async def submit(fields: dict[str, str]) -> str:
        """Fill fields ({name or unique name prefix: value}), then submit by pressing
        Enter in the last one, e.g. {"q": "python"} to search. Returns the new url if
        the page changed, else '(same page)'."""
        return await run("s " + " ".join(shlex.quote(f"{k}={v}") for k, v in fields.items()))

    @server.tool()
    async def dump(file: str = "", command: str = "") -> str:
        """The current page converted to markdown (no images; links kept as absolute
        urls). With file, write it there (relative to brepl's directory) instead
        of returning it. With command, the page html is piped to that shell command
        (as is: quotes, pipes ...) and its output written to file, or returned
        if there is no file."""
        if command:
            return await run(f"d {file or '-'} {command}")
        return await run(f"d {file}".strip())

    @server.tool()
    async def same_dump(file: str, command: str = "") -> str:
        """'true' if the output of command (a shell command, as is, fed the page
        html) or, without command, the page markdown is the same as the content
        of file, else 'false'. file is then replaced by the new output, so the next
        call tells whether the page changed since this one. Missing file: written,
        'true'."""
        return await run(f"d ?{file} {command}".strip())

    @server.tool()
    async def toggle_headed() -> str:
        """Switch the browser between headless and headed (visible window): Chrome
        is relaunched, keeping the profile (cookies/logins), open tabs, current
        page and history. Returns the new mode and url."""
        return await run("@")

    @server.tool()
    async def history(n: int | None = None) -> str:
        """No n: numbered history of the current site. n > 0: go to that entry.
        n < 0: go back n pages (-1 = back)."""
        return await run("h" if n is None else f"h {n}")

    @server.tool()
    async def repl(command: str):
        """Run one brepl REPL command line exactly as typed at the brepl> prompt
        (e.g. 'u', 'g hn', 'p 2', 'l /item/', 'c 3 q=x', 'h -1', '?') and return what
        the REPL prints (plus the page images for p/pp). 'q' is not available."""
        command = command.strip()
        if command == "q":
            return "q is not available over MCP"
        return await run(command) if command else "(empty command)"

    # stateless: no MCP sessions to lose, so clients connected before the REPL was
    # stopped keep working when it is started again (all state is in the browser)
    app = server.streamable_http_app(host="127.0.0.1", stateless_http=True)
    http = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=http.run, daemon=True, name="brepl-mcp").start()
    for _ in range(100):
        if http.started:
            return True
        time.sleep(0.05)
    print("mcp: server did not start")
    return False


def register_claude(port: int) -> None:
    """Register this server in Claude Code for the current directory ('claude mcp add',
    default local scope), unless it is already registered with this url."""
    url = f"http://localhost:{port}/mcp"
    if not shutil.which("claude"):
        print(f"mcp: 'claude' not found; register by hand: claude mcp add --transport http {MCP_NAME} {url}")
        return
    try:
        got = subprocess.run(["claude", "mcp", "get", MCP_NAME], capture_output=True, text=True, timeout=30)
        if got.returncode == 0 and url in got.stdout:
            return  # already registered
        subprocess.run(["claude", "mcp", "remove", "-s", "local", MCP_NAME], capture_output=True, timeout=30)
        add = subprocess.run(
            ["claude", "mcp", "add", "--transport", "http", MCP_NAME, url],
            capture_output=True, text=True, timeout=30,
        )
        if add.returncode == 0:
            print(f"mcp: registered '{MCP_NAME}' in Claude Code for this project")
        else:
            print(f"mcp: registering in Claude Code failed: {(add.stderr or add.stdout).strip()}")
    except (OSError, subprocess.SubprocessError) as e:
        print(f"mcp: registering in Claude Code failed: {e}")


def random_port() -> int:
    """A free port picked at random in LOCAL_PORTS."""
    ports = list(range(LOCAL_PORTS[0], LOCAL_PORTS[1] + 1))
    random.shuffle(ports)
    port = next((p for p in ports if port_free(p)), None)
    if port is None:
        sys.exit(f"no free port in {LOCAL_PORTS[0]}-{LOCAL_PORTS[1]}")
    return port


def mcp_json_port() -> int | None:
    """The port of the 'brepl' entry in ./.mcp.json, if there is one."""
    try:
        with open(os.path.join(os.getcwd(), ".mcp.json")) as fh:
            url = json.load(fh)["mcpServers"][MCP_NAME]["url"]
        return urlparse(url).port
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def local_port() -> int:
    """--local: reuse the port in ./.mcp.json while it's free, else a random one."""
    port = mcp_json_port()
    if port and port_free(port):
        return port
    if port:
        print(f"mcp: port {port} from .mcp.json is busy, picking another")
    return random_port()


def write_mcp_json(port: int) -> None:
    """--local: point the 'brepl' entry of ./.mcp.json at this port (project scope),
    keeping any other servers in the file."""
    path = os.path.join(os.getcwd(), ".mcp.json")
    conf: dict = {}
    if os.path.exists(path):
        try:
            with open(path) as fh:
                conf = json.load(fh)
        except (OSError, ValueError) as e:
            print(f"mcp: can't read {path} ({e}); not written")
            return
    entry = {"type": "http", "url": f"http://localhost:{port}/mcp"}
    if conf.get("mcpServers", {}).get(MCP_NAME) == entry:
        return  # reused as is
    conf.setdefault("mcpServers", {})[MCP_NAME] = entry
    with open(path, "w") as fh:
        json.dump(conf, fh, indent=2)
        fh.write("\n")
    print(f"mcp: wrote {path}")


AGENTS_START, AGENTS_END = "<!-- brepl -->", "<!-- /brepl -->"
AGENTS_BLOCK = f"""{AGENTS_START}
## brepl

A brepl browser is available as the `brepl` MCP server. When a chat message is a
brepl command line (`?`, `g <url|num|/substr/>`, `u`, `p [n]`, `pp [n]`,
`G <url|num|/substr/>`, `p <n> ?<image>`, `l [filter]`, `c <button> [field=value ...]`, `s field=value ...`,
`d [file] [command]`, `h [n]`, `@`,
`n <message>`), call the
`repl` tool with the message verbatim and show its output as is; the images from
`p`/`pp` are the page. `?` lists the commands.
{AGENTS_END}"""


def write_agents_md() -> None:
    """Make sure ./AGENTS.md holds AGENTS_BLOCK: create the file, append the block,
    or replace an outdated one between the markers."""
    path = os.path.join(os.getcwd(), "AGENTS.md")
    try:
        old = open(path).read() if os.path.exists(path) else ""
        i, j = old.find(AGENTS_START), old.find(AGENTS_END)
        if i >= 0 and j > i:
            new = old[:i] + AGENTS_BLOCK + old[j + len(AGENTS_END):]
        elif old.strip():
            new = old.rstrip("\n") + "\n\n" + AGENTS_BLOCK + "\n"
        else:
            new = AGENTS_BLOCK + "\n"
        if new == old:
            return
        with open(path, "w") as fh:
            fh.write(new)
        print(f"agents: wrote {path}")
    except OSError as e:
        print(f"agents: can't write {path} ({e})")


USAGE = f"""usage: brepl [url] [--port N] [--global] [--profile=DIR] [--no-profile] [--headed] [--no-mcp]

  url        opened at start, like 'g url'

  By default brepl keeps cookies/logins in ./{PROFILE_DIR} and serves MCP on the
  port of ./.mcp.json (its brepl entry, if the port is free), else on a free random
  port in {LOCAL_PORTS[0]}-{LOCAL_PORTS[1]}, written to ./.mcp.json.

  --port N        serve MCP on port N (still written to ./.mcp.json)
  --global        old style: port {MCP_PORT} (or --port N), registered with
                  'claude mcp add' instead of ./.mcp.json
  --profile=DIR   keep the Chrome profile in DIR instead of ./{PROFILE_DIR}
  --no-profile    throwaway profile: nothing is kept on quit
  --headed        show the browser window (e.g. to log in by hand once)
  --no-mcp        don't start the MCP server / write .mcp.json / register"""


class Brepl:
    """brepl as a Python module: the same browser and commands as the REPL.

        from brepl import Brepl
        with Brepl() as b:
            b.go("news.ycombinator")
            text, images = b.page()

    Every method runs one command line and returns what the REPL would print
    (commands still write link.txt, page.txt etc. in the current directory)."""

    def __init__(self, profile: str | None = None, headed: bool = False) -> None:
        self.worker = Worker(os.path.abspath(profile) if profile else None, headed)
        self.worker.start()
        self.worker.ready.wait()
        if self.worker.error:
            raise RuntimeError(self.worker.error)

    def run(self, line: str, images: bool = False):
        """Run a command line (as typed at the prompt); return its text, or
        (text, [png/jpeg bytes]) with images=True."""
        if line.strip() == "q":
            raise ValueError("use close() instead of q")
        text, imgs = self.worker.call(line, capture=True)
        return (text, imgs) if images else text

    def go(self, target: str) -> str:
        return self.run(f"g {target}")

    def url(self) -> str:
        return self.run("u").strip()

    def page(self, parts: int = 1, hide_images: bool = False):
        """p/pp: (text, [images])."""
        return self.run(f"{'pp' if hide_images else 'p'} {max(0, parts)}", images=True)

    def links(self, filter: str = "") -> str:
        return self.run(f"l {filter}".strip())

    def click(self, target, fields: dict[str, str] | None = None) -> str:
        """c: fill fields, then click button number, text or /regex/."""
        target = str(target)
        is_rx = len(target) > 2 and target.startswith("/") and target.endswith("/")
        args = [target if is_rx else shlex.quote(target)]
        args += [shlex.quote(f"{k}={v}") for k, v in (fields or {}).items()]
        return self.run("c " + " ".join(args))

    def submit(self, fields: dict[str, str]) -> str:
        """s: fill fields, then press Enter in the last one."""
        return self.run("s " + " ".join(shlex.quote(f"{k}={v}") for k, v in fields.items()))

    def dump(self, file: str = "", command: str = "") -> str:
        if command:
            return self.run(f"d {file or '-'} {command}")
        return self.run(f"d {file}".strip())

    def same_dump(self, file: str, command: str = "") -> str:
        return self.run(f"d ?{file} {command}".strip())

    def history(self, n: int | None = None) -> str:
        return self.run("h" if n is None else f"h {n}")

    def toggle(self) -> str:
        return self.run("@")

    def serve_mcp(self, port: int = MCP_PORT) -> bool:
        """Also serve this browser as MCP tools on http://localhost:<port>/mcp."""
        return start_mcp(self.worker, port)

    def close(self) -> None:
        self.worker.stop()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def main() -> None:
    import readline  # noqa: F401  line editing/history for input(); redraw after MCP logs

    args = sys.argv[1:]
    if any(a in ("-h", "--help") for a in args):
        print(USAGE + "\n\ncommands:\n" + HELP)
        return
    use_mcp, local, port, urls = True, True, None, []
    profile, headed = os.path.abspath(PROFILE_DIR), False
    while args:
        a = args.pop(0)
        if a == "--no-mcp":
            use_mcp = False
        elif a == "--local":  # the default; kept for old command lines
            local = True
        elif a == "--global":
            local = False
        elif a == "--profile" or a.startswith("--profile="):
            profile = os.path.abspath(a.partition("=")[2] or PROFILE_DIR)
        elif a == "--no-profile":
            profile = None
        elif a == "--headed":
            headed = True
        elif a == "--port" and args and args[0].isdigit():
            port = int(args.pop(0))
        elif a.startswith("-") or urls:
            sys.exit(USAGE)
        else:
            urls.append(a)

    if port is None:
        port = local_port() if local and use_mcp else MCP_PORT

    worker = Worker(profile, headed)
    worker.start()
    worker.ready.wait()
    if worker.error:
        sys.exit(worker.error)
    try:
        if use_mcp and start_mcp(worker, port):
            global PROMPT
            PROMPT = f"brepl:{port}> "
            print(f"mcp: http://localhost:{port}/mcp")
            write_mcp_json(port) if local else register_claude(port)
            write_agents_md()
        if profile:
            print(f"profile: {profile}")
        print("brepl — type ? for help")
        if urls:  # start page: same rules as 'g' (https://, .com/.net/.org)
            worker.call(f"g {urls[0]}")
        while True:
            try:
                line = input(PROMPT).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if line == "q":
                break
            if line:
                worker.call(line)
    finally:
        worker.stop()


if __name__ == "__main__":
    main()

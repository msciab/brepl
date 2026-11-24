#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Print the latest post of an x.com page (profile or timeline) from its HTML.

Reads the HTML from stdin (or a file argument), e.g. from brepl:
    d - xcom/xcom-latest.py                  print it
    d ?xcom/latest.txt xcom/xcom-latest.py   true/false: same latest post as last time

Latest = the highest status id (ids grow with time), pinned or not;
--no-pinned skips the pinned post. Output (stable, no relative ages):
    https://x.com/<user>/status/<id>
    <ISO datetime>
    <text>
No posts and X shows why (account doesn't exist, suspended, protected, nothing
posted yet): prints '(no posts) <X's message>'. No posts and no message (e.g. the
page did not load): exits 1.
"""
import sys
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}


class Posts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.posts: list[dict] = []
        self.post: dict | None = None
        self.post_depth = 0
        self.empty: list[str] = []  # text of X's "no posts" panel (emptyState)
        self.empty_depth = 0
        self.link = None   # (href, depth) of the <a> we are in
        self.text_depth = 0  # inside the first tweetText
        self.ctx_depth = 0   # inside socialContext

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in VOID:
            self.depth += 1
        tid = a.get("data-testid")
        if tid == "emptyState" and not self.empty_depth and tag not in VOID:
            self.empty_depth = self.depth
        if tag == "article" and tid == "tweet" and self.post is None:
            self.post = {"href": "", "time": "", "text": [], "pinned": False, "context": []}
            self.post_depth = self.depth
            return
        if self.post is None:
            return
        p = self.post
        if tag == "a" and "/status/" in a.get("href", ""):
            self.link = (a["href"], self.depth)
        elif tag == "time" and not p["href"] and self.link:
            # the post's own permalink is the <a> around its first <time>
            p["href"] = self.link[0].split("?")[0]
            for suffix in ("/analytics", "/photo/", "/video/"):
                p["href"] = p["href"].split(suffix)[0]
            p["time"] = a.get("datetime", "")
        elif tid == "tweetText" and not p["text"] and not self.text_depth:
            self.text_depth = self.depth
        elif tid == "socialContext" and not self.ctx_depth:
            self.ctx_depth = self.depth
        elif tag == "img" and self.text_depth > 0:
            p["text"].append(a.get("alt", ""))  # emoji are <img alt="..">

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.empty_depth == self.depth:
            self.empty_depth = -1
        if self.post is not None:
            if self.link and self.depth == self.link[1]:
                self.link = None
            if self.text_depth == self.depth:
                self.text_depth = -1  # done: keep only the first (not a quoted) text
            if self.ctx_depth == self.depth:
                self.ctx_depth = 0
                if "".join(self.post["context"]).strip() == "Pinned":
                    self.post["pinned"] = True
            if self.depth == self.post_depth:
                if self.post["href"]:
                    self.posts.append(self.post)
                self.post, self.text_depth, self.ctx_depth, self.link = None, 0, 0, None
        self.depth -= 1

    def handle_data(self, data):
        if self.empty_depth > 0 and data.strip():
            self.empty.append(data.strip())
        if self.post is None:
            return
        if self.text_depth > 0:
            self.post["text"].append(data)
        if self.ctx_depth:
            self.post["context"].append(data)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__.strip())
        return 0
    html = open(args[0], encoding="utf-8", errors="replace").read() if args else sys.stdin.read()
    parser = Posts()
    parser.feed(html)
    posts = [p for p in parser.posts if not (p["pinned"] and "--no-pinned" in sys.argv)]
    if not posts:
        if parser.empty:  # X says why: doesn't exist, suspended, protected, no posts yet
            print("(no posts) " + " ".join(parser.empty))
            return 0
        print("no posts found", file=sys.stderr)  # e.g. the page did not load
        return 1
    latest = max(posts, key=lambda p: int(p["href"].rsplit("/", 1)[-1]))
    print("https://x.com" + latest["href"])
    print(latest["time"])
    print("".join(latest["text"]).strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())

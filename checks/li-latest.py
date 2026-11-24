#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Print the latest post of a LinkedIn activity page from its HTML.

For https://www.linkedin.com/in/<name>/recent-activity/all/ (posts and reposts,
newest first). Reads the HTML from stdin (or a file argument), e.g. from brepl:
    d - li-latest.py                  print it
    d ?linkedin/latest.txt li-latest.py   true/false: same latest post as last time

Also for a company's https://www.linkedin.com/company/<name>/posts/?feedView=all.

Latest = the highest post id when every card has one (so a pinned post does not
win), else the first card (the list is newest first); --no-reposts skips reposts.
Output (stable, no relative ages like "2w"):
    <post url>            '(no link)' when the page shows none (reposts, some profiles)
    <ISO datetime>        decoded from the post id; empty without a link
    <author>  or  <author> (reposted by <name>)
    <text>
Exits 1 if the page has no posts.
"""
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}
CARD = re.compile(r"^expanded.*FeedType_")  # componentkey of a post card (profile activity)
OLD_CARD = re.compile(r"^urn:li:activity:\d+$")  # data-urn of a post card (company posts)
URN = re.compile(r"/feed/update/(urn:li:(?:share|activity|ugcPost):(\d+))")


class Cards(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.cards: list[dict] = []
        self.card: dict | None = None
        self.card_depth = 0
        self.text_depth = 0  # >0 inside the first expandable-text-box, -1 after it
        self.skip_depth = 0  # inside a button (the "…more" link) in the text

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "br" and self.text_depth > 0 and not self.skip_depth:
            self.card["text"].append("\n")
        if tag in VOID:
            return
        self.depth += 1
        if self.card is None:
            if CARD.match(a.get("componentkey") or ""):
                self.card = {"head": [], "text": [], "urn": ""}
                self.card_depth = self.depth
            elif OLD_CARD.match(a.get("data-urn") or ""):
                self.card = {"head": [], "text": [], "urn": a["data-urn"]}
                self.card_depth = self.depth
            return
        c = self.card
        m = URN.search(a.get("href") or "")
        if m and not c["urn"]:
            c["urn"] = m.group(1)
        cls = (a.get("class") or "").split()
        if "visually-hidden" in cls and not self.skip_depth:
            self.skip_depth = self.depth  # screen-reader copy of the visible text
        elif ((a.get("data-testid") == "expandable-text-box" or "update-components-text" in cls)
                and not self.text_depth):
            self.text_depth = self.depth
        elif tag == "button" and self.text_depth > 0 and not self.skip_depth:
            self.skip_depth = self.depth

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.card is not None:
            if self.skip_depth == self.depth:
                self.skip_depth = 0
            if self.text_depth == self.depth:
                self.text_depth = -1
            if self.depth == self.card_depth:
                self.cards.append(self.card)
                self.card, self.text_depth, self.skip_depth = None, 0, 0
        self.depth -= 1

    def handle_data(self, data):
        c = self.card
        if c is None or self.skip_depth:
            return
        if self.text_depth > 0:
            c["text"].append(data)
        elif self.text_depth == 0 and data.strip():
            c["head"].append(data.strip())


def repost(card: dict) -> tuple[str, list[str]]:
    """(who reposted or '', the header after that): '<name>', 'reposted this'
    or '<name> reposted this', then the author."""
    head = [h for h in card["head"] if h != "Feed post"]
    for i, h in enumerate(head[:4]):
        if h.endswith("reposted this"):
            by = h.removesuffix("reposted this").strip() or (head[i - 1] if i else "?")
            return by, head[i + 1:]
    return "", head


def is_repost(card: dict) -> bool:
    return bool(repost(card)[0])


def describe(card: dict) -> list[str]:
    by, head = repost(card)
    author = head[0] if head else "?"
    if card["urn"]:
        ms = int(card["urn"].rsplit(":", 1)[1]) >> 22  # LinkedIn ids start with a ms timestamp
        when = datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"https://www.linkedin.com/feed/update/{card['urn']}/"
    else:
        when, url = "", "(no link)"
    text = re.sub(r"[ \t]+\n", "\n", "".join(card["text"])).strip()
    return [url, when, f"{author} (reposted by {by})" if by else author, text]


def main() -> int:
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__.strip())
        return 0
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    html = open(args[0], encoding="utf-8", errors="replace").read() if args else sys.stdin.read()
    parser = Cards()
    parser.feed(html)
    cards = parser.cards
    if "--no-reposts" in sys.argv:
        cards = [c for c in cards if not is_repost(c)]
    if not cards:
        print("no posts found", file=sys.stderr)
        return 1
    if all(c["urn"] for c in cards):  # ids grow with time: right even with a pinned post first
        latest = max(cards, key=lambda c: int(c["urn"].rsplit(":", 1)[1]))
    else:  # reposts have no id here: the list is newest first
        latest = cards[0]
    print("\n".join(describe(latest)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Using brepl from Python

`brepl.py` can be imported by another Python script. Importing it does nothing
on its own: no browser starts and no MCP server runs until you create a `Brepl`.

## Setup

Put `brepl.py` on `sys.path` (same directory, or `sys.path.insert`) and install
its dependencies, e.g. with uv:

```sh
uv run --with playwright --with html2text --with "mcp>=2.3.0" python myscript.py
```

or declare them inline in `myscript.py`, as `brepl.py` does:

```python
# /// script
# dependencies = ["playwright>=1.45", "html2text>=2025.4.15", "mcp>=2.3.0"]
# ///
```

## Example

```python
from brepl import Brepl

with Brepl() as b:                      # headless, throwaway profile
    print(b.go("news.ycombinator"))     # "https://news.ycombinator.com/  —  Hacker News"
    text, images = b.page()             # numbered buttons/fields/links + PNG bytes
    open("top.png", "wb").write(images[0])
    print(b.links("/item/"))
    print(b.click("login", {"acct": "me", "pw": "secret"}))
    md = b.dump()                       # page as markdown
```

## API

`Brepl(profile=None, headed=False)` launches Chrome. `profile` is a directory
where cookies and logins are kept (e.g. `".brepl-profile"` to reuse the REPL's
logins; only one brepl can use a profile at a time). `None` throws everything
away on close. If Chrome can't start, it raises `RuntimeError`.

Every method runs one REPL command and returns the text the REPL would print
(see [spec/1-spec.md](spec/1-spec.md) for the commands):

| method | command |
|---|---|
| `run(line, images=False)` | any command line; `images=True` returns `(text, [image bytes])` |
| `go(target)` | `g` (url, link number, `/path`, `/substr/`) |
| `url()` | `u` |
| `page(parts=1, hide_images=False)` | `p` / `pp`, returns `(text, [image bytes])` |
| `links(filter="")` | `l` |
| `click(target, fields=None)` | `c` (number, text or `/regex/`; `fields` is `{name: value}`) |
| `dump(file="")` | `d` |
| `history(n=None)` | `h` |
| `toggle()` | `@` (headless ↔ headed) |
| `serve_mcp(port=12549)` | also serve this browser as MCP tools; `False` if the port is busy |
| `close()` | stop the browser (done automatically by `with`) |

Commands are run one at a time on a background thread, so a `Brepl` can be
used from any thread. Like the REPL, commands write `link.txt`, `page.txt`,
`page*.png` etc. in the current directory.

# brepl as a Python module

`brepl.py` can be imported (`import brepl`, with `brepl.py` on `sys.path` and its
dependencies installed). Importing has no side effects: no browser, no MCP
server, no `readline` (loaded by the REPL only; MCP logs redraw the prompt only
when it is loaded).

`brepl.Brepl(profile=None, headed=False)` starts the browser (worker thread);
`profile` is a directory to keep cookies/logins in (`None`: throwaway). A launch
failure raises `RuntimeError`. It is a context manager; `close()` stops it.

Methods (each runs one command line, output captured, never printed):
- `run(line, images=False)`: any REPL command; returns its text, or
  `(text, [image bytes])` with `images=True`. `q` raises `ValueError`.
- `go(target)`, `url()`, `page(parts=1, hide_images=False)` → `(text, images)`,
  `links(filter="")`, `click(target, fields=None)`, `dump(file="")`,
  `history(n=None)`, `toggle()` (`@`).
- `serve_mcp(port=12549)`: also serve this browser as MCP tools (as
  [2-mcp.md](2-mcp.md)); returns False if the port is busy.

Commands still write their files (`link.txt`, `page.txt`, `page*.png`, ...) in
the current directory.

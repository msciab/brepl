# brepl

[![Follow me on X](https://img.shields.io/badge/Follow%20me%20on-X-000000?logo=x&logoColor=white)](https://x.com/msciab)
[![Follow me on LinkedIn](https://img.shields.io/badge/Follow%20me%20on-LinkedIn-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/msciab)

For an introduction read this [post](POST.md).

![](./splash.png)

REPL browser driving headless Chrome, with inline page snapshots for iTerm2 and the VS Code
terminal (`terminal.integrated.enableImages: true`).

    ./brepl.py [url] [--port N] [--global] [--profile=DIR] [--no-profile] [--headed] [--no-mcp]
    ./brepl.py news.ycombinator

A self-contained uv script: the dependencies are declared inline (PEP 723) at the
top of `brepl.py`, and uv installs them on first run (`uv run brepl.py` works too).

By default, in the directory it's started from, brepl:
- keeps a Chrome profile in `./.brepl-profile`, so cookies and logins survive
  restarts (`--profile=DIR` elsewhere, `--no-profile` for a throwaway one);
- serves the same browser as MCP tools and writes the url to `./.mcp.json`
  (Claude Code project scope): it reuses the port already there if free, else
  picks a free random one in 12000-14000. Several brepls, each in its own
  directory, can run side by side. `--global` is the old behaviour: port 12549,
  registered with `claude mcp add`. See [spec/2-mcp.md](spec/2-mcp.md);
- adds instructions to `./AGENTS.md` (created if missing) so that typing a
  command like `u` or `g hn` in the agent chat runs it in brepl (`repl` tool).

| cmd | does |
|---|---|
| `?` | help |
| `g <url>\|<num>\|/<path>\|/<substr>/` | go to url (`https://` + `.com`/`.net`/`.org` tried), link `<num>` from `l`, `/<path>` on the current origin, or the first link whose text/url contains `<substr>` (case-insensitive) |
| `G <like g>` | `g`, then `p`: navigate and show the page |
| `u` | current url |
| `p [n]` / `pp [n]` | page snapshot (`n` parts of 1600px: default 1, `0` = all) with numbered buttons (red), named fields (blue), and links numbered as in `l` (green); `pp` hides images; blank parts skipped; parts saved as `page1.png`…`pageN.png`, lists to `page.txt` / `link.txt` |
| `p <n> <file>` / `p <n> ?<file>` | save part `n` (png) to `file` / whole-number percentage of matching pixels from `file` (then replaced by part `n`) |
| `l [substr\|/regex/]` | internal links as `[text] url`, numbered, optionally filtered by substring or `/regex/` (ignoring case); saved to `link.txt` |
| `c <num>\|<text>\|/regex/ [field=value ...]` | fill fields (name prefixes ok), then click button `<num>`, the one containing `<text>`, or matching `/regex/` |
| `s field=value ...` | fill fields, then submit (Enter in the last one), e.g. `s q=python` |
| `d [file] [command]` | the page as markdown (no images): printed, or written to `file`; with `command`, the page html is piped to that shell command and its output written to `file` (`-` prints it); `d ?file [command]`: `true`/`false` if the output is the same as `file`, which is then replaced; always saved as `page.html` + `page.md` |
| `h [n]` | site history / go to entry `n` / `h -1` back |
| `@` | switch headless ↔ headed (relaunches Chrome, keeps profile, tabs, page, history) |
| `n <message>` | notification via osascript, else printed |
| `q` | quit |

Logins: log in once with `--headed` (visible window), then run headless and the
session is kept. The profile holds session cookies: keep it private (git-ignored).

Full behaviour: [spec/1-spec.md](spec/1-spec.md) (REPL), [spec/2-mcp.md](spec/2-mcp.md) (MCP).

brepl uses Playwright's own Chromium (not your Chrome), installed on first run.

## License

Copyright 2026 Michele Sciabarrà. Licensed under the [Apache License 2.0](LICENSE).

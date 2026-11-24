# brepl as an MCP server

When started, brepl also launches, in a background thread while the REPL is
active, an MCP server (Streamable HTTP) at `http://localhost:<port>/mcp`, and
tells Claude Code about it through `.mcp.json` in the current directory (Claude
Code's project scope):

- the port is, in order: `--port N` if given; the port of the existing
  `mcpServers.brepl` entry of `.mcp.json`, if it's free (so restarts keep the
  same url and connected clients keep working); else a free port at random in
  12000-14000 (printing `port N from .mcp.json is busy` if the old one was
  taken);
- the entry is then set to `{"type": "http", "url": "http://localhost:<port>/mcp"}`;
  other servers in the file are kept, and the file isn't touched when the entry
  is already right. If it can't be parsed it's left alone and a warning is
  printed.

Several brepls, each in its own directory, can run at once. When the port does
change, a Claude Code session must reconnect (`/mcp`), since it reads
`.mcp.json` at start.

With `--global` (the old behaviour) the port is 12549 (or `--port N`) and brepl
registers itself with
`claude mcp add --transport http brepl http://localhost:12549/mcp` (default
local scope, filed by Claude Code under the git root of the current directory).
If an `brepl` entry with that url is already registered, it's left
alone. If `claude` isn't installed, the command to run by hand is printed.

The `.mcp.json` entry (or registration) is permanent: quitting brepl doesn't remove it. You can stop
and restart the REPL as often as you like:
- the server is **stateless** (no MCP sessions), so a client that connected
  before the restart keeps working against the new brepl without reconnecting;
- the port can be reused right away;
- while brepl is stopped, tool calls fail (connection refused) and work again
  once it's running.

What a restart does lose is the browser: it starts again on a blank page, with
empty history and no link or button numbers (and, with `--no-profile`, no
cookies or logins: see the browser profile section of [1-spec.md](1-spec.md)).

The REPL commands are exposed as tools, and they **share the same browser** as
the REPL: page, link numbers, button numbers and history are the same whether a
command comes from the terminal or from MCP.

## Options

    brepl [url] [--port N] [--global] [--profile=DIR] [--no-profile] [--headed] [--no-mcp]

- `--port N`: serve on port N (written to `.mcp.json`, or registered with `--global`).
- `--global`: port 12549 and `claude mcp add` instead of `.mcp.json` (see above).
  `--local`, the old flag for the `.mcp.json` mode, is still accepted.
- `--profile=DIR`, `--no-profile`, `--headed`: the browser profile, see
  [1-spec.md](1-spec.md).
- `--no-mcp`: REPL only; no server, no `.mcp.json`, no registration.
- If the port is busy (e.g. `--port N` or `--global` with another brepl running),
  a warning is printed and only the REPL runs.

## Tools

| tool | REPL | result |
|---|---|---|
| `go(target)` | `g` | url, link number, `/path` on the current origin or `/substr/`; returns `url — title` or the errors |
| `current_url()` | `u` | the current url |
| `page(hide_images=false, pages=1)` | `p [n]` / `pp [n]` | the annotated screenshot as one or more images, plus the buttons/fields list |
| `save_page(n, file, hide_images=false)` | `p <n> <file>` | part `n` as an image, copied as png to `file` |
| `same_page(n, file, hide_images=false)` | `p <n> ?<file>` | percentage of pixels that match the png in `file`, rounded to a whole percent with `%` (`0%` if there is no part `n`; if `file` does not exist, part `n` is saved there and `100%` returned; error if unreadable; then part `n` replaces `file`) |
| `links(filter="")` | `l` | numbered `[text] url` lines; `filter` is a substring or `/regex/` |
| `click(target, fields={})` | `c` | button number, text or `/regex/`; `fields` maps name (or unique prefix) to value |
| `submit(fields)` | `s` | fill `fields` (name or unique prefix to value), then press Enter in the last one |
| `same_dump(file, command="")` | `d ?<file> [command]` | `true` if the output (of `command` on the page html, or the markdown) is the same as the content of `file`, else `false`; `file` is then replaced by the new output (missing: written, `true`) |
| `dump(file="", command="")` | `d` | the page as markdown (no images), or written to `file`; with `command`, the page html piped to that shell command and its output written to `file` (returned if no `file`) |
| `history(n=null)` | `h` | site history, or go to entry `n` / back `-n` |
| `toggle_headed()` | `@` | relaunch Chrome headless ↔ headed, keeping profile, tabs, page, history; returns `headed: <url>` / `headless: <url>` |
| `repl(command)` | any | one REPL command line run verbatim (`q` refused); returns what the REPL prints, plus the images for `p`/`pp` |

## AGENTS.md

When the MCP server starts (both the `.mcp.json` and `--global` modes; not with
`--no-mcp`), brepl makes sure `./AGENTS.md` holds its instructions, as a block
between `<!-- brepl -->` and `<!-- /brepl -->`:

- no file: it's created with the block;
- a file without the block: the block is appended (after a blank line), the rest kept;
- the block present but different: replaced in place; identical: file untouched.

It prints `agents: wrote <path>` when the file changes (a write error is only a
warning). The block tells the agent that when a chat message is a brepl command
line (`?`, `g …`, `u`, `p [n]`, `pp`, `l …`, `c …`, `d …`, `h …`, `@`, `n …`), it calls
the `repl` tool with the message verbatim and shows the output, so typing `u` in
the chat works like typing `u` at the `brepl>` prompt.

`q` is not exposed: the server lives as long as the REPL.

Each tool returns as text what the REPL would print. `page` also returns the
page as JPEG images: `pages` of them (default 1, as for `p`; `0` = all, but at
most 20 over MCP, since they go into the client's context), top to bottom: parts 1600px high overlapping
by 40px, so each part stays legible after a client downscales it, and a badge on
a boundary is whole in one part. The text says how many parts there are; it
stops at 20 parts (32000px) with a note. A short page is a single image. Parts
are shot as rendered and blank parts are skipped, as for the REPL `p`.
`link.txt`, `page.txt`, `page1.png` ... `pageN.png` (and, for `dump`, `page.html`
and `page.md`) are written as for the REPL.

## How it works

Playwright's sync API only works on the thread that started it, so the browser
lives in one worker thread that runs command lines from a queue, one at a time.
The REPL (main thread) and the MCP server (uvicorn on a background thread) both
send their command lines there and wait for the result. For an MCP call, the
output that would go to the terminal is captured and returned as the tool result.
Each MCP call is logged in the terminal as `[mcp] <command>`, followed by what it
returned to the client, indented by two spaces (for `page`, the text plus
`(N images sent to the client)`); then the `brepl:<port>>` prompt is redrawn with any
half-typed line, so the terminal shows everything the client sees.

The server binds to 127.0.0.1 only: plain http is fine locally, but anything that
can reach the port can drive the browser.

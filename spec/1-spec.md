# brepl

brepl is a REPL browser written in Python, run as a self-contained uv script
(`./brepl.py`, dependencies declared inline in PEP 723 format).

Usage: `brepl [url] [--profile=DIR] [--no-profile] [--headed]` (MCP options in
[2-mcp.md](2-mcp.md)). The optional `url` is opened at start with the same
rules as `g <url>` (so `brepl news.ycombinator` works); a failure is reported
and the REPL starts anyway. `brepl -h` prints the usage and the commands.

At startup it launches headless Chrome (Playwright, using the installed Google
Chrome; it falls back to Playwright's bundled Chromium), then reads commands at
an `brepl:<port>>` prompt (`<port>` is the MCP server port; plain `brepl>` with
`--no-mcp` or when the server could not start). A failing command prints an error and the REPL keeps going.

## Browser profile

By default brepl keeps a real Chrome profile in `./.brepl-profile` (the
directory it is started from). Cookies with an expiry (logins), localStorage,
IndexedDB and the cache survive restarts. Session cookies (no expiry) are dropped
on quit, as in a normal Chrome. The path is printed at startup.

- `--profile=DIR` keeps the profile in `DIR` instead (`--profile` alone is
  the default, still accepted).
- `--no-profile` uses a throwaway profile: nothing is kept on quit.
- A profile can be used by one brepl at a time; a second one exits with
  `profile <dir> is in use by another brepl/Chrome`.
- `--headed` shows the browser window instead of running headless, e.g. to log
  in to a site by hand (captcha, 2FA) once with `--headed`, then run headless
  and keep the session.

### The current page follows you (`--headed`)

Before every command (REPL or MCP) brepl syncs its current page with the window:
- a url you reached by hand (typed, clicked) becomes the current page and is
  added to history;
- the tab you were last active in (switched to, focused, clicked, typed in, or
  loaded a page in) wins over the one brepl last used; brepl prints
  `(switched to the tab at <url>)`. A small script injected in every page stamps
  that activity, and Playwright's focus emulation is turned off so focus is real;
- if the current tab was closed, the most recently active one is used.

`g`, `c` and `h <n>` bring their page to the front, so you see where brepl went.
Headless, the current page only changes by commands (and a closed tab falls back
to another): pop-ups opened by sites are not followed.

### Not announcing automation

Chrome is launched without `--enable-automation`, a script run in every page
before the site's own makes `navigator.webdriver` false, and headless tabs send
the normal `Chrome/…` user agent instead of `HeadlessChrome/…`: sites like x.com
otherwise refuse to log in. No extra command-line flags are used for this:
headed Chrome shows an "unsupported command-line flag" bar for any of them
(e.g. `--disable-blink-features=AutomationControlled`).

Chrome runs with its sandbox on (`chromium_sandbox=True`): Playwright otherwise
passes `--no-sandbox`, and a headed Chrome shows a "You are using an unsupported
command-line flag: --no-sandbox" bar.

The profile holds live session cookies: treat it as a secret (it is
git-ignored, as is `.mcp.json`).

Every command that navigates waits until the page has really rendered, not just
when its HTML has loaded (load + network idle). Otherwise the screenshots come
out empty.

## Commands

### `?`
Show the commands.

### `g <url>` | `g <num>` | `g /<path>` | `g /<substr>/`
- `<url>`: add the `https://` prefix if there's no scheme. Try the name as given,
  then with the suffixes `.com`, `.net`, `.org`, and go to the first one that loads.
  Names that don't resolve in DNS are skipped without asking Chrome. Redirects
  count as success. If all of them fail, print the error for each attempt.
  A name that already ends in `.com`/`.net`/`.org`, or that has a port, is tried
  only as given.
- `<num>`: go to url `<num>` from the latest `l` listing.
- `/<path>` (starts with `/` but doesn't end with it, or `/` alone): go to that
  path (with any `?query` or `#fragment`) on the current page's origin, e.g.
  `g /login` on `https://x.com/home` opens `https://x.com/login`. With no page
  open yet (no http(s) origin): `no current site for <path>`.
- `/<substr>/`: go to the first internal link (page order, as in an unfiltered
  `l`) whose line contains `<substr>`, ignoring case. Matches are ranked: the
  whole word in an anchor text, then anywhere in an anchor text, then in the url.
  E.g. `g /next/` follows "Next page" rather than "Yourgirlnextdoor". If several match, say how
  many and follow the first; if none, say so. Doesn't change the `l` numbering.

### `u`
Print the current url.

### `p [n]`
Show the page inline in the terminal (iTerm2's image protocol, also shown by VS
Code's terminal with `terminal.integrated.enableImages`), as JPEG parts 1600px
high stacked top to bottom. `n` is how many (non-blank) parts to show and save,
from the top: **default 1**, `0` = all. When it stops with parts left it prints
`(page <h>px high: <n> parts shown, up to <m> more below; p <n> or p 0 for more)`.
A bad argument prints
`usage: p [n] | p <n> <file> | p <n> ?<file>   (also pp; n parts, 0 = all, default 1)`.

- `p <n> <file>`: make the parts up to `n` only, show only part `n` and write it,
  lossless png, to `file` (folders created as needed); prints
  `page <n> -> <file> (<k> KB png)`. No `page<N>.png` is written or removed.
- `p <n> ?<file>`: the same without writing anything: show nothing, and print
  the percentage of pixels in part `n` that match the png in `file`, rounded to
  the nearest whole percent and followed by `%`. A pixel differs if any RGBA
  channel differs. Pixels present in only one image count as changed; the
  denominator is the area covered by either image. `100%` means identical (so
  `1.77%` changed prints `98%` matching). Together they measure page changes:
  `p 1 snap.png` once, later `p 1 ?snap.png`.
- In both, `n` counts non-blank parts from 1; if the page has fewer, `p <n> <file>`
  prints `no page <n>: the page has <m> non-blank parts` and `p <n> ?<file>`
  prints `0%`. If the comparison image does not exist, part `n` is written
  there (as `p <n> <file>`, folders created) and `100%` is printed; if it is
  unreadable, print an error. The parts never overlap (also
  over MCP), so part `n` is the same from the REPL and from MCP. The buttons/fields
  list is not printed (`page.txt` is emptied), but link numbers are updated.
The page is still scrolled to the bottom first and all its buttons, fields,
images and links are listed and numbered, whatever `n` is. Parts, not one image: VS Code (xterm.js) silently drops an image
over ~16M pixels or ~20MB, which a long page easily is.

Each part is shot as rendered: the viewport is made 1600px high, scrolled to the
part, and captured (then restored and scrolled back to the top). A single
full-page shot would be blank below the first screen or two on virtualized
pages (e.g. the x.com timeline only renders the posts near the viewport). Fixed
bars (headers) therefore appear in every part.

**Blank parts are skipped**: a part is shown only if some text, image, video,
canvas, svg, iframe, form control or background image overlaps it (fixed/sticky
bars, hidden elements and brepl's own overlays don't count); then
`(<n> blank parts of 1600px not shown)` is printed.

Each part shown is also saved, lossless, as `page1.png` ... `pageN.png` in the
current directory (`saved page1.png ... pageN.png`); the `page<N>.png` files of
the previous `p` are deleted first. With:
- buttons outlined in **red** with a number to identify them;
- input fields outlined in **blue** with the name of the field;
- images (`<img>` seen in the screenshot, at least 24×24px, so icons and tracking
  pixels are left out) listed with their size and source, without a screenshot
  overlay or number;
- internal links outlined in **green** with their number, placed right after
  the link. The numbers are the same as an unfiltered `l`: `p` also refreshes
  the list used by `g <num>` and rewrites `link.txt`, so `g <num>` follows a link
  seen in the image. Every anchor pointing to the same url shows the same number.

Then print the numbered buttons, the fields (with their type and current value),
and the images (size and src),
and save the same listing, headed by the page url, to `page.txt` in the current
directory (overwritten each time).

- Before the screenshot the page is scrolled to the end, so content that only
  loads when scrolled into view is rendered.
- Only what is really visible is marked: clipped areas (inside scroll boxes or
  overflow-hidden ancestors), transparent or tiny elements are left out.
  Buttons that are not visible are not numbered.
- Fields that are not visible (e.g. custom-styled radios) are still listed,
  marked `(not shown)`, and can still be filled by name.

### `pp [n]`
Like `p` (including `page.txt`), but with the page's images removed: images,
icons, video, canvas and iframes are replaced by white space of the same size,
so the layout doesn't change; background images are dropped.

### `l [substr|/regex/]`
List the internal (same-host) urls of the page, numbered, in the format
`[<text>] url`, where `<text>` is the text of the anchor. For an icon-only link it
is the icon's alt, title or aria-label.

With `substr`, list only the links whose path/query or anchor text contains it
(the text match ignores case). If several anchors point to the same url, any of
their texts can match, and the matching one is shown.

With `/regex/` (starts and ends with `/`), list the links whose path/query or
anchor text matches the regular expression (Python syntax, ignoring case), e.g.
`l /^download/` for the anchors that start with "Download", or
`l /page\/[0-9]+\/$/` for the pagination links. An invalid regex prints the error.

The listing, headed by the page url, is also saved to `link.txt` in the current
directory (overwritten each time).

### `d [file]`
Dump the current page as markdown: the rendered DOM (after scripts ran), converted
with html2text. Images are dropped, links are kept with absolute urls, lines are
not wrapped.
- no `file`: print the markdown, then `(saved page.html, page.md)`;
- `file`: write it there (folders created as needed), and print
  `<url> -> <file> (<n> lines)`.

Either way the rendered DOM is saved as `page.html` and its markdown as `page.md`
in the current directory (overwritten by each `d`).

### `c <num>|<text>|/regex/ [<field>=<value> ...]`
Click a button:
- `<num>`: the button with that number in the latest `p`/`pp`;
- `<text>`: the first visible button whose text/description contains `<text>`;
- `/regex/`: the first visible button whose text/description matches the regular
  expression (Python syntax, ignoring case), e.g. `c /^log\s*in/`. The regex is
  taken as typed (backslashes kept); fields can follow it. An invalid regex prints
  the error.

As with `<text>`, if no button matches, any visible element whose text matches is
clicked (links too), so anchor the regex (`^...$`) to avoid surprises.

Optionally fill a sequence of fields before clicking:
- `<field>` can be shortened to a prefix of its name. If the prefix is
  ambiguous, print a warning and do nothing.
- When the same name exists in several forms (e.g. login and signup), the field
  in the clicked button's form is used.
- radio: `<value>` picks the option with that value (e.g. `amount=10`);
  checkbox: `on`/`off`; select: the option value or label.
- Values with spaces are quoted: `q="hello world"`.

If the page changes (including a reload of the same url), print the new url,
otherwise `(same page)`. If the click opens a new tab, switch to it.

### `h [<n>]`
Keep a history of every page visited.
- No `<n>`: show the history of the current site, numbered, with `*` on the
  current page.
- `<n> > 0`: go to entry `<n>` of that list.
- `<n> < 0`: go back `<n>` pages in the full history (`h -1` goes back).
  Moving with `h` doesn't add entries, so repeating `h -1` keeps going back.

### `@`
Switch between headless and headed (visible window). A running Chrome can't
change mode, so it is closed and relaunched in the other mode with the same
profile (cookies and logins kept; with `--no-profile` they are lost). The open
tabs are reopened at their urls, the current page stays current (and, headed,
is brought to the front), and history and link numbers are kept; button numbers
and filled-in form values are not. Prints `headed: <url>` or `headless: <url>`
(plus the tab count if more than one). If the new mode can't start, it goes back
to the old one and says so. Same as starting with or without `--headed`.

### `n <message>`
Show `<message>` as a macOS notification titled `brepl` via `osascript`
(`display notification`, the message passed as an argument, not inlined in the
script) and print `notified`, e.g. for an agent to say a long task is done.
Otherwise (no `osascript`, it fails, or no result within 10s) print
`brepl: <message>`. No message: prints `usage: n <message>`.

If the message contains a link (the first `http://` or `https://` url), the
notification is clickable and opens that link in the default browser:
`osascript` shows a dialog (`display dialog`, title `brepl`) with `Cancel` and
`Open` buttons (Open is the default; gives up after 10 minutes); `Open` makes
the same osascript open the url in the default browser started as a new
instance (`/usr/bin/open -n -a <default app> <url>`, the app looked up via
`NSWorkspace`): a plain `open`/`open location` hands the url to an already
running instance, which may be brepl's own headless Chrome, so nothing shows. The osascript runs detached
(it outlives brepl): `n` prints
`notified` at once and does not wait for the click.
Doesn't touch the browser.

### `q`
Quit (EOF / Ctrl-D too).

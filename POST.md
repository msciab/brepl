# `brepl`: browse the web from a REPL, together with your AI agent

There are tons of browser automation tools out there, yet I couldn't find a plain, simple REPL to browse the web from the shell. So I built one, and it feels a lot like a debugger: **brepl**.

Here's how it works: you start it with `./brepl.py` (you just need [uv](https://docs.astral.sh/uv/) installed) and you get a command line. Under the hood it runs a headless Chrome, which you normally never see.

Most of the time you browse without looking at anything, straight from the command line. For the cases where you do need to interact, the `@` command shows the browser window, which is handy for logging in to the various services. brepl keeps a browser profile, so your logins persist across sessions.

Say I want to browse Reddit from the command line: I run `./brepl.py reddit.com`, type `@` right away and log in. From then on, I can do everything from the command line.

I go to `g /r/commandline` and grab the page with `p`. If your terminal supports graphics, the page is shown inline, and it's also saved as `page1.png` (that's the first part; `p 0` gives you all of them, `p 3` the first three). Feeling lazy? `G` is a `g` immediately followed by a `p`.

The screenshot highlights links (green) and buttons (red), both numbered, and input fields (blue) labeled with their name.

So you can follow a link by its number: `g 13` opens the link marked 13. You can also click buttons and fill in fields: `c 3` clicks button 3, `c login user=me pw=secret` fills in the login form and clicks "login", and `s q=python` types "python" into the `q` field and submits it.

All of this makes it really easy to build automations interactively and then have an AI run them. The REPL is also an MCP server that agents can talk to: on startup it writes a `.mcp.json` and an `AGENTS.md` that tell the agent how to use it.

So the same commands can be run by an AI agent like Claude.

I usually run it in a VS Code terminal: I browse, mixing commands I type myself with the AI handling the repetitive work, and then I write specs that use those commands to describe the automations.

**NOTE** the repl is an http agent so it must be active to use it from your agent. I usually have it open and work together command line and on the agent in parallel, an I use vscode as glue and to see easily images. 

This lets me build scrapers, checkers and other tools in a fraction of the time, just by trying things out. And example is in folder `check` where I built a tool to check frequent accounts.

There are many other features, just check the README on github.

The project is on [github.com/msciab/brepl](https://github.com/msciab/brepl), and it is released under the Apache 2.0 license.

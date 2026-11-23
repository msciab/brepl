be brief

update the spec before implementing

<!-- brepl -->
## brepl

A brepl browser is available as the `brepl` MCP server. When a chat message is a
brepl command line (`?`, `g <url|num|/substr/>`, `u`, `p [n]`, `pp [n]`,
`p <n> ?<image>`, `l [filter]`, `c <button> [field=value ...]`, `d [file]`, `h [n]`, `@`,
`n <message>`), call the
`repl` tool with the message verbatim and show its output as is; the images from
`p`/`pp` are the page. `?` lists the commands.
<!-- /brepl -->

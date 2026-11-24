Create `checks.py` as a uv script.

it  is a python script connecting to a browser availabe as `brepl` mcp server  connected over http using an .mcp.json passed ad parameters. Read its spec from ../README.md

---

It reads the following files

- likedin/000.txt
- x.com/000.txt

and creates a list of ({url}, {site}, {name}) to monitor and sort it random, then every 30 seconds picks one item and perform a check and a notify.

Once the list has been emptied the files are read again and the loop restart

## x.com

For every  account do:

```text
g x.com/{name}
d ?xcom/{name}.html ./xcom-latest.py
```

If false notify with `n changed {url}` 

## linkedin.com

For eveyry account

```text
g https://linkedin.com/in/{name}/recent-activity/all
d ?linkedin/{name}.html ./li-latest.py
```

If false notify with `n changed {url}` 

Notify with
`n changed https://linkedin.com/in/{name}/recent-activity/all` if false (post changed).

notifying with `n changed https://www.linkedin.com/company/{name}/posts/?feedView=all`.


# fallback
Any other output (no posts found, command error) is reported to stderr as a
failure; continue with the remaining accounts.

# notes
- Usage: `./checks.py [path/to/.mcp.json]` (default `../.mcp.json`, the brepl
  directory); the lines are sent to the `repl` tool of the `brepl` server.
- It never starts brepl nor creates a profile: it uses the brepl already running
  (with its profile); if none answers it exits with an error.
- Files and scripts in `d` are given as absolute paths under `checks/`, since
  brepl resolves them from its own directory.
- `xcom/000.txt` and `linkedin/000.txt`: one url or name per line, `#` comments;
  `linkedin.com/company/{name}` is saved as `linkedin/company-{name}.html`.


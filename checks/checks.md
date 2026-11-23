Create `checks.py` as a uv script using the `Brepl` module documented in
`../MODULE.md`, importing `../brepl.py` (add the parent directory to
`sys.path`), run from this
directory, and use the persistent Chrome profile in `.brepl-profile` so the
saved logins are available. The script reads account identifiers from the
newline-delimited files below and uses the existing PNGs as comparison
baselines.

`checks.sh` runs `checks.py` from this directory in an endless loop, every
`INTERVAL` minutes (a variable at the top, default 30). A failed run is
reported to stderr and the loop continues; Ctrl-C stops it.

## X

Open `x.com` once. For every non-empty identifier in `xcom/000.txt`, navigate
to `/{identifier}` and compare the first screenshot part with
`xcom/{identifier}.png`, equivalent to:

```text
g x.com
g /MiaAI_Lab
p 1 ?xcom/MiaAI_Lab.png
```

Notify with `n changed https://x.com/{identifier}` if the matching-pixel percentage is below 80%.

## LinkedIn

Open `linkedin.com` once. For every non-empty, URL-encoded identifier in
`linkedin/000.txt`, navigate to
`https://linkedin.com/in/{identifier}/recent-activity/all` and compare the
first screenshot part with `linkedin/{identifier}.png`, equivalent to:

```text
g linkedin.com
g https://linkedin.com/in/franco-bernab%C3%A8/recent-activity/all
p 1 ?linkedin/franco-bernab%C3%A8.png
```

Notify with
`n changed https://linkedin.com/in/{identifier}/recent-activity/all` if the
matching-pixel percentage is below 90%.

When a baseline image is missing, `p 1 ?<image>` creates it and reports `100%`;
do not notify for that first capture. Report a failed navigation or comparison
to stderr, continue with the remaining accounts, and exit nonzero if any check
failed.

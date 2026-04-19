# shmoney-scraper

## Source adapters

### Facebook Pages (v1 status: blocked by robots.txt)

`FacebookPagesAdapter` scrapes public Page HTML with a pre-fetch
`urllib.robotparser` check. In practice, `facebook.com/robots.txt` disallows
every user-agent not on Meta's allowlist, so a compliant run against real
Pages emits 0 rows:

```
$ pipeline run --source facebook --limit 2
upserted 0 rows
```

This is the correct behavior per the PRD ("respect robots.txt, no anti-bot
evasion"). The adapter, tests, rate limiter, and loud-fail parser are in
place and exercised by fixtures, so the module is ready to plug into a
lawful access path when one is chosen.

Practical paths forward (deferred beyond v1):

- **Graph API** via an approved app + Page access token — the intended
  long-term route; would replace the HTML scrape entirely.
- **Operator-supplied Page data** (export / manual copy) fed through the
  same adapter surface.

Do not bypass the robots check. If a future run ever needs to hit Facebook
HTML, the UA must be on Meta's allowlist or the access path must switch to
the Graph API.

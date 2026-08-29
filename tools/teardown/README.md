# tools/teardown

Turns a raw run log into a readable post-mortem.

Regenerates a typeset campaign teardown PDF from a campaign's raw run log.
Run everything from a directory holding `<run>.jsonl` and `<run>.log`
(they are named `live3.*` throughout — edit the filenames for another run).

```sh
python build.py    # live3.jsonl + live3.log  ->  live3_full.json  (merged, per-call)
python dump.py     # live3_full.json          ->  live3_all_calls.txt  (grep-able)
python mkhtml.py   # live3_full.json          ->  live3_teardown.html
node topdf.js live3_teardown.html live3_teardown.pdf
```

`topdf.js` drives Chrome through the `puppeteer-core` already installed under
`services/messaging/node_modules`. Fonts come from Google Fonts at render time, so the
PDF build needs network; Devanagari falls back to Nirmala UI without it.

Pull a fresh run off the VPS with:

```sh
scp root@<host>:/opt/voice-agent/campaignlogs/<run>.{jsonl,log} .
```

The rendered analysis of one such run — anonymised — is
[`docs/campaign-teardown.md`](../../docs/campaign-teardown.md).

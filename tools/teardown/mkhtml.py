"""Render the live3 campaign into a print-ready HTML document (-> PDF via Chrome)."""
import json, html, statistics, collections, io, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
B = json.load(open('live3_full.json', encoding='utf-8'))
MAN, CALLS, WA = B['manifest'], B['calls'], B['whatsapp']
E = html.escape


def pct(vals, p):
    if not vals:
        return 0
    v = sorted(vals)
    k = (len(v) - 1) * p
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def secs(ms):
    if ms is None or ms < 0:
        return '—'
    return f'{ms/1000:.1f}s'


# ── aggregates ────────────────────────────────────────────────────────────
conn = [c for c in CALLS if c['answered_ms'] > 0]
never = [c for c in CALLS if c['answered_ms'] <= 0]
disp = collections.Counter(c['disposition'] for c in CALLS)
vm = [c for c in CALLS if c['disposition'] == 'voicemail']
people = [c for c in conn if c['disposition'] != 'voicemail']
links = [c for c in CALLS if c['link_channel']]
overtalk = [c for c in CALLS if c['spoke_during_opener_ms'] > 0]
deadair = [c for c in conn if c['frames_with_speech'] == 0]
ans = [c['answered_ms'] / 1000 for c in conn]
talk = [c['talk_ms'] / 1000 for c in CALLS if c['talk_ms'] > 0]
lat = [v for c in CALLS for v in (c['reply_latencies_ms'] or [])]
short = [c for c in people if 0 < c['talk_ms'] < 10000]
warn_heads = collections.Counter(w['head'] for c in CALLS for w in c['warnings'])
opener_reason = collections.Counter(c['opener_reason'] or '(never released)' for c in CALLS)
amd_kinds = collections.Counter(c['amd'] or '(no verdict)' for c in CALLS)
never_cause = collections.Counter(c['hangup_cause'] for c in never)
turns_total = sum(c['turns'] for c in CALLS)
p_turns = sum(1 for c in CALLS for t in (c['transcript'] or []) if 'PROSPECT' in t)
a_turns = sum(1 for c in CALLS for t in (c['transcript'] or []) if 'AGENT' in t)

DISP_LABEL = {
    'link_sent': 'link sent', 'hung_up': 'hung up', 'no_answer': 'no answer',
    'no_response': 'no response', 'voicemail': 'voicemail', 'incomplete': 'incomplete',
}
DISP_TONE = {
    'link_sent': 'good', 'voicemail': 'warn', 'incomplete': 'crit',
    'hung_up': 'plain', 'no_response': 'plain', 'no_answer': 'muted',
}


def flags(c):
    f = []
    if c['link_channel']:
        f.append(('good', 'demo link ' + c['link_status']))
    if c['disposition'] == 'voicemail' and c['talk_ms'] > 0:
        f.append(('crit', f"pitched a recording for {c['talk_ms']/1000:.1f}s"))
    if c['spoke_during_opener_ms'] > 0:
        f.append(('warn', f"talked over them · {c['spoke_during_opener_ms']}ms"))
    if c['answered_ms'] > 0 and c['frames_with_speech'] == 0:
        f.append(('warn', 'dead air — not one speech frame'))
    if 'overridden' in (c['amd'] or ''):
        f.append(('warn', 'AMD said machine, agent overruled it'))
    if any(w['head'].startswith('playout underrun') for w in c['warnings']):
        g = max(int(w['attrs'].get('gap_ms', 0)) for w in c['warnings'] if w['head'].startswith('playout underrun'))
        f.append(('crit', f'playout underrun · {g/1000:.1f}s of silence'))
    if c['refused']:
        f.append(('crit', 'refused — nothing may be sent'))
    return f


# ── chart bits ────────────────────────────────────────────────────────────
def hbar(label, value, total, note=''):
    w = 0 if not total else value / total * 100
    return (f'<div class="fr"><div class="fl">{E(label)}</div>'
            f'<div class="ft"><div class="fb" style="width:{w:.2f}%"></div></div>'
            f'<div class="fv">{value}</div><div class="fn">{E(note)}</div></div>')


def histogram(c):
    """Frames that crossed the 300 speech floor, by loudness band. The two sub-floor
    bands swamp every call, so they are counted in the caption instead of plotted."""
    hist = c['energy_hist']
    bands = ['300–599', '600–999', '1k–2k', '2k–4k', '4k–8k', '8k+']
    speech = hist[2:]
    below = hist[0] + hist[1]
    peak = max(range(len(speech)), key=lambda i: speech[i])
    top = speech[peak] or 1
    bars = []
    for i, v in enumerate(speech):
        h = 0 if not v else max(2, round(v / top * 34))
        lab = f'<span class="hn">{v}</span>' if i == peak and v else ''
        bars.append(f'<div class="hcol">{lab}<div class="hbar" style="height:{h}px"></div>'
                    f'<div class="hlab">{bands[i]}</div></div>')
    body = ('<div class="hrow">' + ''.join(bars) + '</div>') if sum(speech) else \
           '<div class="hempty">not one inbound frame crossed the speech floor</div>'
    return ('<div class="hist">' + body +
            f'<div class="hcap">{sum(speech)} of {sum(hist)} inbound frames crossed the 300 speech floor · '
            f'{below} sat below it (silence and line noise, not plotted) · barge-in needs 900</div></div>')


def kv(rows):
    return '<dl class="kv">' + ''.join(
        f'<dt>{E(k)}</dt><dd>{v}</dd>' for k, v in rows if v not in (None, '')) + '</dl>'


# ── document ──────────────────────────────────────────────────────────────
O = []
W = O.append

W('''<meta charset="utf-8">
<title>Campaign live3 — Call Teardown</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Devanagari:wght@400;500;600&display=swap">
<style>
:root{
  --paper:#ffffff; --tint:#f3f4f8; --tint2:#e9ebf2;
  --ink:#141726; --ink2:#464b60; --ink3:#767c92;
  --rule:#d6d9e4; --rule2:#b9bed0;
  --blue:#2a78d6; --blue-d:#184f95; --blue-l:#cde2fb;
  --good:#0ca30c; --warn:#fab219; --crit:#d03b3b;
  --body:"IBM Plex Sans Devanagari","Nirmala UI",system-ui,sans-serif;
  --disp:"Archivo","Archivo Narrow",system-ui,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,Consolas,monospace;
}
*{box-sizing:border-box}
@page{ size:A4; margin:0; }
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:var(--body); font-size:8.6pt; line-height:1.5;
  font-variant-numeric:tabular-nums;
}
h1,h2,h3,h4{font-family:var(--disp);text-wrap:balance;margin:0}
.mono{font-family:var(--mono)}

/* ---------- cover ---------- */
.cover{break-after:page;padding-top:34mm}
.eyebrow{font-family:var(--mono);font-size:8pt;letter-spacing:.18em;text-transform:uppercase;color:var(--blue-d)}
.cover h1{font-size:41pt;line-height:.98;letter-spacing:-.02em;font-weight:700;margin:6mm 0 4mm}
.cover .sub{font-size:12pt;color:var(--ink2);max-width:62ch;line-height:1.45}
.rulebar{height:3px;background:var(--blue-d);width:38mm;margin:7mm 0}
.coverstats{display:grid;grid-template-columns:repeat(4,1fr);gap:5mm;margin-top:14mm;
  border-top:1px solid var(--rule);padding-top:5mm}
.cs .n{font-family:var(--disp);font-size:25pt;font-weight:700;line-height:1;letter-spacing:-.02em}
.cs .l{font-size:7.6pt;color:var(--ink3);text-transform:uppercase;letter-spacing:.09em;margin-top:2mm}
.cs.good .n{color:var(--good)} .cs.warn .n{color:#8a6100} .cs.crit .n{color:var(--crit)}

/* ---------- generic page furniture ---------- */
.sheet{padding:0}
section{break-inside:avoid}
h2.sec{font-size:15pt;font-weight:700;letter-spacing:-.01em;margin:0 0 1mm}
h2.sec + .dek{color:var(--ink2);margin:0 0 4mm;max-width:70ch}
h3.sub{font-family:var(--mono);font-size:7.6pt;letter-spacing:.16em;text-transform:uppercase;
  color:var(--blue-d);border-bottom:1px solid var(--rule);padding-bottom:1.4mm;margin:0 0 3mm}

/* ---------- funnel ---------- */
.funnel{display:flex;flex-direction:column;gap:1.6mm}
.fr{display:grid;grid-template-columns:44mm 1fr 11mm 46mm;align-items:center;gap:3mm}
.fl{font-size:8.4pt}
.ft{height:9px;background:var(--tint2);border-radius:2px;overflow:hidden}
.fb{height:9px;background:var(--blue);border-radius:0 2px 2px 0}
.fv{font-family:var(--disp);font-weight:700;font-size:10pt;text-align:right}
.fn{font-size:7.6pt;color:var(--ink3)}

/* ---------- tables ---------- */
table{width:100%;border-collapse:collapse;font-size:8pt}
th{font-family:var(--mono);font-size:7pt;letter-spacing:.12em;text-transform:uppercase;
  color:var(--ink3);text-align:left;font-weight:400;border-bottom:1px solid var(--rule2);padding:1.6mm 2mm}
td{padding:1.5mm 2mm;border-bottom:1px solid var(--rule);vertical-align:top}
tbody tr:nth-child(even){background:#fafbfd}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
td.seq{font-family:var(--disp);font-weight:700;color:var(--ink3);text-align:right;width:8mm}

/* ---------- chips ---------- */
.chip{display:inline-flex;align-items:center;gap:1.4mm;font-size:7.4pt;line-height:1;
  padding:1.1mm 2mm;border-radius:2px;background:var(--tint);border:1px solid var(--rule);
  white-space:nowrap;color:var(--ink)}
.chip .dot{width:6px;height:6px;border-radius:50%;background:var(--ink3);flex:none}
.chip.good .dot{background:var(--good)} .chip.good{background:#eaf7ea;border-color:#bfe3bf}
.chip.warn .dot{background:var(--warn)} .chip.warn{background:#fdf4e0;border-color:#eed9a8}
.chip.crit .dot{background:var(--crit)} .chip.crit{background:#fbebeb;border-color:#eec2c2}
.chip.muted{color:var(--ink3)} .chip.muted .dot{background:var(--rule2)}

/* ---------- key/value ---------- */
.kv{display:grid;grid-template-columns:auto 1fr;gap:.8mm 4mm;margin:0}
.kv dt{font-family:var(--mono);font-size:7.2pt;color:var(--ink3);letter-spacing:.04em;white-space:nowrap}
.kv dd{margin:0;font-size:8.2pt}

/* ---------- call sheet ---------- */
.call{break-before:page;padding-top:2mm}
.callhead{display:grid;grid-template-columns:16mm 1fr auto;gap:4mm;align-items:start;
  border-bottom:2px solid var(--ink);padding-bottom:3mm;margin-bottom:4mm}
.callno{font-family:var(--disp);font-size:30pt;font-weight:700;line-height:.85;
  letter-spacing:-.03em;color:var(--blue-d)}
.callno small{display:block;font-family:var(--mono);font-size:6.4pt;font-weight:400;
  letter-spacing:.14em;color:var(--ink3);margin-top:1.5mm}
.callhead h3{font-family:var(--disp);font-size:14.5pt;font-weight:700;letter-spacing:-.01em;line-height:1.12}
.meta{font-size:8pt;color:var(--ink2);margin-top:1.2mm}
.meta .mono{font-size:7.6pt;color:var(--ink3)}
.chipstack{display:flex;flex-direction:column;gap:1.2mm;align-items:flex-end}

.grid2{display:grid;grid-template-columns:1fr 1fr;gap:4mm 7mm}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:4mm 6mm}
.block{break-inside:avoid}
.opener{background:var(--tint);border-left:2px solid var(--blue);padding:2.5mm 3mm;margin-top:2mm}
.opener .line{font-size:10pt;line-height:1.45}
.flagrow{display:flex;flex-wrap:wrap;gap:1.5mm;margin:3mm 0 0}

/* ---------- histogram ---------- */
.hist{margin-top:1mm}
.hrow{display:flex;align-items:flex-end;gap:2px;height:44px}
.hcol{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:stretch;position:relative}
.hbar{background:var(--blue);border-radius:2px 2px 0 0}
.hempty{height:44px;display:flex;align-items:center;justify-content:center;background:var(--tint);
  border-radius:2px;font-size:7.4pt;color:var(--ink3)}
.hn{position:absolute;top:-4.5mm;left:0;right:0;text-align:center;font-family:var(--mono);
  font-size:6.2pt;color:var(--ink2)}
.hlab{font-family:var(--mono);font-size:5.6pt;color:var(--ink3);text-align:center;margin-top:1mm}
.hcap{font-size:6.8pt;color:var(--ink3);margin-top:1.5mm}

/* ---------- transcript ---------- */
.tx{margin-top:2mm;border-top:1px solid var(--rule)}
.txr{display:grid;grid-template-columns:11mm 17mm 1fr;gap:2mm;padding:1.6mm 0;
  border-bottom:1px solid var(--rule);break-inside:avoid}
.txt-t{font-family:var(--mono);font-size:7pt;color:var(--ink3);text-align:right;padding-top:.6mm}
.who{font-family:var(--mono);font-size:6.6pt;letter-spacing:.12em;text-transform:uppercase;padding-top:.8mm}
.who.agent{color:var(--blue-d)} .who.prospect{color:var(--ink3)}
.said{font-size:9.4pt;line-height:1.5}
.txr.agent{background:#f7f9fd}
.empty{color:var(--ink3);font-style:italic;padding:2.5mm 0}

/* ---------- warnings + trace ---------- */
.warns{margin-top:2mm;display:flex;flex-direction:column;gap:1.4mm}
.wrow{display:grid;grid-template-columns:12mm 1fr;gap:2mm;font-size:7.6pt;
  border-left:2px solid var(--warn);padding:1.4mm 2mm;background:#fdf9ef;break-inside:avoid}
.wrow.err{border-left-color:var(--crit);background:#fcf0f0}
.wrow .wt{font-family:var(--mono);font-size:6.8pt;color:var(--ink3)}
.wrow .wa{font-family:var(--mono);font-size:6.6pt;color:var(--ink2);word-break:break-word}
.trace{margin-top:2mm;font-family:var(--mono);font-size:6.5pt;line-height:1.65;color:var(--ink2)}
.trace .tr{display:grid;grid-template-columns:11mm 8mm 1fr;gap:1.5mm;
  border-bottom:1px dotted var(--rule);padding:.35mm 0}
.trace .lv{color:var(--ink3)}
.trace .lv.WARN{color:#8a6100} .trace .lv.ERROR{color:var(--crit)}
.trace .at{text-align:right;color:var(--ink3)}
.trace .ta{color:var(--ink3)}
.note{font-size:7.4pt;color:var(--ink3);margin-top:1.5mm}
ul.tight{margin:1mm 0 0;padding-left:4mm}
ul.tight li{margin-bottom:1.2mm}
</style>
''')

# ══ COVER ══
W('<div class="sheet cover">')
W('<div class="eyebrow">Outbound voice agent · run log teardown</div>')
W('<h1>Campaign live3<br>50 calls, end to end</h1>')
W(f'<div class="rulebar"></div>')
W(f'<p class="sub">Every call the agent placed on {E(MAN["started_ist"])} — what the network did, '
  f'what the agent said, what the prospect said back, and every warning the run threw. '
  f'Reconstructed from <span class="mono">{E(MAN["results_csv"])}</span>, '
  f'<span class="mono">campaignlogs/{E(MAN["run"])}.jsonl</span> and '
  f'<span class="mono">campaignlogs/{E(MAN["run"])}.log</span> on the Mumbai VPS.</p>')
W('<div class="coverstats">')
for cls, n, l in [('', 50, 'dialled'), ('', len(conn), 'connected'),
                  ('', len(people), 'reached a person'), ('good', len(links), 'demo links promised')]:
    W(f'<div class="cs {cls}"><div class="n">{n}</div><div class="l">{l}</div></div>')
W('</div>')
W('<div class="coverstats" style="margin-top:5mm;border-top:0;padding-top:0">')
for cls, n, l in [('warn', len(vm), 'recordings pitched'), ('warn', len(overtalk), 'openers talked over'),
                  ('crit', sum(warn_heads.values()), 'warnings + errors'),
                  ('', f'{sum(talk)/60:.0f}m', 'total talk time')]:
    W(f'<div class="cs {cls}"><div class="n">{n}</div><div class="l">{l}</div></div>')
W('</div>')
W('</div>')

# ══ SUMMARY ══
W('<div class="sheet">')
W('<h2 class="sec">What the run did</h2>')
W('<p class="dek">Fifty numbers dialled in sequence, 8&nbsp;s apart, ring timer 90&nbsp;s, stop after 50 answered. '
  'The funnel below counts each call once, at the furthest stage it reached.</p>')

W('<h3 class="sub">Funnel</h3><div class="funnel">')
W(hbar('dialled', 50, 50, 'the whole lead file'))
W(hbar('the network connected', len(conn), 50, f'{len(never)} never did'))
W(hbar('a person answered', len(people), 50, f'{len(vm)} were answering machines'))
W(hbar('stayed past 10 s', len([c for c in people if c['talk_ms'] >= 10000]), 50,
       f'{len(short)} left inside 10 s'))
W(hbar('heard the whole pitch', len([c for c in people if c['talk_ms'] >= 60000]), 50, 'talked 60 s or more'))
W(hbar('demo link promised', len(links), 50, 'both queued on WhatsApp, 0 failed'))
W('</div>')

W('<div class="grid2" style="margin-top:7mm">')
W('<div class="block"><h3 class="sub">Where the 12 dead calls died</h3><table><tbody>')
for k, v in never_cause.most_common():
    note = {'timeout': 'rang out unanswered inside the 90 s timer',
            'unspecified': 'Telnyx gave no cause — worth checking'}.get(k, '')
    W(f'<tr><td>{E(k)}</td><td class="num">{v}</td><td style="color:var(--ink3)">{E(note)}</td></tr>')
W('</tbody></table></div>')

W('<div class="block"><h3 class="sub">How every call ended</h3><table><tbody>')
for k, v in disp.most_common():
    tone = DISP_TONE.get(k, 'plain')
    W(f'<tr><td><span class="chip {tone}"><span class="dot"></span>{E(DISP_LABEL.get(k,k))}</span></td>'
      f'<td class="num">{v}</td></tr>')
W('</tbody></table></div>')
W('</div>')

W('<div class="grid2" style="margin-top:7mm">')
W('<div class="block"><h3 class="sub">Which rule decided someone was there</h3><table><tbody>')
for k, v in opener_reason.most_common():
    W(f'<tr><td class="mono" style="font-size:7.4pt">{E(k)}</td><td class="num">{v}</td></tr>')
W('</tbody></table><p class="note">The opener fires on whichever rule trips first. '
  '<span class="mono">timeout</span> means nothing tripped and the 1.5&nbsp;s ceiling ran out — '
  'the agent started talking on faith.</p></div>')

W('<div class="block"><h3 class="sub">What answering-machine detection said</h3><table><tbody>')
for k, v in amd_kinds.most_common():
    W(f'<tr><td class="mono" style="font-size:7.4pt">{E(k)}</td><td class="num">{v}</td></tr>')
W('</tbody></table><p class="note">AMD verdicts land ~4&ndash;6&nbsp;s after pickup — often after the opener '
  'has already gone out.</p></div>')
W('</div>')

W('<h3 class="sub" style="margin-top:7mm">Timing</h3>')
W('<table><thead><tr><th>measure</th><th class="num">median</th><th class="num">p90</th>'
  '<th class="num">worst</th><th class="num">n</th></tr></thead><tbody>')
W(f'<tr><td>answer supervision — dial to “answered”</td><td class="num">{statistics.median(ans):.1f}s</td>'
  f'<td class="num">{pct(ans,.9):.1f}s</td><td class="num">{max(ans):.1f}s</td><td class="num">{len(ans)}</td></tr>')
W(f'<tr><td>talk time once connected</td><td class="num">{statistics.median(talk):.1f}s</td>'
  f'<td class="num">{pct(talk,.9):.1f}s</td><td class="num">{max(talk):.1f}s</td><td class="num">{len(talk)}</td></tr>')
W(f'<tr><td>agent reply latency</td><td class="num">{statistics.median(lat)/1000:.2f}s</td>'
  f'<td class="num">{pct(lat,.9)/1000:.2f}s</td><td class="num">{max(lat)/1000:.2f}s</td>'
  f'<td class="num">{len(lat)}</td></tr>')
W('</tbody></table>')
W('</div>')

# ══ FINDINGS ══
W('<div class="sheet" style="break-before:page">')
W('<h2 class="sec">What went wrong, ranked</h2>')
W('<p class="dek">Every item below is counted from the run log, not inferred. '
  'The per-call sheets that follow carry the evidence for each one.</p>')

findings = [
    ('crit', f'The agent pitched {len(vm)} answering machines',
     f'On calls {", ".join("#"+str(c["seq"]) for c in vm)} the opener went out, then AMD came back '
     f'“machine”. Between {min(c["talk_ms"] for c in vm)/1000:.1f}s and {max(c["talk_ms"] for c in vm)/1000:.1f}s '
     f'of pitch was delivered to a recording before <span class="mono">end_call</span> fired. '
     f'The cause is ordering: the opener ceiling is 1.5&nbsp;s but AMD does not return until 4&ndash;6&nbsp;s.'),
    ('crit', f'{len(deadair)} connected calls carried no prospect audio at all',
     f'Calls {", ".join("#"+str(c["seq"]) for c in deadair)} answered, streamed hundreds of frames, and '
     f'not one crossed the 300 speech floor — every frame sat in the 0&ndash;99 band. The agent talked to '
     f'silence for up to {max(c["talk_ms"] for c in deadair)/1000:.0f}s before giving up. Either the far end '
     f'never had a live mic, or inbound audio is arriving muted.'),
    ('warn', f'The agent talked over the prospect on {len(overtalk)} of {len(conn)} connected calls',
     f'Median {statistics.median([c["spoke_during_opener_ms"] for c in overtalk])/1000:.2f}s of their speech '
     f'landed underneath the opener, worst {max(c["spoke_during_opener_ms"] for c in overtalk)/1000:.2f}s '
     f'(#{max(overtalk, key=lambda c: c["spoke_during_opener_ms"])["seq"]}). '
     f'The 1.5&nbsp;s hard ceiling fires while people are still saying “hello”.'),
    ('warn', f'AMD was overruled {sum(1 for c in CALLS if "overridden" in (c["amd"] or ""))} times',
     f'{amd_kinds.get("machine_overridden_by_config",0)} by config '
     f'(<span class="mono">AMD_MACHINE_HANGUP=false</span>) and '
     f'{amd_kinds.get("machine_overridden_by_human_shape",0)} because the speech looked human-shaped. '
     f'That override is what let the three voicemail pitches through.'),
    ('warn', f'Playout underran on {sum(v for k, v in warn_heads.items() if k.startswith("playout underrun"))} calls',
     'ElevenLabs audio arrived after the caller’s jitter buffer had already drained — the prospect heard '
     'dead air mid-sentence, up to 11 s of it. Every occurrence is flagged on its call sheet.'),
    ('warn', f'{warn_heads.get("read start event",0)} “read start event” errors and '
             f'{warn_heads.get("no session for call",0)} “no session for call” errors',
     'Media-stream plumbing, not conversation logic: a websocket start frame that could not be read, and '
     'audio arriving for a call whose session had already closed. Neither killed a call, but both mean '
     'frames were dropped on the floor.'),
    ('plain', 'Hindi ASR transcription is unreliable enough to distrust the transcripts',
     'Prospect turns routinely come back as nonsense — “निक्वायनस्नि निक्वायनस्नि अनुरंज”, “कर्मण्ये”, '
     '“शर्मनें वकीलन से पूछ। खिला।” — while the agent’s own lines are clean. What the model <em>heard</em> and '
     'what it <em>acted on</em> may not match, so read every PROSPECT line as a guess.'),
    ('plain', f'The two links that landed both took {min(c["talk_ms"] for c in links)/1000:.0f}s+ of conversation',
     f'#{links[0]["seq"]} {E(links[0]["business"])} ({links[0]["talk_ms"]/1000:.0f}s) and '
     f'#{links[1]["seq"]} {E(links[1]["business"])} ({links[1]["talk_ms"]/1000:.0f}s) are the only two '
     f'that reached <span class="mono">send_demo_link</span> and queued. A third, #50, called the tool '
     f'before the prospect had answered the offer and was refused by the guard.'),
]
SEV = {'crit': 'defect', 'warn': 'risk', 'plain': 'note'}
W('<table style="margin-top:2mm"><tbody>')
for tone, head, body in findings:
    W(f'<tr><td style="width:17mm;padding-top:2.2mm"><span class="chip {tone}"><span class="dot"></span>'
      f'{SEV[tone]}</span></td>'
      f'<td><div style="font-family:var(--disp);font-weight:600;font-size:9.6pt;margin-bottom:1mm">{head}</div>'
      f'<div style="color:var(--ink2)">{body}</div></td></tr>')
W('</tbody></table>')

if B['orphan_events']:
    W('<h3 class="sub" style="margin-top:7mm">Run-level warnings — not tied to any one call</h3>')
    W('<div class="warns">')
    for e in B['orphan_events']:
        attrs = ' '.join(f'{k}={v}' for k, v in e['attrs'].items())
        W(f'<div class="wrow"><div class="wt">{E(e["level"])}</div><div>'
          f'<div>{E(e["msg"].splitlines()[0])}</div><div class="wa">{E(attrs)}</div></div></div>')
    W('</div>')
    W('<p class="note">The opener line is synthesised ahead of the dial and capped at 5.5&nbsp;s. '
      'Four business names are long enough that every take ran over, so the shortest take shipped — '
      'those prospects heard a rushed greeting.</p>')

W('<h3 class="sub" style="margin-top:7mm">Conversation shape</h3>')
W('<div class="grid3">')
for n, l in [(turns_total, 'transcript turns'), (a_turns, 'agent turns'), (p_turns, 'prospect turns'),
             (sum(c['barge_ins'] for c in CALLS), 'interruptions allowed'),
             (sum(c['back_channels'] for c in CALLS), 'back-channels withheld'),
             (sum(c['thinking_fillers'] for c in CALLS), 'thinking fillers played'),
             (sum(c['resume_prompts'] for c in CALLS), 'resume prompts'),
             (f'{sum(c["prospect_speech_seconds"] for c in CALLS):.0f}s', 'prospect speech, all 50 calls'),
             (f'{len([c for c in CALLS if c["transcript"]])}', 'calls with any transcript')]:
    W(f'<div class="cs"><div class="n" style="font-size:17pt">{n}</div><div class="l">{l}</div></div>')
W('</div>')
W('</div>')

# ══ INDEX ══
W('<div class="sheet" style="break-before:page">')
W('<h2 class="sec">All 50 calls</h2>')
W('<p class="dek">In dial order. Talk time is measured from pickup to hangup; a dash means the call never '
  'connected.</p>')
W('<table><thead><tr><th class="num">#</th><th>business</th><th>city</th><th>outcome</th>'
  '<th class="num">answered</th><th class="num">talk</th><th class="num">turns</th><th>flagged</th>'
  '</tr></thead><tbody>')
for c in CALLS:
    fl = flags(c)
    tone = DISP_TONE.get(c['disposition'], 'plain')
    fl_txt = ' · '.join(t for _, t in fl) or '—'
    name = c['business'] if len(c['business']) <= 42 else c['business'][:41].rstrip() + '…'
    W(f'<tr><td class="seq">{c["seq"]}</td><td>{E(name)}</td><td>{E(c["city"])}</td>'
      f'<td><span class="chip {tone}"><span class="dot"></span>{E(DISP_LABEL.get(c["disposition"],c["disposition"]))}</span></td>'
      f'<td class="num">{secs(c["answered_ms"])}</td><td class="num">{secs(c["talk_ms"])}</td>'
      f'<td class="num">{c["turns"] or "—"}</td>'
      f'<td style="font-size:7.2pt;color:var(--ink2)">{E(fl_txt)}</td></tr>')
W('</tbody></table>')

W('<h3 class="sub" style="margin-top:7mm">Run configuration</h3>')
W('<div class="grid2">')
W(kv([('run', E(MAN['run'])), ('started', E(MAN['started_ist'])), ('leads', f'<span class="mono">{E(MAN["leads_file"])}</span> · {MAN["lead_count"]} rows'),
      ('from', f'<span class="mono">{E(MAN["from_number"])}</span>'),
      ('brain', f'<span class="mono">{E(MAN["brain_model"])}</span> · reasoning {E(MAN["reasoning_effort"])}'),
      ('voice', f'<span class="mono">{E(MAN["voice_model"])}</span> · {E(MAN["voice_id"])}'),
      ('vad', E(MAN['vad_mode'])), ('go', E(MAN['go_version'])),
      ('whatsapp', E(MAN['preflight']['whatsapp_detail']))]))
W(kv([(k, f'<span class="mono">{E(str(v))}</span>') for k, v in MAN['tuning'].items()]))
W('</div>')
W(f'<p class="note">WhatsApp sidecar at close of run: sent {WA[-1]["sent"]}, queued {WA[-1]["queued"]}, '
  f'failed {WA[-1]["failed"]}, declined {WA[-1]["declined"]}, '
  f'{WA[-1]["sent_today"]} of {WA[-1]["daily_cap"]} used today.</p>')
W('</div>')

# ══ PER-CALL SHEETS ══
for c in CALLS:
    tone = DISP_TONE.get(c['disposition'], 'plain')
    W('<div class="call">')
    W('<div class="callhead">')
    W(f'<div class="callno">{c["seq"]:02d}<small>of 50</small></div>')
    W('<div>')
    W(f'<h3>{E(c["business"])}</h3>')
    W(f'<div class="meta">{E(c["city"])} · {E(c["vertical"].replace("_"," "))} · '
      f'{E(c["rating"])}★ from {E(c["reviews"])} reviews</div>')
    W(f'<div class="meta mono">{E(c["phone"])} · {"landline" if c["landline"] else "mobile"} · '
      f'dialled {E(c["at"])}</div>')
    W('</div>')
    W(f'<div class="chipstack"><span class="chip {tone}"><span class="dot"></span>'
      f'{E(DISP_LABEL.get(c["disposition"], c["disposition"]))}</span>'
      f'<span class="chip muted"><span class="dot"></span>hung up by {E(c["hangup_by"] or "—")}</span></div>')
    W('</div>')

    fl = flags(c)
    if fl:
        W('<div class="flagrow">' + ''.join(
            f'<span class="chip {t}"><span class="dot"></span>{E(txt)}</span>' for t, txt in fl) + '</div>')

    W('<div class="grid3" style="margin-top:4mm">')
    W('<div class="block"><h3 class="sub">Timing</h3>' + kv([
        ('answered', f'{secs(c["answered_ms"])} after dial'),
        ('talked', secs(c['talk_ms'])),
        ('line open', secs(c['duration_ms'])),
        ('hangup', f'{E(c["hangup_cause"] or "—")} / {E(c["hangup_source"] or "—")}'),
        ('sip cause', E(c['sip_cause'] or '—')),
        ('telnyx says', E(c['hangup_explanation'] or '—')),
    ]) + '</div>')
    W('<div class="block"><h3 class="sub">Pickup detection</h3>' + kv([
        ('opener rule', E(c['opener_reason'] or 'never released')),
        ('fired at', secs(c['opener_after_ms'])),
        ('opener audio', secs(c['opener_audio_ms'])),
        ('they spoke', f'{c["spoke_during_opener_ms"]}ms during it'),
        ('before opener', f'{c["pre_opener_utterances"]} utterance(s)'),
        ('amd verdict', E(c['amd'] or 'none')),
        ('amd at', secs(c['amd_after_ms'])),
    ]) + '</div>')
    lat_c = c['reply_latencies_ms'] or []
    W('<div class="block"><h3 class="sub">Audio &amp; floor</h3>' + kv([
        ('frames in', f'{c["frames_from_telnyx"]}'),
        ('with speech', f'{c["frames_with_speech"]}'),
        ('to the model', f'{c["frames_to_model"]}'),
        ('echo gate ate', f'{c["speech_frames_eaten_by_gate"]}'),
        ('prospect spoke', f'{c["prospect_speech_seconds"]:.1f}s'),
        ('barge-ins', f'{c["barge_ins"]} allowed · {c["back_channels"]} withheld'),
        ('fillers', f'{c["thinking_fillers"]} thinking · {c["resume_prompts"]} resume'),
        ('reply latency', ', '.join(f'{v/1000:.1f}s' for v in lat_c) if lat_c else '—'),
    ]) + '</div>')
    W('</div>')

    W('<div class="grid2" style="margin-top:4mm">')
    W('<div class="block"><h3 class="sub">Opening line</h3>')
    W(f'<div class="opener"><div class="line">{E(c["opener_line"]) or "—"}</div></div>')
    if c['link_channel'] or c['demo_url'] or c['followup_kind'] or c['owner_name']:
        W('<div style="margin-top:3mm">' + kv([
            ('owner', E(c['owner_name'])),
            ('link', f'{E(c["link_channel"])} / {E(c["link_status"])}' if c['link_channel'] else ''),
            ('demo', f'<span class="mono" style="font-size:7pt;overflow-wrap:anywhere">{E(c["demo_url"])}</span>' if c['demo_url'] else ''),
            ('followup', f'{E(c["followup_kind"])} @ {E(c["followup_when"])}' if c['followup_kind'] else ''),
        ]) + '</div>')
    if c['tool_calls']:
        W('<p class="note">tools called: ' + ', '.join(
            f'<span class="mono">{E(t["attrs"].get("name",""))} {E(t["attrs"].get("args",""))}</span>'
            for t in c['tool_calls']) + '</p>')
    W(f'<p class="note">recording · <span class="mono">{E(c["recording"] or "none saved")}</span></p>')
    W('</div>')
    W('<div class="block"><h3 class="sub">Inbound loudness</h3>' + histogram(c) + '</div>')
    W('</div>')

    W(f'<h3 class="sub" style="margin-top:5mm">Transcript — {c["turns"]} turn' +
      ('' if c['turns'] == 1 else 's') + '</h3>')
    if c['transcript']:
        W('<div class="tx">')
        for t in c['transcript']:
            stamp = t[t.find('[') + 1:t.find(']')].strip() if '[' in t else ''
            rest = t[t.find(']') + 1:].strip() if ']' in t else t
            who = 'AGENT' if rest.startswith('AGENT') else ('PROSPECT' if rest.startswith('PROSPECT') else '')
            said = rest[len(who):].strip() if who else rest
            cls = who.lower()
            W(f'<div class="txr {cls}"><div class="txt-t">{E(stamp)}</div>'
              f'<div class="who {cls}">{E(who or "—")}</div><div class="said">{E(said)}</div></div>')
        W('</div>')
    else:
        W('<div class="empty">Nothing was transcribed on either side.</div>')

    if c['warnings']:
        W('<h3 class="sub" style="margin-top:5mm">Warnings and errors</h3><div class="warns">')
        for w in c['warnings']:
            attrs = ' '.join(f'{k}={v}' for k, v in w['attrs'].items())
            cls = 'err' if w['level'] == 'ERROR' else ''
            W(f'<div class="wrow {cls}"><div class="wt">{w["at"]:.1f}s</div><div>'
              f'<div>{E(w["msg"].splitlines()[0])}</div>'
              + (f'<div class="wa">{E(attrs)}</div>' if attrs else '') + '</div></div>')
        W('</div>')

    W('<h3 class="sub" style="margin-top:5mm">Event trace</h3><div class="trace">')
    for e in c['timeline']:
        attrs = ' '.join(f'{k}={v}' for k, v in e['attrs'].items())
        msg = e['msg'].splitlines()[0]
        W(f'<div class="tr"><div class="at">{e["at"]:.1f}s</div>'
          f'<div class="lv {e["level"]}">{e["level"]}</div>'
          f'<div>{E(msg)}' + (f' <span class="ta">{E(attrs)}</span>' if attrs else '') + '</div></div>')
    W('</div>')
    W('</div>')

doc = '\n'.join(O)
open('live3_teardown.html', 'w', encoding='utf-8').write(doc)
print('wrote live3_teardown.html', len(doc), 'chars')

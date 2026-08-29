"""Merge live3.jsonl (per-call records) with live3.log (event trace) into one dataset."""
import re, json, sys, io, datetime, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

LINE = re.compile(r'^time=(\S+) level=(\w+) msg=(?:"((?:[^"\\]|\\.)*)"|(\S+))(.*)$')
ATTR = re.compile(r'(\w+)=(?:"((?:[^"\\]|\\.)*)"|(\S*))')

# ---------- 1. the structured records ----------
rows = [json.loads(l) for l in open('live3.jsonl', encoding='utf-8')]
manifest = next(r for r in rows if r.get('type') == 'manifest')
summary = next((r for r in rows if r.get('type') == 'summary'), None)
whatsapp = [r for r in rows if r.get('type') == 'whatsapp']
calls = [r for r in rows if r.get('type') == 'call']
calls.sort(key=lambda r: r['seq'])
by_id = {c['call_id']: c for c in calls}

# ---------- 2. the log ----------
# events we keep out of the trace (pure noise / already in the record)
DROP = {
    'opener ready', 'using pre-connected brain session', 'using pre-connected voice stream',
    'telnyx websocket connected', 'unhandled event', 'reply latency',
    'held speech did not sustain — back-channel, agent keeps the floor',
    'turn cut short — keeping playout so the sentence finishes',
    'barge-in qualified — releasing held frames',
    'agent went quiet — releasing withheld speech',
    'health check: the agent is holding conversations',
}
SPEECH = {'· AGENT', '· PROSPECT'}

events = collections.defaultdict(list)
orphan_events = []          # errors with no call attached
current = None              # call_id of the call being dialled
dial_t0 = {}
pending_dial = None

def parse_attrs(rest):
    out = {}
    for k, q, p in ATTR.findall(rest):
        v = q if q != '' or p == '' else p
        out[k] = v.replace('\\"', '"').replace('\\n', '\n')
    return out

for raw in open('live3.log', encoding='utf-8'):
    m = LINE.match(raw)
    if not m:
        continue
    ts, level, msg_q, msg_b, rest = m.groups()
    msg = (msg_q or msg_b or '').replace('\\n', '\n').replace('\\"', '"')
    head = msg.split('\n')[0]
    attrs = parse_attrs(rest)
    t = datetime.datetime.fromisoformat(ts)

    cid = attrs.get('call') or attrs.get('call_control_id')
    if cid and cid in by_id:
        current = cid
        dial_t0.setdefault(cid, t)
    target = cid if cid in by_id else current
    # the opener cache pre-synthesises a LATER call's line — that is a run-level event,
    # not something that happened on the call it interleaves with
    if 'opener ceiling' in head:
        target = None
    # a 'dialing' line closes the previous call — anything after it belongs to the next one
    if head == 'dialing':
        current = None
        target = None

    if head in DROP:
        continue
    rec = {
        'iso': ts, 'level': level, 'msg': msg, 'head': head,
        'attrs': {k: v for k, v in attrs.items()
                  if k not in ('call', 'call_control_id', 'phone', 'business', 'stream',
                               'telnyx_media_peer', 'component')},
    }
    if target:
        events[target].append(rec)
    elif level in ('WARN', 'ERROR'):
        orphan_events.append(rec)

# ---------- 3. merge ----------
out = []
for c in calls:
    ev = events.get(c['call_id'], [])
    t0 = dial_t0.get(c['call_id'])
    timeline = []
    for e in ev:
        t = datetime.datetime.fromisoformat(e['iso'])
        off = (t - t0).total_seconds() if t0 else 0.0
        timeline.append({
            'at': round(off, 1),
            'level': e['level'],
            'msg': e['msg'],
            'head': e['head'],
            'attrs': e['attrs'],
        })
    rec = dict(c)
    rec['timeline'] = timeline
    rec['recording'] = next((e['attrs'].get('file') for e in ev if e['head'] == 'recording saved'), '')
    hang = next((e for e in ev if e['head'] == 'call hangup'), None)
    rec['hangup_explanation'] = hang['attrs'].get('explanation', '') if hang else ''
    rec['sip_cause'] = hang['attrs'].get('sip_cause', '') if hang else ''
    acct = next((e for e in ev if e['head'] == 'inbound audio accounting'), None)
    rec['prospect_speech_seconds'] = float(acct['attrs'].get('prospect_speech_seconds', 0)) if acct else 0.0
    hist = next((e for e in ev if e['head'].startswith('inbound loudness histogram')), None)
    rec['loudness'] = hist['attrs'] if hist else {}
    rec['warnings'] = [e for e in timeline if e['level'] in ('WARN', 'ERROR')]
    rec['tool_calls'] = [e for e in timeline if e['head'] == 'tool call']
    out.append(rec)

blob = {
    'manifest': manifest,
    'summary': summary,
    'whatsapp': whatsapp,
    'orphan_events': orphan_events,
    'calls': out,
}
json.dump(blob, open('live3_full.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('calls:', len(out))
print('total timeline events:', sum(len(c['timeline']) for c in out))
print('with transcript:', sum(1 for c in out if c.get('transcript')))
print('with recording:', sum(1 for c in out if c['recording']))
print('warnings/errors:', sum(len(c['warnings']) for c in out), '| orphan:', len(orphan_events))
print('dispositions:', collections.Counter(c['disposition'] for c in out))
print('sample tool call:', json.dumps(out[5]['tool_calls'], ensure_ascii=False)[:400])

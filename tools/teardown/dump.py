"""Plain-text full dump: every call, every field, every transcript turn, every event."""
import json, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
blob = json.load(open('live3_full.json', encoding='utf-8'))
man = blob['manifest']
out = []
W = out.append


def ms(v):
    if v is None or v < 0:
        return '—'
    return f'{v/1000:.1f}s'


W('=' * 78)
W(f" CAMPAIGN {man['run']}  ·  {man['started_ist']}")
W('=' * 78)
W(f" leads        {man['leads_file']} ({man['lead_count']} rows), stop after {man['max_answered']} answered")
W(f" from         {man['from_number']}")
W(f" brain        {man['brain_model']} (reasoning={man['reasoning_effort']}, vad={man['vad_mode']})")
W(f" voice        {man['voice_model']} / {man['voice_id']}")
W(f" results csv  {man['results_csv']}")
W(' tuning       ' + json.dumps(man['tuning'], ensure_ascii=False))
W(' whatsapp     ' + json.dumps(man['preflight'], ensure_ascii=False))
for w in blob['whatsapp']:
    W(f" whatsapp {w['when']:5s} sent={w['sent']} queued={w['queued']} failed={w['failed']} "
      f"declined={w['declined']} unreachable={w['unreachable']} sent_today={w['sent_today']}/{w['daily_cap']}")
W('')

for c in blob['calls']:
    W('')
    W('#' * 78)
    W(f"# CALL {c['seq']:>2}  {c['business']}")
    W('#' * 78)
    W(f"  phone            {c['phone']}   ({'landline' if c['landline'] else 'mobile'})")
    W(f"  city / vertical  {c['city']} · {c['vertical']}")
    W(f"  google rating    {c['rating']} ({c['reviews']} reviews)")
    W(f"  dialled at       {c['at']}  (UTC)")
    W(f"  call_id          {c['call_id']}")
    W(f"  recording        {c['recording'] or '—'}")
    W('')
    W(f"  DISPOSITION      {c['disposition']}   hangup_by={c['hangup_by']}  refused={c['refused']}")
    W(f"  hangup           cause={c['hangup_cause']} source={c['hangup_source']} sip={c['sip_cause'] or '—'}")
    if c['hangup_explanation']:
        W(f"                   {c['hangup_explanation']}")
    W(f"  timing           answered {ms(c['answered_ms'])} after dial · talked {ms(c['talk_ms'])} · line open {ms(c['duration_ms'])}")
    W('')
    W(f"  opener           style={c['opener_style']} personalised={c['opener_personalised']}")
    W(f"                   released on '{c['opener_reason'] or '—'}' after {ms(c['opener_after_ms'])}, audio {ms(c['opener_audio_ms'])}")
    W(f"                   line: {c['opener_line']}")
    W(f"                   prospect spoke {c['spoke_during_opener_ms']}ms during it · "
      f"{c['pre_opener_utterances']} utterance(s) before it")
    W(f"  amd              {c['amd'] or '—'} after {ms(c['amd_after_ms'])}")
    W(f"  language         {c['language']}")
    W('')
    W(f"  audio            {c['frames_from_telnyx']} frames from telnyx · {c['frames_with_speech']} with speech · "
      f"{c['frames_to_model']} reached the model · {c['speech_frames_eaten_by_gate']} eaten by the echo gate")
    W(f"  loudness bands   " + ' '.join(f'{k}={v}' for k, v in c['loudness'].items()
                                        if k not in ('speech_floor', 'barge_in_floor', 'frames_from_telnyx')))
    W(f"  prospect speech  {c['prospect_speech_seconds']}s")
    W(f"  barge-in         {c['barge_ins']} allowed · {c['back_channels']} back-channels withheld · "
      f"{c['thinking_fillers']} thinking fillers · {c['resume_prompts']} resume prompts")
    lat = c['reply_latencies_ms'] or []
    if lat:
        W(f"  reply latency    " + ', '.join(f'{v}ms' for v in lat))
    if c['link_channel'] or c['demo_url']:
        W('')
        W(f"  LINK             {c['link_channel']}/{c['link_status']}")
        W(f"  demo url         {c['demo_url']}")
    if c['followup_kind']:
        W(f"  followup         {c['followup_kind']} @ {c['followup_when']}")
    if c['owner_name']:
        W(f"  owner name       {c['owner_name']}")

    W('')
    W(f"  ── TRANSCRIPT ({c['turns']} turns) " + '─' * 40)
    if c['transcript']:
        for t in c['transcript']:
            W('    ' + t)
    else:
        W('    (nothing was transcribed on either side)')

    if c['warnings']:
        W('')
        W('  ── WARNINGS / ERRORS ' + '─' * 44)
        for w in c['warnings']:
            a = ' '.join(f'{k}={v}' for k, v in w['attrs'].items())
            W(f"    [{w['at']:>6.1f}s] {w['level']:5s} {w['msg']}" + (f'  ({a})' if a else ''))

    W('')
    W('  ── EVENT TRACE ' + '─' * 50)
    for e in c['timeline']:
        a = ' '.join(f'{k}={v}' for k, v in e['attrs'].items())
        msg = e['msg'].split('\n')[0]
        W(f"    [{e['at']:>6.1f}s] {e['level']:5s} {msg}" + (f'  · {a}' if a else ''))

txt = '\n'.join(out)
open('live3_all_calls.txt', 'w', encoding='utf-8').write(txt)
print('wrote live3_all_calls.txt', len(txt), 'chars,', txt.count('\n'), 'lines')

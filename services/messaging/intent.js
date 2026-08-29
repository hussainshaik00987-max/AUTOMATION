import { config } from './config.js'

// ---------------------------------------------------------------------------
// Reply classification.
//
// When a lead answers the "link bhej doon?" ping we need one decision: send the
// link, or don't. Getting this wrong in the "don't" direction costs a lead;
// getting it wrong in the "send" direction risks a spam report. So there are two
// layers — a fast keyword pass that resolves the overwhelmingly common one-word
// replies with zero latency and zero cost, and OpenAI for everything else.
//
// The bias is deliberate and asymmetric: only a CLEAR refusal blocks the link.
// Anything ambiguous still gets it, because the link is free, was verbally
// agreed to on the call, and a confused "kya?" is an engaged lead, not a no.
// ---------------------------------------------------------------------------

export const YES = 'yes'
export const NO = 'no'
export const UNCLEAR = 'unclear'

// Clear refusals, in Hinglish and English. Matched as whole words so "nahi"
// doesn't fire inside "nahin socha tha" — and note "no" would otherwise match
// inside "now", "note", "nominal".
const NO_PATTERNS = [
  /\bnahi+n?\b/i, /\bnhi+\b/i, /\bnai\b/i, /\bmat\b/i, /\bnako\b/i,
  /\bno\b/i, /\bnope\b/i, /\bdont\b/i, /\bdon'?t\b/i, /\bnot interested\b/i,
  /\binterested nahi/i, /\bzarurat nahi/i, /\bzaroorat nahi/i, /\bjarurat nahi/i,
  /\bchahiye nahi/i, /\bnahi chahiye/i, /\bnhi chahiye/i,
  /\bstop\b/i, /\bunsubscribe\b/i, /\bblock\b/i, /\bremove\b/i,
  /\bcall mat\b/i, /\bmat karo\b/i, /\bpareshan\b/i, /\bband karo\b/i,
  /\bspam\b/i, /\bfraud\b/i, /\bscam\b/i,
]

// Clear agreement.
const YES_PATTERNS = [
  /\bhaan?\b/i, /\bhan\b/i, /\bha\b/i, /\bji\b/i, /\bjee\b/i,
  /\bok+\b/i, /\bokay\b/i, /\btheek\b/i, /\bthik\b/i, /\bteek\b/i,
  /\bbhej\s?d?o\b/i, /\bbhejo\b/i, /\bbhej\b/i, /\bbhejdo\b/i, /\bbheje\b/i,
  /\bsend\b/i, /\byes\b/i, /\byeah\b/i, /\byep\b/i, /\bsure\b/i, /\bplease\b/i,
  /\bpls\b/i, /\bplz\b/i, /\bshare\b/i, /\bdekhte\b/i, /\bdekhenge\b/i,
  /\bdikhao\b/i, /\bkardo\b/i, /\bkar do\b/i, /\bchalega\b/i,
  /^\s*(👍|👌|🙏|✅|ok|k)\s*$/iu,
]

function keywordPass(text) {
  const t = (text || '').trim()
  if (!t) return UNCLEAR
  // Refusal wins on a mixed message ("haan par nahi chahiye") — the safe read.
  if (NO_PATTERNS.some((re) => re.test(t))) return NO
  if (YES_PATTERNS.some((re) => re.test(t))) return YES
  return UNCLEAR
}

const SYSTEM = `You classify a WhatsApp reply from an Indian small-business owner.

Context: we cold-called them, offered a free demo website we had already built for their business, and messaged asking "shall I send the link here?". They replied. Replies are usually Hinglish (Hindi in Roman script), sometimes English, sometimes a regional language, often one or two words.

Answer with exactly one word:
- yes — they clearly agree to receive the link ("haan", "bhej do", "ok", "sure", "please send")
- no — they clearly refuse, say they're not interested, ask you to stop or to not contact them, or accuse you of spam
- unclear — anything else: a question, "who is this?", a price query, "I'm busy right now", a greeting, or something you can't read

Only answer "no" for an unmistakable refusal — "unclear" is the right answer for anything merely ambiguous, and they still get the link.`

async function classifyWithOpenAI(text) {
  if (!config.openaiKey) return UNCLEAR
  try {
    const res = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${config.openaiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: config.openaiModel,
        messages: [
          { role: 'system', content: SYSTEM },
          { role: 'user', content: text.slice(0, 500) },
        ],
        max_completion_tokens: 300,
      }),
      signal: AbortSignal.timeout(15_000),
    })
    if (!res.ok) return UNCLEAR
    const data = await res.json()
    const out = (data?.choices?.[0]?.message?.content || '').toLowerCase()
    if (out.includes(NO)) return NO
    if (out.includes(YES)) return YES
    return UNCLEAR
  } catch {
    return UNCLEAR // never let the classifier decide the outcome by failing
  }
}

// classify resolves a reply to yes / no / unclear. The keyword pass handles the
// short replies that make up most of the traffic; OpenAI is only consulted for
// the genuinely ambiguous ones, which keeps both latency and spend near zero.
export async function classify(text) {
  const quick = keywordPass(text)
  if (quick !== UNCLEAR) return { intent: quick, via: 'keyword' }
  const intent = await classifyWithOpenAI(text)
  return { intent, via: 'openai' }
}

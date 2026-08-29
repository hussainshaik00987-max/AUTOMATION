#!/usr/bin/env python3
"""
Local, zero-cost listen to what the agent says.

Reads agent lines (from a -probe transcript on stdin, or as arguments),
transliterates the Devanagari to Latin, and speaks them through a Windows
voice. Nothing here touches ElevenLabs or OpenAI.

It is NOT a voice test — no Hindi voice ships with Windows, and the Indian
English voices (Heera/Ravi) are OneCore-only and invisible to SAPI. What it
IS good for is the thing that actually decides these calls: how LONG each
turn runs, whether a turn is one beat or three, and whether the agent hands
the floor back. Those you can hear.

    ./voice-agent -probe ... | python devtools_speak.py          # speak the agent's turns
    python devtools_speak.py --print-only < transcript.txt       # just show them
    python devtools_speak.py "मैंने आपके लिए website बना दी है"
"""
import re
import subprocess
import sys

# Devanagari → Latin. Longest-first so conjuncts and matras win over bare
# consonants. Tuned for how an Indian English voice pronounces it, not for
# scholarly accuracy — "sir" has to come out "sir", not "sara".
_MAP = [
    ("क्ष", "ksh"), ("त्र", "tra"), ("ज्ञ", "gya"), ("श्र", "shra"),
    ("ौ", "au"), ("ो", "o"), ("ै", "ai"), ("े", "e"), ("ू", "oo"), ("ु", "u"),
    ("ी", "ee"), ("ि", "i"), ("ा", "aa"), ("ृ", "ri"), ("ं", "n"), ("ँ", "n"),
    ("ः", "h"), ("्", ""), ("़", ""),
    ("क", "k"), ("ख", "kh"), ("ग", "g"), ("घ", "gh"), ("ङ", "ng"),
    ("च", "ch"), ("छ", "chh"), ("ज", "j"), ("झ", "jh"), ("ञ", "ny"),
    ("ट", "t"), ("ठ", "th"), ("ड", "d"), ("ढ", "dh"), ("ण", "n"),
    ("त", "t"), ("थ", "th"), ("द", "d"), ("ध", "dh"), ("न", "n"),
    ("प", "p"), ("फ", "ph"), ("ब", "b"), ("भ", "bh"), ("म", "m"),
    ("य", "y"), ("र", "r"), ("ल", "l"), ("व", "v"), ("ळ", "l"),
    ("श", "sh"), ("ष", "sh"), ("स", "s"), ("ह", "h"),
    ("अ", "a"), ("आ", "aa"), ("इ", "i"), ("ई", "ee"), ("उ", "u"), ("ऊ", "oo"),
    ("ए", "e"), ("ऐ", "ai"), ("ओ", "o"), ("औ", "au"), ("ऋ", "ri"),
    ("।", "."), ("॥", "."), ("०", "0"), ("१", "1"), ("२", "2"), ("३", "3"),
    ("४", "4"), ("५", "5"), ("६", "6"), ("७", "7"), ("८", "8"), ("९", "9"),
]

# Words the mapping mangles, spelled the way the voice should say them.
_FIXES = {
    "sar": "sir", "jee": "ji", "basa": "bas", "naheen": "nahin",
    "koee": "koi", "lie": "liye", "maaphee": "maafi", "minat": "minute",
    "mainne": "maine", "achchhaa": "achha", "bilakul": "bilkul",
}

# Devanagari codepoint ranges, so English words inside a Hindi sentence are
# left completely alone. Getting this wrong is not cosmetic: an earlier pass
# applied the inherent-vowel rule to the whole string and turned "WhatsApp"
# into "WhatasApap" and "Fairlight Studio" into "Sharama Studio".
_MATRAS = set("ािीुूृेैोौंँः़")
_VIRAMA = "्"
_CONSONANT_RANGE = ("क", "ह")


def _is_deva(ch: str) -> bool:
    return "ऀ" <= ch <= "ॿ"


def _is_consonant(ch: str) -> bool:
    return _CONSONANT_RANGE[0] <= ch <= _CONSONANT_RANGE[1]


def translit(text: str) -> str:
    """Devanagari → Latin, leaving every non-Devanagari character untouched.

    The only rule that needs care is the inherent vowel. A Hindi consonant
    carries an implied 'a' unless a matra, a virama or the end of the word
    takes it away — so बना is "banaa" and काम is "kaam", not "kaama".
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if not _is_deva(ch):
            out.append(ch)
            i += 1
            continue

        for src, dst in _MAP:              # longest-first: conjuncts win
            if text.startswith(src, i):
                out.append(dst)
                i += len(src)
                break
        else:
            out.append(ch)
            i += 1
            continue

        # Inherent 'a', unless something takes it away.
        if _is_consonant(ch) and len(src) == 1:
            nxt = text[i] if i < n else ""
            word_ends = not nxt or not _is_deva(nxt)
            if nxt not in _MATRAS and nxt != _VIRAMA and not word_ends:
                out.append("a")

    s = "".join(out)
    return " ".join(_fix(w) for w in s.split())


def _fix(word: str) -> str:
    """Respell a mangled word, keeping whatever punctuation was attached."""
    core = word.strip(".,!?;:—-")
    repl = _FIXES.get(core.lower())
    return word.replace(core, repl, 1) if repl else word


def agent_lines(stream):
    """Pull the AGENT turns out of a -probe transcript."""
    for raw in stream:
        line = raw.rstrip("\n")
        if line.startswith("AGENT:"):
            said = line[len("AGENT:"):].strip()
            if said:
                yield said


def speak(lines, print_only=False):
    for i, line in enumerate(lines, 1):
        latin = translit(line)
        words = len(latin.split())
        print(f"\n[{i}] {line}")
        print(f"    → {latin}")
        print(f"    ~{words} words")
        if print_only:
            continue
        safe = latin.replace("'", "''")
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Add-Type -AssemblyName System.Speech; "
             "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
             "try { $s.SelectVoice('Microsoft Zira Desktop') } catch {}; "
             f"$s.Rate = -2; $s.Speak('{safe}')"],
            check=False,
        )


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only = "--print-only" in sys.argv
    if args:
        speak(args, only)
    else:
        found = list(agent_lines(sys.stdin))
        if not found:
            print("no AGENT: lines on stdin", file=sys.stderr)
            sys.exit(1)
        speak(found, only)

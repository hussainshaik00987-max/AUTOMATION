# Compliance, ethics and intended use

**Read this before pointing any part of this system at a real person.**

Coldwire automates three activities that are separately regulated in most
jurisdictions: collecting business contact data, placing automated outbound calls,
and sending unsolicited commercial messages. Nothing here is legal advice, and the
rules differ by country and — in the United States — by state. What follows is the
list of things you must have an answer for.

---

## Status

This repository is a **research prototype and portfolio piece**. It is published to
show how the problem was decomposed, instrumented and debugged.

It is not a product, it ships no hosted service, and several of its dependencies
(an unofficial messaging client, a scraping path that replays a captured internal
API parameter) are explicitly unsuitable for production use at any volume. Those
choices are documented in place, with what they cost.

---

## Automated calling

Automated and prerecorded-voice calls are regulated. Depending on where the caller
and the recipient are, some or all of the following apply.

| Area | What to establish before dialling |
|---|---|
| **Consent** | Several regimes require prior express consent for automated or prerecorded calls to a mobile number, with *written* consent for marketing. Some carry statutory damages **per call**, which makes a 2,000-lead campaign a five-figure exposure if the basis is wrong. |
| **Do-not-call registries** | National and sometimes state registries must be scrubbed against, usually on a defined refresh cadence. A registry check is not a one-time step. |
| **Calling hours** | Local-time windows for the *recipient*, not the caller. This system's campaign runner enforces a working-hours window and refuses weekends unless explicitly overridden — the override has to be typed. |
| **Identification** | Many regimes require the caller to identify the business on whose behalf the call is made, and to provide a contactable number. The playbook names the company and its location in the first turn; that is a compliance requirement, not a sales technique. |
| **Opt-out** | An immediate, honoured mechanism to stop calling. This system latches a refusal in code and carries a suppression list; that is the floor, not the ceiling. |
| **AI disclosure** | A growing number of jurisdictions require disclosure that the caller is an automated system, sometimes at the start of the call. This system **does not deny being an AI when asked** — but "does not deny" is weaker than "discloses", and where disclosure is required the playbook must be changed to lead with it. |
| **Caller ID** | Misrepresenting the originating number is separately unlawful in most places. Do not spoof. |
| **Recording** | Consent to record varies from one-party to all-party by jurisdiction. Call recording ships **disabled** in the example configuration for that reason. |

The reference deployment dialled international, which puts the caller under one
regime and the recipient under another. Both apply.

---

## Messaging

The follow-up sidecar drives an **unofficial** client for a consumer messaging
platform.

> [!WARNING]
> **Accounts get banned for this.** Not "might" — cold outreach at volume from a
> personal number is the exact behaviour the platform's anti-spam systems target,
> and a ban takes the number and every conversation on it.

The mitigations built into it — randomised send gaps, a hard daily cap, a sending
window, realistic typing and read behaviour, thirty variants of every message dealt
from a shuffled deck — are **harm reduction, not a safe harbour**. They reduce the
shape-based signals; they do nothing about the fact that the underlying activity is
against the platform's terms.

The supported path at any real volume is the platform's **official business API**
with approved message templates. It is slower to set up and it costs money, and
that is the trade.

Independently of platform terms, unsolicited commercial messaging is regulated in
much the same way as calling: consent, identification and opt-out all apply.

---

## Data collection

The system collects publicly listed business contact information — the same fields a
person would see on a map listing.

**Public is not the same as free to use for any purpose.**

- **Platform terms** generally prohibit automated collection, regardless of whether
  the data is public. One source here is accessed by replaying a captured internal
  API parameter; that is a technical description, not a permission.
- **Data-protection law** may still apply. A sole trader's mobile number is, in
  several regimes, personal data even when it is published as a business contact —
  which brings lawful-basis, notification and erasure obligations with it.
- **Purpose limitation.** Data collected to evaluate a market is not automatically
  data you may use to run a marketing campaign.
- **Retention.** Decide how long records live and delete them. This repository's
  `.gitignore` excludes every real record for that reason.

---

## What this repository does *not* contain

Deliberately, and permanently:

- No lead data. Every fixture in `services/*/data/` is synthetic, uses reserved
  example phone ranges and `.example` domains, and describes businesses that do not
  exist.
- No call recordings.
- No raw campaign logs. The teardown in [`campaign-teardown.md`](campaign-teardown.md)
  quotes timings, counts and dispositions with names and numbers removed.
- No credentials, no linked-device session, no deployment hostnames.

If you fork this, keep it that way. `.gitignore` is written to make the safe thing
the default, but it is a backstop, not a guarantee.

---

## Design constraints that are not configuration

Three behaviours are enforced in code rather than in a prompt, because a prompt is a
request and this system has a recorded instance of the model ignoring each one:

1. **A refusal ends the call.** A transcript scanner latches refusal independently of
   the model, and two separate gates refuse the send — because on a live call the
   model was told "I don't need anything, thank you", pitched anyway, and logged a
   delivery.
2. **Nothing is sent without an explicit yes.** Consent is latched from the
   prospect's own words, not from the model's assertion that they agreed.
3. **The agent cannot claim things the prospect did not say.** A tool call is refused
   when there has been no prospect audio and no transcript since the model's previous
   tool call — because a model that invents the other half of a conversation will put
   an invented name into a real message to a real person.

Removing these is not a configuration change this repository supports, and any fork
that removes them is doing something different from what is documented here.

---

## A note on the honest version of the results

The reference run converted 2 of 50 dials. That number is in the README and in the
teardown, unrounded, next to the analysis of why.

Publishing the working number rather than the flattering one is the same discipline
as the rest of the system: a metric with no recorded baseline cannot be regressed,
which means it cannot be trusted either.

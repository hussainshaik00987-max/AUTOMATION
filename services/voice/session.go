package main

import (
	"context"
	"encoding/base64"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

// These two floors decide what counts as the prospect talking. Both were
// inherited from a US landline campaign and were far too high for this one: an
// Indian mobile reached from a US carrier arrives much quieter. Measured over a
// real call on this route, inbound frames landed in just two clusters —
//
//	0–99      3079 frames   line noise / silence (96% of the call)
//	300–1999   123 frames   the prospect actually speaking
//	2000+        0 frames
//
// — so the old 2500 barge-in floor made interruption literally impossible: every
// word spoken over the agent was discarded as echo, which is exactly what "the
// agent can't hear me" felt like. The defaults below sit in the empty band
// between noise and speech. Override per-route via .env if a carrier differs.
var (
	// speechEnergyThreshold: above this, a frame is the prospect talking rather
	// than line noise. Drives reply-latency measurement and the silence supervisor.
	speechEnergyThreshold = 250

	// bargeInThreshold: how loud inbound audio must be to get through WHILE the
	// agent is still playing. Above the noise floor and above attenuated echo of
	// the agent's own voice, but comfortably below real speech so the prospect can
	// actually interrupt.
	bargeInThreshold = 1200

	// bargeInMinFrames: how many CONSECUTIVE frames above bargeInThreshold the
	// prospect must produce, while the agent is still audible, before any of it
	// is forwarded to the brain and allowed to interrupt the turn.
	//
	// Loudness alone cannot tell a back-channel from an interruption — "हाँ" and
	// "रुकिए, कितने का है?" arrive at the same level. Length can: a back-channel is
	// ~200ms, an interruption keeps going. 18 frames ≈ 360ms sits between them.
	// Below this the agent keeps the floor and the acknowledgement is discarded,
	// which is exactly what a human speaker does with an "mm-hm".
	bargeInMinFrames = 18
)

// One Telnyx media frame is 160 μ-law bytes: 20ms at 8kHz. Everything that
// counts frames — the barge-in gate, the opener player, the latency bench —
// works in these units.
const (
	frameMillis      = 20
	telnyxFrameBytes = 160
)

// shortTurnAudioMs is how little of a turn the caller must have HEARD for an
// interruption to count as "the agent barely got a word out". Below this the
// playout buffer is kept so the sentence finishes; above it the rest is dropped,
// because the prospect has genuinely moved the conversation on.
//
// Measured as elapsed playback time, never as bytes sent — Gemini delivers a
// turn's audio much faster than realtime, so byte counts describe our outbound
// buffer rather than anything a human has heard.
const shortTurnAudioMs = 500

// resumeAfterInterrupt is how long to wait after a cut-short turn before
// prompting the model to carry on. The silence supervisor's 10s nudge is far
// too slow — a live call sat in dead air for 11 seconds waiting for it.
const resumeAfterInterrupt = 1500 * time.Millisecond

// maxResumePrompts bounds the watchdog. If two nudges haven't restarted the
// conversation, a third won't either, and the silence supervisor takes over.
const maxResumePrompts = 2

// idleNudgeAfter is how long the line may stay silent AFTER the agent finishes
// talking before it checks "you still there?". If it's still silent the same
// span again, the agent hangs up (it does not keep asking).
const idleNudgeAfter = 10 * time.Second

// ---------------------------------------------------------------------------
// Session — one outbound marketing call
// ---------------------------------------------------------------------------

type Session struct {
	CallControlID string
	Phone         string
	prospect      Prospect

	cfg    Config
	log    *slog.Logger
	ctx    context.Context
	cancel context.CancelFunc

	telnyxWS *websocket.Conn
	telnyxMu sync.Mutex
	streamID string

	// brain is installed either by the campaign loop's pre-connect (opened while
	// the phone is still ringing) or by the media-stream handler, whichever
	// wins — hence brainMu. Once installed it is never replaced, so the bridge
	// goroutines can read it without locking.
	brainMu sync.Mutex
	brain   *BrainConn

	// voice is this call's ElevenLabs socket. Opened at dial time next to the
	// brain, for the same reason: a cold TLS handshake is 100–200ms and must not
	// land inside a conversation.
	voiceMu sync.Mutex
	voice   *VoiceStream

	// Turn bookkeeping shared by the two bridge goroutines. In the Gemini build
	// these were locals in a single loop, because text and audio arrived on the
	// same socket. They no longer do — the brain produces text and ElevenLabs
	// produces the audio for it — so the turn's state is read and written by both
	// and has to be atomic.
	agentSpeaking      atomic.Bool  // audio for the current turn has reached the caller
	turnFirstAudioNano atomic.Int64 // when the caller started hearing THIS turn
	agentTurnEndNano   atomic.Int64 // when the agent's previous turn finished

	playing atomic.Int32

	// lastSpeechNano holds the time (UnixNano) of the most recent inbound frame
	// where the prospect was actually talking (above the silence threshold). Used
	// to measure per-turn reply latency: agent-audio-start minus last-speech.
	lastSpeechNano atomic.Int64

	// Conversation-supervisor state.
	// playbackEndNano is a wall-clock estimate of when all audio we've sent to
	// Telnyx so far will have finished PLAYING at the caller. We send faster than
	// realtime (Telnyx buffers), so this — not "when we finished sending" — is when
	// the agent actually goes quiet from the caller's side. The silence timer keys
	// off it so "you still there?" only fires 10s after they truly stop hearing us.
	playbackEndNano atomic.Int64

	// fillerMulaw is a short, pre-generated line (μ-law 8kHz) played the instant
	// we're confident a human is on the line. It masks the connect+first-token
	// latency while the LIVE model generates its real, adaptive continuation.
	//
	// When openerPersonal is true this is the FULL Stage 1 line, already naming
	// the business ("नमस्ते सर, मीरा बोल रही हूँ Sharma IT Solution से — क्या मैं
	// Meridian Studios से बात कर रहा हूँ?"), so the model must not repeat it. When
	// false it's the generic name-free greeting and the model still owes Stage 1.
	fillerMulaw    []byte
	openerPersonal bool

	// While the pre-synthesised opener is playing, the mic gate is already open,
	// so the model can hear the prospect answer the opener's closing question and
	// start composing its reply (see maybePlayOpener). That reply must not play on
	// top of the opener, so it is held here and flushed the instant playback ends.
	//
	// This overlap is worth several seconds at the most important moment of the
	// call: with the model only starting after the opener finished, a live call
	// showed a 4-second silence right after the prospect's first "haan boliye".
	fillerPlaying atomic.Bool
	heldMu        sync.Mutex
	heldAudio     [][]byte

	// Inbound audio accounting. "Did the model actually hear the prospect?" is not
	// answerable from the conversation alone — a silent call looks identical
	// whether the person said nothing, the gate ate their words, or the frames
	// never arrived. These counters separate those three cases at a glance.
	// energyHist buckets every inbound frame by loudness so the speech floor can be
	// set from measurement rather than assumption. speechEnergyThreshold was
	// inherited from a US landline campaign; an Indian mobile leg routed through a
	// US carrier arrives much quieter, and if the floor sits above the caller's
	// actual voice the agent looks deaf while the audio is in fact arriving fine.
	energyHist [8]atomic.Int64

	framesIn      atomic.Int64 // media frames received from Telnyx
	framesSpeech  atomic.Int64 // ...of which were above the speech-energy floor
	framesToModel atomic.Int64 // ...that actually reached the brain

	// echoDropSpeech counts inbound frames loud enough to be real speech that the
	// barge-in gate discarded anyway — echo of the agent, or a back-channel the
	// gate deliberately withheld so the agent could keep the floor.
	echoDropSpeech atomic.Int64

	// bargeIns counts interruptions that sustained long enough to qualify;
	// backChannels counts loud runs that died before they did. Together they say
	// whether bargeInMinFrames is set anywhere near right for this route: all
	// back-channels and no barge-ins means the prospect cannot interrupt at all.
	bargeIns atomic.Int64
	// How many tool-call preambles were caught before the caller heard them.
	// Logged per call so a model or prompt change that starts leaking them again
	// is visible in the run log rather than only on someone's phone.
	preamblesDropped atomic.Int64
	backChannels     atomic.Int64

	// lastFillerIdx remembers which acknowledgement was played last, so the next
	// one is never the same clip. Starts at -1: nothing has played yet.
	// recentFillers is the short history pickThinkingFiller excludes from, so the
	// same acknowledgement is not heard twice in one conversation.
	fillerMu      sync.Mutex
	recentFillers []int

	// refused latches when the prospect declines. Nothing may be promised or sent
	// after it — see the send_demo_link guard.
	refused   atomic.Bool
	doNotCall atomic.Bool // they asked to be removed, not merely declined

	// linkOffered latches once the agent has actually ASKED, out loud, whether it
	// may send the demo link. offerAnswered latches when the prospect says
	// anything at all after that ask. Together they are the precondition for
	// send_demo_link.
	//
	// This exists because the model does not reliably tell the two yeses apart.
	// On a live call it asked "आपकी अपनी कोई website नहीं दिखी, सही है ना सर?",
	// the prospect said "yes" — confirming he had no website — and it went
	// straight to "मैं अभी WhatsApp पर लिंक भेज रही हूँ". The site was never
	// offered, permission was never asked, and the run was recorded as link_sent.
	// The prospect had agreed to a fact about himself, not to being sent
	// anything.
	//
	// Prompting alone does not hold here, for the same reason the refusal guard
	// and the stats list are handled in code: the model is one turn away from the
	// goal and any yes in earshot looks like the one it needs. So the ask is a
	// physical precondition — it has to have happened, in audio, and they have to
	// have had a turn to answer it.
	linkOffered   atomic.Bool
	offerAnswered atomic.Bool

	// endingCall goes true once end_call has been invoked. Nothing may fill
	// silence after that — the call is closing, not waiting on an answer.
	endingCall atomic.Bool

	// agentTurns counts how many times the LIVE model started speaking. The
	// pre-recorded opener is not a turn — it plays whether or not the model is
	// alive, which is exactly why a broken agent can look like a normal call.
	agentTurns atomic.Int64

	// thinkingFills counts acknowledgements played while the model was still
	// composing. Read against reply latency: if it is near zero on a call with
	// slow replies, thinkingFillerAfterMs is set too high to be doing anything.
	thinkingFills atomic.Int64

	// Resume watchdog. interruptedNano is when a turn was last cut off by the
	// prospect; lastModelAudioNano is when the model last spoke. If the first is
	// newer, the model stopped mid-thought and has not come back — the
	// conversation has stalled and needs a prod. resumePrompts bounds how often.
	interruptedNano    atomic.Int64
	lastModelAudioNano atomic.Int64
	resumePrompts      atomic.Int64

	// modelSpoke goes true the first time the live model produces any audio, so
	// openerFollowThrough can tell "waiting on a silent prospect" apart from
	// "the conversation is already moving".
	modelSpoke atomic.Bool

	// Listen-first opening state. The agent stays silent until it knows a human is
	// on the line (AMD or the prospect speaking), then plays the filler and triggers
	// the live model; a detected voicemail suppresses the opener and the call is hung up.
	brainGateOpen atomic.Bool  // inbound audio reaches the brain only after the opener is delivered
	voicemail     atomic.Bool  // AMD or cue-word detection says machine → suppress opener; call ends as voicemail
	openerPlayed  atomic.Bool  // the opening (filler + live-model trigger) has been delivered
	openerOnce    sync.Once    // delivers the opener exactly once
	speechMillis  atomic.Int64 // cumulative ms of real inbound speech before the opener — tells a "hello" from a noise blip
	gateOpenNano  atomic.Int64 // when the brain gate opened (UnixNano); the voicemail cue-word scan only runs for a short window after this

	// Answering-machine detection. Telnyx premium AMD is the only thing on this
	// call that can reliably tell a recorded greeting from a person saying
	// "हैलो" — measured across the first live campaign, the two are
	// indistinguishable by audio shape in the first two seconds, and AMD got all
	// six answered calls right. What it is not is fast: verdicts landed 3.0s,
	// 3.1s, 4.3s, 4.9s, 5.1s and 6.5s after pickup. So the opener cannot simply
	// wait for it, and cannot simply ignore it either — see awaitOpenerThenPlay.
	amdVerdict atomic.Int32 // amdPending | amdHuman | amdMachine | amdUnsure
	amdAtNano  atomic.Int64 // when the verdict landed, for the answered→verdict latency
	amdRaw     atomic.Value // string — exactly what Telnyx said ("human_residence", "machine", "not_sure"…)

	// curUtteranceMs / longestUtteranceMs measure the LONGEST single thing the far
	// end has said before the opener. Total speech cannot separate a person saying
	// "हैलो?" twice from a recording's first two bursts — both land around 700ms —
	// but the shapes differ: a recorded greeting always contains one long
	// continuous run, and a person checking whether the line is alive never does.
	curUtteranceMs     atomic.Int64
	longestUtteranceMs atomic.Int64

	// humanEvidence latches when the opening gate positively identified a PERSON
	// from the shape of what it heard — see the repeated_greeting rule. Telnyx AMD
	// is a heuristic listening to the same audio and it gets this exact case
	// wrong, so once this is set an AMD "machine" verdict no longer hangs up the
	// call. It only ever gets set by evidence a machine cannot produce.
	humanEvidence atomic.Bool

	// preOpenerUtterances counts how many SEPARATE things the far end said before
	// the agent spoke at all. It is the one cheap machine tell that survived
	// measurement: a person who answers says one thing — "हैलो?", "हाँ, स्टूडियो
	// बोल रहा हूँ" — and then waits for the caller. A recorded greeting pauses for
	// breath and carries on, and on the two voicemails that fooled the old settle
	// trigger those pauses were 940ms and 1020ms — long enough to look exactly
	// like a human's "hello" landing and going quiet.
	preOpenerUtterances atomic.Int64

	// Opener decision telemetry — which rule released the opening line and how
	// long after pickup. Written once, read by the end-of-call event record.
	openerReason atomic.Value // string
	openerAtNano atomic.Int64
	openerLine   string // the exact words that were pre-recorded for this lead

	// spokeDuringOpenerMs is how much the prospect said WHILE the pre-recorded
	// opener was still playing. The opener is exempt from barge-in and plays to
	// the end, so this is the direct measure of how much it talks over the person
	// it just reached — and therefore of how long an opening line the campaign
	// can afford. It is a measurement rather than an argument on purpose: the
	// opener grew by 1.8s when it started leading with their review count.
	spokeDuringOpenerMs atomic.Int64

	// seq is this call's position in the run, so an event record lines up with
	// the campaign log and results.csv without matching on timestamps.
	seq int

	// replyLatencies collects every measured turnaround on this call — the gap a
	// prospect actually sat through between finishing their sentence and hearing
	// the agent start. One median per campaign is worth more than any amount of
	// argument about whether the model "feels" slow.
	latencyMu      sync.Mutex
	replyLatencies []int64

	// answeredNano is when call.answered arrived. With dial-time media streaming
	// the socket opens while the phone is still RINGING, so frames — ringback
	// tone, not a person — start arriving seconds before anyone picks up. Nothing
	// may act on inbound audio, and the opener may not play, until this is set.
	answeredNano atomic.Int64

	// transcriptBuf accumulates the prospect-side transcription right after the
	// opener, scanned for voicemail/IVR greeting cues. Only touched by the single
	// brain read goroutine, so it needs no lock.
	transcriptBuf strings.Builder

	// Live conversation transcript. The brain transcribes the prospect and the agent text IS the agent transcript, but on a
	// live call those fragments were only ever fed to voicemail detection and then
	// dropped — so a finished call left no record of what was actually said, and
	// "why did that call go wrong?" could only be answered by listening to the
	// recording. Fragments arrive a few words at a time; they are accumulated per
	// speaker and flushed as one line when the turn changes hands, which is the
	// only boundary that reliably ends an utterance.
	//
	// Written only by the single brain read goroutine. convo is also read
	// by Finish on another goroutine, hence convMu.
	utterance    strings.Builder
	utterSpeaker string
	startNano    int64 // session start, for call-relative transcript timestamps
	convMu       sync.Mutex
	convo        []string

	// bookingStarted guards book_meeting so the model can't create duplicate
	// calendar events by calling it more than once (seen live: 3 events for one slot).
	bookingStarted atomic.Bool

	// lastToolBatchNano is when the model last called any tool, and
	// lastProspectTextNano when the prospect was last transcribed. Together they
	// answer the question that catches the agent holding a conversation with
	// itself: has a real person said ANYTHING since the last time the model
	// claimed they had? See fabricatedToolCall.
	lastToolBatchNano    atomic.Int64
	lastProspectTextNano atomic.Int64

	// Why the carrier says the call ended. Set from the call.hangup webhook and
	// carried onto the event record, so a dial that never connected says WHY on
	// the same line that says it did not.
	hangupCause  atomic.Value // string
	hangupSource atomic.Value // string

	// Captured during the conversation.
	mu            sync.Mutex
	leadName      string
	disposition   string
	callback      string
	attendeeEmail string
	meetingTime   string
	meetLink      string
	whatsapp      string // whether the prospect is on WhatsApp ("yes"/"no")
	linkChannel   string // "whatsapp"|"email" — set by send_demo_link; "" = nothing promised
	language      string // "hi"|"en" — the language the call ended up in, drives the follow-up message
	followupWhen  string // the day/time they agreed to be contacted again, in their own words
	followupKind  string // "meeting"|"callback"|"declined" — set by set_followup

	// done is closed exactly once when the call completes.
	done       chan Result
	finishOnce sync.Once
}

func (s *Session) Done() <-chan Result { return s.done }

// markAnswered records that the prospect picked up. Idempotent: Telnyx can
// redeliver a webhook, and the answer instant anchors every opener timer.
func (s *Session) markAnswered() {
	if s.answeredNano.CompareAndSwap(0, time.Now().UnixNano()) {
		s.log.Debug("call answered — releasing opener and inbound audio")
	}
}

// answered reports whether the call has been picked up.
func (s *Session) answered() bool { return s.answeredNano.Load() != 0 }

// answerLost reports the failure this route actually has: a call the prospect
// picked up, held to their ear, heard nothing from, and hung up — while Telnyx
// was still telling us it was ringing.
//
// On the Telnyx→India route answer supervision arrives late and sometimes not at
// all. Two controlled dials on 21 Aug 2026 to a phone that was answered on the
// first ring produced no call.answered webhook whatsoever (nginx logged three
// POSTs per call: initiated, hangup, streaming.stopped) and died at ~28s with
// hangup_cause=timeout. From results.csv both were "no_answer" — indistinguishable
// from a phone nobody was near.
//
// The tell is WHO cleared the call. A phone that genuinely rings out is cleared
// by our own ring timer (hangup_source=caller) or by the network (unknown). Only
// a person can clear a call from the far end, so hangup_source=callee on a call
// we were never told was answered means somebody was holding that handset.
//
// This is deliberately measured against Session state rather than the carrier's
// cause code, because the cause on such a call is "normal_clearing" — which is
// exactly what a completed conversation looks like.
//
// It could not fire at all while RingTimeout was 30s: we always cancelled first,
// and all 40 dials of live2 came back caller or unknown. Raising the timer past
// how long a person will hold a silent line is what makes this observable.
func (s *Session) answerLost() bool {
	return !s.answered() && strValue(&s.hangupSource) == "callee"
}

// connectedSeconds is how long live media flowed, or 0 if nobody ever answered.
func (s *Session) connectedSeconds() int {
	n := s.answeredNano.Load()
	if n == 0 {
		return 0
	}
	return int(time.Since(time.Unix(0, n)).Seconds())
}

// awaitAnswered blocks until the call is answered and returns that instant, or
// the zero time if the call ended first.
func (s *Session) awaitAnswered() time.Time {
	if n := s.answeredNano.Load(); n != 0 {
		return time.Unix(0, n)
	}
	t := time.NewTicker(20 * time.Millisecond)
	defer t.Stop()
	for {
		select {
		case <-s.ctx.Done():
			return time.Time{}
		case <-t.C:
			if n := s.answeredNano.Load(); n != 0 {
				return time.Unix(0, n)
			}
		}
	}
}

// installBrain publishes a live brain session on this call, exactly once.
// It returns false if one is already in place (or the call has already ended),
// in which case the caller owns the connection it passed in and must close it.
func (s *Session) installBrain(g *BrainConn) bool {
	s.brainMu.Lock()
	defer s.brainMu.Unlock()
	if s.brain != nil || s.ctx.Err() != nil {
		return false
	}
	s.brain = g
	return true
}

// hasBrain reports whether a session has already been installed — used by the
// media-stream handler to skip connecting when the pre-connect already landed.
func (s *Session) hasBrain() bool {
	s.brainMu.Lock()
	defer s.brainMu.Unlock()
	return s.brain != nil
}

// installVoice publishes this call's ElevenLabs socket, exactly once. Same race
// as the brain: the dial-time pre-connect and the media-stream handler both try,
// and the loser closes its own connection rather than replacing the winner's.
func (s *Session) installVoice(v *VoiceStream) bool {
	s.voiceMu.Lock()
	defer s.voiceMu.Unlock()
	if s.voice != nil || s.ctx.Err() != nil {
		return false
	}
	s.voice = v
	return true
}

func (s *Session) hasVoice() bool {
	s.voiceMu.Lock()
	defer s.voiceMu.Unlock()
	return s.voice != nil
}

// Finish marks the session complete and returns the result on the done channel.
// hangupBy is "agent", "prospect", "timeout", or "interrupted".
func (s *Session) Finish(hangupBy string) {
	s.finishOnce.Do(func() {
		s.mu.Lock()
		name := s.leadName
		disposition := s.disposition
		callback := s.callback
		attendeeEmail := s.attendeeEmail
		meetingTime := s.meetingTime
		meetLink := s.meetLink
		linkChannel := s.linkChannel
		language := s.language
		followupWhen := s.followupWhen
		followupKind := s.followupKind
		s.mu.Unlock()

		if language == "" {
			language = "hi"
		}

		if disposition == "" {
			// The agent never set an outcome — derive one from how the call ended.
			switch {
			case s.answerLost():
				// The far end put the phone down on a call Telnyx never told us was
				// answered. See answerLost — this is a person who picked up, heard
				// silence, and gave up, and calling it "no_answer" hides the single
				// most expensive failure this campaign has.
				disposition = "answer_lost"
			case !s.answered():
				// call.hangup fires for a rejected or unanswered call too, and the
				// old default called that "hung_up" — as though a person had picked
				// up and put the phone down. Three of nine calls in one live run were
				// labelled that way having never connected at all, which inflates
				// every answer-rate and pickup-quality number derived from the CSV.
				// Zero media frames is zero conversation.
				disposition = "no_answer"
			case hangupBy == "timeout":
				disposition = "no_answer"
			case hangupBy == "interrupted":
				disposition = "interrupted"
			case hangupBy == "prospect":
				disposition = "hung_up"
			default:
				disposition = "incomplete"
			}
		}

		result := Result{
			CallID:        s.CallControlID,
			Phone:         s.Phone,
			Business:      s.prospect.Name,
			OwnerName:     name,
			City:          s.prospect.City,
			Rating:        s.prospect.Rating,
			Reviews:       s.prospect.TotalReviews,
			Disposition:   disposition,
			Callback:      callback,
			HangupBy:      hangupBy,
			AttendeeEmail: attendeeEmail,
			MeetingTime:   meetingTime,
			MeetLink:      meetLink,
			LinkChannel:   linkChannel,
			Language:      language,
			FollowupWhen:  followupWhen,
			FollowupKind:  followupKind,
			Timestamp:     time.Now(),

			ConnectedSec:     s.connectedSeconds(),
			ProspectSpeechMs: int(s.framesSpeech.Load()) * frameMillis,
			AgentTurns:       int(s.agentTurns.Load()),
		}
		s.cancel()
		if s.telnyxWS != nil {
			s.telnyxWS.Close()
		}
		s.brainMu.Lock()
		if s.brain != nil {
			s.brain.Close()
		}
		s.brainMu.Unlock()
		s.voiceMu.Lock()
		if s.voice != nil {
			s.voice.Close()
		}
		s.voiceMu.Unlock()

		// Deliver what the agent promised. This runs AFTER the line is down, so a
		// slow sidecar can never add latency to a live call — and the whole win of
		// this campaign is on the other side of it, because "haan bhej do" is worth
		// nothing until the message actually lands. A late refusal still suppresses
		// it here.
		dispatchLink(s.cfg, s.prospect, &result)

		s.log.Info("inbound loudness histogram (frames per mean-abs band)",
			"0-99", s.energyHist[0].Load(), "100-299", s.energyHist[1].Load(),
			"300-599", s.energyHist[2].Load(), "600-999", s.energyHist[3].Load(),
			"1000-1999", s.energyHist[4].Load(), "2000-3999", s.energyHist[5].Load(),
			"4000-7999", s.energyHist[6].Load(), "8000+", s.energyHist[7].Load(),
			"speech_floor", speechEnergyThreshold, "barge_in_floor", bargeInThreshold)
		s.log.Info("inbound audio accounting",
			"frames_from_telnyx", s.framesIn.Load(),
			"frames_with_speech", s.framesSpeech.Load(),
			"frames_reaching_model", s.framesToModel.Load(),
			"speech_frames_eaten_by_echo_gate", s.echoDropSpeech.Load(),
			"prospect_speech_seconds", float64(s.framesSpeech.Load())*0.02)
		// How the barge-in gate actually behaved. All back-channels and no
		// barge-ins means bargeInMinFrames is too high and the prospect cannot
		// interrupt at all; the reverse means it is too low and short
		// acknowledgements are still killing the agent's turns.
		s.log.Info("barge-in gate",
			"preambles_dropped", s.preamblesDropped.Load(),
			"interruptions_allowed", s.bargeIns.Load(),
			"back_channels_withheld", s.backChannels.Load(),
			"thinking_fillers", s.thinkingFills.Load(),
			"resume_prompts", s.resumePrompts.Load(),
			"min_frames", bargeInMinFrames, "min_ms", bargeInMinFrames*frameMillis,
			"barge_in_floor", bargeInThreshold)
		// Replay the whole conversation in one block. The individual lines were
		// already logged live, but on a campaign they arrive interleaved with every
		// other call's telemetry; this is the one place a single call can be read
		// end to end. A trailing part-utterance is deliberately not flushed here —
		// the read goroutine owns that buffer.
		s.convMu.Lock()
		convo := append([]string(nil), s.convo...)
		s.convMu.Unlock()
		if len(convo) > 0 {
			s.log.Info("call transcript (" + strconv.Itoa(len(convo)) + " turns)\n" + strings.Join(convo, "\n"))
		} else {
			s.log.Info("call transcript: nothing was transcribed on either side")
		}

		s.log.Info("session closed",
			"owner", result.OwnerName, "disposition", result.Disposition,
			"hangup_by", hangupBy, "link", result.LinkChannel, "link_status", result.LinkStatus,
			"followup", result.FollowupKind, "followup_when", result.FollowupWhen)

		// One machine-readable record of everything this call knew about itself.
		// Written last, after dispatch, so link_status on it is what actually
		// happened rather than what was promised.
		runLog.Call(s.event(result, hangupBy, convo))

		s.done <- result
		close(s.done)
	})
}

// strValue reads an atomic.Value that only ever holds strings. Taken by
// POINTER: copying an atomic.Value copies the interface word it guards, which
// races with a concurrent Store from the webhook goroutine.
func strValue(v *atomic.Value) string {
	s, _ := v.Load().(string)
	return s
}

// hangupCauseString is what the carrier said about how this call ended, or ""
// if it never said.
func (s *Session) hangupCauseString() string { return strValue(&s.hangupCause) }

// noteReplyLatency keeps one measured turnaround for the end-of-call record.
// Bounded, because a pathological call must not be able to grow this without
// limit — sixty turns is already far more than any call on this campaign runs.
func (s *Session) noteReplyLatency(ms int64) {
	s.latencyMu.Lock()
	if len(s.replyLatencies) < 60 {
		s.replyLatencies = append(s.replyLatencies, ms)
	}
	s.latencyMu.Unlock()
}

// event assembles this call's record for the run log. Everything on it is
// already known to the session; the value is in having it in one place, in one
// object, next to the transcript — so "why did that call go wrong?" is a
// question the file answers rather than one the recordings have to.
func (s *Session) event(r Result, hangupBy string, convo []string) CallEvent {
	ms := func(from, to int64) int64 {
		if from <= 0 || to <= 0 || to < from {
			return -1
		}
		return (to - from) / 1e6
	}
	now := time.Now().UnixNano()
	answered := s.answeredNano.Load()

	ev := CallEvent{
		Seq:      s.seq,
		At:       r.Timestamp.UTC().Format(time.RFC3339),
		CallID:   s.CallControlID,
		Phone:    s.Phone,
		Business: s.prospect.Name,
		City:     s.prospect.City,
		Vertical: s.prospect.Vertical,
		Rating:   s.prospect.Rating,
		Reviews:  s.prospect.TotalReviews,
		Landline: !s.prospect.IsMobile(),

		AnsweredMs: ms(s.startNano, answered),
		DurationMs: ms(s.startNano, now),
		TalkMs:     ms(answered, now),

		OpenerAfterMs:       ms(answered, s.openerAtNano.Load()),
		OpenerStyle:         openerStyle,
		OpenerLine:          s.openerLine,
		OpenerPersonal:      s.openerPersonal,
		OpenerAudioMs:       int64(len(s.fillerMulaw)) / 8, // 8 μ-law bytes = 1ms at 8kHz
		SpokeDuringOpenerMs: s.spokeDuringOpenerMs.Load(),

		AMD:        s.amdRawString(),
		AMDAfterMs: ms(answered, s.amdAtNano.Load()),

		HangupCause:  strValue(&s.hangupCause),
		HangupSource: strValue(&s.hangupSource),

		FramesFromTelnyx:    s.framesIn.Load(),
		FramesWithSpeech:    s.framesSpeech.Load(),
		FramesToModel:       s.framesToModel.Load(),
		SpeechEatenByGate:   s.echoDropSpeech.Load(),
		BargeIns:            s.bargeIns.Load(),
		BackChannels:        s.backChannels.Load(),
		ThinkingFills:       s.thinkingFills.Load(),
		ResumePrompts:       s.resumePrompts.Load(),
		PreOpenerUtterances: s.preOpenerUtterances.Load(),

		Turns:      len(convo),
		Transcript: convo,

		Disposition:  r.Disposition,
		HangupBy:     hangupBy,
		Refused:      s.refused.Load(),
		OwnerName:    r.OwnerName,
		Language:     r.Language,
		LinkChannel:  r.LinkChannel,
		LinkStatus:   r.LinkStatus,
		DemoURL:      r.DemoURL,
		FollowupKind: r.FollowupKind,
		FollowupWhen: r.FollowupWhen,
	}
	if v, ok := s.openerReason.Load().(string); ok {
		ev.OpenerReason = v
	}
	if ev.AMD == "pending" {
		ev.AMD = "" // no verdict is not the same fact as a pending one on a finished call
	}
	for i := range ev.EnergyHist {
		ev.EnergyHist[i] = s.energyHist[i].Load()
	}
	s.latencyMu.Lock()
	ev.ReplyLatenciesMs = append([]int64(nil), s.replyLatencies...)
	s.latencyMu.Unlock()
	return ev
}

// ---------------------------------------------------------------------------
// SessionRegistry
// ---------------------------------------------------------------------------

type SessionRegistry struct {
	mu       sync.Mutex
	sessions map[string]*Session
	labels   map[string]string // callControlID → filesystem label, survives Remove for late recording webhooks
	cfg      Config
}

func NewSessionRegistry(cfg Config) *SessionRegistry {
	return &SessionRegistry{
		sessions: make(map[string]*Session),
		labels:   make(map[string]string),
		cfg:      cfg,
	}
}

// Label returns the recording filename label for a call (kept after the session
// is removed, since call.recording.saved can arrive seconds later).
func (r *SessionRegistry) Label(callControlID string) string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.labels[callControlID]
}

func (r *SessionRegistry) Create(callControlID string, p Prospect) *Session {
	ctx, cancel := context.WithCancel(context.Background())
	s := &Session{
		CallControlID: callControlID,
		Phone:         p.Phone,
		prospect:      p,
		cfg:           r.cfg,
		log:           slog.With("call", callControlID, "phone", p.Phone),
		ctx:           ctx,
		cancel:        cancel,
		done:          make(chan Result, 1),
		startNano:     time.Now().UnixNano(),
	}
	r.mu.Lock()
	r.sessions[callControlID] = s
	r.labels[callControlID] = sanitizeLabel(p.Name, p.Phone)
	r.mu.Unlock()
	return s
}

func (r *SessionRegistry) Get(callControlID string) *Session {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.sessions[callControlID]
}

func (r *SessionRegistry) Remove(callControlID string) {
	r.mu.Lock()
	delete(r.sessions, callControlID)
	r.mu.Unlock()
}

// ---------------------------------------------------------------------------
// Media-stream WebSocket handler — Telnyx connects here after streaming_start
// ---------------------------------------------------------------------------

var wsUpgrader = websocket.Upgrader{
	CheckOrigin:     func(r *http.Request) bool { return true },
	ReadBufferSize:  2048,
	WriteBufferSize: 2048,
}

func HandleMediaStream(cfg Config, sessions *SessionRegistry) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ws, err := wsUpgrader.Upgrade(w, r, nil)
		if err != nil {
			slog.Error("ws upgrade failed", "error", err)
			return
		}
		defer ws.Close()

		log := slog.With("component", "media-stream")
		log.Info("telnyx websocket connected")

		var callControlID, streamID string
		for {
			var msg telnyxWSMsg
			if err := ws.ReadJSON(&msg); err != nil {
				log.Error("read start event", "error", err)
				return
			}
			switch msg.Event {
			case "connected":
				continue
			case "start":
				if msg.Start == nil {
					log.Error("start event missing payload")
					return
				}
				callControlID = msg.Start.CallControlID
				streamID = msg.Start.StreamID
				if streamID == "" {
					streamID = msg.StreamID
				}
			default:
				continue
			}
			break
		}

		// Where Telnyx anchors media decides a large, fixed slice of the per-turn
		// latency: every reply travels prospect → Telnyx media → here → the model and
		// back. Logging the peer makes that visible instead of guessed at — if this
		// is a US address while the agent and the model are elsewhere, roughly
		// half a second of every turn is spent crossing the Atlantic twice.
		// nginx terminates TLS in front of us, so RemoteAddr is always loopback —
		// the real peer is the X-Real-IP it forwards.
		mediaPeer := r.Header.Get("X-Real-IP")
		if mediaPeer == "" {
			mediaPeer = r.RemoteAddr
		}
		log = log.With("call", callControlID, "stream", streamID)
		log.Info("stream started", "telnyx_media_peer", mediaPeer)

		// With dial-time streaming Telnyx can connect this socket before the dial
		// POST has even returned the call_control_id we register the session under,
		// so a miss here is a race to wait out, not an error.
		sess := sessions.Get(callControlID)
		for i := 0; sess == nil && i < 50; i++ {
			time.Sleep(20 * time.Millisecond)
			sess = sessions.Get(callControlID)
		}
		if sess == nil {
			log.Error("no session for call")
			return
		}

		sess.telnyxWS = ws
		sess.streamID = streamID

		// If AMD already flagged a voicemail and hung the call up before the stream
		// attached, don't bother connecting the brain.
		if sess.ctx.Err() != nil {
			log.Info("call already ended before media stream attached")
			return
		}

		// The brain may already be connected — the campaign loop pre-connects at dial
		// time so the WebSocket handshake and setup round-trip happen while the phone
		// is still ringing, instead of after pickup where the prospect would hear it
		// as dead air. Fall back to connecting here if the pre-connect didn't land.
		fillerReady := len(sess.fillerMulaw) > 0
		if sess.hasBrain() {
			log.Debug("using pre-connected brain session")
		} else {
			brain, err := ConnectBrain(sess.ctx, cfg,
				buildCallInstruction(cfg, sess.prospect, fillerReady, sess.openerPersonal))
			if err != nil {
				log.Error("brain connect failed", "error", err)
				sess.Finish("agent")
				return
			}
			if !sess.installBrain(brain) {
				brain.Close() // the pre-connect landed first — discard this one
			}
		}

		// Same for the voice. Without it the agent has a brain and no mouth, so a
		// failure here ends the call rather than running one that can never speak.
		if sess.hasVoice() {
			log.Debug("using pre-connected voice stream")
		} else {
			voice, err := ConnectVoice(sess.ctx, cfg)
			if err != nil {
				log.Error("voice connect failed — the agent would have no way to speak", "error", err)
				sess.Finish("agent")
				return
			}
			if !sess.installVoice(voice) {
				voice.Close() // the pre-connect landed first
			}
		}

		// Listen-first: hold the agent silent until a human is detected (AMD or the
		// prospect speaking), then deliver the opening hook. A voicemail never gets
		// the opener — it's hung up. Inbound audio stays gated out of the brain until the
		// opener lands, so the live model can't step on the pre-recorded hook.
		go sess.awaitOpenerThenPlay(fillerReady)

		// THREE bridges, not the two the Gemini build had: the agent's words and
		// the agent's voice now arrive on separate sockets. The count here must
		// match, and when it did not the process panicked with
		// "sync: negative WaitGroup counter" the instant a call ended — after a
		// clean 162-second conversation, taking results.csv and the run log with
		// it. A campaign would have died on its first call.
		var wg sync.WaitGroup
		wg.Add(3)
		go func() { defer wg.Done(); sess.bridgeTelnyxToBrain() }()
		go func() { defer wg.Done(); sess.bridgeBrainToVoice() }()
		go func() { defer wg.Done(); sess.bridgeVoiceToTelnyx() }()
		go sess.superviseConversation() // opening + silence handling; exits on ctx.Done
		wg.Wait()

		// Bridges exited — call is over.
		sess.Finish("agent")
	}
}

// ---------------------------------------------------------------------------
// Telnyx → Brain
// ---------------------------------------------------------------------------

func (s *Session) bridgeTelnyxToBrain() {
	// Only this goroutine touches these, so they need no lock.
	var gate bargeGate
	var think thinkingGate

	for {
		select {
		case <-s.ctx.Done():
			return
		default:
		}

		var msg telnyxWSMsg
		if err := s.telnyxWS.ReadJSON(&msg); err != nil {
			if s.ctx.Err() == nil {
				s.log.Info("telnyx ws closed", "error", err)
			}
			s.cancel()
			return
		}

		switch msg.Event {
		case "media":
			if msg.Media == nil || msg.Media.Payload == "" {
				continue
			}
			if msg.Media.Track == "outbound" {
				continue
			}
			mulaw, err := base64.StdEncoding.DecodeString(msg.Media.Payload)
			if err != nil {
				continue
			}

			// Still ringing. These frames are ringback tone; counting them as the
			// prospect speaking would trip the listen-first opener into greeting a
			// ringing phone, and feeding them to the model would have it answer a
			// dial tone.
			if !s.answered() {
				continue
			}

			// Track when the prospect is actually talking (vs. line silence) so the
			// listen-first trigger and the reply-latency measurement have a signal.
			now := time.Now().UnixNano()
			energy := mulawEnergy(mulaw)
			s.framesIn.Add(1)
			s.energyHist[energyBucket(energy)].Add(1)
			if energy > speechEnergyThreshold {
				prevSpeech := s.lastSpeechNano.Load()
				s.framesSpeech.Add(1)
				s.lastSpeechNano.Store(now)
				if !s.brainGateOpen.Load() {
					// Pre-opener: tally how much the prospect has ACTUALLY spoken, so the
					// listen-first trigger fires on a real "hello", not a click or hiss.
					s.speechMillis.Add(int64(len(mulaw)) / 8) // 8 μ-law bytes = 1ms at 8kHz

					// ...and count how many SEPARATE things they have said. A person who
					// answers says one thing and then waits for the caller; a recorded
					// greeting pauses for breath and starts again, which is what walked
					// two voicemails straight through the old settle trigger. This is the
					// only machine tell available before AMD rules, and it costs one
					// comparison per frame.
					if prevSpeech == 0 || now-prevSpeech > int64(preOpenerUtteranceGap) {
						s.preOpenerUtterances.Add(1)
						s.curUtteranceMs.Store(0)
					}
					// How long THIS utterance has run, and the longest any of them has.
					cur := s.curUtteranceMs.Add(int64(len(mulaw)) / 8)
					if cur > s.longestUtteranceMs.Load() {
						s.longestUtteranceMs.Store(cur)
					}
				}
			}

			// Listen-first gate: until the opening hook has been delivered, don't feed
			// the prospect's audio to the model — otherwise it answers their "hello"
			// and steps on our pre-recorded opener.
			if !s.brainGateOpen.Load() {
				continue
			}

			// Thinking filler: if they've stopped and nothing has come back yet, make
			// the sound a person makes while working out what to say. Checked here,
			// on every 20ms frame, because the 500ms supervisor is far too coarse to
			// land inside the gap it is meant to cover.
			if !s.fillerPlaying.Load() && !s.endingCall.Load() &&
				think.observe(now, s.lastSpeechNano.Load(), s.playbackEndNano.Load(),
					s.lastModelAudioNano.Load(), energy > speechEnergyThreshold) {
				s.playThinkingFiller()
			}
			// Barge-in gate — see bargeGate. While the caller is still hearing the
			// LIVE model, a loud frame is not enough to interrupt it: only a
			// sustained run is, because loudness cannot separate a back-channel
			// ("हाँ") from a real interruption but length can.
			//
			// The PRE-RECORDED opener is deliberately exempt. It plays to completion
			// whatever the model does, so there is no turn to protect — and what the
			// prospect says over it is not an interruption, it is their ANSWER.
			// Holding a 200ms "हाँ" back there would leave the model with nothing to
			// reply to at the one moment the whole listen-first design exists to
			// cover. Echo of our own audio is still dropped.
			if now < s.playbackEndNano.Load() {
				if s.fillerPlaying.Load() {
					// They are answering while the pre-recorded opener is still going.
					// It will play to the end regardless — that is deliberate, since
					// what they say here is their ANSWER and not an interruption — so
					// this is simply how much of it lands on top of them.
					if energy > speechEnergyThreshold {
						s.spokeDuringOpenerMs.Add(int64(len(mulaw)) / 8)
					}
					if energy < bargeInThreshold {
						if energy > speechEnergyThreshold {
							s.echoDropSpeech.Add(1)
						}
						continue
					}
				} else {
					decision, release, held := gate.admit(mulaw, energy)
					switch decision {
					case bargeHold:
						continue
					case bargeEcho:
						if energy > speechEnergyThreshold {
							s.echoDropSpeech.Add(1)
						}
						continue
					case bargeBackChannel:
						s.backChannels.Add(1)
						s.echoDropSpeech.Add(int64(held))
						s.log.Debug("held speech did not sustain — back-channel, agent keeps the floor",
							"frames_held", held, "ms", held*frameMillis)
						continue
					case bargeQualified:
						s.bargeIns.Add(1)
						s.log.Debug("barge-in qualified — releasing held frames",
							"frames", held, "ms", held*frameMillis, "energy", energy)
						for _, f := range release {
							if !s.forwardToBrain(f) {
								return
							}
						}
						continue
					}
					// bargePass: the gate is already open for this playback window.
				}
			} else if held := gate.release(); len(held) > 0 {
				// The agent has just gone quiet with frames still held back. They were
				// withheld to protect a turn that is now over, and they are the opening
				// syllables of the prospect's answer — forward them, oldest first,
				// rather than making the model wait for the prospect to start again.
				s.log.Debug("agent went quiet — releasing withheld speech",
					"frames", len(held), "ms", len(held)*frameMillis)
				for _, f := range held {
					if !s.forwardToBrain(f) {
						return
					}
				}
			}

			if !s.forwardToBrain(mulaw) {
				return
			}

		case "stop":
			s.log.Info("telnyx stream stopped")
			s.cancel()
			return
		}
	}
}

// forwardToBrain sends one inbound μ-law frame to the model.
//
// There is no transcode here and that is the point. The Gemini build had to turn
// every 20ms frame into 16kHz linear PCM — a codec conversion plus a resample,
// fifty times a second, for the whole call. OpenAI Realtime accepts
// `audio/pcmu` directly, so the bytes that arrived from Telnyx are the bytes
// that leave for the model, base64 and nothing else.
//
// Returns false if the session is finished, in which case the caller must stop
// reading — this is the single place inbound audio leaves for the model, so the
// frame counter is accurate by construction.
func (s *Session) forwardToBrain(mulaw []byte) bool {
	s.framesToModel.Add(1)
	b64 := base64.StdEncoding.EncodeToString(mulaw)
	if err := s.brain.SendAudio(b64); err != nil {
		if s.ctx.Err() == nil {
			s.log.Error("brain send failed", "error", err)
		}
		s.cancel()
		return false
	}
	return true
}

// ---------------------------------------------------------------------------
// Brain → Voice → Telnyx (also handles tool calls)
//
// Two goroutines, because the agent's words and the agent's voice now arrive on
// two different sockets:
//
//	bridgeBrainToVoice   reads text deltas, tool calls and turn boundaries from
//	                     the model and feeds speakable clauses to ElevenLabs
//	bridgeVoiceToTelnyx  reads synthesised μ-law back and puts it on the call
//
// The Gemini build did all of this in one loop because one socket carried both.
// Splitting it is what makes an ElevenLabs voice possible at all, and it is also
// where the one genuinely new failure mode lives — see Interrupt below.
// ---------------------------------------------------------------------------

func (s *Session) bridgeBrainToVoice() {
	endCallPending := false
	agg := newClauseAggregator()
	firstClauseOfTurn := true

	// speak pushes one ready clause at the synthesiser. The first clause of a
	// turn is flushed immediately: it is the only one whose latency the caller
	// can perceive, because everything after it queues behind audio that is
	// already playing.
	speak := func(text string) {
		if text == "" {
			return
		}
		if err := s.voice.Say(text, firstClauseOfTurn); err != nil {
			if s.ctx.Err() == nil {
				s.log.Warn("voice say failed", "error", err)
			}
			return
		}
		s.noteTranscript("agent", text)
		firstClauseOfTurn = false
	}

	for {
		select {
		case <-s.ctx.Done():
			return
		default:
		}

		ev, err := s.brain.ReadEvent()
		if err != nil {
			if s.ctx.Err() == nil {
				s.log.Info("brain ws closed", "error", err)
			}
			s.cancel()
			return
		}

		switch ev.Kind {

		case BrainProspectSaid:
			// Voicemail/IVR cue-word bailout: scan the prospect-side transcript right
			// after the opener. If a recorded-greeting phrase shows up, hang up before
			// the agent pitches a machine.
			s.noteTranscript("prospect", ev.Text)
			s.noteRefusal(ev.Text)
			if s.detectVoicemailFromTranscript(ev.Text) {
				s.flushTranscript()
				return
			}

		case BrainTextDelta:
			if s.voice.Current() == "" {
				s.voice.Begin()
				agg.reset()
				firstClauseOfTurn = true
			}
			speak(agg.push(ev.Text))

		case BrainToolCalls:
			// Swallow the preamble.
			//
			// The Realtime models narrate before a function call - "मैं अभी WhatsApp
			// पर भेजने की प्रक्रिया पूरी कर देती हूँ", "एक पल, मैं इसे ध्यान से ले
			// लेती हूँ". The prospect hears the agent describing its own plumbing,
			// and on a live call it was the single most machine-like thing left.
			// Three separate rounds of prompting failed to stop it: a NEVER NARRATE
			// section, exact scripted lines, and a SILENT TOOLS rule. It is
			// structural to the model, so it is dealt with here instead.
			//
			// Tools are silent BY DESIGN in this script - every line the prospect is
			// meant to hear is spoken in the response AFTER the tool result - so any
			// text sharing a response with a tool call is preamble by definition and
			// nothing of value is lost by dropping it.
			//
			// The condition is what makes this safe and free: turnFirstAudioNano is
			// zero until the first byte of this turn actually reaches the caller. If
			// it is still zero, the preamble exists only in our own buffers and
			// ElevenLabs', and can be thrown away with nobody having heard a thing.
			// Once they HAVE started hearing it, cutting mid-word would sound far
			// worse than the sentence itself, so it is left to finish.
			if len(ev.Calls) > 0 && s.turnFirstAudioNano.Load() == 0 && s.voice.Current() != "" {
				s.log.Info("dropped the model's tool-call preamble before it was heard",
					"tool", ev.Calls[0].Name, "text", s.voice.Current())
				s.voice.Interrupt()
				s.dropHeldAudio()
				agg.reset()
				firstClauseOfTurn = true
				s.preamblesDropped.Add(1)
			}
			s.handleToolCalls(ev.Calls, &endCallPending)

		case BrainInterrupted:
			s.flushTranscript()
			s.playing.Store(0)

			now := time.Now().UnixNano()

			// How long the caller has actually BEEN HEARING this turn. Not how many
			// bytes were produced: synthesis runs far faster than realtime, so
			// bytes-sent says "3 seconds" while the caller has heard 800ms, and a
			// fragment gets treated as a finished sentence.
			var heardMs int64
			if t := s.turnFirstAudioNano.Load(); t > 0 {
				heardMs = (now - t) / 1e6
			}

			// THREE buffers, in this order, and all three are load-bearing.
			//
			// 1. ElevenLabs is still synthesising the rest of the sentence. Closing
			//    the context stops that; marking it dead discards what is already in
			//    flight. The Gemini build had no equivalent — the model that decided
			//    to stop talking was the same one making the audio. Skip this and the
			//    prospect hears the sentence they interrupted play out on top of the
			//    answer to their interruption.
			// 2. Our own queued frames.
			// 3. What Telnyx already holds in its playout buffer.
			//
			// Steps 2 and 3 are skipped inside the first shortTurnAudioMs. The agent
			// got two words out; wiping the buffer means the prospect hears a
			// fragment, or nothing, and asks "hello?". Let the sentence finish —
			// worst case they hear one more clause than they wanted, which is what
			// happens between two people on a real phone call. Step 1 still runs:
			// there is never a reason to keep synthesising an abandoned turn.
			s.voice.Interrupt()
			agg.reset()
			firstClauseOfTurn = true

			if heardMs >= shortTurnAudioMs {
				s.clearTelnyxBuffer()
				s.dropHeldAudio()            // stale — they've moved on
				s.playbackEndNano.Store(now) // buffered audio was flushed
			} else {
				// Nothing was flushed, so playbackEndNano still describes reality and
				// must not be stomped — the barge-in gate and the silence supervisor
				// both key off it.
				s.log.Debug("turn cut short — keeping playout so the sentence finishes",
					"heard_ms", heardMs)
			}

			// Arm the resume watchdog on EVERY interruption, long or short. The
			// model does not restart on its own afterwards — from its side the
			// prospect took the floor and never gave it back — and it does not
			// matter how much it had said first.
			s.interruptedNano.Store(now)
			s.agentSpeaking.Store(false)
			s.turnFirstAudioNano.Store(0)

		case BrainTurnComplete:
			// The last clause of a turn has no boundary after it and would never be
			// spoken unless it is drained and flushed here.
			speak(agg.drain())
			if err := s.voice.Finish(); err != nil && s.ctx.Err() == nil {
				s.log.Debug("voice finish failed", "error", err)
			}
			agg.reset()
			firstClauseOfTurn = true

			s.flushTranscript()
			s.agentSpeaking.Store(false)
			s.turnFirstAudioNano.Store(0)
			s.interruptedNano.Store(0) // the turn finished on its own — nothing to resume
			s.agentTurnEndNano.Store(time.Now().UnixNano())

			if endCallPending {
				endCallPending = false
				s.hangUpAfterGoodbye()
			}

		case BrainError:
			s.log.Warn("brain error", "detail", ev.Text)
		}
	}
}

// hangUpAfterGoodbye waits for the closing line to actually reach the caller
// before dropping the line.
//
// The Gemini build slept a flat 2s here, which worked because its audio arrived
// already spoken. Ours does not: the goodbye is still being synthesised when the
// turn completes, so a fixed sleep hangs up on the word "thanks". playbackEndNano
// is the estimate of when the caller finishes hearing what we have sent, so wait
// for that — with a floor, because a goodbye that never synthesises must not hold
// the line open forever.
func (s *Session) hangUpAfterGoodbye() {
	go func() {
		deadline := time.Now().Add(goodbyeMaxWait)
		for time.Now().Before(deadline) {
			end := s.playbackEndNano.Load()
			if end > 0 && time.Now().UnixNano() >= end {
				break
			}
			select {
			case <-time.After(100 * time.Millisecond):
			case <-s.ctx.Done():
				return
			}
		}
		// A short tail so the last syllable is not clipped by the hangup itself.
		time.Sleep(goodbyeTail)
		if err := telnyxHangup(s.cfg, s.CallControlID); err != nil {
			s.log.Warn("hangup failed", "error", err)
		}
	}()
}

const (
	// goodbyeMaxWait bounds how long a hangup waits for the closing line to play.
	goodbyeMaxWait = 8 * time.Second
	// goodbyeTail keeps the hangup from clipping the last syllable.
	goodbyeTail = 400 * time.Millisecond
)

// ---------------------------------------------------------------------------
// Voice → Telnyx
// ---------------------------------------------------------------------------

// bridgeVoiceToTelnyx puts synthesised audio on the call.
//
// Everything here is already μ-law at 8kHz — ElevenLabs is asked for
// `ulaw_8000` — so the bytes off the socket go into a Telnyx media frame
// unmodified. The Gemini build transcoded every chunk from 24kHz linear PCM.
func (s *Session) bridgeVoiceToTelnyx() {
	var playoutDeadline int64 // est. wall-time the caller finishes what we've already sent THIS turn

	for {
		select {
		case <-s.ctx.Done():
			return
		case chunk, ok := <-s.voice.Chunks():
			if !ok {
				if s.ctx.Err() == nil {
					s.log.Info("voice stream ended")
				}
				return
			}
			if len(chunk.Mulaw) == 0 {
				continue
			}

			// First audio of a new turn → measure reply latency from when the
			// prospect last spoke. This is the number that matters and it is
			// measured where the caller experiences it: not when the model produced
			// a token, but when sound left for the phone. Only counted as a reply if
			// they actually spoke since the agent's last turn ended — otherwise the
			// agent is continuing, and the gap would be meaningless.
			if !s.agentSpeaking.Load() {
				if last := s.lastSpeechNano.Load(); last > s.agentTurnEndNano.Load() {
					ms := (time.Now().UnixNano() - last) / 1e6
					s.log.Info("reply latency", "ms", ms)
					s.noteReplyLatency(ms)
				}
				s.agentSpeaking.Store(true)
				playoutDeadline = 0 // new turn — don't count the normal cross-turn gap as an underrun
			}

			now := time.Now().UnixNano()

			// Playout-underrun probe: if the caller already finished everything we'd
			// sent THIS turn before this chunk arrived, they heard a gap — i.e. the
			// audio reached us late, which is upstream of Telnyx and the phone leg.
			if playoutDeadline > 0 {
				if gap := now - playoutDeadline; gap > 60*int64(time.Millisecond) {
					s.log.Warn("playout underrun — synthesised audio arrived after the caller's buffer emptied",
						"gap_ms", gap/int64(time.Millisecond))
				}
			}

			s.playing.Store(1)
			s.modelSpoke.Store(true)
			s.lastModelAudioNano.Store(now)
			if s.turnFirstAudioNano.Load() == 0 {
				s.turnFirstAudioNano.Store(now)
				s.agentTurns.Add(1)
			}

			if s.fillerPlaying.Load() {
				// The opener is still playing. Hold the reply back so the two don't
				// overlap; flushHeldAudio sends it the instant the opener ends. The
				// playout probe is meaningless while held, so reset the deadline
				// rather than warn about a gap we created.
				s.holdAudio(chunk.Mulaw)
				playoutDeadline = 0
				continue
			}
			if s.sendTelnyxAudio(chunk.Mulaw) {
				playoutDeadline = s.playbackEndNano.Load()
			}
		}
	}
}

// superviseConversation watches for dead air the bridges can't. The silence
// clock starts when the AGENT stops talking; if the prospect says nothing for
// idleNudgeAfter, the agent checks "you still there?" — exactly once — and if
// they're still silent for the same span again, it hangs up (it never loops the
// question). Runs until the session context is cancelled.
func (s *Session) superviseConversation() {
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()

	nudged := false
	var nudgeNano int64

	for {
		select {
		case <-s.ctx.Done():
			return
		case <-ticker.C:
		}

		if !s.openerPlayed.Load() {
			continue // haven't opened yet — nothing to supervise
		}

		now := time.Now().UnixNano()

		playEnd := s.playbackEndNano.Load()
		if playEnd > now {
			continue // the caller is still HEARING the agent — not silence yet
		}

		// Resume watchdog. An interrupted turn leaves the model stopped mid-thought;
		// it will not restart on its own, because from its side the prospect took
		// the floor and never gave it back. Nothing else catches this quickly — the
		// silence clock below waits 10s, and a live call spent 11 seconds in exactly
		// this state while the prospect said "हलो? हलो?".
		//
		// The stall is timed from the last thing that ACTUALLY happened, not from
		// the interruption: prompting while the prospect is still mid-sentence would
		// make the agent talk over the very person it just yielded to.
		if cut := s.interruptedNano.Load(); cut > 0 && s.lastModelAudioNano.Load() < cut {
			stallFrom := cut
			if sp := s.lastSpeechNano.Load(); sp > stallFrom {
				stallFrom = sp
			}
			if now-stallFrom <= int64(resumeAfterInterrupt) {
				continue // they're still talking, or it's simply too soon
			}
			if s.interruptedNano.CompareAndSwap(cut, 0) {
				if n := s.resumePrompts.Add(1); n <= maxResumePrompts {
					s.log.Info("turn stalled after interruption — prompting the model to continue",
						"stalled_ms", (now-stallFrom)/1e6, "attempt", n)
					if err := s.brain.SendText("[You were cut off mid-sentence and the line has gone quiet. Carry on from where you stopped, in ONE short sentence. Do not greet them again, do not start over, and do not read this note aloud.]"); err != nil {
						s.log.Warn("resume prompt failed", "error", err)
					}
				}
			}
			continue
		}

		lastSpeech := s.lastSpeechNano.Load()

		// Anchor the silence clock to the latest of: the prospect's last speech,
		// when the agent's audio finished PLAYING at the caller (so we time from when
		// they actually stopped hearing us, not when we finished sending), and our
		// last nudge (so the nudge's own audio can't trip the hangup prematurely).
		ref := lastSpeech
		if playEnd > ref {
			ref = playEnd
		}
		if nudgeNano > ref {
			ref = nudgeNano
		}
		if ref == 0 {
			continue // nothing has happened yet (agent hasn't even greeted)
		}

		if lastSpeech > nudgeNano {
			nudged = false // the prospect spoke after our nudge — reset
		}

		if time.Duration(now-ref) <= idleNudgeAfter {
			continue
		}

		if !nudged {
			s.log.Info("silence — checking if prospect is still there")
			_ = s.brain.SendText("[The line has gone quiet for about ten seconds since you finished talking. This is a system cue, not the prospect — gently check if they're still there, in one short line, e.g. \"You still with me?\"]")
			nudged = true
			nudgeNano = now
		} else {
			s.log.Info("still silent after nudge — ending call")
			s.mu.Lock()
			if s.disposition == "" {
				s.disposition = "no_response"
			}
			s.mu.Unlock()
			_ = telnyxHangup(s.cfg, s.CallControlID)
			s.Finish("timeout")
			return
		}
	}
}

// applyFollowup records Stage 7's outcome. Split out from the tool switch so the
// normalisation can be tested without a live session.
func (s *Session) applyFollowup(args map[string]any) {
	kind, _ := args["kind"].(string)
	when, _ := args["when"].(string)
	switch kind {
	case "meeting", "callback", "declined":
	default:
		// The model occasionally invents or omits the kind. A follow-up we cannot
		// classify is still the only next step the call produced, so keep it as a
		// loose callback rather than dropping it.
		kind = "callback"
	}
	when = strings.TrimSpace(when)

	s.mu.Lock()
	s.followupKind = kind
	s.followupWhen = when
	s.mu.Unlock()
	s.log.Info("follow-up agreed", "kind", kind, "when", when)
}

// fabricatedToolCall reports whether this batch of tool calls is the model
// talking to itself.
//
// Seen live, and it is the worst failure this agent has: after one ordinary
// reply from the prospect, the model produced four tool calls in 4.1 seconds —
// send_demo_link, capture_lead(name: "human"), set_followup(meeting, "kal
// shaam"), end_call — with NOT ONE WORD from the far end in between. It asked
// "और आपका नाम?", answered itself "अच्छा human", asked "कल शाम को call करूँ?",
// answered itself "ठीक है", and hung up. The name and the appointment were
// invented outright, and both would have gone into results.csv and the WhatsApp
// greeting as though a person had said them.
//
// The playbook already says STOP, in those words, eleven separate times. It was
// ignored, because a prompt is a request. This is the check:
//
//	capture_lead's argument is "the name they just gave you".
//	set_followup's argument is "the time they just agreed to".
//	send_demo_link's premise is "they just said yes".
//
// All three are claims about something the prospect said. If the prospect has
// not made a sound OR produced a word of transcript since the model's previous
// tool call, no such thing was said and the argument is invented.
//
// Tools in the SAME batch are all fine — they are one response to one utterance
// ("haan bhej do, mera naam Rakesh hai" legitimately produces two). It is the
// NEXT batch, arriving out of silence, that is fiction.
func (s *Session) fabricatedToolCall() bool {
	last := s.lastToolBatchNano.Load()
	if last == 0 {
		return false // first tools of the call; there is nothing to have talked over
	}
	// One reply can legitimately produce two batches in quick succession — "haan
	// bhej do, mera naam Rakesh hai" is a yes AND a name, and the model may emit
	// send_demo_link and capture_lead back to back rather than together. Anything
	// inside toolBurstWindow is treated as one response to one utterance.
	//
	// The monologue this guard exists to catch does not look like that: its four
	// calls were 1.7s, 1.3s and 1.1s apart, because the model was SPEAKING an
	// invented conversation between them. The window sits well below that, and
	// the error it avoids is the expensive one — refusing a link to somebody who
	// really did say yes.
	if time.Now().UnixNano()-last < int64(toolBurstWindow) {
		return false
	}
	heard := s.lastSpeechNano.Load()
	if t := s.lastProspectTextNano.Load(); t > heard {
		heard = t
	}
	return heard <= last
}

// toolBurstWindow is how close two batches of tool calls have to be to count as
// one response to one thing the prospect said.
const toolBurstWindow = 500 * time.Millisecond

func (s *Session) handleToolCalls(calls []BrainToolCall, endCallPending *bool) {
	// Judged once for the whole batch, BEFORE the timestamp is moved on.
	fabricated := s.fabricatedToolCall()
	s.lastToolBatchNano.Store(time.Now().UnixNano())
	if fabricated {
		names := make([]string, 0, len(calls))
		for _, c := range calls {
			names = append(names, c.Name)
		}
		s.log.Warn("MODEL IS TALKING TO ITSELF — tool calls with no prospect speech since the last ones",
			"tools", strings.Join(names, ","), "args_are_invented", true)
		// It is mid-monologue and everything still queued is the agent answering
		// its own questions. Stop it reaching the caller: this is one of the very
		// few moments where we KNOW the audio in flight is wrong. All three
		// buffers, for the same reason a barge-in clears all three.
		s.voice.Interrupt()
		s.clearTelnyxBuffer()
		s.dropHeldAudio()
		s.playbackEndNano.Store(time.Now().UnixNano())
	}

	var responses []BrainToolResult
	// batchSeen keys every answered call in THIS response by tool + arguments, so
	// a repeat inside the same batch can be answered identically without running
	// its side effect a second time. Scoped to one response on purpose: the same
	// tool called again in a LATER turn is a new decision, not a duplicate.
	batchSeen := map[string]map[string]any{}
	reply := func(c BrainToolCall, resp map[string]any) {
		resp = sealNote(resp)
		batchSeen[c.Name+"\x00"+fmt.Sprint(c.Args)] = resp
		responses = append(responses, BrainToolResult{ID: c.ID, Name: c.Name, Response: resp})
	}

	for _, c := range calls {
		s.log.Info("tool call", "name", c.Name, "args", c.Args)

		// The SAME call, repeated inside ONE response. Seen in the probe: three
		// identical send_demo_link calls and three identical set_followup calls in
		// a single batch, from one "haan bhej do".
		//
		// This is not the fabricated-call case — the prospect really did speak, and
		// the first of these is legitimate — so that guard lets all three through.
		// Answering each one separately queues the same WhatsApp job repeatedly and
		// tells the model three times that it succeeded, which reads to it as three
		// separate agreements. One key per (tool, arguments): the first is honoured,
		// the rest get the same answer without the side effect running again.
		key := c.Name + "\x00" + fmt.Sprint(c.Args)
		if prev, dup := batchSeen[key]; dup {
			s.log.Warn("duplicate tool call inside one response — not run again",
				"name", c.Name, "args", c.Args)
			reply(c, prev)
			continue
		}

		// Tools whose arguments are claims about what the prospect just said.
		// end_call is exempt: hanging up is the agent's own decision, and a call
		// that has gone wrong is better ended than looped.
		if fabricated && (c.Name == "capture_lead" || c.Name == "set_followup" ||
			c.Name == "send_demo_link" || c.Name == "set_language") {
			s.log.Warn("REJECTED invented tool call — nobody said this", "name", c.Name, "args", c.Args)
			reply(c, map[string]any{"recorded": false, "error": "nobody_said_that",
				"note": "REJECTED. The prospect has not said a single word since your last tool call — you asked a question and then answered it yourself. Nothing has been recorded. STOP TALKING RIGHT NOW. Do not finish the sentence, do not ask anything else, do not say what they replied. Say nothing at all and wait for them to actually speak."})
			continue
		}

		switch c.Name {

		case "capture_lead":
			// The model reliably confuses "their name" with the business name it was
			// just given — seen live capturing "Meridian Studios" as the owner. A wrong
			// name is worse than no name: it goes into the calendar invite, where
			// being greeted by your own company name is an instant tell.
			if name, ok := c.Args["name"].(string); ok {
				if clean := cleanPersonName(name, s.prospect.Name); clean != "" {
					s.mu.Lock()
					s.leadName = clean
					s.mu.Unlock()
				} else {
					s.log.Warn("rejected implausible lead name", "got", name, "business", s.prospect.Name)
				}
			}
			reply(c, map[string]any{"recorded": true,
				"note": "Name recorded. Say ONE short warm line using it, then STOP TALKING and WAIT for them to speak. Your next job happens in a LATER turn, after they have replied. Do NOT ask it now. Do NOT call end_call."})

		case "set_language":
			// The call can flip language mid-conversation — an owner who answers in
			// Hindi and switches to English the moment a website is mentioned is
			// ordinary. This drives the language of the WhatsApp follow-up, which is
			// written after the call and cannot ask which one to use.
			if lang, ok := c.Args["language"].(string); ok && (lang == "hi" || lang == "en") {
				s.mu.Lock()
				s.language = lang
				s.mu.Unlock()
				s.log.Info("call language set", "language", lang)
			}
			reply(c, map[string]any{"recorded": true,
				"note": "Noted silently. Say nothing about the language itself — just carry on speaking in it."})

		case "send_demo_link":
			// Recorded here and dispatched after hangup (see Session.Finish). Doing the
			// network work now would stall the brain read loop mid-conversation, and the
			// prospect would hear the agent freeze at the most important moment of the
			// call. The model gets an instant success so it can keep talking.
			channel, _ := c.Args["channel"].(string)
			if channel != "email" {
				channel = "whatsapp"
			}
			email, _ := c.Args["email"].(string)
			email = strings.TrimSpace(email)

			// Consent is decided in one place, shared with the probe harness, so a
			// rehearsal can never be more permissive than the call that gets dialled.
			if refusal, ok := s.checkLinkConsent(); !ok {
				reply(c, refusal)
				continue
			}

			// The email branch is the fallback, not the norm: this campaign delivers on
			// WhatsApp. A placeholder address is worse than a blank one — it passes an
			// emptiness check and records the lead as delivered.
			if channel == "email" && !looksLikeEmail(email) {
				s.log.Warn("send_demo_link asked for email but had no usable address", "got", email)
				reply(c, map[string]any{"sent": false, "error": "no_email",
					"note": "Nothing was sent — that is not an email address. Do NOT call this again until they have actually said one out loud. Ask for it in ONE short line, then STOP and WAIT."})
				continue
			}

			s.mu.Lock()
			s.linkChannel = channel
			// Only a real address goes in this field. The probe caught the model
			// calling send_demo_link(email="whatsapp") — it had nothing to put there
			// and filled the slot with the channel name, which would have been
			// written to results.csv as the lead's email address.
			if looksLikeEmail(email) {
				s.attendeeEmail = email
			} else if email != "" {
				s.log.Warn("send_demo_link passed a non-address in the email field — ignored",
					"got", email, "channel", channel)
			}
			if s.disposition == "" {
				s.disposition = "link_sent"
			}
			s.mu.Unlock()

			s.log.Info("demo link promised", "channel", channel, "email", email, "demo", s.prospect.DemoURL)

			// The link is QUEUED, not sent — dispatch happens after hangup, so it is
			// still 20+ seconds away even when everything downstream is healthy. Say
			// so. "भेज दिया" is a claim the prospect can check while still on the
			// phone, and finding nothing there is how a warm lead decides this was a
			// scam call. Tool responses are prompt: the model says what this says.
			// This note used to be a four-step script — say it's coming, then ask
			// their name, then ask when to talk again, then call set_followup, then
			// end_call. The model ran the whole thing in ONE turn: four tool calls
			// in 4.1 seconds, inventing the prospect's answer to every question it
			// asked. So it names exactly ONE thing to say and one thing to do next,
			// and the next thing is to be quiet. The step after that is still named —
			// a note that ends a turn without naming what follows is how the agent
			// came to hang up right after asking someone's name — but it is
			// explicitly placed in a LATER turn.
			note := linkQueuedNote(channel)

			reply(c, map[string]any{"queued": true, "channel": channel, "note": note})

		case "set_followup":
			s.applyFollowup(c.Args)
			reply(c, map[string]any{"recorded": true,
				"note": "Noted. Confirm the day/time back to them warmly in ONE short line, thank them, then call end_call. This is the goodbye — it is the only place two steps belong in one turn."})

		case "end_call":
			s.mu.Lock()
			if disp, ok := c.Args["disposition"].(string); ok && disp != "" {
				s.disposition = disp
			} else if s.disposition == "" {
				s.disposition = "incomplete"
			}
			if cb, ok := c.Args["callback"].(string); ok && cb != "" {
				s.callback = cb
			}
			disp := s.disposition
			s.mu.Unlock()
			if disp == "not_interested" || disp == "do_not_call" {
				s.refused.Store(true)
			}
			*endCallPending = true
			// The goodbye is the last thing on this call. Any silence after it is
			// the line winding down, not the agent thinking, and an acknowledgement
			// there lands as "uh..." tacked onto "thanks!" — the agent sounding like
			// it wants to keep talking as it hangs up.
			s.endingCall.Store(true)
			reply(c, map[string]any{"output": "ok"})

		default:
			s.log.Warn("unknown tool call", "name", c.Name)
			reply(c, map[string]any{"output": "ok"})
		}
	}

	if len(responses) > 0 {
		if err := s.brain.SendToolResponse(responses); err != nil {
			s.log.Warn("tool response failed", "error", err)
		}
	}
}

// ---------------------------------------------------------------------------
// Google Meet booking
// ---------------------------------------------------------------------------

// parseMeetingTime recovers the wall-clock time the model intended and anchors it
// in the meeting timezone. The model is unreliable about the UTC offset (it often
// sends +00:00 for what it means as a local time), so we deliberately IGNORE the
// offset: "5pm" always becomes 5pm in tz, never noon. Google Calendar then shows
// it correctly in everyone's own zone (Central for the prospect, IST for you).
func parseMeetingTime(v, tz string) (time.Time, error) {
	v = strings.TrimSpace(v)
	if v == "" {
		return time.Time{}, fmt.Errorf("empty start_time")
	}
	loc, err := time.LoadLocation(tz)
	if err != nil {
		loc = time.UTC
	}

	var t time.Time
	parsed := false
	if tt, e := time.Parse(time.RFC3339, v); e == nil {
		t, parsed = tt, true // t.Hour() etc. give the written wall-clock, whatever the offset
	} else {
		for _, layout := range []string{"2006-01-02T15:04:05", "2006-01-02T15:04", "2006-01-02 15:04"} {
			if tt, e := time.ParseInLocation(layout, v, loc); e == nil {
				t, parsed = tt, true
				break
			}
		}
	}
	if !parsed {
		return time.Time{}, fmt.Errorf("unrecognized time %q", v)
	}

	// Re-anchor the wall-clock components into the meeting timezone.
	return time.Date(t.Year(), t.Month(), t.Day(), t.Hour(), t.Minute(), t.Second(), 0, loc), nil
}

func (s *Session) sendToolResult(callID, name string, resp map[string]any) {
	if s.ctx.Err() != nil {
		return
	}
	if err := s.brain.SendToolResponse([]BrainToolResult{{ID: callID, Name: name, Response: sealNote(resp)}}); err != nil && s.ctx.Err() == nil {
		s.log.Warn("tool response failed", "name", name, "error", err)
	}
}

// ---------------------------------------------------------------------------
// Telnyx write helpers
// ---------------------------------------------------------------------------

func (s *Session) sendTelnyxMedia(payload string) {
	msg := struct {
		Event string `json:"event"`
		Media struct {
			Payload string `json:"payload"`
		} `json:"media"`
	}{Event: "media"}
	msg.Media.Payload = payload

	s.telnyxMu.Lock()
	err := s.telnyxWS.WriteJSON(msg)
	s.telnyxMu.Unlock()
	if err != nil && s.ctx.Err() == nil {
		s.log.Error("telnyx send failed", "error", err)
		s.cancel()
	}
}

// sendTelnyxAudio sends one chunk of μ-law audio to the caller and advances the
// playback-end estimate (μ-law 8kHz → 1 byte = 125µs of audio), so the silence
// supervisor knows when the caller will actually finish hearing the agent.
// sendTelnyxAudio queues one chunk for the caller and advances the playout
// estimate. It reports whether anything was actually sent: a chunk can decode to
// zero μ-law bytes (downsampleEncode8k returns nil below three input samples),
// and a caller that assumed otherwise would read a playbackEndNano still
// describing the PREVIOUS utterance — which is how the underrun probe came to
// report a 7.5-second gap nobody heard.
func (s *Session) sendTelnyxAudio(mulaw []byte) bool {
	if len(mulaw) == 0 {
		return false
	}
	dur := int64(len(mulaw)) * 125000 // ns per μ-law byte at 8kHz
	now := time.Now().UnixNano()
	end := s.playbackEndNano.Load()
	if end < now {
		end = now
	}
	s.playbackEndNano.Store(end + dur)
	s.sendTelnyxMedia(base64.StdEncoding.EncodeToString(mulaw))
	return true
}

// Answering-machine detection verdicts, as this session sees them. "Unsure" is
// deliberately NOT "machine": Telnyx returns not_sure/silence when it ran out of
// analysis time, and treating that as a machine would hang up on real people.
const (
	amdPending int32 = iota
	amdHuman
	amdMachine
	amdUnsure
)

// How the listen-first opener decides someone is really there. Every one of
// these was set from the first live campaign's own recordings, measured frame by
// frame off the prospect channel — the numbers in the comments are from those
// six answered calls, not from intuition.
var (
	// hardTimeout: speak even though we have heard nothing at all. This was 3s,
	// and on a live call that is exactly what a silent answerer got — 2.8 seconds
	// of nothing, then a voice, then they hung up. People who pick up without
	// speaking are common and they are not being rude; they are waiting for the
	// caller to identify themselves, and silence tells them it is an autodialer.
	//
	// It can be this short because the media socket is open before the phone
	// rings (STREAM_BEFORE_DIAL), so there is nothing left to wait for.
	openerHardTimeout = 1500 * time.Millisecond

	// minListen gives AMD a beat before a speech-only trigger is trusted.
	openerMinListen = 800 * time.Millisecond

	// settleQuiet: how long the line must stay quiet after they stop for their
	// utterance to count as finished and handed back to us.
	openerSettleQuiet = 500 * time.Millisecond

	// minUtterance: how much they must actually have said for it to be a GREETING
	// rather than a fragment. Raised from 250ms after two voicemails walked
	// straight through the old floor: their greetings opened with a 280ms and a
	// 60ms burst, then paused for ~1s — the exact shape of a person saying
	// "हैलो?" and waiting. The human on the same run spoke for 1,320ms before
	// pausing. 600ms sits in that gap.
	//
	// A person who answers with a curt "हाँ?" falls short of it and is picked up
	// by openerFragmentQuiet instead.
	openerMinUtterance = 600 * time.Millisecond

	// fragmentQuiet: how long the line must stay quiet after a sound too SHORT to
	// be a greeting before the agent speaks anyway.
	//
	// This number is the whole trade, and it was set from a validation call that
	// showed the old one costing 1.6 seconds of dead air. Measured off the
	// prospect channel, a person's first sound and a voicemail's first sound are
	// the same signal — 280ms on the validation call, 280ms and 60ms on the two
	// recordings that fooled the earlier logic. Nothing distinguishes them. What
	// distinguishes them is the SILENCE AFTER:
	//
	//	human    280ms, then quiet for 8,440ms — waiting for the caller
	//	machine  280ms, then quiet for   940ms — a breath, then the rest of it
	//	machine   60ms, then quiet for 1,020ms — likewise
	//
	// So this sits just past a recorded greeting's breath and not one millisecond
	// further, because everything past it is silence on a stranger's phone. The
	// errors are not symmetric: speaking too early to a machine costs a couple of
	// seconds of Telnyx time and AMD hangs up anyway, while waiting too long on a
	// person loses the lead — one hung up at 2.8 seconds of exactly this silence.
	//
	// If a machine does slip through, preOpenerUtterances still catches it the
	// moment the greeting resumes, and the opener is held for AMD from there.
	openerFragmentQuiet = 1100 * time.Millisecond

	// repeatGreetingAfter / repeatGreetingMaxSpeech: the signature of a PERSON who
	// cannot hear anything — "हैलो? ... हैलो? ... हैलो?" — several SHORT bursts
	// with real gaps between them.
	//
	// This is the bug that produced a silent hangup on a live human. He picked up,
	// said hello, and kept saying it into the dead air. Each "हैलो?" was ~380ms,
	// which is under openerMinUtterance, so rule (1) never fired; he kept talking,
	// so `quiet` never reached openerFragmentQuiet and rule (2b) never fired
	// either. The call fell through to "machine-shaped — wait for AMD", and AMD,
	// listening to a voice saying "hello?" over and over with pauses, called it a
	// recorded greeting. The agent hung up without ever making a sound.
	//
	// The loop is self-inflicted: the agent's own silence is what makes a person
	// say hello repeatedly, and saying hello repeatedly is what gets them
	// classified as a machine.
	//
	// What separates the two is SHAPE, not loudness. A recorded greeting is one
	// continuous run of several seconds — by 2s it has always delivered a long
	// stretch of speech. A person checking whether the line is alive produces a
	// few hundred milliseconds, total, spread over several bursts. So: two or more
	// utterances, 2s in, and still under 1.2s of speech between them, is a human
	// waiting for someone to talk to him.
	openerRepeatGreetingAfter     = 2000 * time.Millisecond
	openerRepeatGreetingMaxSpeech = 1200 * time.Millisecond

	// preOpenerUtteranceGap: quiet longer than this ends one utterance and starts
	// the next. Well above the 60–80ms micro-gaps inside ordinary speech (the
	// human on this run had gaps of ≤60ms inside a 1.3s greeting) and well below
	// the ~1s a recorded greeting pauses for breath.
	preOpenerUtteranceGap = 300 * time.Millisecond

	// openerHoldQuick / openerHoldEvidence bound how long the opener may be held
	// back waiting for AMD once the far end has stopped looking like a person.
	//
	// Two different numbers because silence is expensive and speech is free. If
	// the line is quiet we are the only thing that can end it, so the wait is
	// short. If the far end is still talking — or has already given itself away
	// by starting a second utterance — waiting costs nothing, since speaking would
	// only mean talking over it, and AMD's verdicts on this route landed as late
	// as 6.5s.
	openerHoldQuick    = 3500 * time.Millisecond
	openerHoldEvidence = 7000 * time.Millisecond
)

// awaitOpenerThenPlay is the listen-first opener. It keeps the agent silent until
// it is confident a HUMAN is on the line, then delivers the opening hook. It
// NEVER speaks once a voicemail is flagged — that path is hung up elsewhere.
//
// The hard part is that on the first two seconds of audio a recorded greeting and
// a person saying hello are the same signal. Measured off the campaign's own
// recordings:
//
//	#4  human    spoke 1,320ms straight, then went quiet for 6.6s      → a greeting
//	#6  machine  spoke   280ms, paused 940ms, carried on               → looked identical
//	#7  machine  spoke    60ms, paused 1,020ms, carried on             → looked identical
//	#3  machine  spoke continuously, longest internal gap 80ms         → never settled
//	#5  human    silent for 3.1s, then spoke                           → nothing to go on
//
// So the opener fires on positive evidence of a PERSON and nothing else:
//
//  1. AMD says human.
//  2. One whole utterance has landed and the line has gone quiet — a person
//     handing the turn back.
//  3. Nothing has been said at all and the line is quiet — someone who answered
//     without speaking.
//
// Anything else — a fragment, a second utterance, speech still running — is
// machine-shaped, and the opener is held for AMD up to a bounded deadline. On the
// six calls above that holds all three machines past their verdict and delays no
// human by a single tick.
func (s *Session) awaitOpenerThenPlay(fillerReady bool) {
	// With dial-time streaming this goroutine starts while the phone is still
	// ringing, so it must wait for the pickup before any of the timers above mean
	// anything. Without this the hard timeout fires at second 3 of the RINGING and
	// the opener is spoken to nobody.
	start := s.awaitAnswered()
	if start.IsZero() {
		return // the call ended before anyone picked up
	}

	held := false // logged once, so the run can be read for how often a hold happened
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-s.ctx.Done():
			return
		case <-ticker.C:
		}

		if s.voicemail.Load() {
			return // machine — the call is being hung up; never speak
		}

		now := time.Now()
		elapsed := now.Sub(start)

		spoke := time.Duration(s.speechMillis.Load()) * time.Millisecond
		// Never having spoken is the maximum quiet, not zero: a call where nobody
		// has made a sound is the most silent case there is, and reading it as
		// "they just spoke" would hold the opener forever.
		quiet := elapsed
		if last := s.lastSpeechNano.Load(); last > 0 {
			quiet = time.Duration(now.UnixNano() - last)
		}

		state := openerState{
			elapsed:       elapsed,
			spoke:         spoke,
			quiet:         quiet,
			utterances:    s.preOpenerUtterances.Load(),
			longest:       time.Duration(s.longestUtteranceMs.Load()) * time.Millisecond,
			amd:           s.amdVerdict.Load(),
			humanEvidence: s.humanEvidence.Load(),
		}
		reason, decided := openerDecide(state)
		if !decided {
			// Everything before openerHardTimeout is the ordinary listening window
			// that every call passes through, so it is not a hold and must not be
			// logged as one. Past it, the agent would have spoken by now and is
			// deliberately not — that IS worth a line, exactly once.
			if !held && elapsed >= openerHardTimeout {
				held = true
				s.log.Info("holding the opener — this does not look like someone answering",
					"elapsed_ms", elapsed.Milliseconds(), "spoke_ms", spoke.Milliseconds(),
					"utterances", state.utterances, "quiet_ms", quiet.Milliseconds(),
					"waiting_for", "amd", "deadline_ms", openerHoldDeadline(state).Milliseconds())
			}
			continue
		}
		if reason == "" {
			return // AMD says machine — the webhook is hanging this call up; say nothing
		}
		s.openerDecision(fillerReady, reason, elapsed)
		return
	}
}

// openerState is everything the opener decision is allowed to look at. Pulling
// it out of the Session is what lets the six calls this logic was built from be
// replayed as tests instead of argued about.
type openerState struct {
	elapsed       time.Duration // since the call was answered
	spoke         time.Duration // cumulative prospect speech since the answer
	quiet         time.Duration // since their last speech frame (== elapsed if never)
	utterances    int64         // separate things the far end has said
	longest       time.Duration // the longest single one of them
	amd           int32
	humanEvidence bool // the shape says a person, whatever AMD says
}

// looksHuman reports whether the far end sounds like a PERSON rather than a
// recorded greeting — someone who cannot hear anything and is checking the line
// is alive ("हैलो? ... हैलो?"), or simply someone talking while we stay silent.
//
// It is consulted at exactly one moment: when Telnyx AMD comes back "machine".
// It is deliberately NOT part of the pre-AMD gate, because at two seconds the
// two are not separable and trying anyway pitched all three of the real
// voicemails in opener_test.go. By the time AMD rules (3.0-6.5s on this route)
// there is enough audio.
//
// Measured on the four recordings and the live call this went wrong on:
//
//	JP Studio    machine  utt=1  spoke=4460  longest=4460  mean=4460
//	Neeraj Kamra machine  utt=4  spoke=1360  longest= 840  mean= 340
//	MAA Anim.    machine  utt=5  spoke= 860  longest= 380  mean= 172
//	meera1       HUMAN    utt=3  spoke=1140  longest= 380  mean= 380
//	wa_human_test2 HUMAN  utt>=2 spoke=2400  (picked up, said hello, hung up on)
//
// `longest` alone throws out JP Studio and Neeraj but NOT MAA, which collides
// with the human exactly. What separates those two is the MEAN: a recording
// chopped into fragments averages 172ms per burst, while "हैलो?" is a whole
// word and averages 380ms. So both bounds are load-bearing.
//
// The last row is why this was rewritten. The original rule required speech to
// stay UNDER 1.5s to count as human, so a real prospect who picked up and said
// "hello" for 2.4 seconds fell outside every bound and was hung up on — the more
// he talked, the more certain the agent was that he was a machine. Sustained
// speech is now positive evidence of a person, not evidence against one.
//
// This is one human sample against three machines, and it should be re-derived
// as more calls come in. It is shipped anyway because the errors are wildly
// asymmetric: vetoing a real voicemail costs some Telnyx seconds and the
// transcript voicemail-cue detector still catches it from the words, while
// obeying AMD on a real person hangs up on a live lead who has been saying
// hello into silence — which is exactly what happened.
func (s *Session) looksHuman() bool {
	utt := s.preOpenerUtterances.Load()
	if utt == 0 {
		return false // answered in silence — there is no person here to protect
	}
	spoke := s.speechMillis.Load()
	longest := s.longestUtteranceMs.Load()

	// One unbroken run of several seconds is a recording reading itself out.
	// JP Studio's was 4.46s. Nobody answering their own phone says a single
	// uninterrupted thing that long before letting the caller speak — and a
	// greeting followed by a beep lands here too, which is the shape most
	// carrier voicemail takes.
	if longest >= int64(openerMonologue/time.Millisecond) {
		return false
	}

	// (a) Plenty of speech, broken into pieces: a person talking into dead air.
	//     "Hello? ... Hello, kaun hai? ... Haan boliye?"
	//
	//     This is the clause the old rule was missing, and its absence is what
	//     hung up on a live prospect who had spoken for 2.4 seconds. The old
	//     bound was an upper one — speech had to stay UNDER 1.5s to count as
	//     human — so the more a real person said, the more certain the agent
	//     became that it was talking to a machine. That is backwards: sustained,
	//     broken-up speech is the strongest evidence of a person there is.
	if spoke >= int64(openerHumanSpeechFloor/time.Millisecond) {
		return true
	}

	// (b) The quiet case this was originally built for: two or more short,
	//     whole-word bursts with silence between them. The mean is what separates
	//     "हैलो?" (380ms a burst) from a recording chopped into 172ms fragments.
	if utt >= 2 &&
		longest < int64(openerMinUtterance/time.Millisecond) &&
		spoke < int64(openerRepeatGreetingMaxSpeech/time.Millisecond)+300 &&
		spoke/utt >= openerHelloMeanMinMs {
		return true
	}

	// (c) One short sound, and then a long wait. A recording does not say a
	//     single 400ms thing and then stop — by the time AMD rules, three to six
	//     seconds in, a greeting has been talking continuously for most of it.
	//     Someone who says "हैलो?" and then holds the phone to their ear waiting
	//     for an answer produces exactly this and nothing more.
	//
	//     This is the shape fragment_settled already releases the opener on at
	//     ~1.5s. Without it here, AMD arriving at 4.4s would hang the call up
	//     again a few seconds later — mid-sentence, with the agent already
	//     talking to them.
	if spoke > 0 && spoke < int64(openerMinUtterance/time.Millisecond) {
		if last := s.lastSpeechNano.Load(); last > 0 &&
			time.Since(time.Unix(0, last)) >= openerFragmentQuiet {
			return true
		}
	}

	return false
}

// amdMachineHangup decides what a Telnyx "machine" verdict actually DOES.
//
// true  — hang up immediately (cheapest: a real voicemail costs ~4s of Telnyx)
// false — never hang up on AMD alone; release the opener and let the call run,
//
//	leaving the transcript voicemail cues and the model's own
//	end_call(voicemail) to catch a recording from the WORDS instead
//
// It exists because AMD is a heuristic on a route it was not tuned for, and when
// it is wrong the failure is silent and total: the prospect answers, hears
// nothing at all, and the line drops after four seconds. There is no way to tell
// that apart from a broken agent from the prospect's end, and no way to debug it
// from the run log without letting the call actually proceed.
//
// Set AMD_MACHINE_HANGUP=false to trade Telnyx seconds for never doing that to a
// live person.
var amdMachineHangup = true

// openerHelloMeanMinMs is the floor on the average burst length, and it is the
// only thing separating a person from the MAA Animation recording.
const openerHelloMeanMinMs = 300

const (
	// openerMonologue: one unbroken utterance at least this long is a recording.
	// JP Studio's greeting ran 4.46s; the longest single burst any of the human
	// samples produced was 380ms. The gap between those is wide enough that this
	// bound does not need to be precise.
	//
	// The residual risk is a person who answers with one continuous sentence of
	// 2s or more. They are rare — people say something short and stop when they
	// pick up — and they are usually released by speech_settled long before AMD
	// rules at 3-6.5s.
	openerMonologue = 2000 * time.Millisecond

	// openerHumanSpeechFloor: this much speech, in more than one burst and none
	// of them a monologue, is a person. Set above the loudest machine sample
	// (Neeraj Kamra, 1360ms) and below the live human this was derived from
	// (2400ms). Both numbers come from real calls; see the table above.
	openerHumanSpeechFloor = 1800 * time.Millisecond
)

// openerDecide reports the rule that releases the opening line, if any. An empty
// reason with decided=true means "stop, and never speak" — AMD called it a
// machine. decided=false means keep waiting.
func openerDecide(st openerState) (reason string, decided bool) {
	// The veto is checked BEFORE the machine verdict on purpose: it exists
	// precisely for the calls AMD gets wrong. See humanEvidence.
	if st.humanEvidence {
		return "amd_overridden_human", true
	}
	switch st.amd {
	case amdHuman:
		return "amd_human", true
	case amdMachine:
		return "", true
	}

	// (1) A person answered, said one whole thing, and stopped. This is the good
	// case: they have handed the turn back and are waiting for us.
	if st.utterances <= 1 && st.elapsed >= openerMinListen &&
		st.spoke >= openerMinUtterance && st.quiet >= openerSettleQuiet {
		return "speech_settled", true
	}

	// (2) They answered and said nothing at all. Common on Indian mobiles, and the
	// most expensive case to get wrong — every millisecond here is dead air on a
	// stranger's phone, and one person hung up on 2.8 seconds of it.
	if st.utterances == 0 && st.elapsed >= openerHardTimeout {
		return "timeout", true
	}

	// (2b) One sound too short to be a greeting — a curt "हाँ?", a click, a
	// rustle — and the line has been quiet since. See openerFragmentQuiet: this
	// is indistinguishable from a recorded greeting's opening syllable, and the
	// only thing that tells them apart is how long the silence after it lasts.
	if st.utterances == 1 && st.elapsed >= openerMinListen && st.quiet >= openerFragmentQuiet {
		return "fragment_settled", true
	}

	// (3) Machine-shaped: a fragment that stopped short of a greeting, a second
	// utterance, or speech that is still running. Wait for AMD — but Telnyx
	// giving up ("not_sure" / "silence") is not a machine, it is Telnyx having
	// nothing to say, so it releases the hold rather than extending it.
	if st.amd == amdUnsure {
		return "amd_inconclusive", true
	}
	if st.elapsed >= openerHoldDeadline(st) {
		return "amd_hold_expired", true
	}
	return "", false
}

// openerHoldDeadline bounds how long the opener may be held waiting for AMD.
//
// Two values, because silence is expensive and speech is free. If the line has
// gone quiet we are the only thing that can end it, so the wait is short. If the
// far end is still talking — or has already given itself away by starting a
// second utterance — waiting costs nothing, because speaking would only mean
// talking over it, and AMD's verdicts on this route landed as late as 6.5s.
func openerHoldDeadline(st openerState) time.Duration {
	if st.utterances >= 2 || st.quiet < openerSettleQuiet {
		return openerHoldEvidence
	}
	return openerHoldQuick
}

// openerDecision records WHY the opener was released and how long after pickup,
// then releases it. The reason is the single most useful field in the run log:
// "timeout" on a call AMD later called a machine is the agent pitching a
// recording, and it is invisible from the disposition column alone.
func (s *Session) openerDecision(fillerReady bool, reason string, after time.Duration) {
	s.openerReason.Store(reason)
	s.openerAtNano.Store(time.Now().UnixNano())
	s.log.Info("opener released", "reason", reason, "after_ms", after.Milliseconds(),
		"spoke_ms", s.speechMillis.Load(), "utterances", s.preOpenerUtterances.Load(),
		"amd", s.amdRawString())
	s.maybePlayOpener(fillerReady, reason)
}

// amdRawString reports what Telnyx actually said, or "pending" if it has not
// ruled yet. Kept as the raw string because "human_residence" and "human_business"
// are different facts about who picked up, and both collapse to "human" in the
// verdict enum.
func (s *Session) amdRawString() string {
	if v, ok := s.amdRaw.Load().(string); ok && v != "" {
		return v
	}
	return "pending"
}

// maybePlayOpener delivers the opening exactly once, unless a voicemail was flagged
// first. It plays the short connection-check filler (if pre-generated) to mask
// latency, opens the Gemini gate, and then triggers the LIVE model to deliver its
// real, adaptive hook. The trigger is a user-role text turn (SendText) — the valid
// way to prompt the model. We must NOT inject a model-role clientContent turn: the
// Gemini Live API rejects role:"model"/turnComplete:false with a 1007 "invalid
// argument" close that tears the whole session down (the canary dead-air bug).
func (s *Session) maybePlayOpener(fillerReady bool, reason string) {
	s.openerOnce.Do(func() {
		if s.voicemail.Load() || s.ctx.Err() != nil {
			return
		}
		s.log.Debug("delivering opener", "reason", reason, "filler", fillerReady, "personalised", s.openerPersonal)
		if fillerReady {
			// Hold the model's audio so it cannot play on top of the opener...
			s.fillerPlaying.Store(true)

			// ...but open the mic gate NOW, so the prospect's reply reaches the model
			// WHILE the opener is still being spoken. They almost always answer the
			// opener's closing question ("क्या मैं X से बात कर रहा हूँ?") before it has
			// finished playing, so by the time playback ends the model has already
			// heard them and generated its reply — the latency is hidden without the
			// model ever having to guess.
			//
			// It is deliberately NOT given a text cue to start talking. An earlier
			// version did exactly that, triggering it before the gate opened: with no
			// prospect audio to respond to, the model invented their answer ("हाँ,
			// शर्मा स्टूडियो है, बोलिए") and then replied to it. On a live call that is
			// two people talking to each other. The model must hear a real human
			// before it says anything.
			s.openBrainGate()

			s.playFiller() // pre-synthesised audio — the opener the model continues from

			// flushHeldAudio always runs, including on a cancelled call, because it
			// is what clears fillerPlaying — leaving it set would silently swallow
			// every remaining chunk of the call.
			s.flushHeldAudio()
			go s.openerFollowThrough()
			return
		}
		// No pre-generated audio: open the gate and let the live model open cold.
		// There's a little dead air before its first token, but it still adapts.
		s.openBrainGate()
		if err := s.brain.SendText("[The prospect just answered. Read who picked up and open per your OPENING instructions — Stage 1 to a person, the gatekeeper approach for staff, or end_call(voicemail) for a recording. Do not read this note aloud.]"); err != nil {
			s.log.Error("live opener trigger failed", "error", err)
		}
	})
}

// onAMD* are called from the webhook when answering-machine detection resolves.
// Human → release the opener. Machine → suppress it (the webhook hangs the call
// up and logs a voicemail). Inconclusive → Telnyx has nothing for us, which
// RELEASES any hold rather than extending it: "not_sure" and "silence" mean the
// analysis ran out, not that a machine answered, and on this campaign the two
// silent answerers who were held longest were both real people.
func (s *Session) onAMDHuman(raw string) { s.noteAMD(amdHuman, raw) }

func (s *Session) onAMDInconclusive(raw string) { s.noteAMD(amdUnsure, raw) }

func (s *Session) onAMDMachine() {
	s.noteAMD(amdMachine, "machine")
	s.voicemail.Store(true)
	s.mu.Lock()
	if s.disposition == "" {
		s.disposition = "voicemail"
	}
	s.mu.Unlock()
}

// noteAMD records the verdict and how long it took from pickup. The latency is
// the number the opener's hold deadlines are set from — on the first live
// campaign it ranged 3.0s to 6.5s — so every run must keep measuring it rather
// than trusting a constant that was true once.
func (s *Session) noteAMD(verdict int32, raw string) {
	if raw != "" {
		s.amdRaw.Store(raw)
	}
	if !s.amdVerdict.CompareAndSwap(amdPending, verdict) {
		return // already ruled; Telnyx can redeliver a webhook
	}
	now := time.Now().UnixNano()
	s.amdAtNano.Store(now)
	if ans := s.answeredNano.Load(); ans > 0 {
		// The speech shape goes on this line too. Without it a wrong verdict is
		// undiagnosable after the fact: the run log said only "machine", and
		// working out WHY a live person was hung up on meant having the call
		// happen again with a debug build attached.
		utt := s.preOpenerUtterances.Load()
		var mean int64
		if utt > 0 {
			mean = s.speechMillis.Load() / utt
		}
		s.log.Info("amd verdict", "result", raw, "after_ms", (now-ans)/1e6,
			"opener_already_out", s.openerPlayed.Load(),
			"utterances", utt, "spoke_ms", s.speechMillis.Load(),
			"longest_ms", s.longestUtteranceMs.Load(), "mean_ms", mean,
			"looks_human", s.looksHuman())
	}
}

// voicemailCues are phrases that can ONLY be a recording.
//
// The bar for inclusion is deliberately brutal, because the two failure modes
// are not symmetric. Missing a machine costs forty seconds of pitching a
// recording. Matching a LIVE prospect hangs up on a real lead mid-sentence — and
// the prospect-side ASR feeding this is not perfect, so a cue that is merely
// probable will eventually match something a person said.
//
// So everything here is a full operator sentence stem that no human utters in
// the first seconds of answering their own phone. Deliberately NOT here, and
// each for a specific reason:
//
//	"voicemail", "mailbox"      a person says "just leave me a voicemail"
//	"press one"                 a person says "press one for what?"
//	"not available"             "I'm not available right now, call me back" is a
//	                            CALLBACK — the best non-yes outcome on the list
//	"please try again later"    same
//	"the person you are trying" too close to normal speech under bad ASR
//
// Telnyx premium AMD is the real machine detector. This is only a backstop for
// the case where AMD runs out of analysis time, and it is gated further in
// detectVoicemailFromTranscript: within 8s of the gate opening, no name
// captured, no barge-in yet, and ≥1.5s of continuous prospect speech.
var voicemailCues = []string{
	"leave a message after", "leave your message after", "leave a message at",
	"record your message", "message at the tone", "after the tone",
	"after the beep", "at the sound of the tone",
	"you have reached the voicemail", "the voicemail service",
	"is not able to take your call", "has a voice mailbox that has not been set up",
	"your call has been forwarded to an automated",
	"the subscriber you have dialed", "the number you have dialed has been",
	"is no longer in service", "has been disconnected",
}

// refusalCues are phrases that can only be a refusal of the OFFER. They are
// checked against the prospect's own transcript, independently of the model,
// because the model is the component that failed here: told "don't need
// anything, thank you", it said "sure, no problem" and pitched on anyway, then
// called the send tool without ever getting a yes.
//
// The list is deliberately narrow. A bare "no" is NOT here — the agent asks
// "do you have a website?" and the answer to that is often "no", which is the
// single best-qualified lead on the list. Only phrases that reject the thing
// being offered count, or the guard kills the campaign's best prospects.
var refusalCues = []string{
	"not interested", "no thanks", "no thank you", "not intrested",
	"don't need", "do not need", "dont need", "no need", "not required",
	"don't want", "do not want", "dont want",
	"remove my number", "remove me from", "take me off", "stop calling",
	"don't call", "do not call", "dont call", "never call",
	"not looking for", "we're all set", "we are all set",
	"quit calling", "lose my number",

	// The Hindi half. Without it this guard was dead on the only calls that
	// actually run: every cue above is English, and this campaign dials India in
	// Hindi. A prospect saying "मुझे नहीं चाहिए" was latching nothing, so the
	// code-level protection that stops a refused number being messaged existed
	// only for prospects who happened to refuse in English.
	"नहीं चाहिए", "नही चाहिए", "नहीं चाहिये", "मुझे नहीं चाहिए",
	"जरूरत नहीं", "ज़रूरत नहीं", "जरुरत नहीं", "कोई जरूरत नहीं", "कोई ज़रूरत नहीं",
	"नहीं करना", "नही करना", "नहीं लेना", "इंटरेस्टेड नहीं", "इंटरेस्ट नहीं",
	"रहने दो", "रहने दीजिए", "मत भेजो", "मत भेजिए", "मत करो", "मत कीजिए",
	"फोन मत", "कॉल मत", "call मत", "phone मत", "दोबारा मत",
	"नंबर हटा", "नाम हटा", "परेशान मत",
	// Latin-script Hindi. The transcriber emits this constantly on mixed-language
	// turns, so the Devanagari forms above are only half the coverage.
	"nahi chahiye", "nahin chahiye", "nai chahiye", "mujhe nahi chahiye",
	"zarurat nahi", "zaroorat nahi", "jarurat nahi", "koi zarurat nahi",
	"rehne do", "rehne dijiye", "mat bhejo", "mat bhejiye", "mat karo",
	"phone mat", "call mat", "dobara mat", "number hata", "pareshan mat",
	"interested nahi", "intrested nahi",

	// Urdu script. Not hypothetical and not a different language — the Hindi
	// transcriber emits it. On a live call the prospect's "नहीं, मुझे नहीं
	// चाहिए" came back as "نہیں، مجھے نہیں چاہیے", which matched nothing here.
	// The link was suppressed only because the model happened to call end_call;
	// the code-level guard, which exists precisely so that outcome does not
	// depend on the model, saw an ordinary sentence.
	"نہیں چاہیے", "مجھے نہیں چاہیے", "ضرورت نہیں", "کوئی ضرورت نہیں",
	"نہیں چاہئے", "رہنے دو", "مت بھیجو", "فون مت", "کال مت", "دوبارہ مت",
}

// doNotCallCues are the subset that is not merely a no but an instruction. They
// are latched separately because they mean the number goes on the suppression
// list permanently, not just that this call is over.
var doNotCallCues = []string{
	"remove my number", "remove me from", "take me off", "stop calling",
	"don't call", "do not call", "dont call", "never call",
	"quit calling", "lose my number",
}

// looksLikeEmail is a deliberately loose sanity check, not a validator. Its job
// is only to reject what the MODEL produces when it wants an address it has not
// been given — placeholders, prose, parentheses, empty strings. A real address
// the prospect actually said will pass; whether it delivers is the mail server's
// problem, not this function's.
func looksLikeEmail(s string) bool {
	s = strings.TrimSpace(s)
	if len(s) < 6 || len(s) > 254 || strings.ContainsAny(s, " ()<>,;\"\\") {
		return false
	}
	at := strings.LastIndex(s, "@")
	if at < 1 || at == len(s)-1 {
		return false
	}
	domain := s[at+1:]
	dot := strings.LastIndex(domain, ".")
	return dot > 0 && dot < len(domain)-1
}

// noteRefusal scans one prospect utterance for an unambiguous refusal and
// latches it. Once latched, send_demo_link is refused in code.
func (s *Session) noteRefusal(text string) {
	if s.refused.Load() || text == "" {
		return
	}
	lower := strings.ToLower(text)
	for _, cue := range doNotCallCues {
		if strings.Contains(lower, cue) {
			s.refused.Store(true)
			s.doNotCall.Store(true)
			s.log.Info("prospect asked to be removed — suppressing this number permanently",
				"cue", cue, "said", text)
			return
		}
	}
	for _, cue := range refusalCues {
		if strings.Contains(lower, cue) {
			s.refused.Store(true)
			s.log.Info("prospect declined — nothing may be sent on this call", "cue", cue, "said", text)
			return
		}
	}
}

// linkQueuedNote is what the model is told after the link is queued.
//
// It contains NO example sentence, and that is the whole point. It used to end
// with the exact line to say — "अभी भेज रही हूँ सर, एक-दो मिनट में आ जाएगा" —
// and on a live call the model said its own version, called this tool, and then
// recited that quoted line word for word straight after it, so the prospect was
// told twice in one breath. A sentence handed to the model at the moment it
// acts gets spoken, conditionals above it notwithstanding. Same lesson as the
// stats list and the refusal guard: what it cannot see it cannot recite. The
// phrasing it needs is already in the playbook; this note only says whether to
// speak at all. It lives
// here, and not at the two call sites, because the probe harness answers this
// tool too — and a rehearsal that acknowledges it differently rehearses a
// different agent. That drift is not hypothetical: the probe went on telling the
// model to announce the send long after the live path had stopped, which is the
// very repetition this note now exists to prevent.
//
// The repetition it prevents: the model normally announces the send and THEN
// calls the tool. An unconditional "now say it's on its way" makes it say the
// same sentence twice in one breath, and a prospect on a live call heard exactly
// that.
func linkQueuedNote(channel string) string {
	if channel == "email" {
		return "Queued. You have almost certainly ALREADY told them it is coming — if so, say NOTHING MORE: stop, and wait for them to speak. Only if you have genuinely not mentioned it at all, say so in ONE short line, in the future or present-continuous tense, never the past. Do not read the address or the URL aloud. Then STOP TALKING and WAIT. Never say what they replied. AFTER they speak, your next job is their name; it does not happen now."
	}
	return "Queued, NOT yet delivered. You have almost certainly ALREADY told them it is on its way — announcing it and then calling this is the normal order — so SAY NOTHING MORE: stop, and wait for them to speak. Only if you have genuinely not mentioned it at all, say so in ONE short line, in the future or present-continuous tense, NEVER the past: nothing has arrived yet, and a prospect who checks their phone mid-call and finds nothing decides this was a scam. Do not read the URL aloud. Then STOP TALKING and WAIT. Never say what they replied. AFTER they speak, your next job is their name; it does not happen now."
}

// sealNote marks a tool result's "note" as a stage direction rather than
// something to say.
//
// The notes are written as prose instructions to the model — "Nothing was sent —
// you asked, but they have not answered you yet. Say NOTHING and WAIT." On a
// live call the model translated that one into Hindi and READ IT TO THE
// PROSPECT: "मैंने पूछा था, पर अभी आपका हाँ नहीं आया है — जब आप बोलेंगे, तब मैं
// भेज दूँगी।" It did the same with the next one, telling him the important thing
// was for him to say something else. From his side the agent had started
// narrating its own plumbing, and he stayed on the line for another ninety
// seconds of that before the call timed out.
//
// Square brackets are already established in the system prompt as the marker for
// system cues that are never read aloud (see CALL-CONTROL CUES). Putting the
// notes inside the same envelope means ONE rule covers every piece of text the
// model receives that is not the prospect talking.
func sealNote(resp map[string]any) map[string]any {
	note, ok := resp["note"].(string)
	if !ok || note == "" || strings.HasPrefix(note, "[") {
		return resp
	}
	sealed := make(map[string]any, len(resp))
	for k, v := range resp {
		sealed[k] = v
	}
	sealed["note"] = "[SYSTEM NOTE — a direction for you, NOT words to say. Never read it out, never translate it, never paraphrase it to the prospect: " + note + "]"
	return sealed
}

// checkLinkConsent decides whether the demo link may go out. It returns the
// response to hand back to the model and false when it may not.
//
// Three things have to be true, and each one is a failure seen on a real call:
//
//  1. They have not refused. A prospect said "don't need anything, thank you";
//     the agent acknowledged it, pitched anyway, and the run was logged as a
//     success. Messaging someone who said no is how a number gets reported.
//  2. The offer was actually MADE, out loud. A prospect said "yes" to "आपकी
//     अपनी कोई website नहीं दिखी, सही है ना सर?" and the agent treated it as
//     permission — he had agreed to a fact about his own business, and was sent
//     marketing on the strength of it.
//  3. They have HAD a turn since. The model has answered its own questions
//     before now, four tool calls in 4.1 seconds, to a man still listening.
//
// Whether a given reply is a yes stays the model's judgement. This only refuses
// to let it skip the question.
func (s *Session) checkLinkConsent() (map[string]any, bool) {
	if s.refused.Load() {
		s.log.Warn("send_demo_link REFUSED — the prospect already declined on this call")
		s.mu.Lock()
		if s.disposition == "" {
			s.disposition = "not_interested"
		}
		s.mu.Unlock()
		return map[string]any{"sent": false, "refused": true,
			"note": "They already said no on this call. Do NOT send anything and do NOT ask again. Thank them politely in one short line and call end_call with disposition not_interested."}, false
	}
	if !s.linkOffered.Load() {
		s.log.Warn("send_demo_link REFUSED — the offer was never made out loud")
		return map[string]any{"sent": false, "error": "never_offered",
			"note": "Nothing was sent — you have not offered it yet. Whatever they just agreed to, it was not this: agreeing that they have no website, or that you reached the right business, is not permission to send them anything. Do it properly now, in ONE short turn — tell them you have already built the demo site for their business, by name, then ask \"WhatsApp पे भेज दूँ?\" — then STOP and WAIT for their answer. Call this again only after you have heard it."}, false
	}
	if !s.offerAnswered.Load() {
		s.log.Warn("send_demo_link REFUSED — asked, but they have not answered yet")
		return map[string]any{"sent": false, "error": "no_answer_yet",
			"note": "Nothing was sent — you asked, but they have not answered you yet. Say NOTHING and WAIT for them to speak. Do not answer for them."}, false
	}
	return nil, true
}

// linkOfferCues are the ways the agent can actually ASK to send the demo link.
// Every one of them is a question about SENDING — none of them can be satisfied
// by a sentence about their website, their business, or the weather. That is the
// whole point: the latch has to be impossible to trip by accident, because what
// it guards is a marketing message to a stranger's phone.
var linkOfferCues = []string{
	// Hindi, Devanagari.
	"भेज दूँ", "भेज दूं", "भेजूँ", "भेजूं", "भेज दु", "भेज देती हूँ", "भेज देती हूं",
	"screenshot भेज", "स्क्रीनशॉट भेज",
	"भेज सकती हूँ", "भेज सकती हूं", "भेज दें", "share कर दूँ", "share कर दूं",
	// Hindi, Latin script — what the transcriber emits on mixed turns.
	"bhej doon", "bhej dun", "bhej du", "bhejun", "bhejoon", "bhej deti hoon",
	"bhej sakti hoon", "bhej de", "share kar doon",
	// English, after a language switch.
	"send it to you", "send you the link", "send you a link", "send it over",
	"send it across", "shall i send", "should i send", "can i send", "may i send",
	"would you like me to send", "want me to send", "do you want me to send",
	"is it okay if i send", "would you be opposed",
}

// noteLinkOffer latches when the agent has actually put the offer to them out
// loud. It reads what was really SPOKEN, not what the model intended — the audio
// is the only account of this call the prospect ever hears, and it is the only
// evidence that should be able to unlock sending them something.
func (s *Session) noteLinkOffer(text string) {
	if s.linkOffered.Load() || text == "" {
		return
	}
	lower := strings.ToLower(text)
	for _, cue := range linkOfferCues {
		if strings.Contains(lower, cue) {
			s.linkOffered.Store(true)
			s.log.Info("link offered — the prospect has now been asked", "cue", cue, "said", text)
			return
		}
	}
}

// noteTranscript accumulates a transcription fragment for one side of the call.
// A fragment from the OTHER speaker means the previous utterance is over, so it
// is flushed first — the turn changing hands is the only end-of-utterance signal
// the stream actually gives us.
//
// Called only from the brain read goroutine.
func (s *Session) noteTranscript(speaker, text string) {
	if text == "" {
		return
	}
	if speaker != s.utterSpeaker {
		s.flushTranscript()
		s.utterSpeaker = speaker
	}
	if speaker == "prospect" {
		// A second, independent record that a person spoke. The energy floor can
		// be wrong for a quiet caller on an odd route; transcription arriving is
		// separate evidence, and fabricatedToolCall accepts either.
		s.lastProspectTextNano.Store(time.Now().UnixNano())
	}
	// Clause boundaries carry the space. The aggregator hands out "So when people"
	// and then "search for a roofer," as two separate clauses, and the synthesiser
	// gets an explicit trailing space on each — but a raw concatenation here
	// produced "So when peoplesearch for a roofer," in the campaign transcript.
	// The audio was always correct; the RECORD of it was not, and the record is
	// what a call gets read back from.
	if s.utterance.Len() > 0 && !strings.HasSuffix(s.utterance.String(), " ") &&
		!strings.HasPrefix(text, " ") {
		s.utterance.WriteString(" ")
	}
	s.utterance.WriteString(text)
}

// flushTranscript emits whatever the current speaker has said so far as one
// log line and keeps it for the end-of-call replay. Logged at Info so a live
// campaign log is a readable conversation, not just telemetry.
func (s *Session) flushTranscript() {
	text := strings.TrimSpace(s.utterance.String())
	s.utterance.Reset()
	if text == "" {
		return
	}
	at := (time.Now().UnixNano() - s.startNano) / 1e9
	who := "AGENT"
	if s.utterSpeaker == "prospect" {
		who = "PROSPECT"
		// Checked on the whole utterance rather than per fragment — the cues are
		// multi-word and fragments arrive a syllable at a time.
		s.noteRefusal(text)
		// They have now had a turn in which to answer the offer. Whether this
		// utterance IS a yes is the model's judgement and stays the model's; the
		// latch only records that the question was put to them and they replied
		// to it, which is the part the model was inventing.
		if s.linkOffered.Load() && !s.offerAnswered.Swap(true) {
			s.log.Debug("prospect has answered the link offer", "said", text)
		}
	} else {
		s.noteLinkOffer(text)
	}
	s.log.Info("· "+who, "t", strconv.FormatInt(at, 10)+"s", "said", text)

	s.convMu.Lock()
	s.convo = append(s.convo, fmt.Sprintf("[%3ds] %-8s %s", at, who, text))
	s.convMu.Unlock()
}

// vmScanWindow is how long after the gate opens we scan inbound transcription for
// voicemail cues. A carrier announcement starts the instant the leg is answered,
// so anything arriving later is a conversation, not a recording.
const vmScanWindow = 8 * time.Second

// vmMinSpeechFrames is how much continuous prospect audio must have arrived
// before a cue is allowed to end the call: 75 frames = 1.5s. A recording talks
// for several seconds without pausing; a live prospect's opening "हाँ, बोलिए" is
// a fraction of that. Requiring bulk is what stops a garbled ASR fragment of a
// short human utterance from tripping a hangup.
const vmMinSpeechFrames = 75

// detectVoicemailFromTranscript accumulates the prospect-side transcript and, if a
// voicemail/IVR greeting cue appears within the early scan window, flags the call
// a voicemail and hangs up. Returns true if it ended the call. Called only from
// the single brain read goroutine, so transcriptBuf needs no lock.
func (s *Session) detectVoicemailFromTranscript(text string) bool {
	if s.voicemail.Load() {
		return false
	}
	open := s.gateOpenNano.Load()
	if open == 0 || time.Now().UnixNano()-open > int64(vmScanWindow) {
		return false // gate not open yet, or past the early window
	}
	s.mu.Lock()
	gotName := s.leadName != ""
	s.mu.Unlock()
	if gotName {
		return false // we already have a person's name — definitely not a machine
	}
	// A conversation is already underway: the agent has replied to something it
	// heard. Machines do not get replied to twice, and by this point every cue
	// below is far more likely to be mangled ASR than a recording.
	if s.resumePrompts.Load() > 0 || s.bargeIns.Load() > 0 {
		return false
	}
	// Require bulk. A recording talks continuously; a human's first word does not.
	if s.framesSpeech.Load() < vmMinSpeechFrames {
		return false
	}

	s.transcriptBuf.WriteString(strings.ToLower(text))
	s.transcriptBuf.WriteByte(' ')
	buf := s.transcriptBuf.String()
	for _, cue := range voicemailCues {
		if strings.Contains(buf, cue) {
			s.log.Info("voicemail greeting detected — hanging up", "cue", cue, "transcript", strings.TrimSpace(buf))
			s.onAMDMachine() // sets voicemail flag + disposition=voicemail
			go telnyxHangup(s.cfg, s.CallControlID)
			s.Finish("voicemail")
			return true
		}
	}
	return false
}

// openBrainGate marks the opener delivered and starts forwarding the prospect's
// audio to the model. Safe to call more than once.
func (s *Session) openBrainGate() {
	s.openerPlayed.Store(true)
	s.gateOpenNano.CompareAndSwap(0, time.Now().UnixNano())
	s.brainGateOpen.Store(true)
}

// openerFollowThrough covers the one case the listen-first design cannot: a
// prospect who picks up, hears the whole opener, and says nothing at all. The
// model now only speaks once it has heard a human, so without this the line
// would sit silent until the idle supervisor fires ten seconds later.
//
// The cue it sends is careful to tell the model NOT to imagine what the prospect
// said — inventing their side is exactly the failure this whole path exists to
// avoid. It fires at most once, and only when genuinely nobody has spoken.
func (s *Session) openerFollowThrough() {
	const graceAfterOpener = 3500 * time.Millisecond

	select {
	case <-s.ctx.Done():
		return
	case <-time.After(graceAfterOpener):
	}
	if s.ctx.Err() != nil {
		return
	}
	// The model already replied — the conversation is under way.
	if s.modelSpoke.Load() {
		return
	}
	// The prospect spoke since the gate opened; the model's reply is on its way.
	if last := s.lastSpeechNano.Load(); last > s.gateOpenNano.Load() {
		return
	}

	s.log.Info("prospect silent after opener — prompting the model to check in")
	if err := s.brain.SendText("[System cue, NOT the prospect: you finished your opening line and the line has been completely silent since — nobody has replied. Do NOT invent, guess or repeat what they might have said, and do NOT answer your own question. Just check warmly that they can hear you, in one short line (e.g. \"Hello? Can you hear me okay?\"), then stop and wait.]"); err != nil {
		s.log.Warn("opener follow-through failed", "error", err)
	}
}

// energyBucket maps a frame's mean-abs amplitude to a histogram band.
func energyBucket(e int) int {
	switch {
	case e < 100:
		return 0
	case e < 300:
		return 1
	case e < 600:
		return 2
	case e < 1000:
		return 3
	case e < 2000:
		return 4
	case e < 4000:
		return 5
	case e < 8000:
		return 6
	default:
		return 7
	}
}

// holdAudio parks a chunk of the model's reply while the opener is still
// playing. Called only from the gemini→telnyx goroutine.
func (s *Session) holdAudio(mulaw []byte) {
	s.heldMu.Lock()
	// Bound the buffer so a runaway model can't queue an unbounded monologue —
	// ~30s of μ-law at 8kHz is far more than any opening reply should ever be.
	if len(s.heldAudio) < 1500 {
		chunk := make([]byte, len(mulaw))
		copy(chunk, mulaw)
		s.heldAudio = append(s.heldAudio, chunk)
	}
	s.heldMu.Unlock()
}

// flushHeldAudio releases everything the model produced during the opener, in
// order, then stops holding. Ordering matters: the flag is cleared last, so a
// chunk arriving mid-flush is appended to the buffer rather than jumping ahead
// of what is already queued.
func (s *Session) flushHeldAudio() {
	for {
		s.heldMu.Lock()
		// The flag is cleared here and nowhere else, so every exit path — including
		// a call that died mid-opener — goes through it. Leaving it set would make
		// holdAudio swallow the entire rest of the conversation.
		if len(s.heldAudio) == 0 || s.ctx.Err() != nil {
			s.heldAudio = nil
			s.fillerPlaying.Store(false)
			s.heldMu.Unlock()
			return
		}
		batch := s.heldAudio
		s.heldAudio = nil
		s.heldMu.Unlock()

		for _, chunk := range batch {
			if s.ctx.Err() != nil {
				break
			}
			s.sendTelnyxAudio(chunk)
		}
	}
}

// dropHeldAudio discards the parked reply — used when the model is interrupted,
// where sending stale audio would have the agent answer a question the prospect
// has already moved on from.
func (s *Session) dropHeldAudio() {
	s.heldMu.Lock()
	s.heldAudio = nil
	s.heldMu.Unlock()
}

// playFiller streams the pre-generated connection-check filler into the call at a
// 20ms pace (so Telnyx's jitter buffer doesn't run away). Returns when done or on
// cancel.
func (s *Session) playFiller() {
	mulaw := s.fillerMulaw
	if len(mulaw) == 0 {
		return
	}
	const frame = telnyxFrameBytes
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	for off := 0; off < len(mulaw); off += frame {
		select {
		case <-s.ctx.Done():
			return
		case <-ticker.C:
		}
		end := off + frame
		if end > len(mulaw) {
			end = len(mulaw)
		}
		s.playing.Store(1)
		s.sendTelnyxAudio(mulaw[off:end])
	}
	s.playing.Store(0)
}

// playThinkingFiller queues one acknowledgement for the caller. Unlike the
// opener it is NOT paced in realtime — the whole point is that it starts
// immediately — and Telnyx plays it out of its own buffer, so the model's real
// answer queues behind it and follows without a seam.
//
// It goes out through sendTelnyxAudio like any other agent audio, so
// playbackEndNano advances and the barge-in gate and silence supervisor both
// treat it as the agent holding the floor. That is exactly right: for as long as
// it is playing, the prospect is not sitting in silence.
func (s *Session) playThinkingFiller() {
	audio := s.pickThinkingFiller()
	if len(audio) == 0 {
		return
	}
	s.playing.Store(1)
	s.sendTelnyxAudio(audio)
	s.thinkingFills.Add(1)
	s.log.Debug("thinking filler — holding the floor while the model answers",
		"ms", len(audio)/8, "after_ms", thinkingFillerAfterMs)
}

func (s *Session) clearTelnyxBuffer() {
	msg := struct {
		Event string `json:"event"`
	}{Event: "clear"}

	s.telnyxMu.Lock()
	err := s.telnyxWS.WriteJSON(msg)
	s.telnyxMu.Unlock()
	if err != nil && s.ctx.Err() == nil {
		s.log.Warn("telnyx clear failed", "error", err)
	}
}

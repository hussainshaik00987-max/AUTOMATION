package main

import (
	"encoding/csv"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Prospect is one row of the scraped input CSV. Phone is required; everything
// else is research the agent may reference on the call (only if present).
type Prospect struct {
	Name               string // business listing name (e.g. "Meridian Studios")
	Phone              string // E.164 — the public/main line
	DirectNumber       string // E.164 — owner/direct line that bypasses the answering service (empty if none)
	OwnerName          string // sanitized owner FIRST name (empty if unknown/unreliable)
	LineType           string // "wireless" | "landline" | "voip" | "" — see IsMobile
	Rating             string // e.g. "4.8"
	TotalReviews       string // e.g. "340"
	Website            string // url or empty
	Address            string // metadata only — not spoken on the call
	City               string
	State              string // e.g. "TX" — carried for timezone/segmentation
	Timezone           string // IANA name, if the scraper resolved one; overrides every other guess
	Category           string // e.g. "Wedding photographer", "Bakery and Cake Shop"

	// India campaign fields (top_leads.csv). All optional — a missing column just
	// means that piece of research isn't available for this lead.
	DemoURL         string // pre-built demo site for THIS business — the thing we send
	Vertical        string // wedding_photo | bakery | wedding_planner | fashion | interior_arch
	Segment         string // A_hot | B_strong | C_worth_a_try
	Area            string // locality within the city (e.g. "Santipur")
	CityTier        string // "1" or "2"
	PitchAngle      string // scraper's own read on their web presence — drives siteGap()
	AutomationAngle string // scraper's read on enquiry volume (context only)
	Email           string // best email for the demo link, if they ask for email
	Instagram       string // profile URL
	Facebook        string // page URL
	IGFollowers     string // e.g. "15312.0"
	Price           string // their own advertised price band (context only)
}

// DialNumber is the number to actually call: the owner/direct line when we have
// one (bypassing the answering service that fronts most of these businesses),
// otherwise the public main line.
func (p Prospect) DialNumber() string {
	if p.DirectNumber != "" {
		return p.DirectNumber
	}
	return p.Phone
}

// Result is one row of the output CSV, written after each call.
type Result struct {
	Phone         string
	Business      string
	OwnerName     string // captured live if the prospect gives their name
	City          string
	Rating        string
	Reviews       string
	Disposition   string // link_sent|interested|callback|not_interested|do_not_call|gatekeeper|wrong_number|voicemail|no_answer|hung_up|dial_failed|interrupted|incomplete
	Callback      string // requested callback time, if any
	HangupBy      string // agent|prospect|timeout|interrupted|dial_failed
	AttendeeEmail string // email the prospect gave, when they asked for the link by email
	MeetingTime   string // booked meeting time (human-readable), if any
	MeetLink      string // Google Meet link, if a meeting was booked

	// India campaign outcomes.
	CallID      string // Telnyx call_control_id — ties a results.csv row to its recording and run-log entry
	LinkChannel string // meeting|email — how the demo reached them ("" = nothing promised)
	LinkStatus  string // queued|failed|declined — result of the dispatch
	DemoURL     string // the demo site that was (or would have been) sent
	Language    string // hi|en — the language the call actually ended up in

	// Follow-up agreed at the end of the call (Stage 7). This is the campaign's
	// actual call-back list: a sent link with nobody chasing it is a wasted lead.
	FollowupWhen string // the day/time in the prospect's own words ("kal shaam", "Sunday ko")
	FollowupKind string // meeting|callback|declined — how firm that commitment was

	// Did the agent actually hold a conversation? A disposition says what the call
	// was FOR; these say whether it functioned. The agent has been silently broken
	// before — a VAD setting once left it deaf and it said nothing for 33 seconds
	// while the prospect asked "हेलो?" five times — and from the outside that call
	// looked like an ordinary hangup. These are what the health check reads.
	ConnectedSec     int // seconds of live media; 0 = never answered
	ProspectSpeechMs int // how much the prospect actually said
	AgentTurns       int // how many times the LIVE model started speaking (the opener doesn't count)

	Timestamp time.Time
}

// normalizeHeader lowercases a header cell and strips spaces/underscores/hyphens
// so "Total_Reviews", "total reviews" and "TotalReviews" all match.
func normalizeHeader(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	s = strings.ReplaceAll(s, " ", "")
	s = strings.ReplaceAll(s, "_", "")
	s = strings.ReplaceAll(s, "-", "")
	return s
}

// ReadProspects parses the input CSV. It maps columns by header name (order
// independent), so the scraper can emit columns in any order. For backward
// compatibility, a single-column file with no recognizable header is treated as
// a plain list of phone numbers.
func ReadProspects(path string) ([]Prospect, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	r := csv.NewReader(f)
	r.FieldsPerRecord = -1 // allow variable fields
	rows, err := r.ReadAll()
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, nil
	}

	// Detect a header row by looking for a "phone" column.
	idx := map[string]int{}
	for i, cell := range rows[0] {
		idx[normalizeHeader(cell)] = i
	}
	col := func(keys ...string) int {
		for _, k := range keys {
			if i, ok := idx[k]; ok {
				return i
			}
		}
		return -1
	}

	phoneCol := col("phone", "phonenumber", "number")
	if phoneCol < 0 {
		// No header — fall back to the legacy phone-only format (column 0).
		return readPhoneOnly(rows)
	}

	get := func(row []string, c int) string {
		if c < 0 || c >= len(row) {
			return ""
		}
		return strings.TrimSpace(row[c])
	}

	nameCol := col("name", "business", "businessname")
	lineTypeCol := col("linetype", "line_type", "carriertype", "numbertype")
	ratingCol := col("rating", "stars", "googlerating")
	reviewsCol := col("totalreviews", "reviews", "reviewcount", "numreviews")
	websiteCol := col("website", "url", "site")
	addressCol := col("address")
	cityCol := col("city", "town")
	stateCol := col("state", "region")
	tzCol := col("timezone", "tz", "ianatimezone")
	categoryCol := col("category", "type", "trade", "businesstype")
	ownerCol := col("ownername", "owner")
	ownerWebCol := col("ownernameweb")
	directCol := col("directnumber", "directphone")
	directLabelCol := col("directnumberlabel")

	// India campaign columns (top_leads.csv).
	demoCol := col("demosite", "demourl", "demo")
	verticalCol := col("vertical")
	segmentCol := col("segment")
	areaCol := col("area", "locality")
	tierCol := col("citytier", "tier")
	pitchCol := col("pitchangle")
	autoCol := col("automationangle")
	emailCol := col("email")
	igEmailCol := col("igemail")
	igCol := col("instagram")
	igFollowCol := col("igfollowers")
	fbCol := col("facebook")
	priceCol := col("price")

	var out []Prospect
	seen := map[string]bool{} // dedupe by the number we'd actually dial
	for _, row := range rows[1:] {
		phone := get(row, phoneCol)
		if phone == "" {
			continue
		}
		normalized, err := normalizePhone(phone)
		if err != nil {
			// Skip unparseable numbers rather than abort the whole campaign — a big
			// enriched list will inevitably carry a few malformed cells.
			continue
		}
		phone = normalized

		// Direct/owner line that bypasses the answering service. Skip SMS-only ("text")
		// labels — those aren't reliable to voice-call.
		var direct string
		if raw := get(row, directCol); raw != "" {
			label := strings.ToLower(get(row, directLabelCol))
			if !strings.Contains(label, "text") {
				if d, e := normalizePhone(raw); e == nil && d != phone {
					direct = d
				}
			}
		}

		name := get(row, nameCol)
		owner := cleanOwnerFirstName(get(row, ownerCol), name)
		if owner == "" {
			owner = cleanOwnerFirstName(get(row, ownerWebCol), name)
		}

		email := get(row, emailCol)
		if email == "" {
			email = get(row, igEmailCol)
		}

		p := Prospect{
			Name:               name,
			Phone:              phone,
			DirectNumber:       direct,
			OwnerName:          owner,
			LineType:           normalizeLineType(get(row, lineTypeCol)),
			Rating:             get(row, ratingCol),
			TotalReviews:       trimFloat(get(row, reviewsCol)),
			Website:            get(row, websiteCol),
			Address:            get(row, addressCol),
			City:               get(row, cityCol),
			State:              get(row, stateCol),
			Timezone:           strings.TrimSpace(get(row, tzCol)),
			Category:           get(row, categoryCol),

			DemoURL:         get(row, demoCol),
			Vertical:        get(row, verticalCol),
			Segment:         get(row, segmentCol),
			Area:            get(row, areaCol),
			CityTier:        get(row, tierCol),
			PitchAngle:      get(row, pitchCol),
			AutomationAngle: get(row, autoCol),
			Email:           email,
			Instagram:       get(row, igCol),
			Facebook:        get(row, fbCol),
			IGFollowers:     trimFloat(get(row, igFollowCol)),
			Price:           get(row, priceCol),
		}
		if seen[p.DialNumber()] {
			continue // duplicate listing (same line) — call once
		}
		seen[p.DialNumber()] = true
		out = append(out, p)
	}
	return out, nil
}

// ownerStopwords are tokens that show up in the scraped owner_name column but are
// never a real first name (business-name fragments, generic words, CTA text).
var ownerStopwords = map[string]bool{
	"the": true, "your": true, "must": true, "call": true, "contact": true,
	"owner": true, "info": true, "team": true, "office": true, "service": true,
	"services": true, "company": true, "plumbing": true, "heating": true,
	"cooling": true, "air": true, "hvac": true, "electric": true, "electrical": true,
	"emergency": true, "best": true, "top": true, "pro": true, "home": true,
}

// cleanOwnerFirstName pulls a usable first name out of the scraped owner field, or
// returns "" if it looks unreliable. The enriched data often carries junk here
// ("Must Know This", or a fragment of the business name like "Knight" for
// "Knight's Plumbing"), and speaking a wrong name is an instant trust-killer — so
// this is deliberately conservative: when in doubt, return "" and the agent just
// asks for "the owner".
func cleanOwnerFirstName(owner, business string) string {
	owner = strings.TrimSpace(owner)
	if owner == "" {
		return ""
	}
	first := owner
	if sp := strings.IndexAny(owner, " \t"); sp >= 0 {
		first = owner[:sp]
	}
	first = strings.Trim(first, ".,'\"()-")
	if n := len(first); n < 2 || n > 15 {
		return ""
	}
	for _, r := range first {
		if !((r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z')) {
			return "" // not a plain alphabetic token
		}
	}
	low := strings.ToLower(first)
	if ownerStopwords[low] {
		return ""
	}
	// Reject names that are just a fragment of the business name.
	if business != "" && strings.Contains(normalizeHeader(business), low) {
		return ""
	}
	return strings.ToUpper(first[:1]) + low[1:]
}

// normalizeLineType folds the many spellings a carrier-lookup column arrives in
// down to the handful IsMobile switches on. Telnyx says "wireless"/"landline",
// other enrichment vendors say "mobile"/"fixed line"/"FixedLine"; an unrecognised
// value is left as-is and therefore counts as mobile, which is the safe side.
func normalizeLineType(v string) string {
	v = strings.ToLower(strings.TrimSpace(v))
	v = strings.ReplaceAll(v, "-", " ")
	v = strings.ReplaceAll(v, "_", " ")
	switch v {
	case "fixed line", "fixedline", "fixed", "landline":
		return "landline"
	case "mobile", "wireless", "cell", "cellular":
		return "wireless"
	}
	return v
}

// IsMobile reports whether the number we are dialling should be treated as a
// wireless line. It is a COMPLIANCE gate, not a delivery gate — under the TCPA a
// mobile number counts as residential no matter who uses it for what, so the
// federal B2B exemption that covers a business landline does not cover the
// owner's cell.
//
// In India this was decidable from the digits: a mobile is ten digits starting
// 6-9 and everything else (011, 044, 020 …) is a landline. **In the US it is
// not.** Number portability means an area code and exchange say nothing about
// line type — a number issued to a landline block in 1998 can be a cell phone
// today. The only reliable answer comes from a carrier lookup (see
// resolveLineType in compliance.go, which fills Prospect.LineType from Telnyx's
// number_lookup API and caches it).
//
// So the digits are never consulted, and the errors are deliberately asymmetric:
// **unknown counts as mobile.** Treating a cell as a landline is the expensive
// mistake ($500–1,500 per call, no cap); treating a landline as a cell only
// costs a lead you could have dialled.
func (p Prospect) IsMobile() bool {
	return p.CanWhatsApp()
}

// CanWhatsApp reports whether the number we are dialling could receive a
// WhatsApp message. Indian mobiles are ten digits starting 6-9; anything else on
// this list is a landline (011, 044, 020 …) and WhatsApp will simply never reach
// it.
//
// 19 of the 2,000 leads are landlines, and one of them turned up inside a
// nine-call test run: the agent asked "WhatsApp पे लिंक भेज दूँ?", the prospect
// was told it was on its way, and the sidecar came back `unreachable`. A promise
// that cannot be kept is worse than no offer, so the agent asks for an email
// address on these instead.
// Both strips here are length-guarded, and the country-code one has to be:
// "91" is also a legal start to a ten-digit mobile, so an unguarded
// TrimPrefix turns 9123456789 into 23456789 and reports a perfectly reachable
// number as unreachable. In the campaign the input is always E.164 by the time
// it arrives — normalizePhone runs at CSV load — but DialNumber can return the
// Direct Number column, and this must not depend on which column it came from.
func (p Prospect) CanWhatsApp() bool {
	d := digitsOnly(p.DialNumber())
	if len(d) == 12 && strings.HasPrefix(d, "91") {
		d = d[2:]
	} else if len(d) == 11 && d[0] == '0' {
		d = d[1:] // trunk prefix, as routinely written on a listing
	}
	return len(d) == 10 && d[0] >= '6' && d[0] <= '9'
}

// digitsOnly strips everything that isn't a digit.
func digitsOnly(s string) string {
	var b strings.Builder
	for _, r := range s {
		if r >= '0' && r <= '9' {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// trimFloat cleans numeric cells the scraper emits through pandas, which writes
// integers as floats ("474.0", "15312.0"). Left as-is these get spoken aloud as
// "four hundred seventy four point zero", which sounds like a machine reading a
// spreadsheet — an instant robocall tell.
func trimFloat(s string) string {
	return strings.TrimSuffix(strings.TrimSpace(s), ".0")
}

// defaultRegion decides how bare, country-code-less phone numbers are read.
// "IN" (the default for this campaign) treats a 10-digit number starting 6–9 as
// an Indian mobile; "US" treats any 10-digit number as US/Canada. It cannot be
// inferred from the digits alone — US area codes also start 6–9 — so it is an
// explicit setting (DEFAULT_REGION in .env). Numbers that already carry a "+"
// country code are unaffected either way, which covers 99% of top_leads.csv.
var defaultRegion = "US"

// normalizePhone converts a human-formatted phone number into E.164. It accepts
// what the scrapers emit — "+91 69012 40190", "09876543210", "9876543210",
// "(832) 328-1010" — and resolves bare numbers using defaultRegion.
func normalizePhone(raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	hasPlus := strings.HasPrefix(raw, "+")

	var digits strings.Builder
	for _, r := range raw {
		if r >= '0' && r <= '9' {
			digits.WriteRune(r)
		}
	}
	d := digits.String()

	if hasPlus {
		// Trust an explicit country code; just strip formatting.
		if len(d) < 8 {
			return "", fmt.Errorf("phone %q too short to be valid", raw)
		}
		return "+" + d, nil
	}

	// Already carries a country code without the "+".
	if len(d) == 12 && strings.HasPrefix(d, "91") {
		return "+" + d, nil
	}
	if len(d) == 11 && strings.HasPrefix(d, "1") {
		return "+" + d, nil
	}
	// Indian numbers are commonly written with a leading 0 (STD prefix).
	if defaultRegion == "IN" && len(d) == 11 && strings.HasPrefix(d, "0") {
		d = d[1:]
	}

	if len(d) == 10 {
		if defaultRegion == "IN" {
			// Every Indian mobile is 10 digits starting 6–9. Anything else at this
			// length is a landline fragment missing its STD code — undialable.
			if d[0] < '6' || d[0] > '9' {
				return "", fmt.Errorf("phone %q is not a valid Indian mobile (must start 6-9)", raw)
			}
			return "+91" + d, nil
		}
		return "+1" + d, nil
	}

	return "", fmt.Errorf("phone %q is not a recognizable number for region %s (got %d digits)", raw, defaultRegion, len(d))
}

// readPhoneOnly handles the legacy "one phone per line" format.
func readPhoneOnly(rows [][]string) ([]Prospect, error) {
	var out []Prospect
	for i, row := range rows {
		if len(row) == 0 {
			continue
		}
		phone := strings.TrimSpace(row[0])
		if phone == "" || strings.EqualFold(phone, "phone") {
			continue // skip header / blank
		}
		normalized, err := normalizePhone(phone)
		if err != nil {
			return nil, fmt.Errorf("row %d: %w", i+1, err)
		}
		out = append(out, Prospect{Phone: normalized})
	}
	return out, nil
}

var resultsMu sync.Mutex

func AppendResult(path string, r Result) error {
	resultsMu.Lock()
	defer resultsMu.Unlock()

	newFile := false
	if _, err := os.Stat(path); os.IsNotExist(err) {
		newFile = true
	}

	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer f.Close()

	w := csv.NewWriter(f)
	defer w.Flush()

	if newFile {
		if err := w.Write([]string{
			"timestamp", "phone", "business", "owner_name", "city",
			"rating", "reviews", "disposition", "callback", "hangup_by",
			"link_channel", "link_status", "demo_url", "email",
			"meeting_time", "meet_link",
			"followup_kind", "followup_when", "call_id",
			"connected_sec", "prospect_speech_ms", "agent_turns",
		}); err != nil {
			return err
		}
	}
	return w.Write([]string{
		r.Timestamp.Format(time.RFC3339),
		r.Phone,
		r.Business,
		r.OwnerName,
		r.City,
		r.Rating,
		r.Reviews,
		r.Disposition,
		r.Callback,
		r.HangupBy,
		r.LinkChannel,
		r.LinkStatus,
		r.DemoURL,
		r.AttendeeEmail,
		r.MeetingTime,
		r.MeetLink,
		r.FollowupKind,
		r.FollowupWhen,
		r.CallID,
		strconv.Itoa(r.ConnectedSec),
		strconv.Itoa(r.ProspectSpeechMs),
		strconv.Itoa(r.AgentTurns),
	})
}

package main

import "testing"

// The refusal that cost an evening: HTTP 403, generic code 10010, and a "detail"
// naming two limits that were both innocent. Only telnyx_error.error_code was
// true. Verbatim, so a change to the parser is measured against the real thing.
const d7Refusal = `{
  "data": { "call_control_id": "v3:ho6y2BdUgo0FuD0Zlmk5iLalPa4uDzI4" },
  "errors": [ { "code": 10010, "title": null,
    "detail": "Daily OB profile limit exceeded D7. Daily outbound concurrent call limit has been reached." } ],
  "telnyx_error": { "error_code": "D7" }
}`

func TestTelnyxErrorCodeReadsTheMachineReadableVerdict(t *testing.T) {
	if got := telnyxErrorCode([]byte(d7Refusal)); got != "D7" {
		t.Fatalf("telnyxErrorCode = %q, want D7", got)
	}
}

// A parameter rejection carries no D-code, and MUST stay retryable — dropping
// dial-time streaming or a gated anchorsite is how one unsupported field stops
// being an outage that fails every lead on the list identically.
func TestTelnyxErrorCodeIgnoresParameterRejections(t *testing.T) {
	for name, body := range map[string]string{
		"validation": `{"errors":[{"code":"10001","title":"Invalid value","detail":"anchorsite_override not allowed for call control applications"}]}`,
		"no body":    ``,
		"not json":   `<html>502 Bad Gateway</html>`,
		"empty code": `{"telnyx_error":{"error_code":""}}`,
		"not a D":    `{"telnyx_error":{"error_code":"10010"}}`,
		"D-ish word": `{"telnyx_error":{"error_code":"DENIED"}}`,
	} {
		if got := telnyxErrorCode([]byte(body)); got != "" {
			t.Errorf("%s: telnyxErrorCode = %q, want \"\" so the fallback ladder still runs", name, got)
		}
	}
}

// Every code the ladder can terminate on has to say something an operator can
// act on. A bare code in the ERROR line is what made this take an hour.
func TestTelnyxErrorMeaningNamesACause(t *testing.T) {
	for _, c := range []string{"D1", "D2", "D3", "D7", "D9", "D19", "D22", "D24", "D35", "D36", "D38", "D41", "D51"} {
		if m := telnyxErrorMeaning(c); m == "" || m == "see Telnyx SIP response codes for "+c {
			t.Errorf("telnyxErrorMeaning(%s) = %q, want a real explanation", c, m)
		}
	}
	if m := telnyxErrorMeaning("D99"); m == "" {
		t.Error("an unknown code must still produce a pointer to the catalogue")
	}
}

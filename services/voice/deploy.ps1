# deploy.ps1 — ship the India voice agent + WhatsApp sidecar to the Mumbai VPS.
#
#   RUN FROM YOUR OWN PowerShell window, in this folder:
#       .\deploy.ps1
#
# Safe by design:
#   * backs up the server's current code before changing anything
#   * uploads ONLY code + docs — never .env, results.csv, recordings, or the
#     WhatsApp auth/ session (those live on the server and are left alone)
#   * aborts WITHOUT restarting if the server build or tests fail
#   * never touches nginx, ufw, or the 7xpro app sharing this box

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$KEY      = "C:\Users\krish\.ssh\id_ed25519"
$HOSTSPEC = "root@YOUR_VPS_IP"
$SSH      = @("-i", $KEY, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20")

$AGENT_DIR   = "/opt/voice-agent"
$SIDECAR_DIR = "/opt/whatsapp-sidecar"

# Go sources + docs + scripts + the lead lists. Secrets and generated data are
# never listed. Every .go file in the package must be here: one missing source
# file does not fail the upload, it fails the SERVER BUILD — after the backup has
# already been taken and half the tree replaced.
$AGENT_FILES = @(
  "main.go", "gemini.go", "session.go", "leads.go", "audio.go", "telnyx.go",
  "opener.go", "outreach.go", "pitch_hi.go", "gcal.go", "recording.go",
  "barge.go", "thinking.go", "bench.go", "runlog.go", "report.go", "health.go",
  "openerpreview.go", "probe.go", "local.go", "simulate.go",
  "pitch_hi_test.go", "outreach_test.go", "barge_test.go", "thinking_test.go",
  "voicemail_test.go", "phone_test.go", "opener_test.go", "report_test.go",
  "selftalk_test.go", "answer_test.go", "health_test.go",
  "go.mod", "go.sum", ".env.example", "CLAUDE.md",
  "run_campaign.sh", "watch_campaign.sh",
  "top_leads.csv", "test_wedding.csv", "wedding_run2.csv"
)

# Node sidecar sources. node_modules, auth/ and jobs.json stay on the server —
# auth/ IS the logged-in WhatsApp session and jobs.json holds leads mid-follow-up.
$SIDECAR_FILES = @(
  "index.js", "queue.js", "store.js", "messages.js", "intent.js", "config.js",
  "test-flow.js", "package.json", "package-lock.json", ".env.example", "README.md"
)

function Inv([string]$cmd) { & ssh @SSH $HOSTSPEC $cmd }

Write-Host "==> Checking SSH..." -ForegroundColor Cyan
if ("$(Inv 'echo ok')" -notmatch "ok") { throw "Cannot reach $HOSTSPEC over SSH." }

# --- verify locally before shipping anything -------------------------------
Write-Host "==> Building and testing locally first..." -ForegroundColor Cyan
& go build ./... ; if ($LASTEXITCODE -ne 0) { throw "Local build failed — nothing deployed." }
& go test ./...  ; if ($LASTEXITCODE -ne 0) { throw "Local tests failed — nothing deployed." }

$missing = $AGENT_FILES | Where-Object { -not (Test-Path $_) }
if ($missing) { throw "Missing agent files: $($missing -join ', ')" }
$missingWa = $SIDECAR_FILES | Where-Object { -not (Test-Path "../whatsapp/$_") }
if ($missingWa) { throw "Missing sidecar files: $($missingWa -join ', ')" }

# --- package ---------------------------------------------------------------
Write-Host "==> Packaging..." -ForegroundColor Cyan
$agentTar = Join-Path $env:TEMP "agent-deploy.tar"
$waTar    = Join-Path $env:TEMP "wa-deploy.tar"
Remove-Item $agentTar, $waTar -Force -ErrorAction SilentlyContinue
tar -cf $agentTar -- $AGENT_FILES
tar -cf $waTar -C ../whatsapp -- $SIDECAR_FILES

# --- back up ---------------------------------------------------------------
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
Write-Host "==> Backing up server code to .bak/$stamp ..." -ForegroundColor Cyan
Inv "mkdir -p '$AGENT_DIR/.bak/$stamp' '$SIDECAR_DIR/.bak/$stamp'; cp -f $AGENT_DIR/*.go '$AGENT_DIR/.bak/$stamp/' 2>/dev/null; cp -f $SIDECAR_DIR/*.js '$SIDECAR_DIR/.bak/$stamp/' 2>/dev/null; echo backed-up"

# --- upload ----------------------------------------------------------------
Write-Host "==> Uploading..." -ForegroundColor Cyan
& scp @SSH $agentTar "${HOSTSPEC}:/tmp/agent-deploy.tar"
& scp @SSH $waTar    "${HOSTSPEC}:/tmp/wa-deploy.tar"
Inv "tar -xf /tmp/agent-deploy.tar -C '$AGENT_DIR' && tar -xf /tmp/wa-deploy.tar -C '$SIDECAR_DIR' && rm -f /tmp/agent-deploy.tar /tmp/wa-deploy.tar && chmod +x '$AGENT_DIR'/*.sh && echo extracted"

# --- build on the server ---------------------------------------------------
Write-Host "==> Building on server..." -ForegroundColor Cyan
$build = Inv "cd '$AGENT_DIR' && export PATH=`$PATH:/usr/local/go/bin && go build -o voice-agent.new . && go test ./... >/dev/null 2>&1 && echo BUILD_OK"
$build | ForEach-Object { Write-Host "    $_" }
if ("$build" -notmatch "BUILD_OK") {
  Inv "rm -f '$AGENT_DIR/voice-agent.new'"
  throw "Server build/tests FAILED. Nothing was swapped; the old binary is intact. Backup: $AGENT_DIR/.bak/$stamp"
}
# Swap only after a clean build+test, so a broken deploy can never replace a
# working binary mid-campaign.
Inv "cd '$AGENT_DIR' && mv -f voice-agent.new voice-agent && echo swapped"

Write-Host "==> Updating sidecar dependencies..." -ForegroundColor Cyan
Inv "cd '$SIDECAR_DIR' && npm install --omit=dev --no-audit --no-fund >/dev/null 2>&1 && node --check index.js && echo SIDECAR_OK"

# --- restart ---------------------------------------------------------------
# The sidecar is a long-lived service and is safe to restart (job state is on
# disk; the WhatsApp session lives in auth/). The agent is a BATCH DIALER and is
# deliberately NOT auto-started here — starting it begins calling people.
Write-Host "==> Restarting sidecar (if it was running)..." -ForegroundColor Cyan
Inv "systemctl is-active --quiet whatsapp-sidecar && systemctl restart whatsapp-sidecar && echo 'sidecar restarted' || echo 'sidecar not running — left stopped'"

Write-Host ""
Write-Host "==> Deploy complete." -ForegroundColor Green
Write-Host "    Nothing is dialling. Start a bounded, fully-recorded run yourself:" -ForegroundColor Yellow
Write-Host "      ssh -i `"$KEY`" $HOSTSPEC '/opt/voice-agent/run_campaign.sh wedding_run2.csv 35 live2'" -ForegroundColor Yellow
Write-Host "    …or watch one that is already running, from a second terminal:" -ForegroundColor Yellow
Write-Host "      ssh -i `"$KEY`" $HOSTSPEC '/opt/voice-agent/watch_campaign.sh live2'" -ForegroundColor Yellow
Write-Host "    …and read any finished run back:" -ForegroundColor Yellow
Write-Host "      ssh -i `"$KEY`" $HOSTSPEC '/opt/voice-agent/voice-agent -report campaignlogs/live2.jsonl'" -ForegroundColor Yellow

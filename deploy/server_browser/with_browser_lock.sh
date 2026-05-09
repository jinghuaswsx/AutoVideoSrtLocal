#!/usr/bin/env bash
# Docs-anchor: docs/superpowers/specs/2026-05-09-roi-hourly-sync-lock-recovery.md
set -euo pipefail

LOCK_PATH="${BROWSER_AUTOMATION_LOCK_PATH:-/data/autovideosrt/browser/runtime/automation.lock}"
LOCK_TIMEOUT_SECONDS="${BROWSER_AUTOMATION_LOCK_TIMEOUT_SECONDS:-300}"
LOCK_RETRY_SECONDS="${BROWSER_AUTOMATION_LOCK_RETRY_SECONDS:-10}"
ALERT_TASK_CODE="${BROWSER_AUTOMATION_LOCK_ALERT_TASK_CODE:-}"
APP_DIR="${APP_DIR:-/opt/autovideosrt}"
PYTHON_BIN="${BROWSER_AUTOMATION_LOCK_PYTHON:-$APP_DIR/venv/bin/python}"
FAILURE_RECORDER="${BROWSER_AUTOMATION_LOCK_FAILURE_RECORDER:-$APP_DIR/tools/record_scheduled_task_failure.py}"

if [[ "$#" -lt 1 ]]; then
  echo "usage: $0 command [args...]" >&2
  exit 64
fi

log() {
  echo "[browser-lock] $*" >&2
}

# Snapshot non-self holders of the lock as JSON, so the timeout report shows
# who is sitting on the lock (PID, command, wall-clock age). Stays tolerant
# when lsof / ps are missing — the wrapper still records the timeout.
collect_lock_holders_json() {
  local self_pid="$$"
  local raw_pids
  if ! command -v lsof >/dev/null 2>&1; then
    printf '[]'
    return
  fi
  # Close FD 9 in the subshell so lsof / ps subprocesses do not inherit it,
  # then drop our own pid and any direct descendant pid (subshells inherit
  # FD 9 briefly during fork even with that close, so a second filter on
  # PPID == self is needed).
  raw_pids="$(exec 9>&-; lsof -t -- "$LOCK_PATH" 2>/dev/null | awk -v self="$self_pid" '$0 != self')"
  if [[ -z "$raw_pids" ]]; then
    printf '[]'
    return
  fi

  local first=1
  printf '['
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    local ppid="" etimes="" cmd=""
    if command -v ps >/dev/null 2>&1; then
      ppid="$(ps -p "$pid" -o ppid= 2>/dev/null | tr -d ' ')"
      etimes="$(ps -p "$pid" -o etimes= 2>/dev/null | tr -d ' ')"
      cmd="$(ps -p "$pid" -o args= 2>/dev/null | head -c 200)"
    fi
    if [[ "$ppid" == "$self_pid" ]]; then
      continue
    fi
    cmd="${cmd//\\/\\\\}"
    cmd="${cmd//\"/\\\"}"
    if (( first )); then
      first=0
    else
      printf ','
    fi
    printf '{"pid":%s,"age_seconds":%s,"cmd":"%s"}' \
      "$pid" "${etimes:-null}" "$cmd"
  done <<< "$raw_pids"
  printf ']'
}

record_timeout_failure() {
  local waited_seconds="$1"
  local message="browser automation lock timeout after ${waited_seconds}s: ${LOCK_PATH}"
  local holders_json
  holders_json="$(collect_lock_holders_json)"
  local summary_json
  summary_json="{\"lock_path\":\"${LOCK_PATH}\",\"timeout_seconds\":${LOCK_TIMEOUT_SECONDS},\"waited_seconds\":${waited_seconds},\"holders\":${holders_json}}"
  log "$message"
  log "lock holders at timeout: ${holders_json}"

  if [[ -z "$ALERT_TASK_CODE" ]]; then
    log "BROWSER_AUTOMATION_LOCK_ALERT_TASK_CODE is not set; skipped DB failure record"
    return
  fi
  if [[ ! -f "$FAILURE_RECORDER" ]]; then
    log "failure recorder not found: $FAILURE_RECORDER"
    return
  fi

  "$PYTHON_BIN" "$FAILURE_RECORDER" \
    --task-code "$ALERT_TASK_CODE" \
    --error-message "$message" \
    --summary-json "$summary_json" \
    || log "failed to record lock timeout for task: $ALERT_TASK_CODE"
}

install -d -m 755 "$(dirname "$LOCK_PATH")"
exec 9>"$LOCK_PATH"

start_epoch="$(date +%s)"
while ! flock -n 9; do
  now_epoch="$(date +%s)"
  elapsed="$((now_epoch - start_epoch))"
  if (( elapsed >= LOCK_TIMEOUT_SECONDS )); then
    record_timeout_failure "$elapsed"
    exit 75
  fi
  log "waiting for shared browser lock: ${LOCK_PATH} (${elapsed}/${LOCK_TIMEOUT_SECONDS}s)"
  sleep "$LOCK_RETRY_SECONDS"
done

log "acquired shared browser lock: $LOCK_PATH"
set +e
"$@"
status="$?"
set -e
flock -u 9 || true

if [[ "$status" -ne 0 ]]; then
  log "command failed with status $status"
else
  log "command completed"
fi
exit "$status"

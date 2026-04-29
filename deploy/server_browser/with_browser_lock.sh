#!/usr/bin/env bash
set -euo pipefail

LOCK_PATH="${BROWSER_AUTOMATION_LOCK_PATH:-/data/autovideosrt/browser/runtime/automation.lock}"
LOCK_TIMEOUT_SECONDS="${BROWSER_AUTOMATION_LOCK_TIMEOUT_SECONDS:-600}"
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

record_timeout_failure() {
  local waited_seconds="$1"
  local message="browser automation lock timeout after ${waited_seconds}s: ${LOCK_PATH}"
  local summary_json
  summary_json="{\"lock_path\":\"${LOCK_PATH}\",\"timeout_seconds\":${LOCK_TIMEOUT_SECONDS},\"waited_seconds\":${waited_seconds}}"

  if [[ -z "$ALERT_TASK_CODE" ]]; then
    log "$message"
    log "BROWSER_AUTOMATION_LOCK_ALERT_TASK_CODE is not set; skipped DB failure record"
    return
  fi
  if [[ ! -f "$FAILURE_RECORDER" ]]; then
    log "$message"
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

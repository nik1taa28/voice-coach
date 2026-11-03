#!/usr/bin/env bash
# For each user count: start a FRESH server (its metrics are cumulative), run Locust against it, stop the server.
#   bash loadtest/sweep.sh 1 10 25 50 100          (DURATION=60 seconds per step, PORT=8000; override with env vars)
# Providers come from the environment (default: all fake). Server logging stays ON because that is how the app ships.
set -euo pipefail
cd "$(dirname "$0")/.."

DURATION=${DURATION:-60}
PORT=${PORT:-8000}
HOST="http://127.0.0.1:$PORT"
mkdir -p loadtest/results
# Keep every sample of a step (the app's default window of 1000 would only cover the last seconds at high throughput).
export METRICS_WINDOW=${METRICS_WINDOW:-200000}

for users in "$@"; do
  label="u${users}${LABEL_SUFFIX:-}"   # e.g. LABEL_SUFFIX=r2 -> u100r2, so repeat runs do not overwrite each other
  .venv/bin/python -m uvicorn backend.main:app --port "$PORT" --log-level warning > "loadtest/results/server-$label.log" 2>&1 &
  server_pid=$!
  for _ in $(seq 1 50); do
    curl -sf "$HOST/health" > /dev/null && break
    sleep 0.2
  done
  if ! curl -sf "$HOST/health" > /dev/null; then
    kill "$server_pid" 2> /dev/null || true
    echo "server did not start; see loadtest/results/server-$label.log" >&2
    exit 1
  fi

  if [[ "${WARMUP:-0}" == "1" ]]; then   # load the ASR model before the measured run (needs LOADTEST_WAV)
    .venv/bin/python loadtest/warmup.py "$HOST" || { kill "$server_pid" 2> /dev/null || true; echo "warm-up failed for $label" >&2; exit 1; }
  fi
  echo "=== $label: $users users, ${DURATION}s ==="
  LOADTEST_LABEL="$label" .venv/bin/locust -f loadtest/locustfile.py --headless \
    -u "$users" -r 10 -t "${DURATION}s" --host "$HOST" --csv "loadtest/results/$label" &
  locust_pid=$!
  # Watchdog: Locust must exit by itself shortly after its time limit. If it does not, record a HUNG marker and stop it.
  deadline=$((SECONDS + DURATION + 40))
  while kill -0 "$locust_pid" 2> /dev/null; do
    if (( SECONDS > deadline )); then
      echo "HUNG: $label still running 40 s after its ${DURATION}s limit; terminating it"
      touch "loadtest/results/$label.HUNG"
      kill -TERM "$locust_pid" 2> /dev/null || true
      break
    fi
    sleep 1
  done
  code=0
  wait "$locust_pid" || code=$?
  echo "locust exit code $code for $label"   # non-zero when requests failed or after a HUNG termination; keep sweeping

  kill "$server_pid" 2> /dev/null || true
  wait "$server_pid" 2> /dev/null || true
done
echo "done. Summary: .venv/bin/python loadtest/summarize.py"

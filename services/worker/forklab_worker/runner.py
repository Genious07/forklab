"""Standalone worker process.

Runs the same claim protocol as the in process worker, so a deployment can run
several of these next to the API without changing anything else.
"""

from __future__ import annotations

import signal
import sys
import time

from forklab_api.db import init_db
from forklab_api.jobs import claim_next, execute, new_worker_id

_running = True


def _stop(_signum, _frame) -> None:
    global _running
    _running = False
    print("worker: stop requested, finishing the current job", flush=True)


def main() -> int:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    init_db()
    worker_id = new_worker_id()
    print(f"worker: {worker_id} polling for jobs", flush=True)

    idle_since = time.time()
    while _running:
        experiment_id = claim_next(worker_id)
        if experiment_id is None:
            if time.time() - idle_since > 60:
                idle_since = time.time()
            time.sleep(0.5)
            continue
        idle_since = time.time()
        print(f"worker: claimed {experiment_id}", flush=True)
        state = execute(experiment_id, worker_id)
        print(f"worker: {experiment_id} finished as {state}", flush=True)

    print("worker: stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

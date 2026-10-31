"""Background processing with an in-process thread pool and live progress tracking."""
import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)


class JobCancelled(Exception):
    pass


class JobManager:
    def __init__(self, config, store):
        self.config = config
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=max(1, config.get("WORKER_THREADS", 2)),
                                       thread_name_prefix="ipr-job")
        self.live = {}
        self.cancel_flags = {}
        self._lock = threading.Lock()

    def status(self, sid):
        with self._lock:
            if sid in self.live:
                return dict(self.live[sid])
        return self.store.load(sid).get("job", {"status": "idle"})

    def is_running(self, sid):
        return self.status(sid).get("status") in ("queued", "running")

    def start(self, sid, target):
        """Run ``target(sid, progress, check_cancel)`` in the background."""
        if self.is_running(sid):
            raise RuntimeError("Processing is already running for this session.")
        state = {"status": "queued", "progress": 0, "stage": "Queued", "message": "", "started": time.time(),
                 "log": []}
        with self._lock:
            self.live[sid] = state
            self.cancel_flags[sid] = threading.Event()
        self._persist(sid)
        self.pool.submit(self._run, sid, target)
        return dict(state)

    def cancel(self, sid):
        flag = self.cancel_flags.get(sid)
        if flag:
            flag.set()
            return True
        return False

    def _persist(self, sid):
        with self._lock:
            state = dict(self.live.get(sid, {}))
        if state:
            def put(s):
                s["job"] = state
            self.store.update(sid, put)

    def _run(self, sid, target):
        last_persist = [0.0]

        def progress(pct=None, stage=None, message=None, log_line=None):
            with self._lock:
                st = self.live[sid]
                st["status"] = "running"
                if pct is not None:
                    st["progress"] = round(max(st.get("progress", 0), min(100.0, pct)), 1)
                if stage is not None:
                    st["stage"] = stage
                if message is not None:
                    st["message"] = message
                if log_line:
                    st["log"] = (st.get("log", []) + [log_line])[-200:]
            if time.time() - last_persist[0] > 2:
                last_persist[0] = time.time()
                self._persist(sid)

        def check_cancel():
            if self.cancel_flags[sid].is_set():
                raise JobCancelled()

        try:
            progress(1, "Starting")
            target(sid, progress, check_cancel)
            final = {"status": "done", "progress": 100, "stage": "Complete", "message": "Processing complete."}
        except JobCancelled:
            final = {"status": "cancelled", "stage": "Cancelled", "message": "Processing was cancelled."}
        except Exception as exc:  # surfaced to the user, never swallowed silently
            log.error("Job %s failed: %s\n%s", sid, exc, traceback.format_exc())
            final = {"status": "error", "stage": "Failed", "message": f"Processing failed: {exc}"}
        with self._lock:
            self.live[sid].update(final, finished=time.time())
        self._persist(sid)
        with self._lock:
            self.live.pop(sid, None)
            self.cancel_flags.pop(sid, None)

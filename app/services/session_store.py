"""File-backed session storage: one JSON document per working session."""
import json
import os
import re
import shutil
import threading
import time
import uuid

SID_RE = re.compile(r"^[a-f0-9]{32}$")


class SessionNotFound(KeyError):
    pass


def new_session(sid):
    return {
        "id": sid,
        "created": time.time(),
        "updated": time.time(),
        "documents": [],
        "interests": {"topics": [], "description": "", "keywords": []},
        "settings": {},
        "job": {"status": "idle", "progress": 0, "stage": "", "message": ""},
        "results": [],
        "topics_run": [],
        "output": None,
    }


class SessionStore:
    def __init__(self, config):
        self.root = config["SESSION_DIR"]
        self.upload_root = config["UPLOAD_DIR"]
        self.output_root = config["OUTPUT_DIR"]
        self.ttl = config.get("SESSION_TTL_HOURS", 72) * 3600
        self._locks = {}
        self._guard = threading.Lock()

    def lock(self, sid):
        with self._guard:
            return self._locks.setdefault(sid, threading.RLock())

    @staticmethod
    def valid(sid):
        return bool(sid and SID_RE.match(sid))

    def session_dir(self, sid):
        return os.path.join(self.root, sid)

    def upload_dir(self, sid):
        return os.path.join(self.upload_root, sid)

    def output_dir(self, sid):
        return os.path.join(self.output_root, sid)

    def snapshot_dir(self, sid):
        return os.path.join(self.session_dir(sid), "snapshots")

    def _path(self, sid):
        return os.path.join(self.session_dir(sid), "session.json")

    def create(self):
        sid = uuid.uuid4().hex
        for d in (self.session_dir(sid), self.upload_dir(sid), self.output_dir(sid), self.snapshot_dir(sid)):
            os.makedirs(d, exist_ok=True)
        self.save(new_session(sid))
        return sid

    def exists(self, sid):
        return self.valid(sid) and os.path.exists(self._path(sid))

    def load(self, sid):
        if not self.exists(sid):
            raise SessionNotFound(sid)
        with self.lock(sid), open(self._path(sid), encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, session):
        sid = session["id"]
        session["updated"] = time.time()
        path = self._path(sid)
        tmp = f"{path}.{threading.get_ident()}.tmp"
        with self.lock(sid):
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(session, fh)
            os.replace(tmp, path)

    def update(self, sid, fn):
        """Atomically load, mutate (fn(session)) and save a session; returns fn's result."""
        with self.lock(sid):
            s = self.load(sid)
            out = fn(s)
            self.save(s)
            return out

    def delete(self, sid):
        for d in (self.session_dir(sid), self.upload_dir(sid), self.output_dir(sid)):
            shutil.rmtree(d, ignore_errors=True)

    def cleanup_expired(self):
        if not os.path.isdir(self.root):
            return 0
        now, removed = time.time(), 0
        for sid in os.listdir(self.root):
            p = self._path(sid)
            if self.valid(sid) and os.path.exists(p) and now - os.path.getmtime(p) > self.ttl:
                self.delete(sid)
                removed += 1
        return removed

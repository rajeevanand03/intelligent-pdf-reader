import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(ROOT, "tests", "fixtures", "generated")
sys.path.insert(0, ROOT)


@pytest.fixture(scope="session")
def fixtures():
    if not os.path.exists(os.path.join(GEN, "annual_operations_review.pdf")):
        subprocess.check_call([sys.executable, os.path.join(ROOT, "tests", "fixtures", "make_samples.py")])
    return GEN


@pytest.fixture(scope="session")
def app(tmp_path_factory, fixtures):
    from config import TestConfig

    data = tmp_path_factory.mktemp("data")

    class Cfg(TestConfig):
        DATA_DIR = str(data)
        UPLOAD_DIR = str(data / "uploads")
        OUTPUT_DIR = str(data / "outputs")
        SESSION_DIR = str(data / "sessions")
        CACHE_DIR = os.path.join(ROOT, "instance", "cache")
        SNAPSHOT_DPI = 110

    from app import create_app
    return create_app(Cfg)


@pytest.fixture()
def client(app):
    return app.test_client()


def upload(client, sid, path, name=None):
    with open(path, "rb") as fh:
        return client.post(f"/api/sessions/{sid}/documents", data={"file": (fh, name or os.path.basename(path))},
                           content_type="multipart/form-data")


def run_job(client, sid, timeout=300):
    r = client.post(f"/api/sessions/{sid}/process")
    assert r.status_code == 202, r.get_json()
    t = time.time()
    while time.time() - t < timeout:
        j = client.get(f"/api/sessions/{sid}/job").get_json()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.5)
    raise TimeoutError("job did not finish")

"""The phone remote: its key works through the tunnel, and only for the
event controls - nothing else the operator token unlocks."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay import config as config_module  # noqa: E402
from relay import server  # noqa: E402

try:  # TestClient needs httpx, which the app itself does not
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

TUNNEL = "captions.example.org"


@unittest.skipIf(TestClient is None, "httpx not installed")
class RemoteTests(unittest.TestCase):
  def test_remote_through_tunnel(self):
    tmp = tempfile.TemporaryDirectory()
    self.addCleanup(tmp.cleanup)
    saved = {}
    patch = mock.patch.object(config_module, "save_env_var", lambda k, v: saved.update({k: v}))
    patch.start()
    self.addCleanup(patch.stop)
    cfg = config_module.RelayConfig(source_token="s", operator_token="op", remote_token="rk",
                                    public_hostname=TUNNEL)
    cfg.config_path = Path(tmp.name) / "event.toml"
    app = server.create_app(cfg)
    public = TestClient(app, base_url=f"https://{TUNNEL}")
    local = TestClient(app, base_url="http://127.0.0.1:8000")

    # No key: the remote and its API are closed through the tunnel.
    assert public.get("/remote").status_code == 401
    assert public.get("/api/remote").status_code == 404
    assert public.post("/api/event", json={"state": "paused"}).status_code == 404
    assert public.get("/remote?key=wrong", follow_redirects=False).status_code == 401

    # Scanning the QR signs the phone in.
    r = public.get("/remote?key=rk", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/remote"
    assert public.get("/remote").status_code == 200
    assert public.get("/api/remote").json()["event"]["state"] == "starting"

    # Event controls work...
    assert public.post("/api/event", json={"state": "paused", "title": "Chapel"}).status_code == 200
    assert public.get("/api/remote").json()["event"]["title"] == "Chapel"
    assert public.post("/api/control", json={"action": "open_stream"}).status_code == 200
    # ...the rest does not.
    assert public.post("/api/control", json={"action": "select_backend",
                                              "backend": "mock"}).status_code == 403
    assert public.get("/api/status").status_code == 404
    assert public.get("/status").status_code == 404
    assert public.post("/api/remote/reset").status_code == 404

    # The operator token still never works through the tunnel.
    stranger = TestClient(app, base_url=f"https://{TUNNEL}")
    assert stranger.get("/api/remote", headers={"Authorization": "Bearer op"}).status_code == 404

    # Booth: the operator gets the link and can reset it, signing the phone out.
    auth = {"Authorization": "Bearer op"}
    assert local.get("/api/status", headers=auth).json()["relay"]["remote_url"] == \
        f"https://{TUNNEL}/remote?key=rk"
    new = local.post("/api/remote/reset", headers=auth).json()["remote_url"]
    assert "key=rk" not in new and saved["CAPTIONS_REMOTE_TOKEN"] in new
    assert public.get("/api/remote").status_code == 404


if __name__ == "__main__":
    unittest.main()

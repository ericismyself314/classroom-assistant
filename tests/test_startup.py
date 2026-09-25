"""启动路径的轻量状态接口回归测试。"""
import json
import threading
import urllib.request

from backend import api as api_mod
from backend import db


def test_brief_state_does_not_probe_models(monkeypatch):
    sem_id = db.create_semester("2026秋", "2026-09-01", "2027-01-31", is_active=1)
    db.create_course(sem_id, "启动测试课")
    server = api_mod.make_server("127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(api_mod.whisper_worker, "gpu_info", lambda: (_ for _ in ()).throw(AssertionError("不应探测 GPU")))
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/state?brief=1"
        with urllib.request.urlopen(url) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body["has_semester"] is True
        assert body["course_count"] == 1
        assert "gpu" not in body
        assert "disk" not in body
    finally:
        server.shutdown()
        server.server_close()

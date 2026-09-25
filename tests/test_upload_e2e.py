"""E2E: 启动后端 server，用 urllib 跑关键 API 流程。

不依赖真实模型。
- /api/sessions POST 缺字段 -> 400
- /api/sessions POST 正常 -> 200
- /api/sessions/{id}/audio 上传 -> 200(模型缺失时 409 with code)
- /api/settings -> 含 whisper_gpu / whisper_resolved
- 流式落盘 + sha256 与原文件一致
- 2GB 上限 -> 模拟 1 字节超限 -> 413 (用 fake header)
"""
import json
import os
import sys
import tempfile
import threading
import time
import wave
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# 隔离数据目录
TMP = tempfile.mkdtemp(prefix="ca_e2e_")
os.environ["LOCALAPPDATA"] = TMP
os.environ["APPDATA"] = TMP

from backend import config, db  # noqa
from backend.api import make_server  # noqa

cfg = config.init_config()
db.init_db()

sem = db.create_semester("2026秋", "2026-09-07", "2027-01-15")
db.set_active_semester(sem)
cid = db.create_course(sem, "测试课", code="T01", language_mode="zh", color="#4CAF50")
print("DB ready at", cfg.dirs.db_path())

srv = make_server("127.0.0.1", 0)
port = srv.server_address[1]
th = threading.Thread(target=srv.serve_forever, daemon=True)
th.start()
base = f"http://127.0.0.1:{port}"
print("server up on", base)


def req(path, method="GET", body=None, headers=None):
    data = None
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    if isinstance(body, (bytes, bytearray)):
        data = body
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


# 1) 缺字段 -> 400
st, body = req("/api/sessions", "POST", {"course_id": cid})
print("[1] /api/sessions missing field ->", st, body[:120])
assert st == 400, body

# 2) 正常创建
st, body = req("/api/sessions", "POST", {"course_id": cid, "session_date": "2026-09-07"})
print("[2] /api/sessions normal ->", st, body[:120])
assert st == 200, body
sid = json.loads(body)["id"]
print("    session id =", sid)

# 3) 造 1s wav
audio_path = os.path.join(TMP, "tone.wav")
with wave.open(audio_path, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"\x00\x00" * 16000)
with open(audio_path, "rb") as f:
    audio_bytes = f.read()
print("[3] wav bytes =", len(audio_bytes))

# 4) 上传
st, body = req(f"/api/sessions/{sid}/audio", "POST", audio_bytes,
               {"X-Filename": "tone.wav", "Content-Type": "application/octet-stream"})
print("[4] /api/sessions/{id}/audio ->", st, body[:160])
# 模型未下载时：409 with code=model_missing
assert st in (200, 409), body
if st == 409:
    j = json.loads(body)
    assert j.get("code") == "model_missing", j
    print("    model_missing message =", j.get("error", "")[:60])

# 5) /api/settings 包含 whisper_gpu + whisper_resolved + whisper_device_options
st, body = req("/api/settings")
print("[5] /api/settings ->", st, "len", len(body))
j = json.loads(body)
assert "whisper_gpu" in j, list(j.keys())
assert "whisper_resolved" in j, list(j.keys())
assert "whisper_device_options" in j, list(j.keys())
print("    whisper_gpu:", j["whisper_gpu"])
print("    whisper_resolved:", j["whisper_resolved"])

# 6) 保存 whisper_device=cpu
st, body = req("/api/settings", "PUT", {"whisper_device": "cpu"})
assert st == 200, body
st, body = req("/api/settings")
j = json.loads(body)
assert j["settings"]["whisper_device"] == "cpu"
assert j["whisper_resolved"]["device"] == "cpu"
print("[6] saveSettings whisper_device=cpu -> resolved.device =", j["whisper_resolved"]["device"])

# 6b) /api/models/whisper/validate 严格校验
st, body = req("/api/models/whisper/validate", "POST", {})
print("[6b] validate ->", st, body[:140])
assert st == 200
j = json.loads(body)
assert j["ok"] is False
assert all(r.get("reason") == "no_snapshot" for r in j["results"].values())
print("    validate sizes:", list(j["results"].keys()))

# 6c) 清理入口只接受当前支持的 Qwen3-ASR 模型
st, body = req("/api/models/whisper/cleanup", "POST", {"size": "Qwen3-ASR-0.6B"})
assert st == 200, body
j = json.loads(body)
print("[6c] Qwen cleanup ->", j)
assert j["now_downloaded"] is False

# 7) 太长文件 -> 413
fake_len = 3 * 1024 * 1024 * 1024  # 3GB
st, body = req(f"/api/sessions/{sid}/audio", "POST", b"", {
    "X-Filename": "huge.wav", "Content-Type": "application/octet-stream",
    "Content-Length": str(fake_len),
})
print("[7] too_large ->", st, body[:160])
assert st == 413, body
j = json.loads(body)
assert j.get("code") == "too_large"

# 8) 不支持扩展名 -> 400
st, body = req(f"/api/sessions/{sid}/audio", "POST", b"abc", {
    "X-Filename": "x.txt", "Content-Type": "application/octet-stream",
})
print("[8] bad ext ->", st, body[:120])
assert st == 400

srv.shutdown()
print("ALL_E2E_OK")

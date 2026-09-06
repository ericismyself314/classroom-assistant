"""Mock-validate whisper_worker.transcribe fallback."""
import os, sys, types, wave, tempfile, importlib, ctypes
if "pytest" in sys.modules:
    import pytest
    pytest.skip("standalone fallback check; run this file directly", allow_module_level=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from backend.ai import whisper_worker as w

# 1) 默认情况：探测 cublas 失败 -> cpu
print("[1] cuda_runtime_ready:", w.cuda_runtime_ready())
print("[1] resolve_device auto:", w.resolve_device("auto")["device"])

# 2) 强制 cuda 探测为 True（monkeypatch ctypes.WinDLL）
w.invalidate_cuda_ready()
orig = ctypes.WinDLL
def fake_loader(name, *a, **k):
    if "cublas" in name:
        class _S:
            def __getattr__(self, item): return lambda *a, **k: 0
        return _S()
    return orig(name, *a, **k)
ctypes.WinDLL = fake_loader
w.invalidate_cuda_ready()
print("[2] after force-true:", w.cuda_runtime_ready(), "->", w.resolve_device("auto")["device"])
# 还原
ctypes.WinDLL = orig
w.invalidate_cuda_ready()
print("[2r] restored:", w.cuda_runtime_ready())

# 3) mock WhisperModel：cuda 抛 cublas 错，cpu 返回 fake 段
class FakeSeg:
    def __init__(self,s,e,t):
        self.start,self.end,self.text=s,e,t
        self.no_speech_prob=0.0; self.avg_logprob=0.0
class FakeInfo: language="zh"
class BoomModel:
    def __init__(self, *a, **k):
        self.dev = k.get("device")
    def transcribe(self, *a, **k):
        if self.dev == "cuda":
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return iter([FakeSeg(0,1.2,"测试一下"), FakeSeg(1.2,2.4,"自动回退 CPU")]), FakeInfo()
    def __del__(self): pass

fw = importlib.import_module("faster_whisper")
fw.WhisperModel = BoomModel

# 静音 wav
tmp = tempfile.mkdtemp(prefix="ca_test_")
fp = os.path.join(tmp, "silence.wav")
with wave.open(fp, "wb") as wv:
    wv.setnchannels(1); wv.setsampwidth(2); wv.setframerate(16000)
    wv.writeframes(b"\x00\x00" * 16000 * 2)
print("[3] wav size:", os.path.getsize(fp))

# 3a) device_pref='cpu' 走 cpu
out, lang, dev = w.transcribe(fp, size="small", language="zh", device_pref="cpu")
print("[3a] CPU path device=", dev, "lang=", lang, "segs=", len(out), "texts=",
      [s["text"] for s in out])

# 3b) 模拟"探测过 -> GPU 失败 -> 兜底 CPU"：直接验证兜底分支
# 我们手动写一个等价循环
fw.WhisperModel = BoomModel
events = []
def on_retry(reason, msg):
    events.append((reason, msg))

# 临时把 _do_transcribe 替换为会抛错版本（不可行，函数是闭包内）
# 改法：调一次 _do_transcribe-like 行为
fake = BoomModel("small", device="cuda", compute_type="float16",
                 download_root=w.models_root(), num_workers=2)
try:
    fake.transcribe(fp)
except Exception as e:
    looks = w._looks_like_cuda_runtime_error(e)
    print("[3b] GPU raised looks_like_cuda=", looks, "msg=", str(e)[:80])
    if looks:
        # 模拟回退
        cpu_model = BoomModel("small", device="cpu", compute_type="int8",
                              download_root=w.models_root(), num_workers=2)
        segs, info = cpu_model.transcribe(fp)
        out = list(segs)
        print("[3b] fallback segs:", [s.text for s in out], "lang=", info.language)
print("DONE")

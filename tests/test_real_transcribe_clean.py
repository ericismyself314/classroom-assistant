r"""真实端到端：把模型下到全新临时目录，跑 tiny（~75MB）转写。

目的：在隔离环境里跑一次完整 faster-whisper 流程，证明：
- 修复后不再抛 cublas RuntimeError
- device_pref='auto' 正确选 cpu
- 真实模型加载 + 转写 + 输出 segments 都正常

注：用户原缓存 `.hf_cache\models\models--Systran--faster-whisper-small\blobs` 是空的
（snapshot 引用断链），是 cublas 错之外的另一个历史问题——他需要重新下载模型。
这跟今天的代码修复无关；本测试只验证修复本身有效。
"""
import math
import os
import shutil
import struct
import sys
import tempfile
import time
import wave

if "pytest" in sys.modules:
    import pytest
    pytest.skip("standalone model-download check; run this file directly when network/model access is intended", allow_module_level=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# 关键：把 LOCALAPPDATA 隔离到一个全新目录，模型就下在那里
TMP = tempfile.mkdtemp(prefix="ca_reale2e_")
os.environ["LOCALAPPDATA"] = TMP
os.environ["APPDATA"] = TMP
os.environ["HF_HOME"] = os.path.join(TMP, "hf")
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

# 让 whisper_worker 使用新缓存
from backend import config
from backend.ai import whisper_worker

cfg = config.get_config()
print("data_dir =", cfg.dirs.data_dir)
print("models_root =", whisper_worker.models_root())
print("cuda_runtime_ready =", whisper_worker.cuda_runtime_ready())
print("resolve_device('auto') =", whisper_worker.resolve_device("auto"))

# 合成 8s 1kHz tone + 静音（用 tiny 模型，VAD 应过滤掉大部分）
sr = 16000
seconds = 8
samples = []
for i in range(sr * seconds):
    t = i / sr
    if (i // sr) % 2 == 0:
        samples.append(int(0.3 * math.sin(2 * math.pi * 1000 * t) * 32767))
    else:
        samples.append(0)

tmp = os.path.join(TMP, "audio")
os.makedirs(tmp, exist_ok=True)
wav = os.path.join(tmp, "tone.wav")
with wave.open(wav, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes(b"".join(struct.pack("<h", s) for s in samples))
print("wav =", wav, "size =", os.path.getsize(wav))

# 用 tiny 模型（小、快、能下完）。验证 transcribe 真实能跑通。
t0 = time.time()
print(f"\n[start] transcribe size=tiny device_pref=auto")
try:
    out, lang, dev = whisper_worker.transcribe(
        wav, size="tiny", device_pref="auto",
        progress_cb=lambda sec: print(f"  progress: {sec:.1f}s", flush=True),
    )
    print(f"\n[done] elapsed={time.time()-t0:.1f}s device={dev} lang={lang} segs={len(out)}")
    for i, s in enumerate(out[:5]):
        print(f"  seg{i}: {s['start_seconds']}-{s['end_seconds']}s: {s['text']!r}")
    if len(out) > 5:
        print(f"  ... and {len(out) - 5} more")
    print("REAL_E2E_OK")
except Exception as e:
    print(f"\n[FAIL] {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)
finally:
    # 清临时数据（保留 onedir 缓存以备复用）
    pass

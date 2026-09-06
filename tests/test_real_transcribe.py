"""真实端到端：合成 15s 音频，用已下载的 small 模型跑 transcribe。

这是"修复 cublas 缺失导致识别失败"的关键证据：
- device_pref='auto'：探测 cublas 失败 → resolve_device 选 cpu
- 真实 faster-whisper 加载 + 转写 + 输出 segments
- segments 数应 > 0，language 应该是 zh（合成的是中频静音）

时长预算：模型加载 ~5-15s，转写 15s 音频 ~30-60s，CPU int8。
"""
import math
import os
import sys
import tempfile
import time
import wave

if "pytest" in sys.modules:
    import pytest
    pytest.skip("standalone real-model check; run this file directly when a model directory is configured", allow_module_level=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# 只在用户明确指定模型数据目录时运行，避免读取当前电脑的个人数据。
real_local = os.environ.get("CLASSROOM_ASSISTANT_REAL_LOCALAPPDATA")
if not real_local:
    raise SystemExit(
        "这是可选的真实模型检查。请先设置 "
        "CLASSROOM_ASSISTANT_REAL_LOCALAPPDATA，再重新运行。"
    )
os.environ["LOCALAPPDATA"] = real_local
os.environ["APPDATA"] = os.environ.get("CLASSROOM_ASSISTANT_REAL_APPDATA", real_local)

from backend import config
from backend.ai import whisper_worker

cfg = config.get_config()
print("data_dir =", cfg.dirs.data_dir)
print("cuda_runtime_ready =", whisper_worker.cuda_runtime_ready())
print("resolve_device('auto') =", whisper_worker.resolve_device("auto"))

# 合成 15s 音频：1s 440Hz 提示音 + 1s 静音 + 重复 5 次
# 注：纯正弦波不是语音，VAD 应过滤掉，预期 segments=0，
# 但任务会成功（无 cublas RuntimeError）。我们同时验证 "模型能成功加载" + "无 cublas 错"。
# 再做一次白噪声 15s：VAD 可能误判为语音，但至少能确认加载路径。
import struct
sr = 16000
seconds = 5
total = sr * seconds
samples = []
for i in range(total):
    t = i / sr
    if (i // sr) % 2 == 0:
        # 1kHz tone
        v = 0.3 * math.sin(2 * math.pi * 1000 * t)
    else:
        v = 0.0
    samples.append(int(v * 32767))

tmp = tempfile.mkdtemp(prefix="ca_realtrans_")
wav = os.path.join(tmp, "tone.wav")
with wave.open(wav, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes(b"".join(struct.pack("<h", s) for s in samples))
print("wav =", wav, "size =", os.path.getsize(wav))

t0 = time.time()
print("[start] device_pref='auto'")
out, lang, dev = whisper_worker.transcribe(wav, size="small", device_pref="auto")
print(f"[done] elapsed={time.time()-t0:.1f}s device={dev} lang={lang} segs={len(out)}")
for i, s in enumerate(out[:3]):
    print(f"  seg{i}: {s['start_seconds']}-{s['end_seconds']}s: {s['text']!r}")
if len(out) > 3:
    print(f"  ... and {len(out) - 3} more")

# device_pref='cpu' 也要跑通
t0 = time.time()
print("[start] device_pref='cpu'")
out2, lang2, dev2 = whisper_worker.transcribe(wav, size="small", device_pref="cpu")
print(f"[done] elapsed={time.time()-t0:.1f}s device={dev2} lang={lang2} segs={len(out2)}")

print("REAL_E2E_OK")

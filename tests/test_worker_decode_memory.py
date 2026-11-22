import json
import os
import subprocess
import sys
import wave


def test_worker_decodes_without_allocating_a_second_full_audio_buffer(tmp_path):
    path = tmp_path / 'synthetic.wav'
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\x00\x00\xff\x7f\x00\x80' * 1000)
    script = r'''
import json, numpy as np, sys
from backend.ai import qwen_worker_service as worker
clip = np.clip
observed = []
def checked_clip(a, lo, hi, *args, **kwargs):
    result = clip(a, lo, hi, *args, **kwargs)
    observed.append(np.shares_memory(a, result))
    return result
np.clip = checked_clip
out = worker._decode(sys.argv[1])
print(json.dumps({'same_buffer': observed, 'samples': out[:3].tolist(), 'count': len(out)}))
'''
    env = dict(os.environ, PYTHONPATH=os.path.join(os.path.dirname(__file__), '..', 'src'))
    result = subprocess.run([sys.executable, '-c', script, str(path)], capture_output=True,
                            text=True, env=env, check=True)
    output = json.loads(result.stdout)
    assert output['same_buffer'] == [True]
    assert output['count'] == 3000
    assert output['samples'] == [0.0, 32767 / 32768, -1.0]

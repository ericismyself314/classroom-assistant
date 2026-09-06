import { useEffect, useState } from 'react';
import { api } from '../api';

type SettingsData = {
  settings: Record<string, string>;
  whisper_tiers: Record<string, { label: string; model_size: string; note: string }>;
  llm_models: Array<{ id: string; display: string; note: string; size_bytes: number }>;
  provider_status: {
    sidecar: { available: boolean; binary: boolean; model: boolean; label: string };
    ollama: { available: boolean; models: string[] };
    openai: { configured: boolean };
  };
  whisper_device_options?: Array<{ value: string; label: string }>;
  whisper_gpu?: {
    available: boolean; runtime_ready: boolean; usable: boolean;
    device: string; count: number; name: string; reason: string;
  };
  whisper_resolved?: {
    device: string; requested: string; used_cuda: boolean; reason: string | null;
  };
};

type ModelStatus = {
  whisper: Record<string, { size: string; downloaded: boolean; label: string; valid?: boolean; reason?: string; detail?: string; missing_files?: string[] }>;
  llm: {
    sidecar: { available: boolean; binary: boolean; model: boolean; label: string };
    ollama: { available: boolean; models: string[] };
    openai: { configured: boolean };
  };
};

type Disk = { data_dir: string; total_bytes: number; audio_bytes: number; models_bytes: number; db_bytes: number };

function fmtMB(b: number): string {
  if (b >= 1024 * 1024 * 1024) return (b / 1024 / 1024 / 1024).toFixed(2) + ' GB';
  if (b >= 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + ' MB';
  if (b >= 1024) return (b / 1024).toFixed(1) + ' KB';
  return b + ' B';
}

const LANG_OPTIONS = [
  { v: 'auto', label: '自动检测（推荐）' },
  { v: 'zh', label: '中文' },
  { v: 'en', label: 'English' },
  { v: 'yue', label: '粤语' },
];

const TZ_OPTIONS = [
  { v: 'Asia/Hong_Kong', label: '香港 (UTC+8)' },
  { v: 'Asia/Shanghai', label: '北京/上海 (UTC+8)' },
  { v: 'Asia/Taipei', label: '台北 (UTC+8)' },
  { v: 'Asia/Tokyo', label: '东京 (UTC+9)' },
  { v: 'Asia/Singapore', label: '新加坡 (UTC+8)' },
  { v: 'America/New_York', label: '纽约 (UTC-5)' },
  { v: 'Europe/London', label: '伦敦 (UTC+0)' },
];

export function Settings() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [disk, setDisk] = useState<Disk | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [apiKey, setApiKey] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const [pollTimer, setPollTimer] = useState<number | null>(null);

  const flash = (kind: 'ok' | 'err', text: string) => {
    setMsg({ kind, text });
    window.setTimeout(() => setMsg(null), 4000);
  };

  const load = async () => {
    try {
      const [s, m, st] = await Promise.all([api.settings(), api.modelStatus(), api.state()]);
      setData(s);
      setStatus(m);
      setDisk(st.disk);
      setForm({ ...s.settings });
    } catch (e: any) {
      flash('err', '加载设置失败：' + (e?.message || e));
    }
  };

  useEffect(() => {
    load();
    return () => {
      if (pollTimer) window.clearInterval(pollTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startPoll = (key: string) => {
    // 轮询直到对应状态翻转
    if (pollTimer) window.clearInterval(pollTimer);
    const t = window.setInterval(async () => {
      try {
        const m = await api.modelStatus();
        setStatus(m);
        const whisperReady = !!m.whisper.balanced?.downloaded;
        const llmReady = m.llm.sidecar.binary && m.llm.sidecar.model;
        const done = key === 'whisper' ? whisperReady : key === 'llm' ? llmReady : whisperReady && llmReady;
        if (done) {
          window.clearInterval(t);
          setPollTimer(null);
          setBusy(null);
          flash('ok', '模型已就绪');
        }
      } catch {
        /* ignore */
      }
    }, 2000);
    setPollTimer(t);
  };

  const save = async () => {
    setBusy('save');
    try {
      const payload: Record<string, string> = { ...form };
      if (apiKey.trim()) payload.openai_api_key = apiKey.trim();
      await api.saveSettings(payload);
      flash('ok', '设置已保存');
    } catch (e: any) {
      flash('err', '保存失败：' + (e?.message || e));
    } finally {
      setBusy(null);
    }
  };

  const downloadWhisper = async (size: string) => {
    setBusy('whisper:' + size);
    try {
      await api.downloadWhisper(size);
      flash('ok', '语音模型开始下载，请稍候…');
      startPoll('whisper');
    } catch (e: any) {
      flash('err', '下载失败：' + (e?.message || e));
      setBusy(null);
    }
  };

  const downloadLlm = async () => {
    setBusy('llm');
    try {
      await api.downloadLlm();
      flash('ok', '文字模型开始下载，约 2.9 GB，请保持网络畅通…');
      startPoll('llm');
    } catch (e: any) {
      flash('err', '下载失败：' + (e?.message || e));
      setBusy(null);
    }
  };

  const deleteLlm = async () => {
    if (!window.confirm('确认删除已下载的文字模型文件？')) return;
    setBusy('delLlm');
    try {
      await api.deleteLlm();
      flash('ok', '已删除文字模型');
      const m = await api.modelStatus();
      setStatus(m);
    } catch (e: any) {
      flash('err', '删除失败：' + (e?.message || e));
    } finally {
      setBusy(null);
    }
  };

  const importLlm = async (file: File) => {
    setBusy('importLlm');
    try {
      const modelId = form.llm_model_id || data?.llm_models[0]?.id || 'qwen2.5';
      await api.importModel(file, 'auto', modelId);
      flash('ok', '文字模型已导入，正在重新检测…');
      const m = await api.modelStatus();
      setStatus(m);
    } catch (e: any) {
      flash('err', '导入失败：' + (e?.message || e));
    } finally {
      setBusy(null);
    }
  };

  const importWhisper = async (file: File) => {
    setBusy('importWhisper');
    try {
      await api.importModel(file, 'whisper', '');
      flash('ok', '语音模型已导入，正在重新检测…');
      const m = await api.modelStatus();
      setStatus(m);
    } catch (e: any) {
      flash('err', '导入失败：' + (e?.message || e));
    } finally {
      setBusy(null);
    }
  };

  const testWhisper = async () => {
    setBusy('testW');
    try {
      const r = await api.testWhisper();
      if (r.ok) flash('ok', '语音引擎可用' + (r.gpu ? '（GPU 已启用）' : '（CPU 模式）'));
      else flash('err', '语音引擎不可用：' + (r.error || ''));
    } catch (e: any) {
      flash('err', '测试失败：' + (e?.message || e));
    } finally {
      setBusy(null);
    }
  };

  const revalidateWhisper = async () => {
    setBusy('revalidate');
    try {
      const r: any = await api.validateWhisper();
      // 更新 status 以反映新状态
      const m = await api.modelStatus();
      setStatus(m);
      const broken = Object.entries(r.results || {}).filter(([, v]: any) => !v.ok);
      if (broken.length === 0) {
        flash('ok', '所有已下载的语音模型校验通过 ✓');
      } else {
        const labels = broken.map(([s, v]: any) => `${s}（${v.detail || v.reason}）`).join('；');
        flash('err', '以下模型文件缺失或不完整：' + labels);
      }
    } catch (e: any) {
      flash('err', '校验失败：' + (e?.message || e));
    } finally {
      setBusy(null);
    }
  };

  const cleanupBroken = async (size: string) => {
    if (!window.confirm(`将删除 ${size} 的模型缓存（不会影响其他档位），之后会重新下载。确定？`)) return;
    setBusy('cleanup:' + size);
    try {
      const r: any = await api.cleanupWhisper(size);
      flash('ok', `${size} 缓存已清理（${(r.removed || []).length} 个目录）`);
      const m = await api.modelStatus();
      setStatus(m);
    } catch (e: any) {
      flash('err', '清理失败：' + (e?.message || e));
    } finally {
      setBusy(null);
    }
  };

  const testAnalysis = async () => {
    setBusy('testA');
    try {
      const r = await api.testAnalysis();
      if (r.ok) flash('ok', '文字分析可用：' + (r.label || ''));
      else flash('err', r.needs_model ? '尚未下载文字模型，请先下载' : '文字分析不可用：' + (r.error || ''));
    } catch (e: any) {
      flash('err', '测试失败：' + (e?.message || e));
    } finally {
      setBusy(null);
    }
  };

  const prepareRecommended = async () => {
    setBusy('quick');
    try {
      await api.saveSettings({ ...form, ai_provider: 'sidecar', whisper_tier: 'balanced', llm_model_id: 'qwen3-4b-q5_k_m' });
      const tasks: Promise<any>[] = [];
      if (!status?.whisper.balanced?.downloaded) tasks.push(api.downloadWhisper('large-v3-turbo'));
      if (!status?.llm.sidecar.available) tasks.push(api.downloadLlm());
      await Promise.all(tasks);
      flash('ok', '模型已开始下载。保持应用打开，完成后状态会自动更新。');
      startPoll('both');
    } catch (e: any) {
      flash('err', '准备失败：' + (e?.message || e));
      setBusy(null);
    }
  };

  const doBackup = async () => {
    setBusy('backup');
    try {
      // 由后端直接以附件形式返回 ZIP
      const a = document.createElement('a');
      a.href = '/api/backup?download=1';
      a.download = 'classroom-assistant-backup.zip';
      document.body.appendChild(a);
      a.click();
      a.remove();
      flash('ok', '备份已生成并开始下载');
    } catch (e: any) {
      flash('err', '备份失败：' + (e?.message || e));
    } finally {
      setBusy(null);
    }
  };

  const doRestore = async (file: File) => {
    if (!window.confirm('恢复备份将覆盖当前数据，建议先备份。继续？')) return;
    setBusy('restore');
    try {
      const r = await api.restore(file);
      flash('ok', '已恢复备份' + (r?.restored ? `（${r.restored} 项）` : ''));
      load();
    } catch (e: any) {
      flash('err', '恢复失败：' + (e?.message || e));
    } finally {
      setBusy(null);
    }
  };

  if (!data || !status) return <div className="empty">加载设置中…</div>;

  const wt = data.whisper_tiers;
  const provider = form.ai_provider || 'auto';
  const curTier = form.whisper_tier || 'balanced';
  const downloading = busy?.startsWith('whisper') || busy === 'llm';

  return (
    <div>
      <h2 className="page-title">设置</h2>
      <p className="page-sub">所有处理都在本机完成；模型和密钥不会上传。</p>
      {msg && <div className={`hint ${msg.kind === 'err' ? '' : ''}`} style={{ borderColor: msg.kind === 'err' ? '#eccfca' : undefined, background: msg.kind === 'err' ? '#fbeae8' : undefined }}>{msg.text}</div>}

      <div className="card setup-card" style={{ marginBottom: 14 }}>
        <div>
          <h3 style={{ margin: 0 }}>第一次使用，只需准备一次</h3>
          <div className="small muted" style={{ marginTop: 4 }}>
            语音模型 {status.whisper.balanced?.downloaded ? '✓' : '待下载'} · 文字模型 {status.llm.sidecar.available ? '✓' : '待下载'}
          </div>
        </div>
        {(!status.whisper.balanced?.downloaded || !status.llm.sidecar.available) && (
          <button className="primary" disabled={busy !== null} onClick={prepareRecommended}>
            {busy === 'quick' ? '正在启动下载…' : '一键准备推荐模型'}
          </button>
        )}
        {status.whisper.balanced?.downloaded && status.llm.sidecar.available && <span className="badge green">已可处理录音</span>}
      </div>

      {/* 语音模型 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>语音识别（faster-whisper）</h3>
        <label>识别档位</label>
        <div className="row wrap">
          {Object.entries(wt).map(([k, v]) => (
            <button
              key={k}
              className={`${curTier === k ? 'primary' : ''}`}
              onClick={() => setForm({ ...form, whisper_tier: k })}
            >
              {v.label}
            </button>
          ))}
        </div>
        <div className="small muted" style={{ marginTop: 6 }}>
          {wt[curTier]?.note}
        </div>

        <label>语言</label>
        <select value={form.whisper_language || 'auto'} onChange={(e) => setForm({ ...form, whisper_language: e.target.value })}>
          {LANG_OPTIONS.map((o) => (
            <option key={o.v} value={o.v}>{o.label}</option>
          ))}
        </select>

        <label style={{ marginTop: 10 }}>识别设备</label>
        <select
          value={form.whisper_device || 'auto'}
          onChange={(e) => setForm({ ...form, whisper_device: e.target.value })}
        >
          {(data.whisper_device_options || [
            { value: 'auto', label: '自动（GPU 可用时优先，否则 CPU）' },
            { value: 'cuda', label: '优先 GPU（faster-whisper CUDA）' },
            { value: 'cpu', label: '仅 CPU（最稳）' },
          ]).map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <div className="small muted" style={{ marginTop: 6, lineHeight: 1.6 }}>
          {(() => {
            const gpu = data.whisper_gpu;
            const r = data.whisper_resolved;
            if (!gpu) return null;
            const tag = (ok: boolean) => ok
              ? <span className="badge green" style={{ marginRight: 6 }}>已可用</span>
              : <span className="badge red" style={{ marginRight: 6 }}>不可用</span>;
            return (
              <div>
                <div>硬件 GPU：{tag(gpu.available)}{gpu.count > 0 ? `${gpu.count} 个 CUDA 设备` : '未检测到'}</div>
                <div>运行库（cublas/cudnn）：{tag(gpu.runtime_ready)}{gpu.runtime_ready ? '可加载' : '缺失'}</div>
                <div>
                  本次实际生效：{r ? (
                    r.used_cuda
                      ? <b style={{ color: '#2e7d32' }}>GPU（cuda）</b>
                      : <b style={{ color: '#a05a00' }}>CPU（GPU {gpu.usable ? '已被禁用' : '不可用'}：{r.reason || gpu.reason || '—'}）</b>
                  ) : '—'}
                </div>
                {!gpu.runtime_ready && (
                  <div style={{ marginTop: 4 }}>
                    想用 GPU？请额外 <code>pip install nvidia-cublas-cu12 nvidia-cudnn-cu12</code>
                    （PyInstaller 打包版目前不会自动带这些 DLL）。
                  </div>
                )}
              </div>
            );
          })()}
        </div>

        <div className="row wrap" style={{ marginTop: 12 }}>
          <button className="primary" disabled={busy !== null} onClick={() => downloadWhisper(wt[curTier].model_size)}>
            {busy === 'whisper:' + wt[curTier].model_size ? '下载中…' : status.whisper[curTier]?.downloaded ? '重新下载当前档' : '下载语音模型'}
          </button>
          <button disabled={busy !== null} onClick={testWhisper}>测试语音引擎</button>
          <button disabled={busy !== null} onClick={revalidateWhisper}>{busy === 'revalidate' ? '校验中…' : '重新校验模型'}</button>
          <span className="small muted">
            {status.whisper[curTier]?.downloaded
              ? (status.whisper[curTier]?.valid ? '已下载 ✓ 文件完整' : '已下载 ⚠ 但文件不完整')
              : '未下载'}
            {curTier !== 'balanced' && !status.whisper[curTier]?.downloaded ? '（首次处理会自动下载）' : ''}
          </span>
        </div>
        <div className="small" style={{ marginTop: 8 }}>
          各档位状态：
          {Object.entries(status.whisper).map(([k, v]: any) => (
            <span key={k} style={{ marginRight: 10 }}>
              {wt[k]?.label}：
              {v.downloaded ? (v.valid
                ? <span style={{ color: '#2e7d32' }}>✓ 完整</span>
                : <span style={{ color: '#c62828' }} title={v.detail}>⚠ 不完整</span>)
                : <span style={{ color: '#888' }}>— 未下载</span>}
              {v.downloaded && !v.valid && (
                <button
                  className="ghost"
                  style={{ marginLeft: 6, padding: '2px 8px', fontSize: 11 }}
                  disabled={busy !== null}
                  onClick={() => cleanupBroken(v.size)}
                  title={v.detail}
                >
                  清理
                </button>
              )}
            </span>
          ))}
        </div>
        <div className="small muted" style={{ marginTop: 10 }}>
          手动导入（下载失败时的兜底）：把已下载好的 whisper 模型 zip 直接导入。
        </div>
        <label className="btn ghost" style={{ display: 'inline-block', padding: '7px 14px', border: '1px solid var(--line)', borderRadius: 8, cursor: 'pointer', marginTop: 6 }}>
          导入语音模型（zip）
          <input type="file" accept=".zip" style={{ display: 'none' }} disabled={busy !== null}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) importWhisper(f); e.currentTarget.value = ''; }} />
        </label>
      </div>

      {/* 文字模型 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>文字分析（本地大模型）</h3>
        <label>AI 提供方</label>
        <select value={provider} onChange={(e) => setForm({ ...form, ai_provider: e.target.value })}>
          <option value="auto">自动（优先本地 sidecar）</option>
          <option value="sidecar">本机自管 llama.cpp</option>
          <option value="ollama">本机 Ollama</option>
          <option value="openai">OpenAI 兼容 API（可选）</option>
        </select>

        {provider === 'sidecar' && (
          <>
            <label>模型</label>
            <select value={form.llm_model_id || ''} onChange={(e) => setForm({ ...form, llm_model_id: e.target.value })}>
              {data.llm_models.map((m) => (
                <option key={m.id} value={m.id}>{m.display}（{fmtMB(m.size_bytes)}）</option>
              ))}
            </select>
            <div className="row wrap" style={{ marginTop: 12 }}>
              <button className="primary" disabled={busy !== null} onClick={downloadLlm}>
                {busy === 'llm' ? '下载中…（可在任务栏观察）' : status.llm.sidecar.model ? '重新下载模型' : '下载文字模型'}
              </button>
              {status.llm.sidecar.model && <button className="danger" disabled={busy !== null} onClick={deleteLlm}>删除模型</button>}
              <label className="btn ghost" style={{ display: 'inline-block', padding: '7px 14px', border: '1px solid var(--line)', borderRadius: 8, cursor: 'pointer' }}>
                导入模型（.gguf / zip）
                <input type="file" accept=".gguf,.zip" style={{ display: 'none' }} disabled={busy !== null}
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) importLlm(f); e.currentTarget.value = ''; }} />
              </label>
            </div>
            <div className="small muted" style={{ marginTop: 8 }}>
              状态：服务端 {status.llm.sidecar.binary ? '✓' : '—'} · 模型 {status.llm.sidecar.model ? '✓' : '—'}（{status.llm.sidecar.label}）
            </div>
          </>
        )}

        {provider === 'ollama' && (
          <>
            <label>Ollama 模型名（如 qwen2.5:7b）</label>
            <input value={form.ollama_model || ''} onChange={(e) => setForm({ ...form, ollama_model: e.target.value })} placeholder="qwen2.5:7b" />
            <div className="small muted" style={{ marginTop: 8 }}>
              本机 Ollama {status.llm.ollama.available ? '✓ 已检测到' : '未检测到'} {status.llm.ollama.models?.length ? `（${status.llm.ollama.models.join(', ')}）` : ''}
            </div>
          </>
        )}

        {provider === 'openai' && (
          <>
            <label>Base URL</label>
            <input value={form.openai_base_url || ''} onChange={(e) => setForm({ ...form, openai_base_url: e.target.value })} placeholder="https://api.openai.com/v1" />
            <label>模型名</label>
            <input value={form.openai_model || ''} onChange={(e) => setForm({ ...form, openai_model: e.target.value })} placeholder="gpt-4o-mini" />
            <label>API Key（存储于系统 DPAPI，不写明文日志）</label>
            <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={status.llm.openai.configured ? '已保存，留空则不修改' : '输入后保存'} />
            <div className="small muted" style={{ marginTop: 8 }}>注意：使用云服务会离开本机，非默认推荐方式。</div>
          </>
        )}

        <div className="row wrap" style={{ marginTop: 12 }}>
          <button disabled={busy !== null} onClick={testAnalysis}>测试文字分析</button>
        </div>
      </div>

      {/* 数据与隐私 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>数据与隐私</h3>
        {disk && (
          <div className="kv" style={{ marginBottom: 8 }}>
            <div>数据目录</div>
            <div style={{ wordBreak: 'break-all' }}>{disk.data_dir}</div>
            <div>数据库</div>
            <div>{fmtMB(disk.db_bytes)}</div>
            <div>音频</div>
            <div>{fmtMB(disk.audio_bytes)}</div>
            <div>模型</div>
            <div>{fmtMB(disk.models_bytes)}</div>
            <div>合计占用</div>
            <div>{fmtMB(disk.total_bytes)}</div>
          </div>
        )}
        <div className="hint">所有数据保存在本机 %LOCALAPPDATA%/ClassroomAssistant。语音与转写均不上传；密钥经 Windows DPAPI 加密后仅存于本机。</div>
        <div className="row wrap" style={{ marginTop: 10 }}>
          <button disabled={busy !== null} onClick={() => api.openDataFolder()}>打开数据文件夹</button>
          <button className="primary" disabled={busy !== null} onClick={doBackup}>备份数据（ZIP）</button>
          <label className="btn ghost" style={{ display: 'inline-block', padding: '7px 14px', border: '1px solid var(--line)', borderRadius: 8, cursor: 'pointer' }}>
            恢复备份
            <input type="file" accept=".zip" style={{ display: 'none' }} onChange={(e) => { const f = e.target.files?.[0]; if (f) doRestore(f); }} />
          </label>
        </div>
      </div>

      {/* 通用 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>通用</h3>
        <label>时区（用于相对日期解析，如“下周五”）</label>
        <select value={form.timezone || 'Asia/Hong_Kong'} onChange={(e) => setForm({ ...form, timezone: e.target.value })}>
          {TZ_OPTIONS.map((o) => (
            <option key={o.v} value={o.v}>{o.label}</option>
          ))}
        </select>
        <label>日志级别</label>
        <select value={form.log_level || 'INFO'} onChange={(e) => setForm({ ...form, log_level: e.target.value })}>
          <option value="DEBUG">DEBUG（详细排错）</option>
          <option value="INFO">INFO（默认）</option>
          <option value="WARNING">WARNING（仅警告）</option>
        </select>
        <div style={{ marginTop: 14 }}>
          <button className="primary" disabled={busy === 'save'} onClick={save}>保存设置</button>
        </div>
      </div>
    </div>
  );
}

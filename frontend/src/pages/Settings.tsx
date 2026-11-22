import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { localDateISO } from '../localDate';
import { TranslationSettings } from '../components/TranslationSettings';
import { AudioStorageSettings } from '../components/AudioStorageSettings';

type SpeechState = {
  size: string; downloaded: boolean; valid?: boolean; detail?: string; missing_files?: string[];
};
type ModelStatus = {
  progress?: Record<string, { downloading?: boolean; stage?: string; done?: boolean; error?: string | null }>;
  whisper: Record<string, SpeechState>;
  llm: { sidecar: { available: boolean; binary: boolean; model: boolean; label: string } };
};
type SettingsData = {
  settings: Record<string, string>;
  llm_models: Array<{ id: string; display: string; note: string; size_bytes: number }>;
  whisper_device_options: Array<{ value: string; label: string }>;
};
type Disk = { data_dir: string; total_bytes: number; audio_bytes: number; models_bytes: number; db_bytes: number };

function fmtSize(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(0, Math.round(bytes / 1024))} KB`;
}

export function Settings({ navigate }: { navigate: (v: any) => void }) {
  const [data, setData] = useState<SettingsData | null>(null);
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [disk, setDisk] = useState<Disk | null>(null);
  const [storageOpen, setStorageOpen] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [courses, setCourses] = useState<Array<{ id: string; name: string }>>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const pollRef = useRef<number | null>(null);
  const loadId = useRef(0);
  const [settingsVersion, setSettingsVersion] = useState(0);

  const [importCourseId, setImportCourseId] = useState('');
  const [importDate, setImportDate] = useState(localDateISO());
  const [importLang, setImportLang] = useState('zh');
  const [importText, setImportText] = useState('');
  const [importBusy, setImportBusy] = useState(false);

  const flash = (kind: 'ok' | 'err', text: string) => {
    setMessage({ kind, text });
    window.setTimeout(() => setMessage(null), 5000);
  };

  const load = async () => {
    const request = ++loadId.current;
    try {
      const [settingsData, modelState, appState, courseData] = await Promise.all([
        api.settings(), api.modelStatus(), api.state(), api.courses(),
      ]);
      if (request !== loadId.current) return;
      setData(settingsData);
      setStatus(modelState);
      setDisk(appState.disk);
      setForm(settingsData.settings);
      setSettingsVersion(current => current + 1);
      const list = (courseData.courses || []) as Array<{ id: string; name: string }>;
      setCourses(list);
      if (list.length) setImportCourseId((current) => current || list[0].id);
    } catch (error: any) {
      if (request === loadId.current) flash('err', `设置加载失败：${error?.message || error}`);
    }
  };

  useEffect(() => {
    load();
    return () => { loadId.current += 1; if (pollRef.current) window.clearInterval(pollRef.current); };
  }, []);

  const changeDevice = async (value: string) => {
    setBusy('device');
    try {
      await api.saveSettings({ whisper_device: value });
      setForm(old => ({ ...old, whisper_device: value }));
      flash('ok', '语音识别设备已保存');
    } catch (error: any) {
      flash('err', `保存失败：${error?.message || error}`);
    } finally { setBusy(null); }
  };

  const startPoll = (target: 'speech' | 'text' | 'both') => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      try {
        const next = await api.modelStatus();
        setStatus(next);
        const speechReady = !!next.whisper.qwen?.downloaded;
        const textReady = !!(next.llm.sidecar.binary && next.llm.sidecar.model);
        const failed = next.progress?.whisper?.error || next.progress?.llm?.error;
        if (failed) {
          if (pollRef.current) window.clearInterval(pollRef.current);
          pollRef.current = null;
          setBusy(null);
          flash('err', `模型准备失败：${failed}`);
          return;
        }
        const done = target === 'speech' ? speechReady : target === 'text' ? textReady : speechReady && textReady;
        if (done) {
          if (pollRef.current) window.clearInterval(pollRef.current);
          pollRef.current = null;
          setBusy(null);
          flash('ok', '模型已经准备好');
        }
      } catch { /* 下一轮继续 */ }
    }, 2000);
  };

  const prepare = async () => {
    if (!status) return;
    setBusy('prepare');
    try {
      const latest = await api.modelStatus();
      setStatus(latest);
      const tasks: Promise<any>[] = [];
      if (!latest.whisper.qwen?.downloaded) tasks.push(api.downloadWhisper('Qwen3-ASR-0.6B'));
      if (!(latest.llm.sidecar.binary && latest.llm.sidecar.model)) tasks.push(api.downloadLlm());
      await Promise.all(tasks);
      if (!tasks.length) {
        setBusy(null);
        flash('ok', '两个本地模型都已就绪');
      } else {
        flash('ok', '已开始准备模型，请保持应用打开');
        startPoll('both');
      }
    } catch (error: any) {
      setBusy(null);
      flash('err', `准备失败：${error?.message || error}`);
    }
  };

  const testSpeech = async () => {
    setBusy('speech-test');
    try {
      const result = await api.testWhisper();
      if (!result.ok) throw new Error(result.error || '模型无法加载');
      flash('ok', `语音识别可用，实际使用 ${String(result.device || 'CPU').toUpperCase()}`);
    } catch (error: any) {
      flash('err', `语音识别不可用：${error?.message || error}`);
    } finally { setBusy(null); }
  };

  const testText = async () => {
    setBusy('text-test');
    try {
      const result = await api.testAnalysis();
      if (!result.ok) throw new Error(result.error || '模型无法完成最小整理任务');
      flash('ok', `文字整理可用：${result.label}`);
    } catch (error: any) {
      flash('err', `文字整理不可用：${error?.message || error}`);
    } finally { setBusy(null); }
  };

  const downloadSpeech = async () => {
    setBusy('speech-download');
    try {
      await api.downloadWhisper('Qwen3-ASR-0.6B');
      flash('ok', '已开始下载 Qwen3-ASR');
      startPoll('speech');
    } catch (error: any) {
      setBusy(null);
      flash('err', `下载失败：${error?.message || error}`);
    }
  };

  const downloadText = async () => {
    setBusy('text-download');
    try {
      await api.downloadLlm();
      flash('ok', '已开始下载本地文字模型');
      startPoll('text');
    } catch (error: any) {
      setBusy(null);
      flash('err', `下载失败：${error?.message || error}`);
    }
  };

  const importTextModel = async (file: File) => {
    setBusy('text-import');
    try {
      await api.importModel(file, 'llm', 'qwen3-4b-q5_k_m');
      flash('ok', '文字模型导入成功');
      setStatus(await api.modelStatus());
    } catch (error: any) {
      flash('err', `导入失败：${error?.message || error}`);
    } finally { setBusy(null); }
  };

  const deleteTextModel = async () => {
    if (!window.confirm('确认删除本地文字整理模型？课堂数据不会被删除。')) return;
    setBusy('text-delete');
    try {
      await api.deleteLlm();
      flash('ok', '文字整理模型已删除');
      setStatus(await api.modelStatus());
    } catch (error: any) {
      flash('err', `删除失败：${error?.message || error}`);
    } finally { setBusy(null); }
  };

  const backup = () => {
    const link = document.createElement('a');
    link.href = '/api/backup?download=1';
    link.download = 'classroom-assistant-backup.zip';
    document.body.appendChild(link);
    link.click();
    link.remove();
    flash('ok', '备份已生成');
  };

  const restore = async (file: File) => {
    if (!window.confirm('恢复备份会覆盖当前课堂数据，确定继续？')) return;
    setBusy('restore');
    try {
      await api.restore(file);
      flash('ok', '备份已恢复');
      await load();
    } catch (error: any) {
      flash('err', `恢复失败：${error?.message || error}`);
    } finally { setBusy(null); }
  };

  const importTranscript = async () => {
    if (!importCourseId || !importText.trim()) {
      flash('err', '请选择课程并粘贴文字');
      return;
    }
    setImportBusy(true);
    try {
      const course = courses.find((item) => item.id === importCourseId);
      const session = await api.createSession({
        course_id: importCourseId, session_date: importDate || localDateISO(),
        title: course?.name || '文字导入',
      });
      await api.importTranscript(session.id, { text: importText, language: importLang });
      setImportText('');
      navigate({ name: 'session', id: session.id });
    } catch (error: any) {
      flash('err', `文字导入失败：${error?.message || error}`);
    } finally { setImportBusy(false); }
  };

  if (!data || !status) return <div className="empty">{message?.kind === 'err'
    ? <><p>{message.text}</p><button onClick={load}>重试</button></>
    : '正在读取本地状态…'}</div>;
  const speech = status.whisper.qwen;
  const textReady = status.llm.sidecar.binary && status.llm.sidecar.model;
  const allReady = !!speech?.downloaded && textReady;
  const textModel = data.llm_models.find((item) => item.id === 'qwen3-4b-q5_k_m') || data.llm_models[0];

  return (
    <div className="settings-page">
      <h2 className="page-title">设置</h2>
      <p className="page-sub">设置转写翻译方式、语音识别设备和课堂备份。</p>

      {message && <div className={`hint ${message.kind === 'err' ? 'settings-error' : ''}`} role={message.kind === 'err' ? 'alert' : 'status'}>{message.text}</div>}

      <TranslationSettings key={settingsVersion} initial={data.settings} disabled={busy === 'restore'} />

      <div className="card setup-card settings-ready-card">
        <div>
          <h3>{allReady ? '本机模型已就绪' : '准备本机模型'}</h3>
          <p>语音识别{speech?.downloaded ? '已就绪' : '待下载'}，文字整理{textReady ? '已就绪' : '待下载'}。</p>
        </div>
        <button className="primary" disabled={busy !== null} onClick={prepare}>
          {busy === 'prepare' ? '正在准备…' : allReady ? '重新检查' : '一键准备'}
        </button>
      </div>

      <div className="settings-model-grid">
        <section className="card model-card">
          <div className="model-card-head">
            <h3>语音识别</h3>
            <span className={`badge ${speech?.downloaded ? 'green' : 'warn'}`}>{speech?.downloaded ? '已就绪' : '待准备'}</span>
          </div>
          <p className="muted">千问在本机将课堂录音转成带时间的原文。</p>
          <div className="settings-fact"><span>模型</span><b>Qwen3-ASR-0.6B</b></div>
          <label htmlFor="speech-device">运行设备</label>
          <select id="speech-device" disabled={busy !== null} value={form.whisper_device || 'auto'} onChange={(event) => void changeDevice(event.target.value)}>
            {data.whisper_device_options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          {!speech?.downloaded && <div className="small settings-detail">{speech?.detail || '模型尚未下载'}</div>}
          <div className="row wrap model-actions">
            {!speech?.downloaded && <button className="primary" disabled={busy !== null} onClick={downloadSpeech}>下载语音模型</button>}
            <button disabled={busy !== null || !speech?.downloaded} onClick={testSpeech}>{busy === 'speech-test' ? '正在加载…' : '测试语音识别'}</button>
            <button disabled={busy !== null} onClick={async () => { const r = await api.validateWhisper(); flash(r.ok ? 'ok' : 'err', r.ok ? '语音模型文件完整' : '语音模型文件不完整'); }}>校验文件</button>
          </div>
          <p className="small muted">第一次测试会加载约 1.8 GB 模型，可能需要几十秒；以后会复用同一服务。</p>
        </section>

        <section className="card model-card">
          <div className="model-card-head">
            <h3>本地文字整理</h3>
            <span className={`badge ${textReady ? 'green' : 'warn'}`}>{textReady ? '已就绪' : '待准备'}</span>
          </div>
          <p className="muted">{form.ai_provider === 'openai' ? '当前笔记沿用此前选定的整理 API。下方可以准备和测试本机模型。' : form.ai_provider === 'ollama' ? '当前笔记使用本机 Ollama 整理。下方可以准备和测试内置模型。' : '从课堂转写中整理章节、重点、作业和日期。'}</p>
          <div className="settings-fact"><span>模型</span><b>{textModel?.display || 'Qwen3-4B'}</b></div>
          <div className="settings-fact"><span>大小</span><b>{textModel ? fmtSize(textModel.size_bytes) : '约 2.9 GB'}</b></div>
          <div className="row wrap model-actions">
            {!textReady && <button className="primary" disabled={busy !== null} onClick={downloadText}>下载文字模型</button>}
            <button disabled={busy !== null || !textReady} onClick={testText}>{busy === 'text-test' ? '正在整理测试文字…' : '测试本机文字整理'}</button>
          </div>
          <details className="advanced">
            <summary>模型文件管理</summary>
            <div className="row wrap model-actions">
              <label className="btn ghost file-button">导入 .gguf / zip<input type="file" accept=".gguf,.zip" hidden disabled={busy !== null} onChange={(event) => { const file = event.target.files?.[0]; if (file) importTextModel(file); event.currentTarget.value = ''; }} /></label>
              {status.llm.sidecar.model && <button className="danger" disabled={busy !== null} onClick={deleteTextModel}>删除文字模型</button>}
            </div>
          </details>
        </section>
      </div>

      <details className="card settings-section">
        <summary><b>已有文字直接整理</b><span className="muted">跳过语音识别</span></summary>
        <div className="settings-inner">
          <label>课程</label>
          <select value={importCourseId} onChange={(event) => setImportCourseId(event.target.value)}>
            {!courses.length && <option value="">请先添加课程</option>}
            {courses.map((course) => <option key={course.id} value={course.id}>{course.name}</option>)}
          </select>
          <div className="grid cols-2">
            <div><label>日期</label><input type="date" value={importDate} onChange={(event) => setImportDate(event.target.value)} /></div>
            <div><label>原文语言</label><select value={importLang} onChange={(event) => setImportLang(event.target.value)}><option value="zh">中文</option><option value="en">英文</option><option value="auto">自动判断</option></select></div>
          </div>
          <label>课堂文字</label>
          <textarea rows={7} value={importText} onChange={(event) => setImportText(event.target.value)} placeholder="粘贴课堂原文；支持 [mm:ss] 时间戳。" />
          <button className="primary" disabled={importBusy || !courses.length} onClick={importTranscript}>{importBusy ? '正在创建…' : '开始整理'}</button>
        </div>
      </details>

      <details className="card settings-section" onToggle={event => setStorageOpen(event.currentTarget.open)}>
        <summary><b>录音空间与备份</b><span className="muted">压缩录音、保留期限和本机备份</span></summary>
        <div className="settings-inner">
          {storageOpen && <AudioStorageSettings formatSize={fmtSize} onFinished={() => {
            api.state().then(result => setDisk(result.disk)).catch(() => {});
          }} />}
          {disk && <div className="kv"><div>数据目录</div><div className="path-value">{disk.data_dir}</div><div>课堂音频</div><div>{fmtSize(disk.audio_bytes)}</div><div>模型</div><div>{fmtSize(disk.models_bytes)}</div><div>总占用</div><div>{fmtSize(disk.total_bytes)}</div></div>}
          <div className="row wrap model-actions"><button onClick={() => api.openDataFolder()}>打开数据文件夹</button><button onClick={backup}>导出备份</button><label className="btn ghost file-button">恢复备份<input type="file" accept=".zip" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) restore(file); event.currentTarget.value = ''; }} /></label></div>
        </div>
      </details>

    </div>
  );
}

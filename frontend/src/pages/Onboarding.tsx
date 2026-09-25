import { useEffect, useState } from 'react';
import { api } from '../api';

interface DraftSlot { weekday: number; start_time: string; end_time: string; location: string }
interface DraftCourse {
  name: string; code: string; teacher_name: string; location: string;
  language_mode: string; color: string; slots: DraftSlot[];
}

const WEEK = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const COLORS = ['#4CAF50', '#2f6bff', '#b7791f', '#9c27b0', '#e0533d', '#00897b'];

function initialSemester() {
  const now = new Date();
  const year = now.getFullYear();
  const autumn = now.getMonth() >= 6;
  return {
    name: `${year}${autumn ? ' 秋季' : ' 春季'}学期`,
    start_date: `${year}-${autumn ? '08-01' : '02-01'}`,
    end_date: autumn ? `${year + 1}-01-31` : `${year}-07-31`,
    timezone: 'Asia/Shanghai',
  };
}

export function Onboarding({ onDone }: { onDone: () => void }) {
  const [sem, setSem] = useState(initialSemester);
  const [courses, setCourses] = useState<DraftCourse[]>([{
    name: '', code: '', teacher_name: '', location: '', language_mode: 'auto', color: COLORS[0],
    slots: [{ weekday: 0, start_time: '10:00', end_time: '11:40', location: '' }],
  }]);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [readiness, setReadiness] = useState<any>(null);
  const [testMsg, setTestMsg] = useState('');
  const [testing, setTesting] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [modelProgress, setModelProgress] = useState<any>(null);

  useEffect(() => {
    api.readiness().then(setReadiness).catch(() => setReadiness({ error: true }));
  }, []);

  useEffect(() => {
    if (!preparing) return;
    const t = window.setInterval(async () => {
      try {
        const [r, m] = await Promise.all([api.readiness(), api.modelStatus()]);
        setReadiness(r); setModelProgress(m.progress || null);
        const failed = m.progress?.whisper?.error || m.progress?.llm?.error;
        if (failed) { setTestMsg(`下载未完成：${failed}`); setPreparing(false); }
        else if (r.whisper_downloaded && r.llm_downloaded) {
          setTestMsg('离线模型已准备完成，现在可以处理录音');
          setPreparing(false);
        }
      } catch { /* 继续等待下一次检测 */ }
    }, 2000);
    return () => window.clearInterval(t);
  }, [preparing]);

  const prepareModels = async () => {
    setPreparing(true); setTestMsg('正在准备离线模型，首次约需下载 4.5 GB，可以先继续填写课程…');
    try {
      await api.saveSettings({ ai_provider: 'sidecar', whisper_device: 'auto', llm_model_id: 'qwen3-4b-q5_k_m' });
      const tasks: Promise<any>[] = [];
      if (!readiness?.whisper_downloaded) tasks.push(api.downloadWhisper('Qwen3-ASR-0.6B'));
      if (!readiness?.llm_downloaded) tasks.push(api.downloadLlm());
      await Promise.all(tasks);
    } catch (e: any) {
      setPreparing(false); setTestMsg(e.message || '模型准备失败，请检查网络后重试');
    }
  };

  const runRealTest = async () => {
    setTesting(true); setTestMsg('');
    try {
      await api.testWhisper();
      await api.testAnalysis();
      setTestMsg('真实测试通过：转写与分析样例均正常');
    } catch (e: any) {
      setTestMsg(e.message || '真实测试失败');
    } finally { setTesting(false); }
  };

  const updateCourse = (i: number, patch: Partial<DraftCourse>) =>
    setCourses((cs) => cs.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const updateSlot = (ci: number, si: number, patch: Partial<DraftSlot>) =>
    setCourses((cs) => cs.map((c, j) => j === ci ? {
      ...c, slots: c.slots.map((s, k) => (k === si ? { ...s, ...patch } : s)),
    } : c));
  const addSlot = (ci: number) => setCourses((cs) => cs.map((c, j) => j === ci ? {
    ...c, slots: [...c.slots, { weekday: 0, start_time: '14:00', end_time: '15:40', location: '' }],
  } : c));
  const removeSlot = (ci: number, si: number) => setCourses((cs) => cs.map((c, j) => j === ci ? {
    ...c, slots: c.slots.filter((_, k) => k !== si),
  } : c));

  const submit = async () => {
    setErr('');
    if (!sem.name || !sem.start_date || !sem.end_date) { setErr('请填写学期名称与起止日期'); return; }
    const valid = courses.filter((c) => c.name.trim());
    if (!valid.length) { setErr('请至少添加一门课程'); return; }
    setBusy(true);
    try {
      const r = await api.createSemester({ ...sem, activate: true });
      for (const c of valid) {
        const slots = c.slots.map((s) => ({
          weekday: s.weekday, start_time: s.start_time, end_time: s.end_time, location: s.location || c.location || null,
          recurrence_start: sem.start_date, recurrence_end: sem.end_date,
        }));
        await api.createCourse({
          semester_id: r.id, name: c.name.trim(), code: c.code || null,
          teacher_name: c.teacher_name || null, location: c.location || null,
          language_mode: c.language_mode, color: c.color, slots,
        });
      }
      onDone();
    } catch (e: any) {
      setErr(e.message || '创建失败');
    } finally { setBusy(false); }
  };

  return (
    <div className="content" style={{ maxWidth: 720, margin: '0 auto' }}>
      <div className="welcome-hero">
        <span className="welcome-mark">课</span>
        <div><h1 className="page-title">欢迎使用课堂助手</h1>
          <p className="page-sub">设置一门课就能开始。以后只需：点课程、传录音、看重点。</p></div>
      </div>

      <div className="card">
        <h3>1. 学期</h3>
        <label>学期名称</label>
        <input value={sem.name} onChange={(e) => setSem({ ...sem, name: e.target.value })} />
        <div className="grid cols-2">
          <div><label>开始日期</label><input type="date" value={sem.start_date} onChange={(e) => setSem({ ...sem, start_date: e.target.value })} /></div>
          <div><label>结束日期</label><input type="date" value={sem.end_date} onChange={(e) => setSem({ ...sem, end_date: e.target.value })} /></div>
        </div>
        <details className="advanced"><summary>高级设置</summary>
          <label>时区</label>
          <input value={sem.timezone} onChange={(e) => setSem({ ...sem, timezone: e.target.value })} />
        </details>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3>2. 课程与每周课表</h3>
        {courses.map((c, i) => (
          <div key={i} style={{ borderTop: i ? '1px solid var(--line)' : 'none', paddingTop: i ? 12 : 0, marginTop: i ? 12 : 0 }}>
            <div className="row">
              <input style={{ flex: 2 }} placeholder="课程名称（必填）" value={c.name} onChange={(e) => updateCourse(i, { name: e.target.value })} />
              <input style={{ flex: 1 }} placeholder="课程代码" value={c.code} onChange={(e) => updateCourse(i, { code: e.target.value })} />
              <input style={{ width: 60 }} type="color" value={c.color} onChange={(e) => updateCourse(i, { color: e.target.value })} />
            </div>
            <div className="grid cols-2">
              <input placeholder="老师姓名" value={c.teacher_name} onChange={(e) => updateCourse(i, { teacher_name: e.target.value })} />
              <input placeholder="默认教室" value={c.location} onChange={(e) => updateCourse(i, { location: e.target.value })} />
            </div>
            <label>课程语言</label>
            <select value={c.language_mode} onChange={(e) => updateCourse(i, { language_mode: e.target.value })}>
              <option value="auto">自动识别</option>
              <option value="zh">中文</option>
              <option value="en">英文</option>
              <option value="mix">中英混合</option>
            </select>
            <label>每周上课时间</label>
            {c.slots.map((s, si) => (
              <div className="row" key={si} style={{ marginBottom: 6 }}>
                <select style={{ width: 90 }} value={s.weekday} onChange={(e) => updateSlot(i, si, { weekday: Number(e.target.value) })}>
                  {WEEK.map((w, wi) => <option key={wi} value={wi}>{w}</option>)}
                </select>
                <input style={{ width: 90 }} type="time" value={s.start_time} onChange={(e) => updateSlot(i, si, { start_time: e.target.value })} />
                <input style={{ width: 90 }} type="time" value={s.end_time} onChange={(e) => updateSlot(i, si, { end_time: e.target.value })} />
                <input style={{ flex: 1 }} placeholder="教室" value={s.location} onChange={(e) => updateSlot(i, si, { location: e.target.value })} />
                <button className="ghost danger" onClick={() => removeSlot(i, si)} disabled={c.slots.length === 1}>✕</button>
              </div>
            ))}
            <button className="ghost" onClick={() => addSlot(i)}>+ 增加一次课</button>
          </div>
        ))}
        <button className="ghost" style={{ marginTop: 10 }} onClick={() => setCourses([...courses, {
          name: '', code: '', teacher_name: '', location: '', language_mode: 'auto', color: COLORS[courses.length % COLORS.length],
          slots: [{ weekday: 0, start_time: '10:00', end_time: '11:40', location: '' }],
        }])}>+ 增加一门课程</button>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3>3. 准备离线处理能力</h3>
        {!readiness && <p className="page-sub">正在检测本机运行环境…</p>}
        {readiness?.error && <div className="badge red">无法连接后端，请确认应用已正常启动后重试</div>}
        {readiness && !readiness.error && (
          <>
            <ReadinessRow label={`语音识别模型（${readiness.whisper_model_size || 'Qwen3-ASR-0.6B'}）`} ok={!!readiness.whisper_downloaded} detail={readiness.whisper_downloaded ? '已就绪' : '尚未下载或文件不完整（可在「设置 → 模型」中下载）'} />
            <ReadinessRow label={`文字分析模型（${readiness.llm_model_label || 'Qwen3-4B (Q5_K_M)'}）`} ok={!!readiness.llm_downloaded} detail={readiness.llm_downloaded ? '已就绪' : '尚未下载或文件不完整（可在「设置 → 模型」中下载）'} />
            <ReadinessRow label="WebView2 运行环境" ok={!!readiness.webview2?.ok} detail={readiness.webview2?.detail || '未知'} />
            <ReadinessRow label="GPU 加速" ok={!!readiness.gpu?.available} detail={readiness.gpu?.name || (readiness.gpu?.available ? '可用' : '不可用，将使用 CPU（较慢）')} />
            {(!readiness.whisper_downloaded || !readiness.llm_downloaded) && (
              <button className="primary big-action" disabled={preparing} onClick={prepareModels}>
                {preparing ? '正在下载，请保持窗口打开…' : '一键准备离线模型（推荐）'}
              </button>
            )}
            {preparing && <div className="small muted" style={{ marginTop: 8 }}>
              {modelProgress?.llm?.stage || modelProgress?.whisper?.stage || '正在连接下载服务…'}
            </div>}
            <p className="page-sub" style={{ marginTop: 10 }}>模型只需下载一次，之后断网也能处理。下载期间可以先完成课程设置。</p>
            <div className="row" style={{ marginTop: 8 }}>
              <button className="ghost" disabled={testing || !readiness.whisper_downloaded || !readiness.llm_downloaded} onClick={runRealTest}>
                {testing ? '测试中…' : '运行真实测试（转写 + 分析样例）'}
              </button>
              {testMsg && <span className="badge" style={{ marginLeft: 8 }}>{testMsg}</span>}
            </div>
          </>
        )}
      </div>

      {err && <div className="badge red" style={{ marginTop: 12 }}>{err}</div>}
      <div className="row" style={{ justifyContent: 'flex-end', marginTop: 16 }}>
        <button className="primary" disabled={busy} onClick={submit}>{busy ? '创建中…' : '完成，进入本周课表'}</button>
      </div>
    </div>
  );
}

function ReadinessRow({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <div className="row" style={{ alignItems: 'center', marginBottom: 8 }}>
      <span className={`badge ${ok ? 'green' : 'warn'}`} style={{ minWidth: 64, justifyContent: 'center' }}>{ok ? '✓ 就绪' : '○ 待配置'}</span>
      <span style={{ fontWeight: 600, minWidth: 120 }}>{label}</span>
      <span className="page-sub" style={{ flex: 1 }}>{detail}</span>
    </div>
  );
}

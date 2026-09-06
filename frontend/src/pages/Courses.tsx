import { useEffect, useState } from 'react';
import { RecognitionSettings, useRecognition } from '../components/RecognitionSettings';
import { api } from '../api';
import { Course, Session, Slot } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { Modal } from '../components/Modal';
import { View } from '../App';
import { QuickGuide } from '../components/QuickGuide';

const WEEK = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

export function Courses({ navigate, courseId }: { navigate: (v: View) => void; courseId?: string }) {
  const [courses, setCourses] = useState<Course[]>([]);
  const [sel, setSel] = useState<string | undefined>(courseId);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [err, setErr] = useState('');
  const [upload, setUpload] = useState<Session | null>(null);
  const [editing, setEditing] = useState<Course | null>(null);

  const loadCourses = () => api.courses().then((r) => {
    setCourses(r.courses);
    if (!sel && r.courses.length) setSel(r.courses[0].id);
  });
  useEffect(() => { loadCourses(); }, []);
  useEffect(() => { if (sel) api.sessions(sel).then((r) => setSessions(r.sessions)); }, [sel]);

  const choose = (id: string) => { setSel(id); navigate({ name: 'courses', courseId: id }); };

  return (
    <div>
      <h1 className="page-title">课程</h1>
      <p className="page-sub">查看课程与历史课堂；新增课堂后可以立即上传录音</p>
      <QuickGuide />
      <div className="grid cols-3">
        <div className="card">
          <h3>课程列表</h3>
          {courses.length === 0 && <div className="muted small">尚无需归档的课程</div>}
          {courses.map((c) => (
            <div key={c.id} className="list-item" style={{ borderLeft: `4px solid ${c.color}`, cursor: 'pointer' }} onClick={() => choose(c.id)}>
              <div><b>{c.name}</b><div className="small muted">{c.code || ''} {c.teacher_name ? '· ' + c.teacher_name : ''}</div></div>
              {sel === c.id && <span className="badge green">当前</span>}
            </div>
          ))}
        </div>
        <div className="card" style={{ gridColumn: 'span 2' }}>
          {!sel && <div className="empty">请选择左侧课程</div>}
          {sel && (
            <>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <h3 style={{ margin: 0 }}>{courses.find((c) => c.id === sel)?.name} · 课堂记录</h3>
                <div className="row">
                  <button onClick={() => setEditing(courses.find((c) => c.id === sel) || null)}>编辑课程</button>
                  <button className="primary" onClick={() => {
                    const d = prompt('课堂日期（YYYY-MM-DD）', new Date().toISOString().slice(0, 10));
                    if (!d) return;
                    api.createSession({ course_id: sel, session_date: d, title: courses.find((c) => c.id === sel)?.name })
                      .then((r) => api.sessionDetail(r.id))
                      .then((r) => setUpload(r.session))
                      .catch((e) => setErr(e.message));
                  }}>新增课堂并上传</button>
                </div>
              </div>
              {sessions.length === 0 && <div className="muted small" style={{ marginTop: 10 }}>还没有课堂记录。点击某次课堂的「上传」开始处理。</div>}
              {sessions.map((s) => (
                <div className="list-item" key={s.id}>
                  <div>
                    <b>{s.session_date}</b> {s.week_number ? `第${s.week_number}周` : ''}
                    <div className="small muted">{s.start_time || ''}–{s.end_time || ''} · <StatusBadge status={s.status} /></div>
                  </div>
                  <div className="row">
                    <button onClick={() => navigate({ name: 'session', id: s.id })}>查看</button>
                    <button className="primary" onClick={() => setUpload(s)}>上传/导入</button>
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
      {upload && <UploadToSession session={upload} courseName={courses.find((c) => c.id === upload.course_id)?.name || ''} onClose={() => setUpload(null)} onDone={(sid) => { setUpload(null); navigate({ name: 'session', id: sid }); }} />}
      {editing && <EditCourse course={editing} onClose={() => setEditing(null)} onSave={() => { setEditing(null); loadCourses(); }} />}
      {err && <div className="badge red">{err}</div>}
    </div>
  );
}

function UploadToSession({ session, courseName, onClose, onDone }: { session: Session; courseName: string; onClose: () => void; onDone: (sid: string) => void }) {
  const recognition = useRecognition(session.course_id);
  const [mode, setMode] = useState<'audio' | 'text'>('audio');
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState('');
  const [lang, setLang] = useState('zh');
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'processing' | 'done'>('idle');
  const [progress, setProgress] = useState({ loaded: 0, total: 0 });
  const [msg, setMsg] = useState('');
  const submit = async () => {
    setMsg('');
    if (mode === 'audio' && !file) { setMsg('先选择一个录音文件'); return; }
    if (mode === 'text' && !text.trim()) { setMsg('先粘贴转写文字'); return; }
    setBusy(true);
    setPhase(mode === 'audio' ? 'uploading' : 'processing');
    setProgress({ loaded: 0, total: file?.size || 0 });
    try {
      if (mode === 'audio') {
        if (!recognition.ready) throw new Error(recognition.error || '课程设置还在加载，请稍候');
        await recognition.save();
        try {
          const r: any = await api.uploadAudio(session.id, file!, (loaded, total) => {
            setProgress({ loaded, total });
            if (total > 0 && loaded >= total) setPhase('processing');
          });
          if (r && r.error) { setMsg(r.error); setBusy(false); setPhase('idle'); return; }
        } catch (e: any) {
          setMsg(e?.message || '上传失败');
          setBusy(false);
          setPhase('idle');
          return;
        }
      } else {
        await api.importTranscript(session.id, { text, language: lang });
        setPhase('processing');
      }
      setPhase('done');
      onDone(session.id);
    } catch (e: any) {
      setMsg(e?.message || e?.message || '处理失败');
      setBusy(false);
      setPhase('idle');
    }
  };
  const pct = progress.total > 0 ? Math.min(100, Math.round(progress.loaded * 100 / progress.total)) : 0;
  const phaseLabel = phase === 'uploading' ? '上传中…'
    : phase === 'processing' ? '服务器已收到，开始转写与分析…'
    : phase === 'done' ? '完成' : '';
  return (
    <Modal title={`上传课堂 · ${courseName} · ${session.session_date}`} onClose={onClose}
      footer={<><button className="ghost" onClick={onClose}>取消</button><button className="primary" disabled={busy} onClick={submit}>{busy ? '处理中…' : '开始处理'}</button></>}>
      <div className="row" style={{ margin: '8px 0' }}>
        <button className={mode === 'audio' ? 'primary' : ''} onClick={() => setMode('audio')}>上传录音</button>
        <button className={mode === 'text' ? 'primary' : ''} onClick={() => setMode('text')}>导入转写文字</button>
      </div>
      {mode === 'audio'
        ? <div className="drop-zone">
          <b>{file ? file.name : '选择课堂录音'}</b>
          <span>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : '支持 MP3、M4A、WAV、AAC、FLAC、OGG、MP4（单文件 ≤ 2GB）'}</span>
          <input type="file" accept=".mp3,.m4a,.wav,.aac,.flac,.ogg,.mp4" onChange={(e) => { setFile(e.target.files?.[0] || null); setProgress({ loaded: 0, total: e.target.files?.[0]?.size || 0 }); setMsg(''); }} />
        </div>
        : <>
          <label>语言</label>
          <select value={lang} onChange={(e) => setLang(e.target.value)}><option value="zh">中文</option><option value="en">英文</option><option value="auto">自动</option></select>
          <label>转写文字（支持 [mm:ss] 时间戳）</label>
          <textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder="粘贴真实课堂转写；有时间戳也可以一并粘贴。" />
        </>}
      {mode === 'audio' && <RecognitionSettings value={recognition.value} onChange={recognition.setValue} disabled={busy || !recognition.ready} />}
      {recognition.error && <p role="alert">{recognition.error}</p>}
      {(phase === 'uploading' || phase === 'processing') && (
        <div style={{ marginTop: 12 }}>
          <div className="small muted" style={{ marginBottom: 4 }}>{phaseLabel}</div>
          <div style={{ height: 8, background: '#eef0f3', borderRadius: 4, overflow: 'hidden' }}>
            <div style={{
              width: phase === 'processing' ? '100%' : `${pct}%`,
              height: '100%',
              background: phase === 'processing' ? '#2f6bff' : '#7a8fa6',
              transition: 'width 120ms ease',
            }} />
          </div>
          {phase === 'uploading' && (
            <div className="small muted" style={{ marginTop: 4 }}>
              {(progress.loaded / 1024 / 1024).toFixed(1)} / {(progress.total / 1024 / 1024).toFixed(1)} MB · {pct}%
            </div>
          )}
          {phase === 'processing' && (
            <div className="small muted" style={{ marginTop: 4 }}>
              已切到后台处理（首次使用会先下载语音模型，耗时较长属正常）
            </div>
          )}
        </div>
      )}
      {msg && <div className="badge red" style={{ marginTop: 10 }}>{msg}</div>}
    </Modal>
  );
}

function EditCourse({ course, onClose, onSave }: { course: Course; onClose: () => void; onSave: () => void }) {
  const [c, setC] = useState<Course>(course);
  const [slots, setSlots] = useState<Slot[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => { api.getCourse(course.id).then((r) => { setSlots(r.slots || []); setLoading(false); }); }, [course.id]);
  const save = async () => {
    await api.updateCourse(course.id, {
      name: c.name, code: c.code, teacher_name: c.teacher_name, location: c.location,
      language_mode: c.language_mode, color: c.color,
    });
    await api.setSlots(course.id, slots.map((s) => ({ ...s, location: s.location || null })));
    onSave();
  };
  return (
    <Modal title="编辑课程" onClose={onClose}
      footer={<><button className="ghost" onClick={onClose}>取消</button><button className="primary" disabled={loading} onClick={save}>保存</button></>}>
      {loading ? <div>加载中…</div> : <>
        <label>课程名称</label><input value={c.name} onChange={(e) => setC({ ...c, name: e.target.value })} />
        <div className="grid cols-2">
          <div><label>课程代码</label><input value={c.code || ''} onChange={(e) => setC({ ...c, code: e.target.value })} /></div>
          <div><label>老师</label><input value={c.teacher_name || ''} onChange={(e) => setC({ ...c, teacher_name: e.target.value })} /></div>
        </div>
        <label>默认教室</label><input value={c.location || ''} onChange={(e) => setC({ ...c, location: e.target.value })} />
        <label>课程语言</label>
        <select value={c.language_mode} onChange={(e) => setC({ ...c, language_mode: e.target.value })}>
          <option value="auto">自动识别</option><option value="zh">中文</option><option value="en">英文</option><option value="mix">中英混合</option>
        </select>
        <label>每周课表</label>
        {slots.map((s, i) => (
          <div className="row" key={i} style={{ marginBottom: 6 }}>
            <select style={{ width: 90 }} value={s.weekday} onChange={(e) => setSlots(slots.map((x, j) => j === i ? { ...x, weekday: Number(e.target.value) } : x))}>
              {WEEK.map((w, wi) => <option key={wi} value={wi}>{w}</option>)}
            </select>
            <input style={{ width: 90 }} type="time" value={s.start_time} onChange={(e) => setSlots(slots.map((x, j) => j === i ? { ...x, start_time: e.target.value } : x))} />
            <input style={{ width: 90 }} type="time" value={s.end_time} onChange={(e) => setSlots(slots.map((x, j) => j === i ? { ...x, end_time: e.target.value } : x))} />
            <input style={{ flex: 1 }} placeholder="教室" value={s.location || ''} onChange={(e) => setSlots(slots.map((x, j) => j === i ? { ...x, location: e.target.value } : x))} />
            <button className="ghost danger" onClick={() => setSlots(slots.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
        <button className="ghost" onClick={() => setSlots([...slots, { weekday: 0, start_time: '10:00', end_time: '11:40', location: '' }])}>+ 增加一次课</button>
      </>}
    </Modal>
  );
}

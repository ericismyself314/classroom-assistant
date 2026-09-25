import { useEffect, useState } from 'react';
import { api } from '../api';
import { Course, Session, Slot } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { Modal } from '../components/Modal';
import { View } from '../App';
import { QuickGuide } from '../components/QuickGuide';
import { localDateISO } from '../localDate';

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
      <p className="page-sub">直接录音或上传已有音频，处理方式可在提交前选择</p>
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
                    const d = prompt('课堂日期（YYYY-MM-DD）', localDateISO());
                    if (!d) return;
                    navigate({ name: 'audio', courseId: sel, date: d, title: courses.find((c) => c.id === sel)?.name });
                  }}>新增课堂 · 录音/上传</button>
                </div>
              </div>
              {sessions.length === 0 && <div className="muted small" style={{ marginTop: 10 }}>还没有课堂记录。点击右上角“新增课堂 · 录音/上传”开始。</div>}
              {sessions.map((s) => (
                <div className="list-item" key={s.id}>
                  <div>
                    <b>{s.session_date}</b> {s.week_number ? `第${s.week_number}周` : ''}
                    <div className="small muted">{s.start_time || ''}–{s.end_time || ''} · <StatusBadge status={s.status} /></div>
                  </div>
                  <div className="row">
                    <button onClick={() => navigate({ name: 'session', id: s.id })}>查看</button>
                    <button className="primary" onClick={() => navigate({ name: 'audio', courseId: s.course_id, sessionId: s.id, date: s.session_date, title: s.title || courses.find((c) => c.id === s.course_id)?.name, source: 'microphone' })}>电脑录音</button>
                    <button className="ghost" onClick={() => setUpload(s)}>导入文字</button>
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
      {upload && <ImportTranscriptToSession session={upload} courseName={courses.find((c) => c.id === upload.course_id)?.name || ''} onClose={() => setUpload(null)} onDone={(sid) => { setUpload(null); navigate({ name: 'session', id: sid }); }} />}
      {editing && <EditCourse course={editing} onClose={() => setEditing(null)} onSave={() => { setEditing(null); loadCourses(); }} />}
      {err && <div className="badge red">{err}</div>}
    </div>
  );
}

function ImportTranscriptToSession({ session, courseName, onClose, onDone }: { session: Session; courseName: string; onClose: () => void; onDone: (sid: string) => void }) {
  const [text, setText] = useState('');
  const [lang, setLang] = useState('zh');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submit = async () => {
    if (!text.trim()) { setError('请先粘贴课堂转写文字。'); return; }
    setBusy(true);
    setError('');
    try {
      await api.importTranscript(session.id, { text, language: lang });
      onDone(session.id);
    } catch (e: any) {
      setError(e?.message || '导入失败');
      setBusy(false);
    }
  };
  return (
    <Modal title={`导入转写文字 · ${courseName} · ${session.session_date}`} onClose={() => { if (!busy) onClose(); }}
      footer={<><button className="ghost" disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy || !text.trim()} onClick={submit}>{busy ? '正在提交…' : '导入并整理'}</button></>}>
      <p className="small muted">已有课堂文字稿时可直接导入；上传录音或使用麦克风，请前往“录音与导入”。</p>
      <label>文字语言</label>
      <select value={lang} disabled={busy} onChange={(e) => setLang(e.target.value)}><option value="zh">中文</option><option value="en">英文</option><option value="auto">自动识别</option></select>
      <label>课堂转写文字（支持 [mm:ss] 时间戳）</label>
      <textarea rows={10} value={text} disabled={busy} onChange={(e) => setText(e.target.value)} placeholder="粘贴真实课堂转写；有时间戳也可以一并粘贴。" />
      {error && <p role="alert" className="quality-warning">{error}</p>}
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

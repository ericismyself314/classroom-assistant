import { useEffect, useState } from 'react';
import { api } from '../api';
import { Course, Semester, Session, Slot } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { Modal } from '../components/Modal';
import { View } from '../App';
import { localDateISO } from '../localDate';

const WEEK = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

export function Courses({ navigate, courseId }: { navigate: (v: View) => void; courseId?: string }) {
  const [courses, setCourses] = useState<Course[]>([]);
  const [sel, setSel] = useState<string | undefined>(courseId);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [err, setErr] = useState('');
  const [upload, setUpload] = useState<Session | null>(null);
  const [editing, setEditing] = useState<Course | 'new' | null>(null);

  const loadCourses = () => api.courses().then((r) => {
    setCourses(r.courses);
    if (!sel && r.courses.length) setSel(r.courses[0].id);
  });
  useEffect(() => { loadCourses().catch(e => setErr(e.message)); }, []);
  useEffect(() => { if (courseId) setSel(courseId); }, [courseId]);
  useEffect(() => {
    let live = true;
    setSessions([]);
    if (sel) api.sessions(sel).then(r => { if (live) setSessions(r.sessions); })
      .catch(e => { if (live) setErr(e.message); });
    return () => { live = false; };
  }, [sel]);

  const choose = (id: string) => { setSel(id); navigate({ name: 'courses', courseId: id }); };
  const selectedCourse = courses.find(course => course.id === sel);

  return (
    <div className="courses-page">
      <header className="surface-header"><h1 className="page-title">我的课程</h1><button onClick={() => setEditing('new')}>添加课程</button></header>
      <div className="grid course-history-grid">
        <div className="card">
          <h3>课程列表</h3>
          {courses.length === 0 && <div className="muted small">本学期还没有课程。</div>}
          <div className="course-list">
            {courses.map((c) => {
              const details = [c.code && c.code !== c.name ? c.code : '', c.teacher_name].filter(Boolean).join(' · ');
              return (
                <button key={c.id} type="button" className="course-list-item" aria-pressed={sel === c.id} onClick={() => choose(c.id)}>
                  <span className="course-list-content"><strong>{c.name}</strong>{details && <span className="course-list-details">{details}</span>}</span>
                  {sel === c.id && <span className="course-list-current">当前</span>}
                </button>
              );
            })}
          </div>
        </div>
        <div className="card">
          {!sel && <div className="empty">请选择左侧课程</div>}
          {sel && (
            <>
              <div className="row wrap course-history-heading">
                <h3 style={{ margin: 0 }}>{selectedCourse?.name} · 课堂记录</h3>
                <div className="row">
                  <button onClick={() => setEditing(selectedCourse || null)}>编辑课程</button>
                  <button className="primary" onClick={() => navigate({ name: 'audio', courseId: sel, date: localDateISO(), title: selectedCourse?.name })}>记录课堂</button>
                </div>
              </div>
              {sessions.length === 0 && <div className="muted small">这门课还没有课堂记录。</div>}
              {sessions.map((s) => (
                <div className="course-history-row" key={s.id}>
                  <div>
                    <strong>{s.title && s.title !== selectedCourse?.name ? s.title : `${s.session_date} 课堂`}</strong>
                    <div className="small muted">{[s.title && s.title !== selectedCourse?.name && s.session_date, s.week_number && `第${s.week_number}周`, [s.start_time, s.end_time].filter(Boolean).join('–')].filter(Boolean).join(' · ')} <StatusBadge status={s.status} /></div>
                  </div>
                  <div className="row">
                    <button className="text-button" onClick={() => navigate({ name: 'session', id: s.id })}>{s.status === 'completed' ? '打开笔记' : s.status === 'ready_to_process' ? '查看录音' : '打开课堂'}</button>
                    <button className="ghost" onClick={() => setUpload(s)}>导入文字</button>
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
      {upload && <ImportTranscriptToSession session={upload} courseName={courses.find((c) => c.id === upload.course_id)?.name || ''} onClose={() => setUpload(null)} onDone={(sid) => { setUpload(null); navigate({ name: 'session', id: sid }); }} />}
      {editing && <EditCourse course={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSave={async id => { setEditing(null); await loadCourses(); choose(id); }} />}
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

export function EditCourse({ course, semesterId, onClose, onSave }: { course: Course | null; semesterId?: string; onClose: () => void; onSave: (id: string) => void | Promise<void> }) {
  const [c, setC] = useState(course || { name: '', code: '', teacher_name: '', location: '', language_mode: 'auto', color: '#286747' });
  const [slots, setSlots] = useState<Slot[]>([]);
  const [loading, setLoading] = useState(!!course);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!course) return;
    let live = true;
    setError(''); setLoading(true);
    api.getCourse(course.id).then(r => {
      if (live) { setSlots(r.slots || []); setLoading(false); }
    }).catch(e => { if (live) setError(e.message || '读取课程失败'); });
    return () => { live = false; };
  }, [course, attempt]);
  const save = async () => {
    if (busy || !c.name.trim()) return;
    const invalid = slots.findIndex(s => !s.start_time || !s.end_time || s.end_time <= s.start_time);
    if (invalid >= 0) { setError(`第 ${invalid + 1} 个时段的结束时间须晚于开始时间。`); return; }
    setBusy(true); setError('');
    try {
      const fields = { name: c.name.trim(), code: c.code, teacher_name: c.teacher_name, location: c.location, language_mode: c.language_mode, color: c.color };
      const times = slots.map(s => ({ ...s, location: s.location || null }));
      if (course) {
        await api.updateCourse(course.id, { ...fields, slots: times });
        await onSave(course.id);
      } else {
        const all: Semester[] = semesterId ? [] : (await api.semesters()).semesters;
        const id = semesterId || all.find(s => s.is_active)?.id || all[0]?.id;
        if (!id) throw new Error('请先设置当前学期。');
        const result = await api.createCourse({ ...fields, semester_id: id, slots: times });
        await onSave(result.id);
      }
    } catch (e: any) { setError(e.message || '课程保存失败'); }
    finally { setBusy(false); }
  };
  return (
    <Modal title={course ? '编辑课程' : '添加课程'} onClose={busy ? () => {} : onClose}
      footer={<><button className="ghost" disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={loading || busy || !c.name.trim()} onClick={save}>{busy ? '保存中…' : '保存'}</button></>}>
      {error && <p role="alert" className="quality-warning">{error}</p>}
      {loading ? error ? <button onClick={() => setAttempt(n => n + 1)}>重试</button> : <div>加载中…</div> : <>
        <label htmlFor="course-name">课程名称</label><input id="course-name" value={c.name} onChange={(e) => setC({ ...c, name: e.target.value })} />
        <div className="grid cols-2">
          <div><label htmlFor="course-code">课程代码</label><input id="course-code" value={c.code || ''} onChange={(e) => setC({ ...c, code: e.target.value })} /></div>
          <div><label htmlFor="course-teacher">老师</label><input id="course-teacher" value={c.teacher_name || ''} onChange={(e) => setC({ ...c, teacher_name: e.target.value })} /></div>
        </div>
        <label htmlFor="course-room">默认教室</label><input id="course-room" value={c.location || ''} onChange={(e) => setC({ ...c, location: e.target.value })} />
        <label htmlFor="course-language">课程语言</label>
        <select id="course-language" className="app-select" value={c.language_mode} onChange={(e) => setC({ ...c, language_mode: e.target.value })}>
          <option value="auto">自动识别</option><option value="zh">中文</option><option value="en">英文</option><option value="mix">中英混合</option>
        </select>
        <h4 className="course-times-heading">每周上课时间</h4>
        {slots.map((s, i) => (
          <div className="course-slot-editor" role="group" aria-label={`时段 ${i + 1}`} key={i}>
            <label>星期<select className="app-select" value={s.weekday} onChange={(e) => setSlots(slots.map((x, j) => j === i ? { ...x, weekday: Number(e.target.value) } : x))}>
              {WEEK.map((w, wi) => <option key={wi} value={wi}>{w}</option>)}
            </select></label>
            <label>开始时间<input type="time" value={s.start_time} onChange={(e) => setSlots(slots.map((x, j) => j === i ? { ...x, start_time: e.target.value } : x))} /></label>
            <label>结束时间<input type="time" value={s.end_time} onChange={(e) => setSlots(slots.map((x, j) => j === i ? { ...x, end_time: e.target.value } : x))} /></label>
            <label>教室<input placeholder="默认教室" value={s.location || ''} onChange={(e) => setSlots(slots.map((x, j) => j === i ? { ...x, location: e.target.value } : x))} /></label>
            <button type="button" className="text-button" aria-label={`移除时段 ${i + 1}`} onClick={() => setSlots(slots.filter((_, j) => j !== i))}>移除</button>
          </div>
        ))}
        <button className="ghost" onClick={() => setSlots([...slots, { weekday: 0, start_time: '10:00', end_time: '11:40', location: '' }])}>+ 增加一次课</button>
      </>}
    </Modal>
  );
}

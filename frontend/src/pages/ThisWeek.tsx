import { useEffect, useRef, useState } from 'react';
import { RecognitionSettings, useRecognition } from '../components/RecognitionSettings';
import { api } from '../api';
import { WeekDay, Session, SlotOcc } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { Modal } from '../components/Modal';
import { View } from '../App';
import { QuickGuide } from '../components/QuickGuide';

function shift(d: string, days: number): string {
  const dt = new Date(d + 'T00:00:00');
  dt.setDate(dt.getDate() + days);
  return dt.toISOString().slice(0, 10);
}

export function ThisWeek({ navigate }: { navigate: (v: View) => void }) {
  const [center, setCenter] = useState(new Date().toISOString().slice(0, 10));
  const [week, setWeek] = useState<WeekDay[]>([]);
  const [semName, setSemName] = useState('');
  const [weekHint, setWeekHint] = useState('');
  const [loading, setLoading] = useState(true);
  const autoJumped = useRef(false);
  const [target, setTarget] = useState<{ course_id: string; course_name: string; date: string; start: string; end: string; loc: string | null; sess: Session | null } | null>(null);

  const load = () => {
    setLoading(true);
    api.week(center).then((r) => {
      const start = r.semester?.start_date;
      if (!autoJumped.current && start && center < start) {
        autoJumped.current = true;
        setWeekHint(`学期从 ${start} 开始，已显示第一周`);
        setCenter(start);
        return;
      }
      setWeek(r.week);
      setSemName(r.semester?.name || '');
    })
      .finally(() => setLoading(false));
  };
  useEffect(load, [center]);

  const openLesson = (occ: SlotOcc, date: string) => {
    if (occ.session) { navigate({ name: 'session', id: occ.session.id }); return; }
    setTarget({ course_id: occ.course_id, course_name: occ.course_name, date, start: occ.start_time, end: occ.end_time, loc: occ.location, sess: null });
  };

  return (
    <div>
      <h1 className="page-title">本周</h1>
      <p className="page-sub">{semName} · 选一节课，上传录音，完成后直接看重点</p>
      <QuickGuide />
      <div className="row" style={{ marginBottom: 14 }}>
        <button className="ghost" onClick={() => setCenter(shift(center, -7))}>← 上一周</button>
        <button className="ghost" onClick={() => setCenter(new Date().toISOString().slice(0, 10))}>本周</button>
        <button className="ghost" onClick={() => setCenter(shift(center, 7))}>下一周 →</button>
      </div>
      {weekHint && <div className="small muted" style={{ marginBottom: 10 }}>{weekHint}</div>}
      {loading ? <div className="empty">加载中…</div> : (
        <div className="week">
          {week.map((d) => (
            <div className={`day ${d.is_today ? 'today' : ''}`} key={d.date}>
              <div className="dname">{d.weekday_cn}</div>
              <div className="ddate">{d.date.slice(5)}</div>
              {d.slots.length === 0 && <div className="small muted">无课</div>}
              {d.slots.map((occ, i) => (
                <div className="lesson" key={i} style={{ borderLeftColor: occ.color }} onClick={() => openLesson(occ, d.date)}>
                  <div className="lt">{occ.course_name}</div>
                  <div className="lm">{occ.start_time}–{occ.end_time}{occ.location ? ' · ' + occ.location : ''}</div>
                  <div style={{ marginTop: 4 }}>{occ.session ? <StatusBadge status={occ.session.status} /> : <span className="badge gray">待上传</span>}</div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
      {target && <UploadModal target={target} onClose={() => setTarget(null)} onUploaded={(sid) => { setTarget(null); navigate({ name: 'session', id: sid }); }} />}
    </div>
  );
}

function UploadModal({ target, onClose, onUploaded }: {
  target: any; onClose: () => void; onUploaded: (sid: string) => void;
}) {
  const recognition = useRecognition(target.course_id);
  const [mode, setMode] = useState<'audio' | 'text'>('audio');
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState('');
  const [lang, setLang] = useState('zh');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const doCreate = async () => {
    return api.createSession({
      course_id: target.course_id, session_date: target.date,
      start_time: target.start, end_time: target.end, location: target.loc, title: target.course_name,
    });
  };

  const submit = async () => {
    setMsg('');
    if (mode === 'audio' && !file) { setMsg('先选择一个录音文件'); return; }
    if (mode === 'text' && !text.trim()) { setMsg('先粘贴转写文字'); return; }
    setBusy(true);
    try {
      if (mode === 'audio') {
        if (!recognition.ready) throw new Error(recognition.error || '课程设置还在加载，请稍候');
        await recognition.save();
      }
      const { id } = await doCreate();
      if (mode === 'audio') {
        const r = await api.uploadAudio(id, file!);
        if (r.error) { setMsg(r.error); setBusy(false); return; }
      } else {
        await api.importTranscript(id, { text, language: lang });
      }
      onUploaded(id);
    } catch (e: any) { setMsg(e.message || '处理失败'); setBusy(false); }
  };

  return (
    <Modal title={`上传课堂 · ${target.course_name}`} onClose={onClose}
      footer={<><button className="ghost" onClick={onClose}>取消</button><button className="primary" disabled={busy} onClick={submit}>{busy ? '处理中…' : '开始处理'}</button></>}>
      <div className="muted small">日期 {target.date} · {target.start}–{target.end}</div>
      <div className="row" style={{ margin: '12px 0' }}>
        <button className={mode === 'audio' ? 'primary' : ''} onClick={() => setMode('audio')}>上传录音</button>
        <button className={mode === 'text' ? 'primary' : ''} onClick={() => setMode('text')}>导入转写文字</button>
      </div>
      {mode === 'audio' ? (
        <div className="drop-zone">
          <b>{file ? file.name : '选择课堂录音'}</b>
          <span>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : '支持 MP3、M4A、WAV、AAC、FLAC、OGG、MP4'}</span>
          <input type="file" accept=".mp3,.m4a,.wav,.aac,.flac,.ogg,.mp4" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        </div>
      ) : (
        <>
          <label>语言</label>
          <select value={lang} onChange={(e) => setLang(e.target.value)}>
            <option value="zh">中文</option><option value="en">英文</option><option value="auto">自动</option>
          </select>
          <label>转写文字（支持 [mm:ss] 时间戳）</label>
          <textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder="粘贴真实课堂转写；有时间戳也可以一并粘贴。" />
        </>
      )}
      {mode === 'audio' && <RecognitionSettings value={recognition.value} onChange={recognition.setValue} disabled={busy || !recognition.ready} />}
      {recognition.error && <p role="alert">{recognition.error}</p>}
      {msg && <div className="badge red" style={{ marginTop: 10 }}>{msg}</div>}
    </Modal>
  );
}

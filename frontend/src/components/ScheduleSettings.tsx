import { useEffect, useState } from 'react';
import { api } from '../api';
import type { Course, Semester, TeachingWeek } from '../types';
import { Icon } from './Icon';
import { Modal } from './Modal';

type Props = {
  semester: Semester; teaching: TeachingWeek; onClose: () => void;
  onEdit: (course: Course | 'new') => void; onCalibrated: (week: number) => void;
};

export function ScheduleSettings({ semester, teaching, onClose, onEdit, onCalibrated }: Props) {
  const [number, setNumber] = useState(teaching.today_number > 0 ? String(teaching.today_number) : '');
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [courseError, setCourseError] = useState('');
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    setCourseError('');
    api.courses(semester.id).then(r => { if (live) setCourses(r.courses); })
      .catch(e => { if (live) setCourseError(e.message || '课程读取失败。'); });
    return () => { live = false; };
  }, [semester.id, retry]);

  async function calibrate(event: React.FormEvent) {
    event.preventDefault();
    const n = Number(number);
    if (busy || !Number.isSafeInteger(n) || n < 1) return;
    setBusy(true); setError('');
    try { await api.calibrateWeek(semester.id, teaching.today, n); onCalibrated(n); }
    catch (e) { setError(e instanceof Error ? e.message : '周次保存失败。'); setBusy(false); }
  }

  return <Modal title="调整课表" onClose={() => { if (!busy) onClose(); }}
    footer={<button type="button" className="ghost" disabled={busy} onClick={onClose}>关闭</button>}>
    <form className="week-calibration" onSubmit={calibrate}>
      <div className="calibration-caption"><label htmlFor="today-week">今天是开学第几周？</label>
        <time dateTime={teaching.today}>{teaching.today.replace(/-/g, '/')}</time></div>
      <div className="calibration-entry"><span>第</span><input id="today-week" type="number" min="1" step="1" required
        value={number} disabled={busy} onChange={event => setNumber(event.target.value)} /><span>周</span>
        <button type="submit" className="primary" disabled={busy}>{busy ? '保存中…' : '校准本周'}</button></div>
      {error && <p className="quality-warning" role="alert">{error}</p>}
    </form>
    <div className="schedule-course-heading"><h4>本学期课程</h4>
      <button type="button" className="text-button" disabled={busy} onClick={() => onEdit('new')}><Icon name="plus" width="14" />添加课程</button></div>
    {courseError ? <p role="alert">{courseError} <button onClick={() => setRetry(v => v + 1)}>重试</button></p>
      : courses === null ? <p className="quiet-empty">正在读取课程…</p>
      : !courses.length ? <p className="quiet-empty">还没有课程。</p>
      : <div className="schedule-course-list">{courses.map(c =>
        <button type="button" key={c.id} className="course-list-item" aria-label={`编辑课程 ${c.name}`} disabled={busy} onClick={() => onEdit(c)}>
          <i aria-hidden="true" style={{ backgroundColor: c.color }} /><span className="course-list-content"><strong>{c.name}</strong>
            {c.code && c.code.trim().toLowerCase() !== c.name.trim().toLowerCase() && <small>{c.code}</small>}</span>
          <Icon name="chevron" width="15" />
        </button>)}</div>}
  </Modal>;
}

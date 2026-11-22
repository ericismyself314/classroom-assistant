import { useEffect, useState } from 'react';
import { api } from '../api';
import type { View } from '../App';
import { Icon } from '../components/Icon';
import { StatusBadge } from '../components/StatusBadge';
import { ScheduleSettings } from '../components/ScheduleSettings';
import { EditCourse } from './Courses';
import { localDateISO } from '../localDate';
import { nextOccurrence, timetableDays, timetableRows } from '../schedule';
import type { Course, Semester, Session, SlotOcc, TeachingWeek, WeekDay } from '../types';
import './ThisWeek.css';

function shift(date: string, days: number) {
  const value = new Date(date + 'T00:00:00');
  value.setDate(value.getDate() + days);
  return localDateISO(value);
}

function dateLabel(date: string) {
  const value = new Date(date + 'T00:00:00');
  return `${value.getMonth() + 1}月${value.getDate()}日`;
}

function duration(seconds: number | null) {
  if (seconds == null) return '录音';
  const total = Math.floor(seconds);
  const minutes = Math.floor(total / 60);
  if (!minutes) return `${total} 秒`;
  return minutes >= 60 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分` : `${minutes} 分钟`;
}

type RecentSession = Pick<Session, 'id' | 'title' | 'session_date' | 'location' | 'status' | 'audio_path' | 'audio_duration_seconds'> & { course_name: string };
type ScheduleProps = { days: WeekDay[]; now: Date; navigate: (view: View) => void };

function WeekSchedule({ days, now, navigate }: ScheduleProps) {
  const today = localDateISO(now);
  const next = days[0]?.date <= today && days[days.length - 1]?.date >= today
    ? nextOccurrence(days, today, now.getHours() * 60 + now.getMinutes()) : null;
  const rows = timetableRows(days);
  function lesson(slot: SlotOcc, day: WeekDay) {
    const highlight = next?.slot === slot ? (next.ongoing ? '正在上课' : '下一节') : '';
    return <button type="button" className={`week-lesson${highlight ? ' is-next' : ''}`}
      key={`${slot.course_id}-${slot.start_time}-${slot.end_time}-${slot.location || ''}`}
      aria-label={`${highlight ? highlight + '，' : ''}${day.weekday_cn} ${slot.start_time} 到 ${slot.end_time} ${slot.course_name}${slot.location ? '，' + slot.location : ''}，${slot.session ? '打开课堂记录' : '记录这节课'}`}
      onClick={() => navigate(slot.session ? { name: 'session', id: slot.session.id }
        : { name: 'audio', courseId: slot.course_id, date: day.date, title: slot.course_name, source: 'microphone' })}>
      <span className="week-lesson-heading"><strong>{slot.course_name}</strong>
        {highlight && <span className="week-lesson-when">{highlight}</span>}
      </span>
      <span className="week-lesson-time"><time>{slot.start_time}</time> – <time>{slot.end_time}</time></span>
      {slot.location && <span className="week-lesson-room"><Icon name="pin" width="12" height="12" />{slot.location}</span>}
    </button>;
  }

  if (!rows.length) return <div className="week-empty"><p>这周没有安排课程。</p></div>;
  return <>
    <table className="week-matrix" aria-label="每周课程表">
      <colgroup><col className="week-time-column" />{days.map(day => <col key={day.date} />)}</colgroup>
      <thead><tr><th scope="col">时间</th>{days.map(day => <th scope="col" key={day.date} aria-current={day.date === today ? 'date' : undefined}>
        <span>{day.weekday_cn.replace('星期', '周')}</span><time dateTime={day.date}>{Number(day.date.slice(-2))}</time>
      </th>)}</tr></thead>
      <tbody>{rows.map(time => <tr key={time}><th scope="row">{time}</th>{days.map(day => <td key={day.date}>
        {day.slots.filter(slot => slot.start_time === time).map(slot => lesson(slot, day))}
      </td>)}</tr>)}</tbody>
    </table>
    <div className="week-agenda" aria-label="按天查看课程">{days.filter(day => day.slots.length).map(day => <section key={day.date}>
      <h3 aria-current={day.date === today ? 'date' : undefined}>{day.weekday_cn.replace('星期', '周')}<time dateTime={day.date}>{dateLabel(day.date)}</time></h3>
      <div>{day.slots.map(slot => lesson(slot, day))}</div>
    </section>)}</div>
  </>;
}

export function ThisWeek({ navigate, refreshToken = 0 }: { navigate: (view: View) => void; refreshToken?: number }) {
  const [browsing, setBrowsing] = useState<string | null>(null);
  const [plan, setPlan] = useState<{ week: WeekDay[]; semester: Semester | null; teaching?: TeachingWeek } | null>(null);
  const [adjusting, setAdjusting] = useState(false);
  const [editCourse, setEditCourse] = useState<Course | 'new' | null>(null);
  const [notice, setNotice] = useState('');
  const [now, setNow] = useState(new Date());
  const [recent, setRecent] = useState<RecentSession[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [weekError, setWeekError] = useState('');
  const [recentError, setRecentError] = useState('');
  const [reload, setReload] = useState(0);
  const center = browsing || localDateISO(now);
  const week = plan?.week || [];
  const teaching = plan?.teaching;
  const days = timetableDays(week);
  const choices = new Set<number>();
  if (teaching) {
    for (let n = Math.max(1, teaching.first); n <= teaching.last; n++) choices.add(n);
    if (teaching.today_number > 0) choices.add(teaching.today_number);
    choices.add(teaching.number);
  }
  function refresh(message: string) {
    setNotice(message); setAdjusting(false); setEditCourse(null); setReload(n => n + 1);
  }

  useEffect(() => {
    let live = true;
    setLoading(true); setWeekError('');
    api.week(center).then(result => { if (live) setPlan(result); })
      .catch(e => { if (live) { setPlan(null); setWeekError(e.message || '课表读取失败'); } })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [center, reload, refreshToken]);
  useEffect(() => {
    let live = true;
    setRecentError('');
    api.dashboard().then(result => { if (live) setRecent(result.recent_sessions); })
      .catch(e => { if (live) setRecentError(e.message || '最近课堂读取失败'); });
    return () => { live = false; };
  }, [reload, refreshToken]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  return <div className="overview-page home-week">
    <header className="surface-header">
      <h1 className="page-title">课程总览</h1>
      <button className="primary" onClick={() => navigate({ name: 'audio', source: 'microphone' })}><Icon name="mic" />新建录音</button>
    </header>
    <section className="glass-panel home-schedule" aria-labelledby="schedule-title" aria-busy={loading}>
      <div className="panel-heading schedule-toolbar">
        <div className="week-heading"><h2 id="schedule-title">课表</h2>
          {teaching && <select className="app-select week-picker" aria-label="查看教学周" value={teaching.number} disabled={loading}
            onChange={event => { const n = Number(event.target.value); setNotice('');
              setBrowsing(n === teaching.today_number ? null : shift(week[0].date, 7 * (n - teaching.number))); }}>
            {[...choices].sort((a,b) => a-b).map(n => <option key={n} value={n}>{n > 0 ? `第 ${n} 周` : '开学前'}</option>)}
          </select>}
          {days.length > 0 && <span>{dateLabel(days[0].date)} – {dateLabel(days[days.length - 1].date)}</span>}</div>
        <div className="week-actions"><div className="week-controls">
          <button type="button" aria-label="上一周" onClick={() => { setNotice(''); setBrowsing(v => shift(v || localDateISO(), -7)); }}><Icon name="chevron" width="15" /></button>
          <button type="button" onClick={() => { setNotice(''); setBrowsing(null); }}>本周</button>
          <button type="button" aria-label="下一周" onClick={() => { setNotice(''); setBrowsing(v => shift(v || localDateISO(), 7)); }}><Icon name="chevron" width="15" /></button>
        </div><button type="button" className="schedule-adjust" disabled={!plan?.semester || !teaching || loading} onClick={() => { setNotice(''); setAdjusting(true); }}>
          <Icon name="calendar" width="15" />调整课表</button>
        </div>
      </div>
      {notice && <p className="schedule-notice" role="status">{notice}</p>}
      {weekError ? <p className="dashboard-notice error" role="alert">{weekError} <button onClick={() => setReload(value => value + 1)}>重试</button></p>
        : loading && !week.length ? <p className="quiet-empty">正在读取课表…</p> : <WeekSchedule days={days} now={now} navigate={navigate} />}
    </section>
    {adjusting && plan?.semester && teaching && (editCourse
      ? <EditCourse course={editCourse === 'new' ? null : editCourse} semesterId={plan.semester.id}
          onClose={() => setEditCourse(null)} onSave={() => refresh(editCourse === 'new' ? '课程已添加。' : '课程安排已保存。')} />
      : <ScheduleSettings semester={plan.semester} teaching={teaching} onClose={() => setAdjusting(false)} onEdit={setEditCourse}
          onCalibrated={n => { setBrowsing(null); refresh(`已校准，本周是第 ${n} 周。`); }} />)}
    <section className="glass-panel recent-classes" aria-labelledby="recent-title">
      <div className="panel-heading"><h2 id="recent-title">最近课堂</h2><button className="text-button" onClick={() => navigate({ name: 'recordings' })}>我的录音<Icon name="chevron" width="14" /></button></div>
      {recentError ? <p className="dashboard-notice error" role="alert">{recentError} <button onClick={() => setReload(value => value + 1)}>重试</button></p>
        : recent == null ? <p className="quiet-empty">正在读取课堂…</p> : !recent.length && <p className="quiet-empty">还没有课堂记录。</p>}
      {recent?.map(session => <button className="recent-class-row" key={session.id} onClick={() => navigate({ name: 'session', id: session.id })}>
        <span className="play-disc"><Icon name={session.audio_path ? 'play' : 'file'} /></span>
        <span className="recent-class-copy"><strong>{session.title || session.course_name}</strong><small>{[session.title && session.title !== session.course_name && session.course_name, session.session_date, session.location].filter(Boolean).join(' · ')}</small></span>
        <span className="recent-duration">{session.audio_path ? duration(session.audio_duration_seconds) : ''}</span><StatusBadge status={session.status} /><Icon name="chevron" width="16" />
      </button>)}
    </section>
  </div>;
}

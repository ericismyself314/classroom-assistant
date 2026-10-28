import { useEffect, useState } from 'react';
import { api } from '../api';
import { Course, Item, Session, SlotOcc, WeekDay } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { Icon } from '../components/Icon';
import { View } from '../App';
import { localDateISO } from '../localDate';
import { minutes, nextOccurrence, timetableRows } from '../schedule';

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
  if (seconds == null) return '时长未知';
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
}
type RecentSession = Session & { course_name: string };

export function ThisWeek({ navigate }: { navigate: (view: View) => void }) {
  const [center, setCenter] = useState(localDateISO());
  const [week, setWeek] = useState<WeekDay[]>([]);
  const [now, setNow] = useState(new Date());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [items, setItems] = useState<Item[]>([]);
  const [recent, setRecent] = useState<RecentSession[]>([]);
  const [overviewError, setOverviewError] = useState('');
  const [savingItem, setSavingItem] = useState<string | null>(null);
  const [courses, setCourses] = useState<Course[]>([]);
  const [selected, setSelected] = useState<{ slot: SlotOcc; date: string } | null>(null);
  const today = localDateISO(now);

  useEffect(() => {
    let live = true;
    api.courses().then(result => { if (live) setCourses(result.courses); }).catch(() => {});
    return () => { live = false; };
  }, []);
  useEffect(() => { setSelected(null); }, [center]);
  useEffect(() => {
    let live = true;
    setLoading(true); setError('');
    api.week(center).then(result => { if (live) setWeek(result.week); })
      .catch(e => { if (live) setError(e.message || '课表读取失败'); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [center, reload]);
  useEffect(() => {
    let live = true;
    setOverviewError('');
    api.dashboard().then(result => {
      if (live) { setItems(result.items); setRecent(result.recent_sessions); }
    }).catch(e => { if (live) setOverviewError(e.message || '最近课堂读取失败'); });
    return () => { live = false; };
  }, [reload]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const next = nextOccurrence(week, today, now.getHours() * 60 + now.getMinutes());
  const focused = selected || next;
  const rows = timetableRows(week);
  const openLesson = (slot: SlotOcc, date: string) => slot.session
    ? navigate({ name: 'session', id: slot.session.id })
    : navigate({ name: 'audio', courseId: slot.course_id, date, title: slot.course_name });
  async function completeItem(item: Item) {
    if (savingItem) return;
    setSavingItem(item.id);
    try {
      await api.updateItem(item.id, { is_completed: 1 });
      setItems(current => current.filter(value => value.id !== item.id));
    } catch (e: any) { setOverviewError(e.message || '事项更新失败'); }
    finally { setSavingItem(null); }
  }

  return <div className="overview-page">
    <header className="surface-header">
      <h1 className="page-title">课程总览</h1>
      <div className="row header-actions">
        <button className="overview-search" onClick={() => navigate({ name: 'search' })}><Icon name="search" /><span>搜索课程、录音或笔记…</span></button>
        <button className="primary" onClick={() => navigate({ name: 'audio', source: 'microphone' })}><Icon name="plus" />新建录音</button>
      </div>
    </header>
    {(error || overviewError) && <div className="dashboard-notice error" role="alert">{error || overviewError}<button onClick={() => setReload(value => value + 1)}>重试</button></div>}
    <div className="overview-grid">
      <section className="glass-panel schedule-panel" aria-labelledby="schedule-title" aria-busy={loading}>
        <div className="panel-heading">
          <div className="row wrap"><h2 id="schedule-title">本周课表</h2><span className="muted small">{week.length ? `${dateLabel(week[0].date)}—${dateLabel(week[week.length - 1].date)}` : ''}</span></div>
          <div className="calendar-controls">
            <button className="icon-button previous" aria-label="上一周" onClick={() => setCenter(value => shift(value, -7))}><Icon name="chevron" /></button>
            <button onClick={() => setCenter(localDateISO())}>本周</button>
            <button className="icon-button" aria-label="下一周" onClick={() => setCenter(value => shift(value, 7))}><Icon name="chevron" /></button>
          </div>
        </div>
        <div className="schedule-scroll">
          {loading && !week.length ? <div className="empty">正在读取课表…</div> : <table className="weekly-timetable">
            <caption className="sr-only">{week[0]?.date} 起的一周课表，可点击课程查看课堂或录音</caption>
            <thead><tr><th scope="col"><span className="sr-only">时间</span></th>{week.map(day => <th scope="col" key={day.date} className={day.date === today ? 'is-today' : ''}><span>{day.weekday_cn.replace('星期', '').replace('周', '')}</span><b aria-label={`${day.date}${day.date === today ? '，今天' : ''}`}>{Number(day.date.slice(-2))}</b></th>)}</tr></thead>
            <tbody>{rows.map(time => <tr key={time}><th scope="row">{time}</th>{week.map(day => <td key={day.date} className={day.date === today ? 'is-today' : ''}>
              {day.slots.filter(slot => slot.start_time === time).map((slot, index) => <button aria-pressed={selected?.slot === slot} className={`calendar-lesson ${selected?.slot === slot ? 'is-selected' : ''} ${day.date === today && minutes(slot.start_time) <= now.getHours() * 60 + now.getMinutes() && minutes(slot.end_time) > now.getHours() * 60 + now.getMinutes() ? 'in-progress' : ''}`} key={`${slot.course_id}-${index}`} onClick={() => setSelected({ slot, date: day.date })} title={`${slot.course_name} · ${slot.start_time}—${slot.end_time} · ${slot.location || '地点待定'}`}>
                <strong>{slot.course_name}</strong><span>{slot.location || '地点待定'}</span><small>{slot.start_time}—{slot.end_time}</small>
              </button>)}
              {!day.slots.some(slot => slot.start_time === time) && <span className="calendar-empty" aria-hidden="true">—</span>}
            </td>)}</tr>)}</tbody>
          </table>}
          {!loading && !rows.length && <div className="schedule-empty"><Icon name="calendar" /><p>这一周暂无课程安排</p><button onClick={() => navigate({ name: 'courses' })}>管理课程与课表</button></div>}
        </div>
        <button className="text-button manage-schedule" onClick={() => navigate({ name: 'courses' })}>管理课程与课表<Icon name="chevron" width="14" /></button>
      </section>
      <aside className="overview-rail">
        <section className="glass-panel next-lesson-panel">
          <div className="panel-heading"><h2>{selected ? '已选课程' : next?.ongoing ? '正在上课' : '下一节课'}</h2><Icon name="book" /></div>
          {focused ? <>
            <div className="next-lesson-copy"><h3>{focused.slot.course_name}</h3>
              <p><Icon name="clock" />{focused.date !== today && <span>{dateLabel(focused.date)} · </span>}{focused.slot.start_time}—{focused.slot.end_time}</p>
              <p><Icon name="pin" />{focused.slot.location || '地点待定'}</p>
            </div>
            <button className="course-detail-link" onClick={() => openLesson(focused.slot, focused.date)}>查看课堂<Icon name="chevron" width="14" /></button>
          </> : <div className="quiet-empty"><p>{loading ? '正在读取课程…' : '所显示周内暂无后续课程'}</p><button onClick={() => navigate({ name: 'audio', source: 'microphone' })}><Icon name="mic" />新建录音</button></div>}
        </section>
        <section className="glass-panel course-capture-panel">
          <div className="panel-heading"><h2>记录课堂</h2></div>
          <div className="capture-actions">{(['microphone', 'upload'] as const).map(source => <button key={source} onClick={() => navigate({ name: 'audio', courseId: focused?.slot.course_id, sessionId: focused?.slot.session?.id, date: focused?.date, title: focused?.slot.course_name, source })}><Icon name={source === 'microphone' ? 'mic' : 'upload'} /><span>{source === 'microphone' ? '电脑录音' : '上传录音'}</span></button>)}</div>
        </section>
        <section className="glass-panel overview-tasks">
          <div className="panel-heading"><h2>重要事项</h2><button className="text-button" onClick={() => navigate({ name: 'important' })}>查看全部<Icon name="chevron" width="14" /></button></div>
          {!items.length && <p className="quiet-empty">{overviewError ? '事项暂时无法读取' : '暂无待完成事项'}</p>}
          {items.slice(0, 3).map(item => <div className="overview-task" key={item.id}>
            <input type="checkbox" checked={savingItem === item.id} disabled={savingItem !== null} aria-label={`完成：${item.title}`} onChange={() => completeItem(item)} />
            <button className="task-copy" onClick={() => navigate({ name: 'session', id: item.session_id })}><strong>{item.title}</strong><span>{item.normalized_date ? dateLabel(item.normalized_date) : '日期待确认'}{item.confidence === 'uncertain' || (item.verification_status === 'pending' && !item.is_confirmed) ? ' · 待核对' : ''}</span></button>
          </div>)}
        </section>
      </aside>
      <section className="glass-panel recent-classes">
        <div className="panel-heading"><h2>最近课堂</h2><button className="text-button" onClick={() => navigate({ name: 'courses' })}>查看全部<Icon name="chevron" width="14" /></button></div>
        {!recent.length && <p className="quiet-empty">{overviewError ? '课堂暂时无法读取' : '录制或导入后，课堂记录会显示在这里'}</p>}
        {recent.slice(0, 3).map(session => <button className="recent-class-row" key={session.id} onClick={() => navigate({ name: 'session', id: session.id })}>
          <span className="play-disc"><Icon name={session.audio_path ? 'play' : 'file'} /></span>
          <span className="recent-class-copy"><strong>{session.title || session.course_name}</strong><small>{session.course_name} · {session.session_date}{session.location ? ` · ${session.location}` : ''}</small></span>
          <StatusBadge status={session.status} /><span className="recent-duration">{session.audio_path ? duration(session.audio_duration_seconds) : '文字稿'}</span><Icon name="chevron" width="16" />
        </button>)}
      </section>
      <section className="glass-panel overview-courses">
        <div className="panel-heading"><h2>我的课程</h2><button className="text-button" onClick={() => navigate({ name: 'courses' })}>管理课程<Icon name="chevron" width="14" /></button></div>
        <div className="course-tile-grid">{courses.map(course => <button className="course-tile" key={course.id} onClick={() => navigate({ name: 'audio', courseId: course.id, title: course.name, source: 'microphone' })}><Icon name="book" /><span><strong>{course.name}</strong><small>{course.code || course.location || '查看课程录音'}</small></span><Icon name="chevron" width="14" /></button>)}</div>
        {!courses.length && <button onClick={() => navigate({ name: 'courses' })}>添加课程</button>}
      </section>
    </div>
  </div>;
}

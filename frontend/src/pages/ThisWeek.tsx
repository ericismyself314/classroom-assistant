import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import { WeekDay, SlotOcc } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { View } from '../App';
import { localDateISO } from '../localDate';

function shift(d: string, days: number): string {
  const dt = new Date(d + 'T00:00:00');
  dt.setDate(dt.getDate() + days);
  return localDateISO(dt);
}

function toMinutes(value: string): number {
  const [hour, minute] = value.split(':').map(Number);
  return hour * 60 + minute;
}

function readableDate(date: string): string {
  if (!date) return '';
  const dt = new Date(date + 'T00:00:00');
  return `${dt.getMonth() + 1} 月 ${dt.getDate()} 日`;
}

function greeting(now: Date): string {
  if (now.getHours() < 11) return '早上好';
  if (now.getHours() < 14) return '中午好';
  if (now.getHours() < 18) return '下午好';
  return '晚上好';
}

function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="9" y="2.5" width="6" height="12" rx="3" />
      <path d="M5.5 11v1a6.5 6.5 0 0 0 13 0v-1M12 18.5V22M9 22h6" />
    </svg>
  );
}

export function ThisWeek({ navigate }: { navigate: (v: View) => void }) {
  const [center, setCenter] = useState(localDateISO());
  const [week, setWeek] = useState<WeekDay[]>([]);
  const [semName, setSemName] = useState('');
  const [weekHint, setWeekHint] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [now, setNow] = useState(new Date());
  const autoJumped = useRef(false);
  const [selectedDate, setSelectedDate] = useState(localDateISO());

  const load = () => {
    setLoading(true);
    setLoadError('');
    api.week(center).then((r) => {
      const start = r.semester?.start_date;
      if (!autoJumped.current && start && center < start) {
        autoJumped.current = true;
        setWeekHint(`学期从 ${start} 开始，已显示第一周`);
        setCenter(start);
        setSelectedDate(start);
        return;
      }
      setWeek(r.week);
      setSemName(r.semester?.name || '本学期');
    }).catch((error) => setLoadError(error instanceof Error ? error.message : '课表读取失败'))
      .finally(() => setLoading(false));
  };

  useEffect(load, [center]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const openLesson = (occ: SlotOcc, date: string) => {
    if (occ.session) { navigate({ name: 'session', id: occ.session.id }); return; }
    navigate({ name: 'audio', courseId: occ.course_id, date, title: occ.course_name });
  };

  const addAudio = (occ: SlotOcc, date: string, source: 'microphone' | 'upload') => {
    navigate({
      name: 'audio', courseId: occ.course_id, sessionId: occ.session?.id,
      date, title: occ.course_name, source,
    });
  };

  const selectedDay = week.find((day) => day.date === selectedDate) || week.find((day) => day.is_today) || week[0];
  const sortedSlots = useMemo(
    () => [...(selectedDay?.slots || [])].sort((a, b) => a.start_time.localeCompare(b.start_time)),
    [selectedDay],
  );
  const currentMinute = now.getHours() * 60 + now.getMinutes();
  const currentLesson = selectedDay?.is_today
    ? sortedSlots.find((slot) => currentMinute >= toMinutes(slot.start_time) && currentMinute < toMinutes(slot.end_time))
    : undefined;
  const nextLesson = currentLesson || (selectedDay?.is_today
    ? sortedSlots.find((slot) => currentMinute < toMinutes(slot.start_time))
    : sortedSlots[0]);
  const nextMinutes = nextLesson && selectedDay?.is_today ? toMinutes(nextLesson.start_time) - currentMinute : null;
  const isCurrentWeek = week.some((day) => day.is_today);
  const weekLessonCount = week.reduce((total, day) => total + day.slots.length, 0);

  const shiftWeek = (days: number) => {
    setCenter((current) => shift(current, days));
    setSelectedDate((current) => shift(current, days));
  };

  const lessonState = (occ: SlotOcc) => {
    if (!selectedDay?.is_today) return '';
    const start = toMinutes(occ.start_time);
    const end = toMinutes(occ.end_time);
    if (currentMinute >= start && currentMinute < end) return '正在上课';
    if (currentMinute >= end) return '已结束';
    const diff = start - currentMinute;
    if (diff < 60) return `${diff} 分钟后`;
    return `还有 ${Math.floor(diff / 60)} 小时`;
  };

  if (loading && !week.length) return <div className="empty">正在读取课表…</div>;

  return (
    <div className="today-dashboard">
      <header className="today-topbar">
        <div>
          <div className="today-eyebrow">{semName}</div>
          <h1>{selectedDay?.is_today ? `${greeting(now)}，今天的课程已准备好` : `${readableDate(selectedDay?.date || '')}的课程安排`}</h1>
          <p>{selectedDay ? `${readableDate(selectedDay.date)} · ${selectedDay.weekday_cn}` : ''}</p>
        </div>
        <div className="week-switcher" aria-label="周切换">
          <button type="button" aria-label="上一周" onClick={() => shiftWeek(-7)}>‹</button>
          <span>{week.length ? `${week[0].date.slice(5)} — ${week[week.length - 1].date.slice(5)}` : ''}</span>
          <button type="button" aria-label="下一周" onClick={() => shiftWeek(7)}>›</button>
          {!isCurrentWeek && <button className="back-this-week" type="button" onClick={() => { setCenter(localDateISO()); setSelectedDate(localDateISO()); }}>回到本周</button>}
        </div>
      </header>

      {weekHint && <div className="dashboard-notice">{weekHint}</div>}
      {loadError && <div className="dashboard-notice error">{loadError}<button type="button" onClick={load}>重试</button></div>}

      {nextLesson ? (
        <section className="next-class-hero">
          <div className="next-class-copy">
            <div className="next-class-status">
              <span>{currentLesson ? '正在上课' : '下一节课'}</span>
              {nextMinutes !== null && nextMinutes > 0 && <b>{nextMinutes < 60 ? `${nextMinutes} 分钟后开始` : `${Math.floor(nextMinutes / 60)} 小时后开始`}</b>}
            </div>
            <button type="button" className="hero-course-name" onClick={() => openLesson(nextLesson, selectedDay.date)}>{nextLesson.course_name}</button>
            <div className="next-class-meta">
              <span>◷ {nextLesson.start_time}–{nextLesson.end_time}</span>
              {nextLesson.location && <span>⌖ {nextLesson.location}</span>}
              <span>第 {nextLesson.week_number} 周</span>
            </div>
          </div>
          <button className="record-orb" type="button" onClick={() => addAudio(nextLesson, selectedDay.date, 'microphone')}>
            <MicIcon />
            <strong>开始录音</strong>
            <small>{currentLesson ? '记录当前课程' : '提前进入录音页'}</small>
          </button>
        </section>
      ) : (
        <section className="next-class-hero all-done">
          <div>
            <div className="next-class-status"><span>今日完成</span></div>
            <h2>{selectedDay?.slots.length ? '今天的课程都结束了' : '今天没有课程安排'}</h2>
            <p>可以整理课堂笔记，或者查看本周其他课程。</p>
          </div>
          <button className="hero-secondary" type="button" onClick={() => navigate({ name: 'courses' })}>查看课程</button>
        </section>
      )}

      <div className="schedule-dashboard-grid">
        <section className="today-timeline-card">
          <div className="dashboard-section-heading">
            <div><span>今日安排</span><h2>{selectedDay?.is_today ? '今天的课程' : `${readableDate(selectedDay?.date || '')}的课程`}</h2></div>
            <b>{sortedSlots.length} 节</b>
          </div>
          <nav className="day-picker" aria-label="选择日期">
            {week.map((day) => (
              <button key={day.date} type="button" className={selectedDay?.date === day.date ? 'active' : ''}
                aria-pressed={selectedDay?.date === day.date} onClick={() => setSelectedDate(day.date)}>
                <span>{day.weekday_cn}</span><b>{day.date.slice(-2)}</b>
              </button>
            ))}
          </nav>
          <div className="timeline-list-new">
            {!sortedSlots.length ? <div className="no-lessons">这一天没有课，可以安心安排自己的时间。</div> : sortedSlots.map((occ) => {
              const state = lessonState(occ);
              const isActive = state === '正在上课' || occ === nextLesson;
              return (
                <article className={`timeline-lesson ${isActive ? 'active' : ''}`} key={`${occ.course_id}-${occ.start_time}`}>
                  <div className="timeline-time"><strong>{occ.start_time}</strong><span>{occ.end_time}</span></div>
                  <div className="timeline-marker"><i /></div>
                  <button className="timeline-lesson-copy" type="button" onClick={() => openLesson(occ, selectedDay.date)}>
                    <strong>{occ.course_name}</strong>
                    <span>{occ.location || '地点待定'}</span>
                  </button>
                  <div className="timeline-actions">
                    {state && <span className={`time-state ${state === '正在上课' ? 'live' : ''}`}>{state}</span>}
                    {occ.session && <StatusBadge status={occ.session.status} />}
                    <button type="button" onClick={() => addAudio(occ, selectedDay.date, 'microphone')}>录音</button>
                  </div>
                </article>
              );
            })}
          </div>
        </section>

        <section className="week-board-card">
          <div className="dashboard-section-heading">
            <div><span>一周课程表</span><h2>本周共 {weekLessonCount} 节课</h2></div>
            <button type="button" className="week-board-link" onClick={() => navigate({ name: 'courses' })}>管理课程</button>
          </div>
          <div className="week-board" role="grid" aria-label="本周课程">
            {week.map((day) => (
              <div className={`week-board-day ${day.is_today ? 'today' : ''} ${day.date === selectedDay?.date ? 'selected' : ''}`} key={day.date} role="gridcell">
                <button className="week-board-day-head" type="button" onClick={() => setSelectedDate(day.date)}>
                  <span>{day.weekday_cn}</span><b>{day.date.slice(5)}</b>
                </button>
                <div className="week-board-lessons">
                  {[...day.slots].sort((a, b) => a.start_time.localeCompare(b.start_time)).map((occ) => (
                    <button type="button" className="week-mini-lesson" style={{ borderLeftColor: occ.color || '#2e7d4f' }}
                      key={`${occ.course_id}-${occ.start_time}`} onClick={() => openLesson(occ, day.date)}>
                      <small>{occ.start_time}</small>
                      <strong>{occ.course_name}</strong>
                      {occ.location && <span>{occ.location}</span>}
                    </button>
                  ))}
                  {!day.slots.length && <span className="week-day-empty">无课</span>}
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

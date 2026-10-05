import { useEffect, useRef, useState } from 'react';
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

export function ThisWeek({ navigate }: { navigate: (v: View) => void }) {
  const [center, setCenter] = useState(localDateISO());
  const [week, setWeek] = useState<WeekDay[]>([]);
  const [semName, setSemName] = useState('');
  const [weekHint, setWeekHint] = useState('');
  const [loading, setLoading] = useState(true);
  const autoJumped = useRef(false);
  const [selectedDate, setSelectedDate] = useState(localDateISO());

  const load = () => {
    setLoading(true);
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
      setSemName(r.semester?.name || '');
    })
      .finally(() => setLoading(false));
  };
  useEffect(load, [center]);

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

  const selectedDay = week.find((day) => day.date === selectedDate) || week[0];
  const pending = week.flatMap((day) => day.slots
    .filter((occ) => occ.session && ['ready_to_process', 'failed'].includes(occ.session.status))
    .map((occ) => ({ day, occ })))
    .filter(({ day }) => day.date !== selectedDay?.date)
    .slice(0, 5);
  const shiftWeek = (days: number) => {
    setCenter((current) => shift(current, days));
    setSelectedDate((current) => shift(current, days));
  };
  const isCurrentWeek = week.some((day) => day.is_today);

  return (
    <div className="agenda-page">
      <header className="agenda-header">
        <div>
          <div className="agenda-term">{semName}</div>
          <h1 className="agenda-title">{selectedDay?.is_today ? '今天' : selectedDay?.weekday_cn || '本周'}</h1>
          {selectedDay && <p className="agenda-date">{selectedDay.date}</p>}
        </div>
        <div className="agenda-week-nav" aria-label="周切换">
          <button className="icon-button" type="button" aria-label="上一周" onClick={() => shiftWeek(-7)}>‹</button>
          <span>{week.length ? `${week[0].date.slice(5)} — ${week[week.length - 1].date.slice(5)}` : ''}</span>
          <button className="icon-button" type="button" aria-label="下一周" onClick={() => shiftWeek(7)}>›</button>
          {!isCurrentWeek && <button className="text-button" type="button" onClick={() => { setCenter(localDateISO()); setSelectedDate(localDateISO()); }}>本周</button>}
        </div>
      </header>

      <nav className="agenda-date-strip" aria-label="选择日期">
        {week.map((day) => (
          <button key={day.date} type="button" className={`agenda-date-chip ${selectedDay?.date === day.date ? 'active' : ''}`}
            aria-pressed={selectedDay?.date === day.date} onClick={() => setSelectedDate(day.date)}>
            <span>{day.weekday_cn}</span><b>{day.date.slice(-2)}</b>
          </button>
        ))}
      </nav>

      {weekHint && <div className="small muted agenda-hint">{weekHint}</div>}
      {loading ? <div className="empty">正在读取课表…</div> : (
        <div className="agenda-layout">
          <main className="agenda-main">
            <div className="agenda-section-head">
              <h2>课程安排</h2>
              <span>{selectedDay?.slots.length || 0} 节</span>
            </div>
            {!selectedDay?.slots.length ? (
              <div className="agenda-no-classes">这一天没有安排课程</div>
            ) : selectedDay.slots.map((occ, index) => (
              <article className={`agenda-class ${index === 0 ? 'featured' : ''}`} key={`${occ.course_id}-${occ.start_time}`}>
                <div className="agenda-class-info">
                  <div className="agenda-class-time">{occ.start_time}–{occ.end_time}</div>
                  <button className="agenda-course-name" type="button" onClick={() => openLesson(occ, selectedDay.date)}>{occ.course_name}</button>
                  <div className="agenda-class-meta">
                    {occ.location && <span>{occ.location}</span>}
                    {occ.session && <StatusBadge status={occ.session.status} />}
                  </div>
                </div>
                <div className="agenda-class-actions">
                  <button className="primary" type="button" onClick={() => addAudio(occ, selectedDay.date, 'microphone')}>电脑录音</button>
                  <button className="quiet-button" type="button" onClick={() => addAudio(occ, selectedDay.date, 'upload')}>导入录音</button>
                </div>
              </article>
            ))}
          </main>

          <aside className="agenda-rail">
            <div className="agenda-section-head"><h2>待处理</h2><span>{pending.length}</span></div>
            {pending.length ? pending.map(({ day, occ }) => occ.session ? (
              <button className="agenda-pending" type="button" key={occ.session.id}
                onClick={() => navigate({ name: 'session', id: occ.session!.id })}>
                <span className="agenda-pending-date">{day.date.slice(5)} · {occ.start_time}</span>
                <strong>{occ.session.title || occ.course_name}</strong>
                <StatusBadge status={occ.session.status} />
              </button>
            ) : null) : <div className="agenda-empty-pending">暂无待处理课堂</div>}
          </aside>
        </div>
      )}
    </div>
  );
}

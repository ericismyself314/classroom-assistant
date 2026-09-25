import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { WeekDay, SlotOcc } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { Modal } from '../components/Modal';
import { View } from '../App';
import { QuickGuide } from '../components/QuickGuide';
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
  const [recordPickerOpen, setRecordPickerOpen] = useState(false);
  const [recordSource, setRecordSource] = useState<'microphone' | 'upload'>('microphone');

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
    navigate({ name: 'audio', courseId: occ.course_id, date, title: occ.course_name });
  };

  return (
    <div>
      <div className="row wrap" style={{ justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <div>
          <h1 className="page-title">本周</h1>
          <p className="page-sub">{semName} · 选择课程后，可直接录音或上传已有录音</p>
        </div>
        <div className="row week-audio-actions">
          <button className="primary" disabled={loading} onClick={() => { setRecordSource('microphone'); setRecordPickerOpen(true); }}>电脑录音</button>
          <button className="ghost" disabled={loading} onClick={() => { setRecordSource('upload'); setRecordPickerOpen(true); }}>上传已有录音</button>
        </div>
      </div>
      <QuickGuide />
      <div className="row" style={{ marginBottom: 14 }}>
        <button className="ghost" onClick={() => setCenter(shift(center, -7))}>← 上一周</button>
        <button className="ghost" onClick={() => setCenter(localDateISO())}>本周</button>
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
                  <div style={{ marginTop: 4 }}>{occ.session ? <StatusBadge status={occ.session.status} /> : <span className="badge gray">待录音 / 上传</span>}</div>
                  <div className="small muted" style={{ marginTop: 5 }}>{occ.session ? '点击查看课堂，或在详情页点「电脑录音」' : '点击后可电脑录音或上传文件'}</div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
      {recordPickerOpen && <Modal title={recordSource === 'microphone' ? '选择课程 · 电脑录音' : '选择课程 · 上传录音'} onClose={() => setRecordPickerOpen(false)}>
        <p className="small muted">{recordSource === 'microphone' ? '进入后点击“开始录音”；录制中可以暂停或继续，停止后可试听、保存到电脑或加入课堂。' : '选择本周的一节课，上传已有录音；可一次添加多段并选择处理时间。'}</p>
        {week.flatMap((day) => day.slots.map((occ) => ({ day, occ }))).map(({ day, occ }) => (
          <button key={`${day.date}-${occ.course_id}-${occ.start_time}`} className="ghost" type="button"
            style={{ display: 'flex', width: '100%', justifyContent: 'space-between', textAlign: 'left', marginBottom: 8, padding: '12px 14px' }}
            onClick={() => { setRecordPickerOpen(false); navigate({ name: 'audio', courseId: occ.course_id, sessionId: occ.session?.id, date: day.date, title: occ.course_name, source: recordSource }); }}>
            <span><b>{occ.course_name}</b><span className="small muted" style={{ display: 'block', marginTop: 3 }}>{day.date} · {occ.start_time}–{occ.end_time}</span></span>
            <span className="small">{recordSource === 'microphone' ? '打开电脑录音' : '上传录音文件'} →</span>
          </button>
        ))}
        {week.every((day) => day.slots.length === 0) && <div className="empty">
          本周课表没有课程。可以先到课程页选择“新增课堂 / 电脑录音”。
          <div style={{ marginTop: 10 }}><button className="primary" onClick={() => { setRecordPickerOpen(false); navigate({ name: 'courses' }); }}>前往课程</button></div>
        </div>}
      </Modal>}
    </div>
  );
}

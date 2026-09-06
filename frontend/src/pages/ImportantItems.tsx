import { useEffect, useState } from 'react';
import { api } from '../api';
import { Item, CATEGORY_LABELS, CONFIDENCE_LABELS } from '../types';
import { View } from '../App';

export function ImportantItems({ navigate }: { navigate: (v: View) => void }) {
  const [items, setItems] = useState<Item[]>([]);
  const [courses, setCourses] = useState<any[]>([]);
  const [f, setF] = useState({ course_id: '', category: '', confidence: '', handled: '', from: '', to: '' });

  useEffect(() => { api.courses().then((r) => setCourses(r.courses)); }, []);
  useEffect(() => {
    const q: any = {};
    if (f.course_id) q.course_id = f.course_id;
    if (f.category) q.category = f.category;
    if (f.confidence) q.confidence = f.confidence;
    if (f.handled) q.handled = f.handled;
    if (f.from) q.from = f.from;
    if (f.to) q.to = f.to;
    api.important(q).then((r) => setItems(r.items));
  }, [f]);

  const courseName = (id: string) => courses.find((c) => c.id === id)?.name || '';

  return (
    <div>
      <h1 className="page-title">重要事项</h1>
      <p className="page-sub">自动汇总所有课程中老师宣布的考试、作业、范围、安排变化与活动</p>
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="row wrap">
          <select style={{ width: 160 }} value={f.course_id} onChange={(e) => setF({ ...f, course_id: e.target.value })}>
            <option value="">全部课程</option>
            {courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select style={{ width: 130 }} value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
            <option value="">全部分类</option>
            {Object.entries(CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <select style={{ width: 130 }} value={f.confidence} onChange={(e) => setF({ ...f, confidence: e.target.value })}>
            <option value="">任意可信度</option>
            <option value="explicit">明确通知</option>
            <option value="probable">较明确</option>
            <option value="uncertain">待确认</option>
          </select>
          <select style={{ width: 140 }} value={f.handled} onChange={(e) => setF({ ...f, handled: e.target.value })}>
            <option value="">全部状态</option>
            <option value="unhandled">未处理</option>
            <option value="handled">已处理</option>
          </select>
          <input style={{ width: 140 }} type="date" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} />
          <input style={{ width: 140 }} type="date" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} />
        </div>
      </div>
      {items.length === 0 && <div className="empty">没有符合条件的重要事项</div>}
      {items.map((it) => (
        <div className={`item ${it.confidence === 'uncertain' ? 'uncertain' : ''}`} key={it.id} style={{ cursor: 'pointer' }} onClick={() => navigate({ name: 'session', id: it.session_id })}>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <div><span className="tag">{CATEGORY_LABELS[it.category] || it.category}</span><span className="it">{it.title}</span></div>
            <span className={`badge ${it.confidence === 'uncertain' ? 'warn' : 'green'}`}>{CONFIDENCE_LABELS[it.confidence]}</span>
          </div>
          {it.details && <div className="small" style={{ marginTop: 4 }}>{it.details}</div>}
          <div className="small muted" style={{ marginTop: 4 }}>
            {courseName(it.course_id)} · {it.session_date}
            {it.raw_date_text && <> · 原话「{it.raw_date_text}」</>}
            {it.normalized_date && <> → {it.normalized_date}</>}
            {it.action_required ? ' · 需你行动' : ''}
            {it.is_confirmed ? ' · 已确认' : ''}{it.is_completed ? ' · 已完成' : ''}
          </div>
        </div>
      ))}
    </div>
  );
}

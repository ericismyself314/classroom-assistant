import { useEffect, useState } from 'react';
import { api } from '../api';
import { View } from '../App';

function escapeHtml(s: string) {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));
}
function highlight(text: string, q: string) {
  const esc = escapeHtml(text);
  if (!q) return esc;
  const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
  return esc.replace(re, (m) => `<span class="search-hit">${m}</span>`);
}

export function Search({ navigate }: { navigate: (v: View) => void }) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState<any[]>([]);
  const [courses, setCourses] = useState<any[]>([]);
  const [f, setF] = useState({ course_id: '', type: '', weeks: '', from: '', to: '' });

  useEffect(() => { api.courses().then((r) => setCourses(r.courses)); }, []);

  const run = () => {
    const query = q.trim();
    if (!query) { setResults([]); return; }
    const p: any = { q: query };
    if (f.course_id) p.course_id = f.course_id;
    if (f.type) p.type = f.type;
    if (f.weeks) p.weeks = f.weeks;
    if (f.from) p.from = f.from;
    if (f.to) p.to = f.to;
    api.search(p).then((r) => setResults(r.results));
  };

  const TYPE_LABELS: Record<string, string> = { transcript: '转写', topic: '概览', important: '重要事项' };

  return (
    <div>
      <h1 className="page-title">搜索</h1>
      <p className="page-sub">搜索课堂转写、概览与重要事项；结果可跳回对应课堂</p>
      <div className="card" style={{ marginBottom: 14 }}>
        <input placeholder="输入关键词，例如：消费者购买决策、期中考试、作业" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && run()} />
        <div className="row wrap" style={{ marginTop: 10 }}>
          <select style={{ width: 160 }} value={f.course_id} onChange={(e) => setF({ ...f, course_id: e.target.value })}>
            <option value="">全部课程</option>
            {courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select style={{ width: 130 }} value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>
            <option value="">全部类型</option>
            <option value="transcript">转写</option>
            <option value="topic">概览</option>
            <option value="important">重要事项</option>
          </select>
          <select style={{ width: 140 }} value={f.weeks} onChange={(e) => setF({ ...f, weeks: e.target.value })}>
            <option value="">全部时间</option>
            <option value="1">最近一周</option>
            <option value="2">最近两周</option>
            <option value="3">最近三周</option>
            <option value="99">整个学期</option>
          </select>
          <input style={{ width: 140 }} type="date" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} />
          <input style={{ width: 140 }} type="date" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} />
          <button className="primary" onClick={run}>搜索</button>
        </div>
      </div>
      {results.length === 0 && q && <div className="empty">没有命中「{q}」</div>}
      {results.map((r, i) => (
        <div className="list-item" key={i} onClick={() => navigate({ name: 'session', id: r.session_id })}>
          <div style={{ flex: 1 }}>
            <div className="small muted">{r.course_name} · {r.session_date} · <span className="tag">{TYPE_LABELS[r.content_type] || r.content_type}</span></div>
            <div dangerouslySetInnerHTML={{ __html: highlight(r.snippet, q) }} />
          </div>
          <span className="badge green">查看 →</span>
        </div>
      ))}
    </div>
  );
}

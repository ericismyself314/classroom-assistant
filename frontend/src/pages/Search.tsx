import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import type { View } from '../App';
import type { Course, Semester, Session } from '../types';
import { Icon } from '../components/Icon';
import './Search.css';

const kinds = { transcript: '转写原文', topic: '课堂概览', important: '重要事项' } as const;
type Kind = keyof typeof kinds;
export type SearchSource = { kind: Kind; id: string; query: string };
type Hit = { ref_id: string; course_name: string; session_date: string; content_type: Kind; session_id: string; snippet: string };
type Recent = Pick<Session, 'id' | 'title' | 'status' | 'session_date'> & { course_name: string };

function Excerpt({ text, query }: { text: string; query: string }) {
  const offset = text.toLowerCase().indexOf(query.toLowerCase());
  const start = Math.max(0, offset - 42);
  const end = Math.min(text.length, Math.max(start + 150, offset + query.length + 42));
  const excerpt = text.slice(start, end);
  const pattern = new RegExp('(' + query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
  return <>{start > 0 && '…'}{excerpt.split(pattern).map((part, index) =>
    index % 2 ? <mark key={index}>{part}</mark> : part)}{end < text.length && '…'}</>;
}

export function Search({ navigate }: { navigate: (v: View) => void }) {
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [composing, setComposing] = useState(false);
  const [course, setCourse] = useState('');
  const [range, setRange] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [kind, setKind] = useState<Kind | ''>('');
  const [retry, setRetry] = useState(0);
  const [courses, setCourses] = useState<Course[]>([]);
  const [semester, setSemester] = useState<Semester | null>(null);
  const [recent, setRecent] = useState<Recent[]>([]);
  const [response, setResponse] = useState<{ key: string; rows: Hit[]; error?: string } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let live = true;
    api.courses().then(r => { if (live) setCourses(r.courses); }).catch(() => {});
    api.state(true).then(r => { if (live) setSemester(r.semester); }).catch(() => {});
    api.dashboard().then(r => { if (live) setRecent(r.recent_sessions.filter((s: Recent) => s.status === 'completed')); }).catch(() => {});
    return () => { live = false; };
  }, []);
  useEffect(() => {
    if (composing) return;
    const timer = window.setTimeout(() => setQuery(input.trim()), 260);
    return () => clearTimeout(timer);
  }, [input, composing]);

  const params: Record<string, string> = { q: query };
  if (course) params.course_id = course;
  if (range === 'dates') { if (from) params.from = from; if (to) params.to = to; }
  else if (range === 'semester' && semester) { params.from = semester.start_date; params.to = semester.end_date; }
  else if (range) params.weeks = range;
  const invalidDates = range === 'dates' && !!from && !!to && from > to;
  const requestKey = JSON.stringify(params);
  useEffect(() => {
    if (!query || invalidDates) return;
    let live = true;
    api.search(JSON.parse(requestKey)).then(r => { if (live) setResponse({ key: requestKey, rows: r.results }); })
      .catch(() => { if (live) setResponse({ key: requestKey, rows: [], error: '暂时无法读取搜索结果。' }); });
    return () => { live = false; };
  }, [query, requestKey, invalidDates, retry]);

  const rows = response?.key === requestKey ? response.rows : [];
  const visibleRows = kind ? rows.filter(row => row.content_type === kind) : rows;
  const error = response?.key === requestKey && response.error;
  const waiting = input.trim() !== query || response?.key !== requestKey;
  return <section className="notes-search">
    <header><h1 className="page-title">搜索笔记</h1></header>
    <section className="ns-tools" aria-label="查找课堂内容">
      <form role="search" onSubmit={e => { e.preventDefault(); if (!composing) setQuery(input.trim()); }}>
        <div className="ns-input"><Icon name="search" />
          <input ref={inputRef} aria-label="搜索笔记关键词" placeholder="搜索课堂原话、知识点或作业…" value={input}
            onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)} onChange={e => setInput(e.target.value)} />
          {input && <button type="button" aria-label="清除关键词" onClick={() => { setInput(''); setQuery(''); inputRef.current?.focus(); }}>×</button>}
        </div><button type="submit" className="primary ns-submit">搜索</button>
      </form>
      <div className="ns-filters">
        <label>课程<select className="app-select ns-course-select" value={course} onChange={e => setCourse(e.target.value)}><option value="">全部课程</option>{courses.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        <label>时间<select className="app-select" value={range} onChange={e => setRange(e.target.value)}><option value="">全部时间</option><option value="1">最近一周</option><option value="2">最近两周</option><option value="3">最近三周</option>{semester && <option value="semester">本学期</option>}<option value="dates">自选日期</option></select></label>
        {range === 'dates' && <><label>从<input type="date" value={from} onChange={e => setFrom(e.target.value)} /></label><label>至<input type="date" value={to} onChange={e => setTo(e.target.value)} /></label></>}
        {(course || range || kind) && <button type="button" className="ns-reset" onClick={() => {setCourse(''); setRange(''); setFrom(''); setTo(''); setKind('');}}>清除筛选</button>}
      </div>
      {invalidDates && <p role="alert">结束日期须在开始日期当天或之后。</p>}
    </section>
    {!input.trim() ? recent.length > 0 && <section className="ns-section"><h2>最近的课堂笔记</h2>
      {recent.map(s => <button className="ns-recent" key={s.id} onClick={() => navigate({ name: 'session', id: s.id, fromSearch: true })}>
        <span className="ns-book"><Icon name="book" width="19" /></span><span><strong>{s.title || s.course_name}</strong><small>{[s.course_name, s.session_date].filter(Boolean).join(' · ')}</small></span><Icon name="chevron" width="15" />
      </button>)}</section> : !invalidDates && <section className="ns-section">
      <div className="ns-heading"><h2>搜索结果</h2><small role="status">{waiting ? '正在查找…' : error ? '' : rows.length >= 200 ? '显示前 200 条匹配' : visibleRows.length + ' 条匹配'}</small></div>
      <div className="ns-kinds" aria-label="结果类型">{(['', ...Object.keys(kinds)] as (Kind | '')[]).map(k =>
        <button key={k} aria-pressed={kind === k} onClick={() => setKind(k)}>{k ? kinds[k] : '全部'}</button>)}</div>
      {waiting ? null : error ? <div className="ns-message" role="alert">{error} <button onClick={() => { setResponse(null); setRetry(n => n + 1); }}>重试</button></div>
        : !visibleRows.length ? <p className="ns-message">没有找到“{query}”。{kind || course || range ? '可以清除筛选，扩大查找范围。' : '可以换个短一点的关键词。'}</p>
        : visibleRows.map(r => <button key={r.session_id + r.content_type + r.ref_id} className="ns-hit" onClick={() => navigate({ name: 'session', id: r.session_id, fromSearch: true, source: { kind: r.content_type, id: r.ref_id, query } })}>
          <span className="ns-copy"><span className="ns-meta"><strong>{r.course_name || '课堂笔记'}</strong><time>{r.session_date}</time><span>{kinds[r.content_type]}</span></span>
            <span className="ns-excerpt"><Excerpt text={r.snippet || ''} query={query} /></span></span><Icon name="chevron" width="15" /></button>)}
    </section>}
  </section>;
}

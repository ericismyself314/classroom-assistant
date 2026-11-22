import { useLayoutEffect, useRef, useState } from 'react';
import type { View } from '../App';
import { api } from '../api';
import { useItemReminders } from '../components/ItemReminders';
import { EditItem } from '../components/EditItem';
import { SelectionIndicator } from '../components/SelectionIndicator';
import './ImportantItems.css';
import { CATEGORY_LABELS, CONFIDENCE_LABELS, type Item } from '../types';
import { dueLabel, type ItemGroup } from '../itemPriorities';

const titles: Record<ItemGroup, string> = { due: '近期作业', overdue: '已过截止日', all: '全部事项' };
const empty = { due: '从今天起 7 天内，没有已明确截止日的未完成作业。', overdue: '没有已过截止日且仍标记为未完成的作业。', all: '暂无重要事项。' };
const blank = { course: '', category: '', confidence: '', handled: '', from: '', to: '' };
export function ImportantItems({navigate, group, revision}: {navigate: (v: View) => void; group?: ItemGroup; revision: number}) {
  const board = useItemReminders(revision);
  const [selected,setSelected] = useState<ItemGroup | undefined>(group);
  const [filters,setFilters] = useState(blank);
  const [limit,setLimit] = useState(12);
  const [editing,setEditing] = useState<Item | null>(null);
  const [busyId,setBusyId] = useState('');
  const [message,setMessage] = useState('');
  const tab = selected || (board.groups.due.length ? 'due' : board.groups.overdue.length ? 'overdue' : 'all');
  const resultsFrame = useRef<HTMLDivElement>(null);
  const results = useRef<HTMLDivElement>(null);
  const previous = useRef<{ height: number; opacity: number; offset: number } | null>(null);
  function selectGroup(key: ItemGroup) {
    if (key === tab) return;
    if (resultsFrame.current && results.current && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const style = getComputedStyle(results.current);
      const moving = results.current.getAnimations().some(animation => animation.playState === 'running');
      const direction = Object.keys(titles).indexOf(key) > Object.keys(titles).indexOf(tab) ? 1 : -1;
      previous.current = {
        height: resultsFrame.current.getBoundingClientRect().height,
        opacity: moving ? Number(style.opacity) : .82,
        offset: moving ? new DOMMatrixReadOnly(style.transform === 'none' ? undefined : style.transform).m41 : 4 * direction,
      };
    }
    setSelected(key); setLimit(12); setMessage('');
  }
  useLayoutEffect(() => {
    const frame = resultsFrame.current;
    const list = results.current;
    const before = previous.current;
    previous.current = null;
    if (!frame || !list || !before || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const reveal = list.animate([
      {opacity: before.opacity, transform: `translate3d(${before.offset}px,0,0)`},
      {opacity: 1, transform: 'translate3d(0,0,0)'},
    ], {duration: 210, easing: 'cubic-bezier(.18,.65,.28,1)'});
    const rect = frame.getBoundingClientRect();
    const viewport = Math.max(0, window.innerHeight - rect.top);
    const from = Math.min(before.height, viewport), to = Math.min(rect.height, viewport);
    const resize = Math.abs(from - to) < 1 ? undefined : frame.animate(
      [{height: from + 'px'}, {height: to + 'px'}],
      {duration: 260 + Math.min(85, Math.abs(from - to) * .14), easing: 'cubic-bezier(.32,.05,.22,1)'}
    );
    return () => { reveal.cancel(); resize?.cancel(); };
  }, [tab]);
  const groups = {...board.groups, all: board.data?.items || []};
  const filtered = groups[tab].filter(i => (!filters.course || filters.course === i.course_id)
    && (!filters.category || filters.category === i.category) && (!filters.confidence || filters.confidence === i.confidence)
    && (!filters.handled || (filters.handled === 'handled') === !!i.is_completed)
    && (!filters.from || !!i.normalized_date && i.normalized_date >= filters.from)
    && (!filters.to || !!i.normalized_date && i.normalized_date <= filters.to));
  function filter(key: keyof typeof blank, value: string) { setFilters(prev => ({...prev,[key]:value})); setLimit(12); }
  async function toggleComplete(item: Item) {
    setBusyId(item.id); setMessage('');
    const patch = {is_completed: item.is_completed ? 0 : 1};
    try { await api.updateItem(item.id, patch); board.applyUpdate(item.id,patch); setMessage(patch.is_completed ? '已标记完成，可在「全部事项」中撤销。' : '已恢复为未完成。'); }
    catch { setMessage('未能保存完成状态，请重试。'); }
    finally { setBusyId(''); }
  }
  return <section className="ir-page"><h1 className="page-title">重要事项</h1>
    {board.error ? <p role="alert">{board.error} <button onClick={board.retry}>重试</button></p> : !board.data ? <p role="status">正在读取重要事项…</p> : <>
      <div className="ir-tabs selection-group" role="group" aria-label="事项分组"><SelectionIndicator value={tab} continuous />{(Object.keys(titles) as ItemGroup[]).map(key =>
        <button key={key} aria-pressed={tab === key} onClick={() => selectGroup(key)}>{titles[key]} <b>{groups[key].length}</b></button>)}</div>
      <section className="glass-panel ir-panel"><div className="ir-head"><h2>{tab === 'due' ? '从今天起 7 天内截止' : titles[tab]}</h2><span>{tab === 'all' ? '所有学期' : '本学期'}</span></div>
        <details className="ir-filters"><summary>筛选{Object.values(filters).some(Boolean) ? '（已启用）' : ''}</summary>
          <div><label>课程<select value={filters.course} onChange={e=>filter('course',e.target.value)}><option value="">全部课程</option>{board.data.courses.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
          <label>分类<select value={filters.category} onChange={e=>filter('category',e.target.value)}><option value="">全部分类</option>{Object.entries(CATEGORY_LABELS).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
          <label>可信度<select value={filters.confidence} onChange={e=>filter('confidence',e.target.value)}><option value="">全部</option>{Object.entries(CONFIDENCE_LABELS).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
          <label>处理状态<select value={filters.handled} onChange={e=>filter('handled',e.target.value)}><option value="">全部</option><option value="handled">已完成</option><option value="unhandled">未完成</option></select></label>
          <label>事项日期从<input type="date" value={filters.from} onChange={e=>filter('from',e.target.value)} /></label><label>至<input type="date" min={filters.from} value={filters.to} onChange={e=>filter('to',e.target.value)} /></label>
          <button onClick={()=>{setFilters(blank);setLimit(12);}}>清除筛选</button></div></details>
        {message && <p role="status">{message}</p>}
        <div className="ir-results-frame" ref={resultsFrame}><div ref={results} className="ir-results">
        {!filtered.length && <p className="ir-empty">{Object.values(filters).some(Boolean) ? '没有符合筛选条件的事项。' : empty[tab]}</p>}
        {filtered.slice(0,limit).map(item=>{
          return <article className="ir-row" key={item.id}><div className="ir-row-top"><strong>{item.title}</strong><span className="ir-state">{item.is_completed ? '已完成' : item.category === 'assignment' ? dueLabel(item,board.today) : '已收录'}</span></div>
            <div className="ir-meta">{[board.names.get(item.course_id) || '往期课程', CATEGORY_LABELS[item.category], item.session_date && item.session_date + ' 课堂'].filter(Boolean).join(' · ')}</div>
            {item.details && <p>{item.details}</p>}
            <div className="ir-row-footer"><span>{item.normalized_date && (item.category === 'assignment' ? '截止 ' : '日期 ') + item.normalized_date}</span>
            <div><button onClick={()=>navigate({name:'session',id:item.session_id,source:{kind:'important',id:item.id,query:''},fromImportant:tab})}>查看课堂</button>
              <button className="ir-action" onClick={()=>setEditing(item)}>编辑</button>
              <button disabled={!!busyId} onClick={()=>toggleComplete(item)}>{item.is_completed ? '恢复未完成' : '标记完成'}</button></div></div>
          </article>;
        })}
        {filtered.length > limit && <button className="ir-more" onClick={()=>setLimit(n=>n+12)}>显示更多（还有 {filtered.length-limit} 条）</button>}
        </div></div>
      </section>
    </>}
    {editing && <EditItem item={editing} onClose={()=>setEditing(null)} onSave={patch=>{board.applyUpdate(editing.id,patch);setMessage('修改已保存。');}}/>}
  </section>;
}

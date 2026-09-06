import { ReactNode, useState } from 'react';
import { Segment } from '../types';

const KINDS: Record<string, string> = { concept: '知识点', example: '课堂例子', formula: '公式与推导', caution: '注意点' };
const time = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

function compactLegacy(rows: any[], limit = 5): any[] {
  if (rows.length <= limit) return rows;
  const size = Math.ceil(rows.length / limit);
  return Array.from({ length: Math.ceil(rows.length / size) }, (_, i) => {
    const group = rows.slice(i * size, (i + 1) * size);
    const first = group[0] || {};
    const summaries = group.map(r => String(r.summary || '').trim()).filter(Boolean);
    return {
      ...first,
      title: `${first.title || `课堂内容（第 ${i + 1} 部分）`}等内容`,
      summary: summaries.join('；').slice(0, 900),
      points: group.flatMap(r => Array.isArray(r.points) ? r.points : []).slice(0, 4),
      start_seconds: group.find(r => r.start_seconds != null)?.start_seconds ?? null,
      end_seconds: [...group].reverse().find(r => r.end_seconds != null)?.end_seconds ?? null,
    };
  });
}

export function Sources({ evidence, onSeek }: { evidence: any[]; onSeek: (s: number) => void }) {
  if (!evidence?.length) return null;
  return <details className="note-sources"><summary>查看中文转写与出处 · {evidence.length} 段</summary>
    {evidence.map((e, i) => <blockquote key={i}>
      {e.start_seconds != null && <button className="source-time" onClick={() => onSeek(e.start_seconds)}>▶ {time(e.start_seconds)}</button>}
      <span>{e.quote}</span>
    </blockquote>)}
  </details>;
}

export function ClassNotes({ notes, onSeek, processing, onEdit, beforeKnowledge }: { notes: any; onSeek: (s: number) => void; processing: boolean; onEdit?: (index: number) => void; beforeKnowledge?: ReactNode }) {
  const rawChapters: any[] = notes?.knowledge || notes?.chapters || [];
  const chapters: any[] = notes?.version >= 3 ? rawChapters : compactLegacy(rawChapters);
  const timeline: any[] = notes?.timeline || chapters;
  const title = notes?.title || notes?.overview_title || '';
  const canEdit = !!onEdit && notes?.version >= 3;
  return <>
    <section className="notes-overview" aria-label="课堂概览">
      <div className="notes-eyebrow">课堂概览</div>
      <h2>{title || (chapters.length ? '本节课内容整理' : '先有可靠的转写，再有清晰的笔记')}</h2>
      {chapters.length ? <p className="overview-lead">这节课主要围绕 {chapters.slice(0, 3).map(c => c.title).filter(Boolean).join('、')} 展开。</p>
        : <p className="muted">{processing ? '正在整理，完成后会显示课程标题和分块笔记。' : notes?.transcript_edited ? '转写已修正。点击「仅重新分析」更新课程整理；修改前的结果保存在历史版本。' : notes ? '暂未得到足够的课程内容。请先查看完整转写。' : '这是旧版结果或尚未分析。转写正确时可「仅重新分析」；语言识别错误时需「重新转写」。'}</p>}
      <div className="small muted">本地 AI 整理 · 按课堂原文组织，不补充录音外的知识；重要信息请核对原话。</div>
    </section>
    <div className="section-heading"><h2>时间轴分块</h2><span className="muted small">按老师的讲授顺序，合并成少量内容段</span></div>
    <div className="timeline-list">
      {timeline.map((c, i) => <article className="timeline-card" key={i}>
        <div className="timeline-index">{String(i + 1).padStart(2, '0')}</div>
        <div><div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}><h3>{c.title}</h3>
          {c.start_seconds != null && <button className="source-time" onClick={() => onSeek(c.start_seconds)}>▶ {time(c.start_seconds)}</button>}</div>
          <p>{c.summary}</p></div>
      </article>)}
    </div>
    {beforeKnowledge}
    <div className="section-heading"><h2>知识点梳理</h2><span className="muted small">按内容块阅读，重点内容单独标记</span></div>
    {chapters.map((c, i) => <article id={`chapter-${i}`} className="chapter-card" key={i}>
      <div className="chapter-number">{String(i + 1).padStart(2, '0')}</div>
      <div className="chapter-body"><div className="row" style={{ justifyContent: 'space-between' }}><h3>{c.title}</h3>
        {canEdit && <button className="ghost" disabled={processing} onClick={() => onEdit?.(i)}>修改笔记</button>}</div>
        {c.important && <span className="badge important">重点</span>}
        {c.manual_edit && <span className="badge">已手动修改 · 原话可供回听核对</span>}
        <p className="chapter-summary">{c.summary}</p>
        <Sources evidence={c.evidence} onSeek={onSeek} />
        {c.points?.map((p: any, j: number) => <div className={`note-point ${p.important ? 'important-point' : ''}`} key={j}>
          <div className="row" style={{ justifyContent: 'space-between' }}><span className="note-kind">{KINDS[p.kind] || '知识点'}</span>{p.important && <span className="badge important">重点</span>}</div><p>{typeof p === 'string' ? p : p.text}</p>
          <Sources evidence={p.evidence} onSeek={onSeek} />
        </div>)}
      </div>
    </article>)}
  </>;
}

export function ReviewNotes({ notes, segments, onSeek }: { notes: any; segments: Segment[]; onSeek: (s: number) => void }) {
  const [showAll, setShowAll] = useState(false);
  const uncertain = segments.filter(s => s.is_uncertain);
  const review: any[] = notes?.review || [];
  return <div className="review-notes">
    {notes?.coverage && <p className="small muted">已对 {notes.coverage.signal_passages} 处可能涉及课程要求的原文进行查漏，
      {notes.coverage.pending_passages ? `其中 ${notes.coverage.pending_passages} 处仍需核对。` : '本轮未留下未决的查漏片段。'} 此检查不能保证没有遗漏。</p>}
    {!review.length && !uncertain.length && <p className="small muted">暂无单独标出的存疑片段。不代表转写完全准确，重要细节仍应回听。</p>}
    {review.map((r, i) => <details key={i} className="review-entry">
      <summary>{r.title} <span className="badge warn">待核对</span></summary>
      <p className="small muted">{r.reason}。以下是待核对草稿，不是确定结论。</p><p>{r.summary}</p>
      <Sources evidence={r.evidence} onSeek={onSeek} />
    </details>)}
    {!!notes?.rejected_count && <p className="small muted">另有 {notes.rejected_count} 条候选缺少有效原文证据，未收录进笔记。</p>}
    {!!uncertain.length && <details><summary>听辨存疑的原文 · {uncertain.length} 段</summary>
      <p className="small muted">这些是识别器标记的不确定片段，并非全部错误。可点击时间回听。</p>
      {(showAll ? uncertain : uncertain.slice(0, 12)).map(s => <div className="review-entry" key={s.id}>
        {s.start_seconds != null && <button className="source-time" onClick={() => onSeek(s.start_seconds!)}>▶ {time(s.start_seconds)}</button>} {s.display_text || '中文展示稿尚未生成，请重新分析。'}
      </div>)}
      {uncertain.length > 12 && <button className="ghost" onClick={() => setShowAll(!showAll)}>{showAll ? '收起' : `查看全部 ${uncertain.length} 段`}</button>}
    </details>}
  </div>;
}

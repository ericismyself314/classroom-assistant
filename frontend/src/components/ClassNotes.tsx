import { ReactNode, createContext, useContext, useState } from 'react';
import { Segment } from '../types';
import { chineseEvidence, displaySummary, displayText, displayTitle, hasChinese } from '../chineseDisplay';

const KINDS = new Map([['concept', '知识点'], ['example', '课堂例子'], ['formula', '公式与推导'], ['caution', '注意点']]);
const time = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

type CueRange = { start: number; end: number } | null;
export const PlaybackCue = createContext<CueRange>(null);

export function cueContains(cue: CueRange, seconds: number | null | undefined) {
  return cue != null && seconds != null && (seconds === cue.start || (seconds > cue.start && seconds < cue.end));
}

function NoteTime({ seconds, onSeek }: { seconds: number; onSeek?: (s: number) => void }) {
  const cue = useContext(PlaybackCue);
  return onSeek ? <button aria-current={cueContains(cue, seconds) ? "time" : undefined} className="source-time" onClick={() => onSeek(seconds)}>▶ {time(seconds)}</button>
    : <span className="source-time">{time(seconds)}</span>;
}

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

export function Sources({ evidence, onSeek, segments = [], label = '查看中文转写与出处' }: { evidence?: any[]; onSeek?: (s: number) => void; segments?: Segment[]; label?: string }) {
  if (!evidence?.length) return null;
  return <details className="note-sources"><summary>{label} · {evidence.length} 段</summary>
    {evidence.map((e, i) => <blockquote key={i}>
      {e.start_seconds != null && <NoteTime seconds={e.start_seconds} onSeek={onSeek} />}
      <span>{displayText(e.quote, chineseEvidence(e, segments))}</span>
    </blockquote>)}
  </details>;
}

type NotePoint = { text: string; kindLabel: string; important?: boolean; evidence?: any[] };

function ChapterPoints({ points, segments, onSeek }: { points: NotePoint[]; segments: Segment[]; onSeek?: (s: number) => void }) {
  if (!points.length) return null;
  const allImportant = points.every(point => point.important);
  const firstKind = points[0].kindLabel;
  const sharedKind = points.every(point => point.kindLabel === firstKind) ? firstKind : '';
  return <div className="chapter-points">
    <h4>{allImportant ? '本节重点' : sharedKind || '课堂要点'}
      {allImportant && sharedKind && sharedKind !== '知识点' && <span className="point-type">{sharedKind}</span>}
    </h4>
    <ul>{points.map((point, i) => <li key={i}>
      <p>{!allImportant && point.important && <strong className="point-priority">重点</strong>}
        {!sharedKind && point.kindLabel !== '知识点' && <span className="point-type">{point.kindLabel}</span>}
        {point.text}</p>
      <Sources evidence={point.evidence} onSeek={onSeek} segments={segments} label="本条出处" />
    </li>)}</ul>
  </div>;
}

export function ClassNotes({ notes, segments = [], onSeek, processing, onEdit, beforeKnowledge, hideTimeline = false }: { notes: any; segments?: Segment[]; onSeek?: (s: number) => void; processing: boolean; onEdit?: (index: number) => void; beforeKnowledge?: ReactNode; hideTimeline?: boolean }) {
  const rawChapters: any[] = notes?.knowledge || notes?.chapters || [];
  const legacy: any[] = notes?.version >= 3 ? rawChapters : compactLegacy(rawChapters);
  const normalize = (row: any, parentEvidence = '') => {
    const evidence = chineseEvidence(row, segments) || parentEvidence;
    const title = displayTitle(row?.title, evidence || row?.summary);
    const summary = displaySummary(row?.summary, evidence);
    const points = (Array.isArray(row?.points) ? row.points : []).map((point: any) => {
      const raw = typeof point === 'string' ? { text: point } : point || {};
      return { ...raw, kindLabel: KINDS.get(raw.kind) || '知识点', text: displayText(raw.text, evidence) };
    });
    return { ...row, title, summary, points, _chineseEvidence: evidence };
  };
  const chapters: any[] = legacy.map((row) => normalize(row));
  const timeline: any[] = (notes?.timeline || chapters).map((row: any) => normalize(row, chineseEvidence(row, segments)));
  const title = displayTitle(notes?.title || notes?.overview_title, chapters[0]?._chineseEvidence || '');
  const canEdit = !!onEdit && notes?.version >= 3;
  return <>
    <section className="notes-overview" aria-label="课堂概览">
      <div className="notes-eyebrow">课堂概览</div>
      <h2>{title || (chapters.length ? '本节课内容整理' : '暂无课堂整理')}</h2>
      {chapters.length ? <p className="small muted">{chapters.length} 个主题</p>
        : <p className="muted">{processing ? '正在整理课堂笔记…' : notes?.transcript_edited ? '转写已修改，点击「重新分析」即可更新笔记。' : segments.length ? '可以先读完整转写，或点击「重新分析」整理笔记。' : '转写完成后，这里会显示课堂笔记。'}</p>}
    </section>
    {!hideTimeline && <><div className="section-heading"><h2>课堂时间轴</h2></div>
    <div className="timeline-list">
      {timeline.map((c, i) => <article className="timeline-card" key={i}>
        <div className="timeline-index">{String(i + 1).padStart(2, '0')}</div>
        <div><div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}><h3>{c.title}</h3>
          {c.start_seconds != null && <NoteTime seconds={c.start_seconds} onSeek={onSeek} />}</div>
          <p>{c.summary}</p></div>
      </article>)}
    </div></>}
    {beforeKnowledge}
    <div className="section-heading"><h2>课堂笔记</h2></div>
    {chapters.map((c, i) => <article id={`chapter-${i}`} className="chapter-card" key={i}>
      <div className="chapter-number">{String(i + 1).padStart(2, '0')}</div>
      <div className="chapter-body"><div className="row" style={{ justifyContent: 'space-between' }}><h3>{c.title}</h3>
        {onSeek && c.start_seconds != null && <NoteTime seconds={c.start_seconds} onSeek={onSeek} />}
        {canEdit && <button className="ghost" disabled={processing} onClick={() => onEdit?.(i)}>修改笔记</button>}</div>
        {c.important && <span className="badge important">重点</span>}
        {c.manual_edit && <span className="badge">已手动修改 · 原话可供回听核对</span>}
        <p className="chapter-summary">{c.summary}</p>
        <ChapterPoints points={c.points} segments={segments} onSeek={onSeek} />
        <Sources evidence={c.evidence} onSeek={onSeek} segments={segments} label="本节出处" />
      </div>
    </article>)}
  </>;
}

export function ReviewNotes({ notes, segments, onSeek }: { notes: any; segments: Segment[]; onSeek?: (s: number) => void }) {
  const [showAll, setShowAll] = useState(false);
  const uncertain = segments.filter(s => s.is_uncertain);
  const review: any[] = notes?.review || [];
  return <div className="review-notes">
    {notes?.coverage && <p className="small muted">已对 {notes.coverage.signal_passages} 处可能涉及课程要求的原文进行查漏，
      {notes.coverage.pending_passages ? `其中 ${notes.coverage.pending_passages} 处仍需核对。` : '本轮未留下未决的查漏片段。'} 此检查不能保证没有遗漏。</p>}
    {!review.length && !uncertain.length && <p className="small muted">暂无单独标出的存疑片段。不代表转写完全准确，重要细节仍应回听。</p>}
    {review.map((r, i) => <details key={i} className="review-entry">
      <summary>{displayTitle(r.title, chineseEvidence(r, segments))} <span className="badge warn">待核对</span></summary>
      <p className="small muted">{displayText(r.reason, chineseEvidence(r, segments))}。以下是待核对草稿，不是确定结论。</p><p>{displayText(r.summary, chineseEvidence(r, segments))}</p>
      <Sources evidence={r.evidence} onSeek={onSeek} segments={segments} />
    </details>)}
    {!!notes?.rejected_count && <p className="small muted">另有 {notes.rejected_count} 条候选缺少有效原文证据，未收录进笔记。</p>}
    {!!uncertain.length && <details><summary>听辨存疑的原文 · {uncertain.length} 段</summary>
      <p className="small muted">这些是识别器标记的不确定片段，并非全部错误。可点击时间回听。</p>
      {(showAll ? uncertain : uncertain.slice(0, 12)).map(s => <div className="review-entry" key={s.id}>
        {s.start_seconds != null && <NoteTime seconds={s.start_seconds} onSeek={onSeek} />} {displayText(s.display_text, hasChinese(s.text) ? s.text : '')}
      </div>)}
      {uncertain.length > 12 && <button className="ghost" onClick={() => setShowAll(!showAll)}>{showAll ? '收起' : `查看全部 ${uncertain.length} 段`}</button>}
    </details>}
  </div>;
}

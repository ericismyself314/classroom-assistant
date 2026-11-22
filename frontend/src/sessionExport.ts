import { CATEGORY_LABELS } from './types';
import type { Segment } from './types';

export function transcriptText(segment: Partial<Segment>): string {
  return [segment.display_text, segment.text, segment.original_text].map(value => String(value || '').trim()).find(Boolean) || '';
}

function time(seconds: unknown): string {
  return typeof seconds === 'number' && Number.isFinite(seconds)
    ? `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}` : '';
}

function sources(item: any): string[] {
  let evidence = item.evidence;
  if (!Array.isArray(evidence)) {
    try { evidence = JSON.parse(item.evidence_data || '[]'); } catch { evidence = []; }
  }
  return Array.isArray(evidence) ? evidence.filter(row => row && typeof row === 'object').map(row => {
    const start = time(row.start_seconds), end = time(row.end_seconds);
    const location = start ? `${start}${end && end !== start ? `—${end}` : ''}` : row.segment_id != null ? `片段 ${row.segment_id}` : '';
    return `来源${location ? ` ${location}` : ''}：${String(row.quote || '').trim()}`;
  }) : [];
}

export function sessionExportText(data: any): string {
  const { session = {}, course = {}, segments = [] } = data;
  const notes = data.notes || {};
  const lines = [String(session.title || notes.title || course.name || '课堂笔记'), String(session.session_date || '')];
  if (course.name && course.name !== session.title) lines.push(`课程：${course.name}`);
  const chapters = notes.knowledge || notes.chapters || [];
  if (chapters.length) {
    lines.push('', '课堂笔记');
    for (const chapter of chapters) {
      lines.push('', String(chapter.title || ''), String(chapter.summary || ''));
      for (const point of chapter.points || []) lines.push(`- ${typeof point === 'string' ? point : point.text || ''}`);
    }
  }
  const items = (Array.isArray(data.items) ? data.items : notes.non_knowledge || []).filter((item: any) => item.verification_status !== 'rejected');
  if (items.length) {
    lines.push('', '重要事项（作业、考试与课程安排）');
    for (const item of items) {
      lines.push('', `[${CATEGORY_LABELS[item.category] || '其它'}${item.is_completed ? ' · 已完成' : ''}] ${item.title || ''}`);
      if (item.details) lines.push(String(item.details));
      if (item.normalized_date) lines.push(`日期：${item.normalized_date}`);
      if (item.raw_date_text) lines.push(`原话日期：${item.raw_date_text}`);
      lines.push(item.action_required ? '需要采取行动' : '通知／参考信息', ...sources(item));
    }
  }
  if (segments.length) {
    lines.push('', '逐字稿（没有中文展示稿时保留识别原文）');
    for (const segment of segments) {
      const content = transcriptText(segment);
      const location = time(segment.start_seconds);
      lines.push('', `${location ? `[${location}] ` : ''}${content}`);
      const original = String(segment.original_text || segment.text || '').trim();
      if (original && original !== content) lines.push(`原文：${original}`);
    }
  }
  return lines.join('\n').trim();
}

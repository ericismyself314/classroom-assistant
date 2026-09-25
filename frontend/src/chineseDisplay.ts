import { Segment } from './types';

const HAN = /[\u3400-\u9fff]/g;
const LATIN = /[A-Za-z]/g;

/**
 * 只判断“是否明显是英文内容”，不拦截 Qwen、PVP、DCF 等必要术语。
 * 这是旧结果的展示保护，不会修改数据库里的原始文字。
 */
export function isEnglishHeavy(value: unknown): boolean {
  const text = String(value ?? '').trim();
  if (!text) return false;
  const han = (text.match(HAN) || []).length;
  const latin = (text.match(LATIN) || []).length;
  return latin >= 18 && latin > Math.max(8, han * 1.35);
}

export function hasChinese(value: unknown): boolean {
  return (String(value ?? '').match(HAN) || []).length >= 2;
}

function compact(value: unknown, limit = 900): string {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim();
  if (!text) return '';
  return text.length > limit ? `${text.slice(0, limit).replace(/[，。；、,.!?！？\s]+$/, '')}…` : text;
}

function segmentIds(row: any): string[] {
  const ids: string[] = [];
  if (Array.isArray(row?.evidence_segment_ids)) ids.push(...row.evidence_segment_ids.map(String));
  if (Array.isArray(row?.evidence)) {
    for (const item of row.evidence) {
      if (item?.segment_id != null) ids.push(String(item.segment_id));
      if (Array.isArray(item?.segment_ids)) ids.push(...item.segment_ids.map(String));
    }
  }
  return [...new Set(ids)];
}

/** 从真实转写片段中取中文展示稿，绝不把英文原文当作中文回填。 */
export function chineseEvidence(row: any, segments: Segment[] = []): string {
  if (!segments.length) return '';
  const ids = new Set(segmentIds(row));
  const matches = segments.filter((s) => ids.has(String(s.sequence_number)) || ids.has(String(s.id)));
  const values = matches
    .map((s) => String(s.display_text || '').trim())
    .filter((text) => text && !isEnglishHeavy(text));
  return compact([...new Set(values)].join(' '));
}

function chinesePhrase(value: string): string {
  const text = value.replace(/\s+/g, ' ').trim();
  const pieces = text.split(/[。！？；;!?\n]+/).map((v) => v.trim()).filter(Boolean);
  const phrase = pieces.find((v) => hasChinese(v));
  return phrase ? compact(phrase, 28) : '';
}

export function displayTitle(value: unknown, fallback = ''): string {
  const text = String(value ?? '').trim();
  if (text && !isEnglishHeavy(text)) return text;
  const source = chinesePhrase(fallback);
  return source ? `${source}（课堂内容）` : '本节课内容整理';
}

export function displaySummary(value: unknown, fallback = ''): string {
  const text = String(value ?? '').trim();
  if (text && !isEnglishHeavy(text)) return compact(text);
  const source = compact(fallback);
  return source || '中文整理尚未生成，请点击「仅重新分析」。';
}

export function displayText(value: unknown, fallback = ''): string {
  const text = String(value ?? '').trim();
  if (text && !isEnglishHeavy(text)) return text;
  return String(fallback || '').trim() || '中文展示稿尚未生成，请点击「仅重新分析」。';
}

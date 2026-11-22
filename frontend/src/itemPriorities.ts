import type { Item } from './types';

export type ItemGroup = 'due' | 'overdue' | 'all';
export function dayDistance(value: string | null, today: string) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const timestamp = Date.parse(value + 'T00:00:00Z');
  if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0, 10) !== value) return null;
  return (timestamp - Date.parse(today + 'T00:00:00Z')) / 86400000;
}

export function priorities(items: Item[], today: string) {
  const due: Item[] = [], overdue: Item[] = [];
  for (const item of items) {
    if (item.is_completed || item.verification_status === 'rejected') continue;
    if (item.category !== 'assignment') continue;
    const days = dayDistance(item.normalized_date, today);
    if (days == null) continue;
    if (days < 0) overdue.push(item);
    else if (days < 7) due.push(item);
  }
  const byDate = (a: Item, b: Item) => a.normalized_date!.localeCompare(b.normalized_date!);
  due.sort(byDate);
  overdue.sort((a,b) => byDate(b,a));
  return { due, overdue };
}

export function dueLabel(item: Item, today: string) {
  const days = dayDistance(item.normalized_date, today);
  if (days == null) return '未定截止日';
  if (days < 0) return '已过截止日';
  if (days === 0) return '今天截止';
  return days === 1 ? '明天截止' : days + ' 天后截止';
}

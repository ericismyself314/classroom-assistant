import { SlotOcc, WeekDay } from './types';

export const minutes = (time: string) => {
  const [h, m] = time.split(':').map(Number);
  return h * 60 + m;
};

// Distinct starts get rows: short, evening and overlapping lessons stay visible.
export function timetableRows(week: WeekDay[]): string[] {
  return [...new Set(week.flatMap(day => day.slots.map(slot => slot.start_time)))].sort();
}

export function nextOccurrence(week: WeekDay[], today: string, nowMinutes: number): { slot: SlotOcc; date: string; ongoing: boolean } | null {
  const occurrences = week.flatMap(day => day.slots.map(slot => ({ slot, date: day.date })))
    .sort((a, b) => a.date.localeCompare(b.date) || a.slot.start_time.localeCompare(b.slot.start_time));
  const next = occurrences.find(({ slot, date }) => date > today || (date === today && minutes(slot.end_time) > nowMinutes));
  return next ? { ...next, ongoing: next.date === today && minutes(next.slot.start_time) <= nowMinutes } : null;
}

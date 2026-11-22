import { SlotOcc, WeekDay } from './types';

export const minutes = (time: string) => {
  const [h, m] = time.split(':').map(Number);
  return h * 60 + m;
};

export function timetableDays(week: WeekDay[]): WeekDay[] {
  return week.filter(day => day.weekday < 5 || day.slots.length > 0);
}

// Distinct starts get rows: short, evening and overlapping lessons stay visible.
export function timetableRows(week: WeekDay[]): string[] {
  return [...new Set(week.flatMap(day => day.slots.map(slot => slot.start_time)))].sort();
}

export function recordingCourseNow(week: WeekDay[], today: string, nowMinutes: number): string {
  const day = week.find(item => item.date === today);
  const active = day?.slots.filter(slot => minutes(slot.start_time) <= nowMinutes && nowMinutes < minutes(slot.end_time)) || [];
  const courses = [...new Set(active.map(slot => slot.course_id))];
  return courses.length === 1 ? courses[0] : '';
}

export function nextOccurrence(week: WeekDay[], today: string, nowMinutes: number): { slot: SlotOcc; date: string; ongoing: boolean } | null {
  const occurrences = week.flatMap(day => day.slots.map(slot => ({ slot, date: day.date })))
    .sort((a, b) => a.date.localeCompare(b.date) || a.slot.start_time.localeCompare(b.slot.start_time));
  const next = occurrences.find(({ slot, date }) => date > today || (date === today && minutes(slot.end_time) > nowMinutes));
  return next ? { ...next, ongoing: next.date === today && minutes(next.slot.start_time) <= nowMinutes } : null;
}

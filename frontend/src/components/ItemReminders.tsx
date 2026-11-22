import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import type { Course, Item } from '../types';
import { localDateISO } from '../localDate';
import { priorities } from '../itemPriorities';

export function useItemReminders(revision: number) {
  const [data, setData] = useState<{items: Item[]; courses: Course[]; semesterId: string} | null>(null);
  const [today, setToday] = useState(localDateISO);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const tick = () => setToday(localDateISO());
    const timer = window.setInterval(tick, 30000);
    window.addEventListener('focus', tick);
    return () => { clearInterval(timer); window.removeEventListener('focus', tick); };
  }, []);
  useEffect(() => {
    let live = true;
    setError('');
    Promise.all([api.important({}), api.courses(), api.state(true)]).then(([result, catalog, state]) => {
      if (live) setData({items: result.items, courses: catalog.courses, semesterId: state.semester?.id || ''});
    }).catch(() => { if (live) setError('未能读取重要事项。'); });
    return () => { live = false; };
  }, [revision, attempt]);
  const names = useMemo(() => new Map(data?.courses.map(c => [c.id, c.name])), [data?.courses]);
  const groups = useMemo(() => {
    const current = new Set(data?.courses.filter(c => c.semester_id === data.semesterId && !c.is_archived).map(c => c.id));
    return priorities(data?.items.filter(it => current.has(it.course_id)) || [], today);
  }, [data, today]);
  function applyUpdate(id: string, patch: Partial<Item>) {
    setData(previous => previous && ({...previous, items: previous.items.map(it => it.id === id ? {...it,...patch} : it)}));
  }
  return { data, groups, today, names, error, retry: () => setAttempt(n => n + 1), applyUpdate };
}


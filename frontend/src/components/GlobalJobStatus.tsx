import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import type { Job } from '../types';
import type { View } from '../App';
import { ACTIVE_JOB_STATUSES, terminalTransitions } from '../jobProgress';
import { JobProgress } from './JobProgress';

export function GlobalJobStatus({ navigate, onTerminal, currentSessionId }: { navigate: (view: View) => void; onTerminal: () => void; currentSessionId?: string }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [notices, setNotices] = useState<Job[]>([]);
  const [error, setError] = useState(false);
  const callback = useRef(onTerminal);
  callback.current = onTerminal;
  useEffect(() => {
    let live = true, busy = false;
    let previous: Record<string, string> | null = null;
    const mountedAt = Date.now();
    const poll = async () => {
      if (busy) return;
      busy = true;
      try {
        const result = await api.jobs();
        if (!live) return;
        const finished = terminalTransitions(previous, result.jobs, mountedAt);
        if (finished.length) {
          setNotices(old => [...old.filter(notice => !finished.some(job => job.session_id === notice.session_id)), ...finished].slice(-3));
          callback.current();
        }
        const running = new Set(result.jobs.filter(job => ACTIVE_JOB_STATUSES.has(job.status)).map(job => job.session_id));
        if (running.size) setNotices(old => old.filter(notice => !running.has(notice.session_id)));
        previous = { ...previous, ...Object.fromEntries(result.jobs.map(job => [job.id, job.status])) };
        setJobs(result.jobs); setError(false);
      } catch { if (live) setError(true); }
      finally { busy = false; }
    };
    void poll();
    const timer = window.setInterval(poll, 2000);
    return () => { live = false; window.clearInterval(timer); };
  }, []);
  const active = jobs.find(job => ACTIVE_JOB_STATUSES.has(job.status));
  const title = (job: Job) => job.session_title || job.course_name || '课堂';
  return <>
    {active && active.session_id !== currentSessionId && <section className="global-job-status" aria-label="当前处理任务">
      <div className="global-job-body"><span className="small muted">{title(active)}</span><JobProgress job={active} compact disconnected={error} />
        {error && <p className="small">暂时无法更新状态，正在重试连接。</p>}</div>
      <button type="button" onClick={() => navigate({ name: 'session', id: active.session_id })}>查看处理</button>
    </section>}
    <div className="job-notices" aria-live="polite">{notices.map(job => <div key={job.id} className={`job-finished-notice ${job.status === 'failed' ? 'is-failed' : ''}`}>
      <span>{title(job)}{job.status === 'completed' ? '已处理完成。' : job.status === 'failed' ? '处理失败，可稍后继续。' : '已停止处理，可稍后继续。'}</span>
      <button type="button" onClick={() => { setNotices(old => old.filter(notice => notice.id !== job.id)); navigate({ name: 'session', id: job.session_id }); }}>{job.status === 'completed' ? '查看结果' : '查看并继续'}</button>
      <button type="button" className="ghost" aria-label="关闭处理通知" onClick={() => setNotices(old => old.filter(value => value.id !== job.id))}>关闭</button>
    </div>)}</div>
  </>;
}

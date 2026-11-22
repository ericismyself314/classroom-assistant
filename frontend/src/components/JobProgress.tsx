import { useEffect, useState } from 'react';
import type { Job } from '../types';
import { jobPresentation } from '../jobProgress';
import './jobProgress.css';

export function JobProgress({ job, compact = false, disconnected = false }: { job: Job; compact?: boolean; disconnected?: boolean }) {
  const [now, setNow] = useState(Date.now());
  const state = jobPresentation(job, now, disconnected);
  useEffect(() => {
    if (!state.active || disconnected) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [state.active, job.id, disconnected]);
  return <div className={`job-progress ${compact ? 'is-compact' : ''}`}>
    <div className="job-progress-heading"><strong>{state.label}</strong>
      {state.active && job.status !== 'pending' && <span>{state.phasePercent === null ? '阶段进度待更新' : `本阶段 ${state.phasePercent}%`}</span>}
      {state.phaseMode && <span>{state.phaseMode}</span>}
    </div>
    {(state.active || state.overallPercent !== null) && <div className="job-overall">
      <div className="job-overall-label"><span>{state.terminal ? '总进度' : '总进度（估计）'}</span>
        <b>{state.overallPercent === null ? '正在估算' : `${state.overallPercent}%`}</b>
        {state.eta && <span className="job-eta" title={state.etaNote}>{state.eta}</span>}</div>
      <div className="job-track" role="progressbar" aria-label="总进度" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={state.overallPercent ?? undefined} aria-valuetext={state.overallPercent === null ? '正在估算' : undefined}>
        {state.overallPercent !== null && <i style={{ width: state.overallPercent + '%' }} />}</div>
    </div>}
    {state.detail && <p>{state.detail}</p>}
    <div className="job-measures">
      {state.phaseEta && (!compact || !state.etaNote) && <span>{state.phaseEta}</span>}
      {state.elapsed && <span>{state.elapsedLabel} {state.elapsed}</span>}
      {state.speed && <span>{state.speed}</span>}
    </div>
    {!compact && state.phaseElapsed && <p>本阶段已用 {state.phaseElapsed}</p>}
    {!compact && state.durations.length > 0 && <details className="job-timings"><summary>阶段耗时</summary>
      <div className="job-phase-times">{state.durations.map(phase =>
        <span key={phase.label}>{phase.label} {phase.elapsed}</span>)}</div>
    </details>}
  </div>;
}

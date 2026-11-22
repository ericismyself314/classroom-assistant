import type { Job } from './types';

export const ACTIVE_JOB_STATUSES = new Set(['pending', 'running', 'waiting_model', 'file_check', 'transcribing', 'analyzing', 'indexing']);
const TERMINAL = new Set(['completed', 'failed', 'cancelled']);
const PHASES: Record<string, string> = {
  file_check: '检查录音', transcribe: '语音识别', load_text: '加载文字模型', translate: '翻译逐字稿',
  analyze: '整理课堂内容', synthesize: '合并全课笔记', verify: '核查重要事项', save: '保存结果',
  pending: '等待开始', running: '准备处理', waiting_model: '等待模型',
  completed: '处理完成', failed: '处理失败', cancelled: '已停止',
};

export function durationText(seconds: number): string {
  if (!Number.isFinite(seconds)) return '';
  const value = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(value / 3600), minutes = Math.floor(value % 3600 / 60);
  return `${hours ? `${hours}小时` : ''}${minutes}分${String(value % 60).padStart(2, '0')}秒`;
}

function elapsed(start?: string | null, end?: string | null, now = Date.now()): string {
  if (!start) return '';
  const from = Date.parse(start), to = end ? Date.parse(end) : now;
  return Number.isFinite(from) && Number.isFinite(to) ? durationText((to - from) / 1000) : '';
}

function numeric(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

function approximateTime(seconds: number) {
  if (seconds < 60) return '不到 1 分钟';
  const minutes = Math.max(1, Math.round(seconds / 60));
  if (minutes < 60) return `约 ${minutes} 分钟`;
  const hours = Math.floor(minutes / 60), rest = minutes % 60;
  return `约 ${hours} 小时${rest ? ` ${rest} 分钟` : ''}`;
}

function speedText(rate: number, unit?: string | null) {
  const value = unit === 'audio_seconds' ? rate : rate * 60;
  const speed = value < 0.1 ? '不足 0.1' : String(Number(value.toFixed(value < 10 ? 1 : 0)));
  if (unit === 'audio_seconds') return `识别速度 ${speed} 倍实时`;
  return unit === 'segments' ? `每分钟处理 ${speed} 段` : '';
}

export function jobPresentation(job: Job, now = Date.now(), disconnected = false) {
  const progress = job.progress || {};
  const active = ACTIVE_JOB_STATUSES.has(job.status);
  const terminal = TERMINAL.has(job.status);
  const interrupted = job.status === 'failed' && Boolean(job.error_message?.includes('重启'));
  const stopping = active && String(job.stage || '').startsWith('正在停止');
  const fallback = ({ transcribing: 'transcribe', analyzing: 'analyze', indexing: 'save' } as Record<string, string>)[job.status] || job.status;
  const phase = progress.phase && !TERMINAL.has(progress.phase) ? progress.phase : progress.stopped_phase || fallback;
  const stage = String(job.stage || '').replace(/\s*\d+(?:\.\d+)?%/g, '').trim();
  const label = terminal ? PHASES[job.status] : stopping ? '正在停止' : PHASES[phase] || stage || '等待处理';
  const unit = progress.unit || (phase === 'transcribe' ? 'audio_seconds' : 'segments');
  const done = progress.completed, total = progress.total;
  const validTotal = numeric(total) && total > 0;
  const phasePercent = numeric(done) && validTotal ? Math.min(100, Math.floor(100 * done / total)) : null;
  const overallPercent = job.status === 'completed' ? 100 : numeric(progress.overall_percent)
    ? Math.min(99, Math.floor(progress.overall_percent)) : null;
  const details: string[] = [];
  if (active && stage && stage !== label && !TERMINAL.has(progress.phase || '')) details.push(stage);
  if (terminal && job.status !== 'completed' && PHASES[phase] && phase !== job.status) {
    details.push(`停止时正在${PHASES[phase]}`);
  }
  if (numeric(done) && unit !== 'operation') {
    if (unit === 'audio_seconds') {
      details.push(`已识别 ${durationText(done)}${validTotal ? ` / ${durationText(total)}` : '，录音总时长待确定'}`);
    } else if (validTotal) {
      details.push(`已处理 ${done} / ${total} 段`);
    }
  }
  const age = Math.max(0, (now - Date.parse(progress.observed_at || '')) / 1000);
  const phaseEstimate = progress.phase_eta_seconds;
  const stale = !Number.isFinite(age) || (numeric(phaseEstimate) && phaseEstimate > 0 && age > Math.max(15, phaseEstimate * 1.25))
    || (numeric(progress.sample_interval) && age > Math.max(90, progress.sample_interval * 4))
    || (!numeric(phaseEstimate) && !numeric(progress.sample_interval) && age > 600);
  const estimating = active && !stopping && !disconnected;
  const canUseRate = estimating && !stale;
  const phaseRemaining = numeric(phaseEstimate) ? phaseEstimate - age : 0;
  const remaining = numeric(progress.eta_seconds)
    ? progress.eta_seconds - (numeric(phaseEstimate) ? Math.min(age, phaseEstimate) : 0) : 0;
  const eta = estimating ? (canUseRate && remaining > 0 ? `剩余${approximateTime(remaining)}` : '剩余时间估算中') : '';
  const phaseEta = canUseRate && phaseRemaining > 0 ? `本阶段剩余${approximateTime(phaseRemaining)}` : '';
  const etaNote = canUseRate && remaining > 0 ? ({
    reference: '按本次速度和一次本地录音的阶段耗时比例估计',
    history: '参考本机相同设置的处理耗时', live: '按本阶段实测速率估计',
  }[progress.eta_basis || 'live']) : '';
  const end = interrupted ? progress.observed_at || progress.phase_started_at : job.finished_at || progress.observed_at || job.updated_at;
  const begin = job.started_at || (job.status === 'pending' ? null : job.created_at);
  return { label, detail: details.join(' · '), active, terminal, phasePercent, overallPercent, eta, phaseEta, etaNote,
    speed: canUseRate && numeric(progress.rate_per_second) && progress.rate_per_second > 0 ? speedText(progress.rate_per_second, unit) : '',
    phaseMode: phase === 'translate' && active ? (progress.translation_provider === 'api' ? 'API 翻译' : progress.translation_provider === 'local' ? '本地翻译' : '') : '',
    elapsedLabel: interrupted ? '中断前已记录' : progress.resumed ? '本次已用' : '已用',
    elapsed: active || end ? elapsed(begin, active ? null : end, now) : '',
    phaseElapsed: active ? elapsed(progress.phase_started_at, null, now) : '',
    durations: Object.entries(progress.phase_durations || {}).filter(([name, seconds]) => !TERMINAL.has(name) && numeric(seconds) && seconds > 0)
      .map(([name, seconds]) => ({ label: PHASES[name] || name, elapsed: durationText(seconds) })),
  };
}

export function terminalTransitions(previous: Record<string, string> | null, current: Job[], startedAfter = 0): Job[] {
  if (!previous) return [];
  return current.filter(job => TERMINAL.has(job.status) && (
    ACTIVE_JOB_STATUSES.has(previous[job.id]) ||
    (previous[job.id] === undefined && Date.parse(job.created_at || '') >= Math.floor(startedAfter / 1000) * 1000)
  ));
}

import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';

async function load(name) {
  const source = await readFile(new URL(`../src/${name}.ts`, import.meta.url), 'utf8');
  let code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
  if (code.includes("from './types'")) code = code.replace("from './types'", `from '${(await load('types')).url}'`);
  const url = `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
  return { ...await import(url), url };
}
let progress = {}, exporter = {};
try { progress = await load('jobProgress'); } catch { /* Assertions describe the missing feature. */ }
try { exporter = await load('sessionExport'); } catch { /* Assertions describe the missing feature. */ }

const job = { id: 'j', session_id: 's', status: 'analyzing', stage: '合并笔记与课程要求 95%',
  created_at: '2026-11-12T00:00:00+00:00', started_at: '2026-11-12T00:00:05+00:00',
  progress: { phase: 'synthesize', phase_started_at: '2026-11-12T00:01:00+00:00', phase_durations: { transcribe: 55 } } };

test('legacy progress shows its measured phase and time while leaving missing percentages unknown', () => {
  const result = progress.jobPresentation(job, Date.parse('2026-11-12T00:02:10Z'));
  assert.equal(result.label, '合并全课笔记');
  assert.equal(result.elapsed, '2分05秒');
  assert.equal(result.phaseElapsed, '1分10秒');
  assert.equal(result.overallPercent, null);
  assert.equal(result.phasePercent, null);
  assert.equal(result.eta, '剩余时间估算中');
  assert.ok(!JSON.stringify(result).includes('95%'));
});

test('completed task elapsed time stops at finished_at', () => {
  const result = progress.jobPresentation({ ...job, status: 'completed', finished_at: '2026-11-12T00:03:05Z' }, Date.parse('2026-11-13T00:00:00Z'));
  assert.equal(result.elapsed, '3分00秒');
  assert.equal(result.overallPercent, 100);
  assert.equal(result.label, '处理完成');
  assert.equal(result.eta, '');
});

const at = '2026-11-12T00:02:10Z';
const translating = { ...job, stage: '翻译第 2/4 块', progress: { phase: 'translate', translation_provider: 'api',
  completed: 5, total: 20, overall_percent: 16, unit: 'segments', phase_started_at: '2026-11-12T00:01:50Z',
  observed_at: at, rate_per_second: 0.25, sample_interval: 5, eta_seconds: 300, phase_eta_seconds: 60, eta_basis: 'history' } };

test('translation gives distinct whole/phase percentages, real counts, calibrated time and speed', () => {
  const state = progress.jobPresentation(translating, Date.parse(at));
  assert.equal(state.overallPercent, 16);
  assert.equal(state.label, '翻译逐字稿');
  assert.equal(state.phasePercent, 25);
  assert.equal(state.phaseMode, 'API 翻译');
  assert.ok(state.detail.includes('已处理 5 / 20 段'));
  assert.equal(state.eta, '剩余约 5 分钟');
  assert.equal(state.phaseEta, '本阶段剩余约 1 分钟');
  assert.equal(state.speed, '每分钟处理 15 段');
  assert.equal(state.elapsed, '2分05秒');
});

test('open-ended audio, first cloud batch, and stale or disconnected samples do not promise a finish', () => {
  const unknown = progress.jobPresentation({ ...translating, status: 'transcribing', stage: '语音识别中 · 第 2/2 段录音',
    progress: { phase: 'transcribe', unit: 'audio_seconds', completed: 66, total: null,
      observed_at: at, rate_per_second: 2.5, overall_percent: 0 } }, Date.parse(at));
  assert.equal(unknown.phasePercent, null);
  assert.equal(unknown.speed, '识别速度 2.5 倍实时');
  assert.ok(unknown.detail.includes('第 2/2 段录音') && unknown.detail.includes('总时长待确定'));
  assert.equal(unknown.eta, '剩余时间估算中');
  const onlyPhase = progress.jobPresentation({ ...translating, progress: { ...translating.progress, eta_seconds: null } }, Date.parse(at));
  assert.equal(onlyPhase.eta, '剩余时间估算中');
  assert.equal(onlyPhase.phaseEta, '本阶段剩余约 1 分钟');
  const stalled = progress.jobPresentation(translating, Date.parse(at) + 5 * 60_000);
  assert.equal(stalled.eta, '剩余时间估算中');
  assert.equal(stalled.speed, '');
  const disconnected = progress.jobPresentation(translating, Date.parse(at), true);
  assert.equal(disconnected.eta, '');
  assert.equal(disconnected.speed, '');
});

test('stopping, failure and restart never present a forecast or a successful result', () => {
  const stopping = progress.jobPresentation({ ...translating, stage: '正在停止，当前计算结束后退出' }, Date.parse(at));
  assert.equal(stopping.label, '正在停止');
  assert.equal(stopping.eta, '');
  const rebooted = { ...translating, status: 'failed', error_message: '应用重启时任务被中断', finished_at: '2026-11-15T10:00:00Z' };
  const stopped = progress.jobPresentation(rebooted, Date.parse('2026-11-20T10:00:00Z'));
  assert.equal(stopped.label, '处理失败');
  assert.equal(stopped.elapsed, '2分05秒');
  assert.equal(stopped.elapsedLabel, '中断前已记录');
  assert.equal(stopped.overallPercent, 16);
  assert.equal(stopped.eta, '');
  assert.ok(stopped.detail.includes('翻译逐字稿'));
  const old = progress.jobPresentation({ ...rebooted, progress: {} });
  assert.equal(old.elapsed, '');
  assert.equal(old.overallPercent, null);
  const finalizing = progress.jobPresentation({ ...translating, status: 'indexing', progress: { phase: 'save', overall_percent: 100 } });
  assert.equal(finalizing.overallPercent, 99);
});

test('global notices catch active transitions and quick tasks between two polls, not history at startup', () => {
  const finished = { ...job, status: 'completed', created_at: '2026-11-12T00:02:00Z' };
  assert.deepEqual(progress.terminalTransitions(null, [finished]), []);
  assert.equal(progress.terminalTransitions({ j: 'analyzing' }, [finished]).length, 1);
  assert.equal(progress.terminalTransitions({}, [finished], Date.parse('2026-11-12T00:01:00Z')).length, 1);
  assert.equal(progress.terminalTransitions({}, [finished], Date.parse('2026-11-12T00:02:00.250Z')).length, 1);
  assert.deepEqual(progress.terminalTransitions({}, [finished], Date.parse('2026-11-12T00:03:00Z')), []);
  assert.deepEqual(progress.terminalTransitions({ j: 'completed' }, [finished]), []);
});

test('export includes important items, dates, confirmation status, source and both transcript versions', () => {
  const text = exporter.sessionExportText({ session: { title: '合成课堂', session_date: '2026-11-13' },
    notes: { knowledge: [{ title: '债券', summary: '利率与价格', points: [{ text: '注意期限' }] }] },
    items: [{ category: 'assignment', title: '报告', details: '三人一组，PDF格式', normalized_date: '2026-11-25',
      verification_status: 'pending', confidence: 'uncertain', action_required: 1,
      evidence_data: JSON.stringify([{ start_seconds: 65, end_seconds: 70, quote: '报告截止十一月二十五日' }]) },
      { category: 'exam', title: '已拒绝误判', verification_status: 'rejected' }],
    segments: [{ start_seconds: 0, display_text: '债券价格', text: 'Bond prices' },
      { start_seconds: 30, display_text: null, text: 'No extra report is required.' }] });
  for (const word of ['报告', '2026-11-25', '待确认', '三人一组', 'PDF', '1:05', '截止十一月二十五日', 'Bond prices', 'No extra report is required.', '债券价格']) assert.ok(text.includes(word), word);
  assert.ok(!text.includes('已拒绝误判'));
  assert.ok(!text.includes('展示稿尚未生成'));
});

test('empty current items do not resurrect stale notes.non_knowledge', () => {
  const text = exporter.sessionExportText({ session: { title: '课堂' }, items: [], notes: { non_knowledge: [{ title: '已删除旧事项' }] } });
  assert.ok(!text.includes('已删除旧事项'));
});

test('unprocessed export tolerates absent notes and whitespace-only display text', () => {
  const text = exporter.sessionExportText({ session: { title: '录音原文' }, notes: null,
    segments: [{ display_text: '   ', text: 'Original speech is retained.' }] });
  assert.ok(text.includes('Original speech is retained.'));
});

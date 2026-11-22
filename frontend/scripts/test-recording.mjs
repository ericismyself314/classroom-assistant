import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';
let RecordingQueue;
try {
  const source = await readFile(new URL('../src/recordingQueue.ts', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
  ({ RecordingQueue } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`));
} catch { /* Assert missing implementation as an observable failure. */ }

test('recording chunks are written in order before finalization', async () => {
  assert.equal(typeof RecordingQueue, 'function');
  const received = [];
  const queue = new RecordingQueue(async (index, blob) => { received.push([index, await blob.text()]); });
  queue.add(new Blob(['first'])); queue.add(new Blob(['second']));
  await queue.flush();
  assert.deepEqual(received, [[0, 'first'], [1, 'second']]);
});
test('failed writes remain pending and retry with the same sequence', async () => {
  assert.equal(typeof RecordingQueue, 'function');
  let fail = true; const received = [];
  const queue = new RecordingQueue(async (index, blob) => {
    if (fail) throw new Error('disk full');
    received.push([index, await blob.text()]);
  });
  queue.add(new Blob(['a'])); queue.add(new Blob(['b']));
  await assert.rejects(queue.flush(), /disk full/);
  fail = false;
  await queue.flush();
  assert.deepEqual(received, [[0, 'a'], [1, 'b']]);
});

test('a saved recording remains saved when refreshing the classroom fails', async () => {
  let finishRecording;
  try {
    const source = await readFile(new URL('../src/finishRecording.ts', import.meta.url), 'utf8');
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
    ({ finishRecording } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`));
  } catch { /* Missing implementation is asserted below. */ }
  assert.equal(typeof finishRecording, 'function');
  const outcome = await finishRecording(async () => {}, async () => ({ session_id: 'class-1' }), async () => { throw new Error('refresh failed'); });
  assert.deepEqual(outcome, { sessionId: 'class-1', refreshFailed: true });
});

test('a failed audio finalization is not reported as saved', async () => {
  let finishRecording;
  try {
    const source = await readFile(new URL('../src/finishRecording.ts', import.meta.url), 'utf8');
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
    ({ finishRecording } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`));
  } catch { /* Missing implementation is asserted below. */ }
  assert.equal(typeof finishRecording, 'function');
  let refreshed = false;
  await assert.rejects(finishRecording(async () => {}, async () => { throw new Error('disk full'); }, async () => { refreshed = true; }), /disk full/);
  assert.equal(refreshed, false);
});

test('interruption survives stop and preserves the first failure reason', async () => {
  let RecordingStop;
  try {
    const source = await readFile(new URL('../src/recordingStop.ts', import.meta.url), 'utf8');
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
    ({ RecordingStop } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`));
  } catch { /* Missing implementation is asserted below. */ }
  assert.equal(typeof RecordingStop, 'function');
  const state = new RecordingStop();
  state.interrupt('写入失败');
  state.interrupt('麦克风停止');
  assert.equal(state.stopped(), '写入失败');
  assert.equal(state.stopped(), '写入失败');
});

test('an unexpected recorder stop is an interruption but a user stop is normal', async () => {
  let RecordingStop;
  try {
    const source = await readFile(new URL('../src/recordingStop.ts', import.meta.url), 'utf8');
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
    ({ RecordingStop } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`));
  } catch { /* Missing implementation is asserted below. */ }
  assert.equal(typeof RecordingStop, 'function');
  const stopped = new RecordingStop();
  stopped.stopByUser();
  assert.equal(stopped.stopped(), '');
  assert.match(new RecordingStop().stopped(), /意外停止/);
  stopped.interrupt('写入失败');
  assert.equal(stopped.stopped(), '写入失败');
});

test('overview recovery notice includes only inactive unfinished drafts with saved chunks', async () => {
  const source = await readFile(new URL('../src/recordingDrafts.ts', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
  const { recoverableDrafts } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
  assert.equal(typeof recoverableDrafts, 'function');
  const drafts = [
    { id: 'recover', status: 'recording', chunk_count: 2, active: false },
    { id: 'live', status: 'recording', chunk_count: 3, active: true },
    { id: 'empty', status: 'recording', chunk_count: 0, active: false },
    { id: 'saved', status: 'saved', chunk_count: 3, active: false },
  ];
  assert.deepEqual(recoverableDrafts(drafts).map((draft) => draft.id), ['recover']);
});

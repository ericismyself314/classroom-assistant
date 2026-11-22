import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/savedAudioClock.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
const { savedAudioClock } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);

test('acknowledged chunks use the buffered media timeline and old audio is released', async t => {
  let media, end = 0;
  const appended = [], removals = [], reported = [];
  const buffer = Object.assign(new EventTarget(), {
    buffered: { get length() { return end > 0 ? 1 : 0; }, end: () => end },
    updating: false,
    appendBuffer(bytes) { this.updating = true; appended.push(new Uint8Array(bytes)[0]); },
    remove(start, stop) { this.updating = true; removals.push([start, stop]); },
  });
  class Source extends EventTarget {
    static isTypeSupported() { return true; }
    constructor() { super(); media = this; }
    addSourceBuffer() { return buffer; }
  }
  const reader = Object.assign(new EventTarget(), { removeAttribute() {}, load() {} });
  globalThis.MediaSource = Source;
  globalThis.document = { createElement: () => reader };
  t.after(() => { delete globalThis.MediaSource; delete globalThis.document; });
  t.mock.method(URL, 'createObjectURL', () => 'blob:test');
  const revoke = t.mock.method(URL, 'revokeObjectURL', () => {});
  const clock = savedAudioClock('audio/webm', s => reported.push(s));
  clock.append(new Blob([new Uint8Array([1])]));
  clock.append(new Blob([new Uint8Array([2])]));
  assert.deepEqual(reported, []);
  media.dispatchEvent(new Event('sourceopen'));
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(appended, [1]);
  function finish(at) {
    end = at; buffer.updating = false; buffer.dispatchEvent(new Event('updateend'));
  }
  finish(1.28);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(appended, [1, 2]);
  finish(32.5);
  assert.deepEqual(reported, [1.28, 32.5]);
  assert.deepEqual(removals, [[0, 27.5]]);
  finish(32.5);
  assert.equal(removals.length, 1);
  clock.close(); clock.close();
  assert.equal(revoke.mock.callCount(), 1);
});

test('recordings can continue without an optional media time reader', () => {
  assert.equal(savedAudioClock('audio/webm', assert.fail), null);
});

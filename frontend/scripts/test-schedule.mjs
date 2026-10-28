import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/schedule.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
const { nextOccurrence, timetableRows } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const slot = (start, end, name = '课程') => ({ start_time: start, end_time: end, course_name: name });
const day = (date, slots) => ({ date, slots });

test('Today finishing does not hide the next teaching day', () => {
  const week = [day('2026-10-21', [slot('08:00', '09:30')]), day('2026-10-22', [slot('10:00', '11:30')])];
  assert.equal(nextOccurrence(week, '2026-10-21', 18 * 60).date, '2026-10-22');
});
test('An ongoing lesson stays actionable; a finished one does not', () => {
  const week = [day('2026-10-21', [slot('10:00', '11:30'), slot('14:00', '15:30')])];
  assert.equal(nextOccurrence(week, '2026-10-21', 620).ongoing, true);
  assert.equal(nextOccurrence(week, '2026-10-21', 690).slot.start_time, '14:00');
  assert.equal(nextOccurrence(week, '2026-10-21', 930), null);
});
test('Distinct times keep evening and irregular lessons visible', () => {
  const week = [day('2026-10-21', [slot('19:35', '21:00'), slot('08:45', '10:00')]), day('2026-10-22', [slot('08:45', '10:00')])];
  assert.deepEqual(timetableRows(week), ['08:45', '19:35']);
});
test('Empty and past weeks have no invented next lesson', () => {
  assert.equal(nextOccurrence([], '2026-10-21', 600), null);
  assert.equal(nextOccurrence([day('2026-10-20', [slot('19:00', '21:00')])], '2026-10-21', 600), null);
});
test('An unsorted API response still selects the ongoing class before a later class', () => {
  const week = [day('2026-10-22', [slot('08:00', '10:00', 'Tomorrow')]), day('2026-10-21', [slot('14:00', '15:30', 'Later'), slot('10:00', '12:00', 'Now')])];
  assert.equal(nextOccurrence(week, '2026-10-21', 660).slot.course_name, 'Now');
});

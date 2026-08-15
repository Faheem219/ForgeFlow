// `parseBuildReport` must never hand the panel something it can crash on.
//
// The panel calls `.length` / `.join` on four array fields, so an old, truncated or hand-edited
// artifact used to take the whole Build tab down. Defaulting every field here — including
// `phases: []` — is also what makes a pre-phase-56 report render exactly as it always did.

import { describe, expect, it } from 'vitest';

import { parseBuildReport } from './api';

const PRE_REWORK_REPORT = JSON.stringify({
  boot_status: 'healthy',
  files_changed: ['backend/src/features/todo/todo.ts'],
  features_built: ['Todos'],
  follow_ups: [],
  notes: ['a note'],
  commit: 'abc1234def',
  tests_passed: true,
  summary: 'Built the todo app.',
  plan: 'the plan',
});

describe('parseBuildReport', () => {
  it('defaults every array on a pre-rework report and yields no phases', () => {
    const report = parseBuildReport(PRE_REWORK_REPORT);

    expect(report).not.toBeNull();
    expect(report?.summary).toBe('Built the todo app.');
    expect(report?.files_changed).toEqual(['backend/src/features/todo/todo.ts']);
    // The phase-56 fields are absent from the artifact — they must still be safe to read.
    expect(report?.phases).toEqual([]);
    expect(report?.outcome).toBe('');
    expect(report?.stop_reason).toBeNull();
    expect(report?.verification).toBeNull();
  });

  it.each([
    ['null', null],
    ['undefined', undefined],
    ['empty string', ''],
    ['truncated json', '{'],
    ['a bare array', '[]'],
    ['a null array field', '{"files_changed":null}'],
    ['non-string array members', '{"notes":[1,2]}'],
    ['a number', '42'],
    ['nested garbage', '{"phases":[1,null,{"no":"title"}]}'],
  ])('never throws and never yields a non-array on %s', (_label, content) => {
    const report = parseBuildReport(content as string | null | undefined);

    if (report === null) return; // a hard parse failure is a legitimate outcome
    for (const field of [
      report.files_changed,
      report.features_built,
      report.follow_ups,
      report.notes,
      report.phases,
    ]) {
      expect(Array.isArray(field)).toBe(true);
    }
  });

  it('parses phases and the stop reason on a phased report', () => {
    const report = parseBuildReport(
      JSON.stringify({
        ...JSON.parse(PRE_REWORK_REPORT),
        phases: [
          { id: 'be-core', title: 'Backend API', kind: 'backend', status: 'done', files: ['a.ts'] },
          { id: 'fe-core', title: 'Frontend', kind: 'frontend', status: 'failed' },
        ],
        outcome: 'partial',
        stop_reason: 'phases_incomplete',
        verification: { ok: false },
      }),
    );

    expect(report?.phases.map((p) => [p.id, p.status])).toEqual([
      ['be-core', 'done'],
      ['fe-core', 'failed'],
    ]);
    expect(report?.phases[1].files).toEqual([]); // defaulted, not undefined
    expect(report?.phases[1].attempts).toBe(0);
    expect(report?.outcome).toBe('partial');
    expect(report?.stop_reason).toBe('phases_incomplete');
    expect(report?.verification).toEqual({ ok: false });
  });

  it('drops phases with no title rather than rendering a blank row', () => {
    const report = parseBuildReport('{"phases":[{"id":"x"},{"id":"y","title":"Real"}]}');

    expect(report?.phases.map((p) => p.title)).toEqual(['Real']);
  });
});

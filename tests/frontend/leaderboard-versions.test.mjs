import test from 'node:test';
import assert from 'node:assert/strict';
import { selectBoard, versionLabel } from '../../web/leaderboard/boards.mjs';

const data = {
  schema_version: 3, generated_at: '2026-10-04',
  boards: [
    { preset: 'full', version: 'legacy-abc', protocol_status: 'unverified', total_runs: 90, models: [{ model: 'old', runs: 90 }] },
    { preset: 'full', version: 'v1', protocol_status: 'frozen', total_runs: 1, models: [{ model: 'same', runs: 1 }] },
    { preset: 'advanced', version: 'snapshot-xyz', protocol_status: 'recorded', total_runs: 2, models: [{ model: 'same', runs: 2 }] },
  ],
};

test('default prefers frozen full, never a merged list', () => {
  const board = selectBoard(data);
  assert.equal(board.preset, 'full');
  assert.equal(board.version, 'v1');
  assert.equal(board.total_runs, 1);
  assert.deepEqual(board.models.map(m => m.model), ['same']);
});

test('preset and version select only their own runs', () => {
  assert.equal(selectBoard(data, 'advanced', 'snapshot-xyz').models[0].runs, 2);
  assert.equal(selectBoard(data, 'full', 'legacy-abc').models[0].runs, 90);
  assert.equal(selectBoard(data, 'advanced').version, 'snapshot-xyz');
});

test('invalid URL selection cannot silently show a different version', () => {
  assert.throws(() => selectBoard(data, 'full', 'snapshot-xyz'), /没有/);
  assert.throws(() => selectBoard(data, 'missing'), /没有/);
  assert.throws(() => selectBoard({ schema_version: 2, models: [] }), /schema_version/);
});

test('legacy versions explicitly disclose unverified protocol', () => {
  assert.match(versionLabel(data.boards[0]), /未核验/);
  assert.match(versionLabel(data.boards[1]), /已冻结/);
});

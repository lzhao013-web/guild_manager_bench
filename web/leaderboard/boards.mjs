// 每次只返回一个榜单，不把不同 preset 的模型集合混在一起。
export function selectBoard(data, preset, version) {
  if (data.schema_version !== 3 || !Array.isArray(data.boards) || !data.boards.length) {
    throw new Error('需要 schema_version=3 的分版本排行榜数据');
  }
  const selectedPreset = preset || data.default_board?.preset || (data.boards.some(b => b.preset === 'full') ? 'full' : data.boards[0].preset);
  const candidates = data.boards.filter(b => b.preset === selectedPreset);
  if (!candidates.length) throw new Error(`没有 preset=${selectedPreset} 的运行`);
  const board = version
    ? candidates.find(b => b.version === version)
    : candidates.find(b => b.version === data.default_board?.version && b.preset === data.default_board?.preset)
      || candidates.find(b => b.version === 'v1') || [...candidates].sort((a, b) => b.total_runs - a.total_runs)[0];
  if (!board) throw new Error(`没有 ${selectedPreset}/${version} 的运行`);
  return { ...board, generated_at: data.generated_at };
}

export function versionLabel(board) {
  if (board.selection_mode === 'manual') return board.label;
  return board.protocol_status === 'unverified'
    ? `${board.version} · 历史协议未核验`
    : `${board.version}${board.protocol_status === 'frozen' ? ' · 已冻结' : ' · 已记录指纹'}`;
}

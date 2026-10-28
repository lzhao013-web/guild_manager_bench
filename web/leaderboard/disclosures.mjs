// 仅保留当前榜单内的展开状态，不写入存档或浏览器持久存储。
export function runPanelKey(run, panel) {
  return JSON.stringify([run.session_id || run.run_id, panel]);
}

export function createDisclosureState() {
  let scope = null;
  const opened = new Set();
  function remember(element) {
    const key = element.getAttribute('data-fold-key');
    if (!key || !element.isConnected) return;
    if (element.open) opened.add(key);
    else opened.delete(key);
  }
  return {
    remember,
    capture(root, nextScope) {
      if (scope !== nextScope) {
        opened.clear();
        scope = nextScope;
        return;
      }
      root.querySelectorAll('details[data-fold-key]').forEach(remember);
    },
    restore(root) {
      root.querySelectorAll('details[data-fold-key]').forEach(element => {
        element.open = opened.has(element.getAttribute('data-fold-key'));
      });
    },
    reset() {
      opened.clear();
      scope = null;
    },
  };
}

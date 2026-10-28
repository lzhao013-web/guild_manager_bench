export function keepRankingHeader(table) {
  const viewport = table.parentElement;
  const head = table.tHead;
  const row = head.rows[0];
  const cells = [...row.cells];
  const cols = [...table.querySelector(':scope > colgroup').children];
  const pageHeader = document.querySelector('.header');
  const original = { head: head.style.cssText, row: row.style.cssText,
    cells: cells.map(cell => cell.style.cssText), padding: table.style.paddingTop };
  let pinned = false;
  let height = 0;

  function release() {
    if (!pinned) return;
    head.style.cssText = original.head;
    row.style.cssText = original.row;
    cells.forEach((cell, index) => { cell.style.cssText = original.cells[index]; });
    table.style.paddingTop = original.padding;
    pinned = false;
  }
  function update() {
    const box = table.getBoundingClientRect();
    const top = pageHeader.getBoundingClientRect().bottom;
    if (innerWidth <= 640 || !box.width || box.top >= top || box.bottom <= top) {
      release();
      return;
    }
    if (!pinned) {
      height = head.getBoundingClientRect().height;
      table.style.paddingTop = `${height}px`;
      head.style.position = 'fixed';
      head.style.display = 'block';
      head.style.overflow = 'hidden';
      row.style.display = 'table';
      row.style.tableLayout = 'fixed';
      row.style.borderSpacing = '0';
      pinned = true;
    }
    const bounds = viewport.getBoundingClientRect();
    const widths = cols.map(col => col.getBoundingClientRect().width);
    head.style.top = `${Math.min(top, box.bottom - height)}px`;
    head.style.left = `${bounds.left}px`;
    head.style.width = `${viewport.clientWidth}px`;
    row.style.width = `${widths.reduce((sum, width) => sum + width, 0)}px`;
    row.style.transform = `translateX(${box.left + table.clientLeft - bounds.left}px)`;
    cells.forEach((cell, index) => { cell.style.width = `${widths[index]}px`; });
  }
  window.addEventListener('scroll', update, { passive: true });
  window.addEventListener('resize', update);
  viewport.addEventListener('scroll', update, { passive: true });
  const observer = new ResizeObserver(update);
  observer.observe(table);
  observer.observe(viewport);
  observer.observe(pageHeader);
  update();
  return () => {
    observer.disconnect();
    window.removeEventListener('scroll', update);
    window.removeEventListener('resize', update);
    viewport.removeEventListener('scroll', update);
    release();
  };
}

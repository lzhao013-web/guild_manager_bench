const MAX_WIDTH = 1600;
const clamp = (value, minimum) => Math.max(minimum, Math.min(MAX_WIDTH, Math.round(value)));

function readWidths(key) {
  const raw = localStorage.getItem(key);
  if (raw === null) return new Map();
  let value;
  try { value = JSON.parse(raw); }
  catch (error) {
    if (error instanceof SyntaxError) return new Map();
    throw error;
  }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return new Map();
  return new Map(Object.entries(value).filter(([, width]) => typeof width === 'number' && Number.isFinite(width) && width > 0)
    .map(([id, width]) => [id, Math.min(MAX_WIDTH, Math.round(width))]));
}

export function createColumnSizing(table, { key, columns }) {
  const storageKey = `gmb.column-widths.v1.${key}`;
  const preferences = readWidths(storageKey);
  const cells = [...table.tHead.rows[0].cells];
  const cols = [...table.querySelector(':scope > colgroup').children];
  const handles = cells.map(cell => cell.querySelector('[data-column-resize]'));
  const indexes = new Map(columns.map((column, index) => [column.id, index]));
  const originalWidth = table.style.width;
  const originalCols = cols.map(col => col.style.width);
  const viewport = table.parentElement;
  let viewportWidth = 0;
  let drag = null;

  function save() {
    if (preferences.size) localStorage.setItem(storageKey, JSON.stringify(Object.fromEntries(preferences)));
    else localStorage.removeItem(storageKey);
  }
  function aria(widths) {
    handles.forEach((handle, index) => {
      handle.setAttribute('aria-controls', table.id);
      handle.setAttribute('aria-valuemin', String(columns[index].minWidth));
      handle.setAttribute('aria-valuemax', String(MAX_WIDTH));
      handle.setAttribute('aria-valuenow', String(widths[index]));
      handle.setAttribute('aria-valuetext', `${widths[index]} 像素`);
    });
  }
  function measure() {
    const widths = cols.map(col => col.getBoundingClientRect().width);
    return widths.every(width => width > 0) ? widths.map((width, index) => clamp(width, columns[index].minWidth)) : null;
  }
  function clear() {
    table.style.width = originalWidth;
    table.removeAttribute('data-column-sized');
    cols.forEach((col, index) => { col.style.width = originalCols[index]; });
  }
  function apply(widths) {
    cols.forEach((col, index) => { col.style.width = `${widths[index]}px`; });
    table.style.width = `${widths.reduce((sum, width) => sum + width, 0)}px`;
    table.setAttribute('data-column-sized', '');
    aria(widths);
  }
  function restore() {
    if (!viewport.getBoundingClientRect().width) return;
    clear();
    const natural = measure();
    if (!natural) return;
    viewportWidth = viewport.getBoundingClientRect().width;
    if (columns.some(column => preferences.has(column.id))) {
      apply(natural.map((width, index) => clamp(preferences.get(columns[index].id) ?? width, columns[index].minWidth)));
    } else aria(natural);
  }
  function handleFor(event) {
    const handle = event.target.closest('[data-column-resize]');
    return handle?.closest('table') === table ? handle : null;
  }
  function stop(event) { event.preventDefault(); event.stopPropagation(); }
  function finish(cancel) {
    const current = drag;
    if (!current) return;
    drag = null;
    table.classList.remove('is-column-resizing');
    if (!cancel && current.width !== current.widths[current.index]) {
      preferences.set(columns[current.index].id, current.width);
      save();
    } else {
      table.style.width = current.before.width;
      if (current.before.sized) table.setAttribute('data-column-sized', '');
      else table.removeAttribute('data-column-sized');
      cols.forEach((col, index) => { col.style.width = current.before.cols[index]; });
      aria(current.widths);
    }
    if (current.handle.hasPointerCapture(current.pointerId)) current.handle.releasePointerCapture(current.pointerId);
    if (viewport.getBoundingClientRect().width !== viewportWidth) restore();
  }
  function down(event) {
    const handle = handleFor(event);
    if (!handle || event.button !== 0 || drag) return;
    const widths = measure();
    if (!widths) return;
    stop(event);
    handle.focus({ preventScroll: true });
    const index = indexes.get(handle.dataset.columnResize);
    drag = { index, widths, width: widths[index], x: event.clientX, pointerId: event.pointerId, handle,
      before: { width: table.style.width, cols: cols.map(col => col.style.width), sized: table.hasAttribute('data-column-sized') } };
    handle.setPointerCapture(event.pointerId);
    table.classList.add('is-column-resizing');
  }
  function move(event) {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const width = clamp(drag.widths[drag.index] + event.clientX - drag.x, columns[drag.index].minWidth);
    if (width === drag.width) return;
    drag.width = width;
    apply(drag.widths.map((value, index) => index === drag.index ? width : value));
  }
  function up(event) { if (drag?.pointerId === event.pointerId) finish(false); }
  function cancel(event) { if (drag?.pointerId === event.pointerId) finish(true); }
  function click(event) { if (handleFor(event)) stop(event); }
  function reset(event) {
    const handle = handleFor(event);
    if (!handle) return;
    stop(event);
    finish(true);
    preferences.delete(handle.dataset.columnResize);
    save();
    restore();
  }
  function keyboard(event) {
    const handle = handleFor(event);
    if (!handle || drag) return;
    const index = indexes.get(handle.dataset.columnResize);
    const widths = measure();
    if (!widths) return;
    const next = event.key === 'ArrowLeft' ? widths[index] - 16
      : event.key === 'ArrowRight' ? widths[index] + 16
      : event.key === 'Home' ? columns[index].minWidth
      : event.key === 'End' ? MAX_WIDTH : null;
    if (next === null) return;
    stop(event);
    widths[index] = clamp(next, columns[index].minWidth);
    apply(widths);
    preferences.set(columns[index].id, widths[index]);
    save();
  }
  const listeners = { pointerdown: down, pointermove: move, pointerup: up, pointercancel: cancel,
    lostpointercapture: cancel, click, dblclick: reset, keydown: keyboard };
  for (const [name, handler] of Object.entries(listeners)) table.addEventListener(name, handler, true);
  const observer = new ResizeObserver(() => {
    const width = viewport.getBoundingClientRect().width;
    if (!drag && width > 0 && width !== viewportWidth) restore();
  });
  observer.observe(viewport);
  restore();
  return {
    refresh() {
      if (drag) return;
      const widths = measure();
      if (widths) aria(widths);
    },
    destroy() {
      finish(true);
      observer.disconnect();
      for (const [name, handler] of Object.entries(listeners)) table.removeEventListener(name, handler, true);
      clear();
    },
  };
}

export function enhanceResizableTable(table, options) {
  const cells = [...table.tHead.rows[0].cells];
  const oldLabels = cells.map(cell => cell.getAttribute('aria-label'));
  let group = table.querySelector(':scope > colgroup');
  const created = !group;
  if (created) {
    group = table.ownerDocument.createElement('colgroup');
    for (const _ of options.columns) group.append(table.ownerDocument.createElement('col'));
    table.insertBefore(group, table.tHead);
  }
  table.classList.add('resizable-table');
  const handles = options.columns.map((column, index) => {
    cells[index].setAttribute('aria-label', column.label);
    const handle = table.ownerDocument.createElement('div');
    handle.className = 'column-resize-handle';
    handle.dataset.columnResize = column.id;
    handle.tabIndex = 0;
    handle.setAttribute('role', 'separator');
    handle.setAttribute('aria-orientation', 'vertical');
    handle.setAttribute('aria-label', `调整${column.label}列宽`);
    handle.title = '拖动调整列宽，双击恢复默认';
    cells[index].append(handle);
    return handle;
  });
  const sizing = createColumnSizing(table, options);
  return () => {
    sizing.destroy();
    handles.forEach(handle => handle.remove());
    cells.forEach((cell, index) => {
      if (oldLabels[index] === null) cell.removeAttribute('aria-label');
      else cell.setAttribute('aria-label', oldLabels[index]);
    });
    if (created) group.remove();
    table.classList.remove('resizable-table');
  };
}

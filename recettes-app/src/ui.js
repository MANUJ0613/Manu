// Briques d'interface : pages empilées, feuilles du bas, messages, icônes, vignettes.
import { $, esc, f0 } from './util.js';
import { photoURL } from './store.js';
import { perPortion, domVar, hasMacros } from './macros.js';

/* ============================================================ icônes (traits 24×24) */
const P = {
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
  back: '<path d="M15 5 8 12l7 7"/>',
  next: '<path d="m9 5 7 7-7 7"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  more: '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
  book: '<path d="M5 4.5h9.5a3 3 0 0 1 3 3V20H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h9.5"/>',
  books: '<rect x="3.5" y="4" width="7" height="16" rx="1.5"/><rect x="13.5" y="4" width="7" height="16" rx="1.5"/><path d="M7 8h0M17 8h0"/>',
  recipes: '<rect x="4" y="3.5" width="16" height="17" rx="2.5"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
  cart: '<path d="M3 4h2.2l2.1 11.2a1.5 1.5 0 0 0 1.5 1.3h8.6a1.5 1.5 0 0 0 1.5-1.2L20.5 8H6.1"/><circle cx="9.5" cy="20" r="1.2"/><circle cx="17" cy="20" r="1.2"/>',
  user: '<circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20.5c.9-3.9 3.9-6 7.5-6s6.6 2.1 7.5 6"/>',
  image: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><circle cx="9" cy="10" r="1.8"/><path d="m21 16-5-5-8 8"/>',
  text: '<path d="M5 6h14M12 6v13M9 19h6"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  pen: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m14 6 4 4"/>',
  share: '<path d="M12 15V4M8 8l4-4 4 4"/><path d="M5 12v6.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V12"/>',
  shareIn: '<circle cx="18" cy="5.5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="18.5" r="2.5"/><path d="m8.2 10.8 7.6-4M8.2 13.2l7.6 4"/>',
  trash: '<path d="M4.5 7h15M10 11v6M14 11v6M6.5 7l1 12.5a1.5 1.5 0 0 0 1.5 1.4h6a1.5 1.5 0 0 0 1.5-1.4l1-12.5M9.5 7V4.5h5V7"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  scale: '<path d="M4 5.5h16"/><path d="M12 5.5V9"/><rect x="3.5" y="9" width="17" height="11" rx="2.5"/><circle cx="12" cy="14.5" r="3"/><path d="m12 14.5 1.7-1.5"/>',
  label: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z"/>',
  flame: '<path d="M12 21c-3.6 0-6-2.4-6-5.6 0-3.8 3.4-5.6 3.4-9.4 2.7 1.4 4 3.6 3.8 6.1 1-.5 1.7-1.5 1.9-2.8 1.7 1.4 2.9 3.6 2.9 6.1 0 3.2-2.4 5.6-6 5.6z"/>',
  swap: '<path d="M7 7h11l-3-3M17 17H6l3 3"/>',
  sort: '<path d="M7 4v16M4 7l3-3 3 3M17 20V4M14 17l3 3 3-3"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  rows: '<rect x="4" y="4.5" width="5" height="5" rx="1.2"/><rect x="4" y="14.5" width="5" height="5" rx="1.2"/><path d="M12 6h8M12 8.5h5M12 16h8M12 18.5h5"/>',
  key: '<circle cx="8" cy="15" r="3.8"/><path d="m10.8 12.3 7.7-7.8M16 7l2.5 2.5M14 9l2 2"/>',
  download: '<path d="M12 4v11M8 11l4 4 4-4"/><path d="M5 19.5h14"/>',
  upload: '<path d="M12 15V4M8 8l4-4 4 4"/><path d="M5 19.5h14"/>',
  help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.7 9.5a2.4 2.4 0 1 1 3.3 2.2c-.6.3-1 .8-1 1.5v.5M12 17h0"/>',
  bolt: '<path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/>',
  target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  sparkle: '<path d="M12 3.5l1.8 5 5 1.8-5 1.8-1.8 5-1.8-5-5-1.8 5-1.8z"/><path d="M19 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
  lock: '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
  unlock: '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 7.7-1.5"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
};
export const icon = (name, cls = '') => `<svg class="ic ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name] || ''}</svg>`;

/* ============================================================ pages plein écran */
export const stack = [];
export function pushPage({ cls = '', render, onClose, onBack, live = true, state = {} }) {
  const el = document.createElement('section');
  el.className = 'page ' + cls;
  $('#pages').appendChild(el);
  const page = { el, render, onClose, onBack, live, state };
  page.close = () => closePage(page);
  page.refresh = () => { if (page.render && page.el.isConnected) page.render(page); };
  stack.push(page);
  page.refresh();
  document.documentElement.classList.add('has-page');
  requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('in')));
  return page;
}
export function closePage(page) {
  const i = stack.indexOf(page);
  if (i < 0) return;
  stack.splice(i, 1);
  page.el.classList.remove('in');
  page.el.classList.add('out');
  setTimeout(() => page.el.remove(), 260);
  if (page.onClose) { try { page.onClose(); } catch (e) { console.error(e); } }
  if (!stack.length) document.documentElement.classList.remove('has-page');
}
export function refreshPages() { for (const p of stack) if (p.live) p.refresh(); }
export const topPage = () => stack[stack.length - 1] || null;

/* ============================================================ feuilles du bas */
export const sheets = [];
export function openSheet({ title = '', html = '', cls = '', onClose, full = false }) {
  const wrap = document.createElement('div');
  wrap.className = 'sheet-wrap';
  wrap.innerHTML = `<div class="scrim" data-close></div>
    <div class="sheet ${full ? 'full' : ''} ${cls}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="grab" aria-hidden="true"></div>
      ${title ? `<header class="sheet-h"><h2>${esc(title)}</h2><button type="button" class="icon-btn" data-close aria-label="Fermer">${icon('close')}</button></header>` : ''}
      <div class="sheet-b"></div>
    </div>`;
  document.body.appendChild(wrap);
  const sheetEl = $('.sheet', wrap);
  const sh = {
    el: wrap, sheet: sheetEl, body: $('.sheet-b', wrap), busy: false,
    close: () => {
      const i = sheets.indexOf(sh);
      if (i < 0) return;
      sheets.splice(i, 1);
      if (!sheets.length) document.documentElement.classList.remove('has-sheet');
      wrap.classList.remove('in');
      setTimeout(() => wrap.remove(), 240);
      if (onClose) { try { onClose(); } catch (e) { console.error(e); } }
    },
  };
  sh.body.innerHTML = html;
  wrap.addEventListener('click', e => { if (e.target.closest('[data-close]') && !sh.busy) sh.close(); });
  // glisser vers le bas pour fermer
  let y0 = null, dy = 0;
  const grabZone = e => e.target.closest('.grab, .sheet-h');
  sheetEl.addEventListener('touchstart', e => { if (grabZone(e)) { y0 = e.touches[0].clientY; dy = 0; sheetEl.style.transition = 'none'; } }, { passive: true });
  sheetEl.addEventListener('touchmove', e => { if (y0 == null) return; dy = Math.max(0, e.touches[0].clientY - y0); sheetEl.style.transform = `translateY(${dy}px)`; }, { passive: true });
  sheetEl.addEventListener('touchend', () => {
    if (y0 == null) return;
    sheetEl.style.transition = ''; sheetEl.style.transform = '';
    if (dy > 90 && !sh.busy) sh.close();
    y0 = null;
  });
  sheets.push(sh);
  document.documentElement.classList.add('has-sheet');
  requestAnimationFrame(() => requestAnimationFrame(() => wrap.classList.add('in')));
  return sh;
}

/** Menu d'actions simple. items : [{ label, icon, danger, run }] */
export function actionSheet(title, items) {
  const sh = openSheet({
    title,
    html: `<div class="menu">${items.map((it, i) => `<button type="button" class="menu-i ${it.danger ? 'danger' : ''}" data-i="${i}">${it.icon ? icon(it.icon) : ''}<span>${esc(it.label)}</span></button>`).join('')}</div>`,
  });
  sh.body.addEventListener('click', e => {
    const b = e.target.closest('[data-i]');
    if (!b) return;
    const it = items[+b.dataset.i];
    sh.close();
    if (it && it.run) setTimeout(() => it.run(), 120);
  });
  return sh;
}

export function confirmSheet({ title, body = '', ok = 'Confirmer', danger = false }) {
  return new Promise(res => {
    let done = false;
    const sh = openSheet({
      title,
      onClose: () => { if (!done) res(false); },
      html: `${body ? `<p class="lead">${body}</p>` : ''}<div class="row-btns"><button type="button" class="btn ghost" data-close>Annuler</button><button type="button" class="btn ${danger ? 'danger' : 'primary'}" data-ok>${esc(ok)}</button></div>`,
    });
    $('[data-ok]', sh.body).addEventListener('click', () => { done = true; res(true); sh.close(); });
  });
}

export function askText({ title, label = '', value = '', placeholder = '', ok = 'Valider', multiline = false }) {
  return new Promise(res => {
    let done = false;
    const sh = openSheet({
      title,
      onClose: () => { if (!done) res(null); },
      html: `<div class="field">${label ? `<label for="ask-in">${esc(label)}</label>` : ''}${multiline
        ? `<textarea id="ask-in" rows="6" placeholder="${esc(placeholder)}">${esc(value)}</textarea>`
        : `<input id="ask-in" autocomplete="off" value="${esc(value)}" placeholder="${esc(placeholder)}">`}</div>
        <button type="button" class="btn primary block" data-ok>${esc(ok)}</button>`,
    });
    const inp = $('#ask-in', sh.body);
    const go = () => { done = true; res(inp.value.trim()); sh.close(); };
    $('[data-ok]', sh.body).addEventListener('click', go);
    if (!multiline) inp.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
    setTimeout(() => { inp.focus(); if (inp.select) inp.select(); }, 280);
  });
}

/* ============================================================ messages */
let toastTimer = null;
export function toast(msg, { action, onAction, ms = 3200 } = {}) {
  let el = $('#toast');
  if (!el) { el = document.createElement('div'); el.id = 'toast'; el.setAttribute('role', 'status'); document.body.appendChild(el); }
  el.innerHTML = `<span>${esc(msg)}</span>${action ? `<button type="button">${esc(action)}</button>` : ''}`;
  if (action) $('button', el).addEventListener('click', () => { el.classList.remove('in'); if (onAction) onAction(); });
  el.classList.add('in');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('in'), action ? Math.max(ms, 5000) : ms);
}

/* ============================================================ retour Android */
export function handleBack() {
  if (sheets.length) { const s = sheets[sheets.length - 1]; if (!s.busy) s.close(); return true; }
  const p = topPage();
  if (p) { if (p.onBack && p.onBack()) return true; p.close(); return true; }
  return false;
}

/* ============================================================ vignettes de recettes */
export function thumbHTML(r, cls = 'thumb', onLoad) {
  const pp = perPortion(r);
  const url = r.photo ? photoURL(r.photo, onLoad) : null;
  if (url) return `<div class="${cls} has-img"><img src="${url}" alt="" loading="lazy" decoding="async"></div>`;
  return `<div class="${cls} no-img" style="--dom:${domVar(pp)}"><span class="kc">${hasMacros(pp) ? f0(pp.kcal) : '—'}<small>kcal</small></span></div>`;
}
export function macroLine(pp) {
  return `<span class="mp">P <b>${f0(pp.p)}</b></span><span class="mg">G <b>${f0(pp.c)}</b></span><span class="ml">L <b>${f0(pp.f)}</b></span>`;
}
export function busyHTML(msg) {
  return `<div class="busy"><span class="spinner" aria-hidden="true"></span><span class="busy-msg">${esc(msg)}</span></div>`;
}

/* ============================================================ navigation entre onglets (branchée par main.js) */
export const nav = { setTab: () => {}, tab: 'recettes' };

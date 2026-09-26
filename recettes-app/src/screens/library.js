// Onglet Recettes : « Livres de recettes » et « Toutes les recettes ».
import { S, savePrefs, saveBook } from '../store.js';
import { $, esc, norm, f0, f2, arr, plural } from '../util.js';
import { icon, thumbHTML, macroLine, actionSheet, askText, toast } from '../ui.js';
import { perPortion, bestFit, fit, compareMeals, mealById, lines, STW, hasMacros } from '../macros.js';
import { openRecipe } from './detail.js';
import { openBook } from './book.js';
import { openAddSheet, openHowTo } from './add.js';
import { photoURL } from '../store.js';

let q = '';
let rerender = () => {};

const SORTS = {
  recent: { label: 'Plus récentes', fn: (a, b) => (b.createdAt || 0) - (a.createdAt || 0) },
  az: { label: 'De A à Z', fn: (a, b) => a.title.localeCompare(b.title, 'fr') },
  prot: { label: 'Plus protéinées', fn: (a, b) => perPortion(b).p - perPortion(a).p },
  kcal: { label: 'Moins caloriques', fn: (a, b) => perPortion(a).kcal - perPortion(b).kcal },
  fit: { label: 'Les plus proches du meal', fn: null },
};

export function renderLibrary(root) {
  rerender = () => renderLibrary(root);
  const tab = S.prefs.libTab === 'books' ? 'books' : 'all';
  const hadFocus = document.activeElement && document.activeElement.id === 'lib-q';
  root.innerHTML = `
    <header class="top">
      <h1>Recettes</h1>
      <button type="button" class="icon-btn" data-act="howto" aria-label="Comment importer depuis Instagram ou TikTok">${icon('help')}</button>
    </header>
    <div class="seg" role="tablist">
      <button type="button" role="tab" data-lib="books" aria-selected="${tab === 'books'}">Livres de recettes</button>
      <button type="button" role="tab" data-lib="all" aria-selected="${tab === 'all'}">Toutes les recettes</button>
    </div>
    <div class="toolbar">
      <label class="search">${icon('search')}<input id="lib-q" type="search" enterkeyhint="search" autocomplete="off" placeholder="Rechercher une recette" value="${esc(q)}"></label>
      ${tab === 'all' ? `<button type="button" class="icon-btn boxed" data-act="sort" aria-label="Trier">${icon('sort')}</button>
      <button type="button" class="icon-btn boxed" data-act="view" aria-label="Changer l'affichage">${icon(S.prefs.view === 'rows' ? 'grid' : 'rows')}</button>` : ''}
    </div>
    <div id="lib-body"></div>`;
  const body = $('#lib-body', root);
  const paint = () => { body.innerHTML = tab === 'books' && !q ? booksHTML() : allHTML(); };
  paint();
  const inp = $('#lib-q', root);
  inp.addEventListener('input', () => { q = inp.value; paint(); });
  if (hadFocus) { inp.focus(); const n = inp.value.length; inp.setSelectionRange(n, n); }
  root.onclick = e => {
    const t = e.target;
    const lib = t.closest('[data-lib]');
    if (lib) { S.prefs.libTab = lib.dataset.lib; savePrefs(); rerender(); return; }
    const op = t.closest('[data-open]');
    if (op) { openRecipe(op.dataset.open); return; }
    const bk = t.closest('[data-book]');
    if (bk) { openBook(bk.dataset.book); return; }
    const meal = t.closest('[data-meal]');
    if (meal) { S.prefs.meal = meal.dataset.meal; savePrefs(); paint(); return; }
    const act = t.closest('[data-act]');
    if (!act) return;
    const a = act.dataset.act;
    if (a === 'howto') openHowTo();
    else if (a === 'add') openAddSheet();
    else if (a === 'newbook') newBook();
    else if (a === 'view') { S.prefs.view = S.prefs.view === 'rows' ? 'grid' : 'rows'; savePrefs(); rerender(); }
    else if (a === 'sort') {
      actionSheet('Trier les recettes', Object.entries(SORTS).map(([k, v]) => ({
        label: (S.prefs.sort === k ? '✓ ' : '') + v.label,
        run: () => { S.prefs.sort = k; savePrefs(); paint(); },
      })));
    } else if (a === 'clear-q') { q = ''; rerender(); }
  };
}

async function newBook() {
  const name = await askText({ title: 'Nouveau livre de recettes', label: 'Nom', placeholder: 'ex. Desserts protéinés', ok: 'Créer' });
  if (!name) return;
  const b = await saveBook({ name: name.slice(0, 60) });
  toast('Livre créé.');
  openBook(b.id);
}

/* ---------- livres */
function coverHTML(recipes) {
  const withPh = recipes.filter(r => r.photo && photoURL(r.photo, () => rerender())).slice(0, 4);
  if (!withPh.length) return `<div class="bcover empty">${icon('book')}</div>`;
  const cells = withPh.map(r => `<span><img src="${photoURL(r.photo)}" alt="" loading="lazy"></span>`);
  return `<div class="bcover n${cells.length}">${cells.join('')}</div>`;
}
function booksHTML() {
  const books = S.books.slice().sort((a, b) => (a.order || 0) - (b.order || 0));
  const cards = books.map(b => {
    const rs = S.recipes.filter(r => arr(r.books).includes(b.id));
    return `<button type="button" class="bcard" data-book="${esc(b.id)}">${coverHTML(rs)}<h3>${esc(b.name)}</h3><p>${plural(rs.length, 'recette', 'recettes')}</p></button>`;
  });
  const all = S.recipes;
  return `<div class="bgrid">
    <button type="button" class="bcard" data-lib="all">${coverHTML(all.slice().sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0)))}<h3>Toutes les recettes</h3><p>${plural(all.length, 'recette', 'recettes')}</p></button>
    ${cards.join('')}
    <button type="button" class="bcard new" data-act="newbook"><div class="bcover empty">${icon('plus')}</div><h3>Nouveau livre</h3><p>&nbsp;</p></button>
  </div>
  ${!all.length ? emptyHTML() : ''}`;
}

/* ---------- toutes les recettes */
function matches(r, words) {
  if (!words.length) return true;
  const hay = norm([r.title, r.notes, r.source && r.source.label, ...lines(r).map(l => l.name)].join(' '));
  return words.every(w => hay.includes(w));
}
export function fitLine(r, mealId) {
  if (mealId && mealId !== 'all') {
    const m = mealById(mealId);
    const f = m && fit(r, m);
    if (!f) return '';
    return `<p class="fitl st-${f.status}"><span class="dot"></span><span class="nw">${esc(STW[f.status])}</span><span class="muted nw">${f2(f.s)} ${f.s >= 2 ? 'portions' : 'portion'}</span></p>`;
  }
  const x = bestFit(r);
  if (!x) return '';
  return `<p class="fitl st-${x.fit.status}"><span class="dot"></span><span class="nw">${esc(x.meal.name)}</span><span class="muted nw">${esc(STW[x.fit.status])}</span></p>`;
}
export function cardHTML(r, mealId, rows = false) {
  const pp = perPortion(r);
  const mac = hasMacros(pp) ? `<p class="rc-m"><span class="kcal">${f0(pp.kcal)} kcal</span>${macroLine(pp)}</p>` : '<p class="rc-m muted">Macros à compléter</p>';
  return `<button type="button" class="rcard ${rows ? 'row' : ''}" data-open="${esc(r.id)}">
    ${thumbHTML(r, 'thumb', () => rerender())}
    <div class="rc-b"><h3>${esc(r.title)}</h3>${mac}${fitLine(r, mealId)}</div>
  </button>`;
}
function emptyHTML() {
  return `<div class="empty-state">
    <div class="es-ic">${icon('recipes')}</div>
    <h2>Aucune recette pour l’instant</h2>
    <p>Sur Instagram ou TikTok, appuie sur <b>Partager</b> puis choisis <b>Au gramme près</b>. Ou ajoute une recette avec le bouton +.</p>
    <div class="row-btns"><button type="button" class="btn primary" data-act="add">${icon('plus')}Ajouter une recette</button><button type="button" class="btn ghost" data-act="howto">Comment faire</button></div>
  </div>`;
}
function allHTML() {
  if (!S.recipes.length) return emptyHTML();
  const words = norm(q).split(' ').filter(Boolean);
  const meals = compareMeals();
  let mealId = S.prefs.meal;
  if (mealId !== 'all' && !mealById(mealId)) mealId = 'all';
  let list = S.recipes.filter(r => matches(r, words));
  if (mealId !== 'all') {
    const m = mealById(mealId);
    list = list.map(r => ({ r, f: fit(r, m) })).filter(x => x.f).sort((a, b) => a.f.score - b.f.score).map(x => x.r);
  } else if (S.prefs.sort === 'fit') {
    list = list.map(r => ({ r, b: bestFit(r) })).sort((a, b) => (a.b ? a.b.fit.score : 99) - (b.b ? b.b.fit.score : 99)).map(x => x.r);
  } else {
    list.sort((SORTS[S.prefs.sort] || SORTS.recent).fn);
  }
  const chips = meals.length ? `<div class="chips" role="group" aria-label="Pour quel meal ?">
      <button type="button" class="chip" data-meal="all" aria-pressed="${mealId === 'all'}">Toutes</button>
      ${meals.map(m => `<button type="button" class="chip" data-meal="${esc(m.id)}" aria-pressed="${mealId === m.id}">${esc(m.name)}</button>`).join('')}
    </div>` : '';
  const rows = S.prefs.view === 'rows';
  const count = `<p class="count">${plural(list.length, 'recette', 'recettes')}${mealId !== 'all' ? ' · triées par écart' : ''}</p>`;
  if (!list.length) return `${chips}${count}<div class="empty-state small"><p>Aucune recette ne correspond à « ${esc(q)} ».</p></div>`;
  return `${chips}${count}<div class="${rows ? 'rlist' : 'rgrid'}">${list.map(r => cardHTML(r, mealId, rows)).join('')}</div>`;
}

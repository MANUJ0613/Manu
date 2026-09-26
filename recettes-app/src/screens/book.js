// Livres de recettes : contenu d'un livre, rangement d'une recette.
import { S, bookById, saveBook, deleteBook, setRecipeBooks, recipeById } from '../store.js';
import { $, esc, arr, norm, plural } from '../util.js';
import { pushPage, icon, openSheet, actionSheet, askText, confirmSheet, toast } from '../ui.js';
import { cardHTML } from './library.js';
import { openRecipe } from './detail.js';

export function openBook(id) {
  if (!bookById(id)) return null;
  const page = pushPage({ cls: 'book', state: { id }, render });
  page.el.addEventListener('click', e => onClick(e, page));
  return page;
}

function render(page) {
  const b = bookById(page.state.id);
  if (!b) { page.close(); return; }
  const rs = S.recipes.filter(r => arr(r.books).includes(b.id)).sort((x, y) => x.title.localeCompare(y.title, 'fr'));
  page.el.innerHTML = `
    <div class="p-top">
      <button type="button" class="icon-btn" data-act="back" aria-label="Retour">${icon('back')}</button>
      <h1>${esc(b.name)}</h1>
      <button type="button" class="icon-btn" data-act="more" aria-label="Options du livre">${icon('more')}</button>
    </div>
    <div class="p-body">
      <p class="count">${plural(rs.length, 'recette', 'recettes')}</p>
      ${rs.length ? `<div class="rgrid">${rs.map(r => cardHTML(r, 'all')).join('')}</div>` : `<div class="empty-state small"><p>Ce livre est vide.</p></div>`}
      <button type="button" class="btn ghost block" data-act="fill">${icon('plus')}Ajouter des recettes</button>
    </div>`;
}

function onClick(e, page) {
  const b = bookById(page.state.id);
  if (!b) return;
  const op = e.target.closest('[data-open]');
  if (op) { openRecipe(op.dataset.open); return; }
  const a = e.target.closest('[data-act]');
  if (!a) return;
  const act = a.dataset.act;
  if (act === 'back') page.close();
  else if (act === 'fill') fillBook(b);
  else if (act === 'more') {
    actionSheet(b.name, [
      { label: 'Ajouter des recettes', icon: 'plus', run: () => fillBook(b) },
      { label: 'Renommer', icon: 'pen', run: async () => { const n = await askText({ title: 'Renommer le livre', value: b.name, ok: 'Renommer' }); if (n) { b.name = n.slice(0, 60); await saveBook(b); } } },
      { label: 'Supprimer le livre', icon: 'trash', danger: true, run: async () => {
        if (await confirmSheet({ title: 'Supprimer ce livre ?', body: 'Les recettes restent dans « Toutes les recettes ».', ok: 'Supprimer', danger: true })) { page.close(); await deleteBook(b.id); toast('Livre supprimé.'); }
      } },
    ]);
  }
}

function fillBook(b) {
  let q = '';
  const sel = new Set(S.recipes.filter(r => arr(r.books).includes(b.id)).map(r => r.id));
  const sh = openSheet({
    title: `Ajouter à « ${b.name} »`, full: true,
    html: `<label class="search">${icon('search')}<input type="search" id="fb-q" placeholder="Rechercher" autocomplete="off"></label><div id="fb-list" class="checklist"></div>
      <button type="button" class="btn primary block sticky-bottom" data-ok>Enregistrer</button>`,
  });
  const paint = () => {
    const w = norm(q).split(' ').filter(Boolean);
    const list = S.recipes.filter(r => { const h = norm(r.title); return w.every(x => h.includes(x)); }).sort((x, y) => x.title.localeCompare(y.title, 'fr'));
    $('#fb-list', sh.body).innerHTML = list.map(r => `<label class="check big"><input type="checkbox" data-r="${esc(r.id)}" ${sel.has(r.id) ? 'checked' : ''}><span>${esc(r.title)}</span></label>`).join('') || '<p class="muted">Aucune recette.</p>';
  };
  paint();
  $('#fb-q', sh.body).addEventListener('input', e => { q = e.target.value; paint(); });
  sh.body.addEventListener('change', e => { const c = e.target.closest('[data-r]'); if (c) { if (c.checked) sel.add(c.dataset.r); else sel.delete(c.dataset.r); } });
  $('[data-ok]', sh.body).addEventListener('click', async () => {
    for (const r of S.recipes) {
      const has = arr(r.books).includes(b.id);
      if (has !== sel.has(r.id)) await setRecipeBooks(r.id, has ? r.books.filter(x => x !== b.id) : [...arr(r.books), b.id]);
    }
    sh.close();
    toast('Livre mis à jour.');
  });
}

/** Choisir les livres d'une recette. */
export function openBookPicker(r) {
  const cur = recipeById(r.id) || r;
  const sel = new Set(arr(cur.books));
  const sh = openSheet({
    title: 'Ranger dans un livre',
    html: `<div id="bp-list" class="checklist"></div>
      <button type="button" class="btn ghost block" data-new>${icon('plus')}Nouveau livre</button>
      <button type="button" class="btn primary block" data-ok>Enregistrer</button>`,
  });
  const paint = () => {
    $('#bp-list', sh.body).innerHTML = S.books.length
      ? S.books.slice().sort((a, b) => (a.order || 0) - (b.order || 0)).map(b => `<label class="check big"><input type="checkbox" data-b="${esc(b.id)}" ${sel.has(b.id) ? 'checked' : ''}><span>${esc(b.name)}</span></label>`).join('')
      : '<p class="muted">Aucun livre pour l’instant.</p>';
  };
  paint();
  sh.body.addEventListener('change', e => { const c = e.target.closest('[data-b]'); if (c) { if (c.checked) sel.add(c.dataset.b); else sel.delete(c.dataset.b); } });
  $('[data-new]', sh.body).addEventListener('click', async () => {
    const name = await askText({ title: 'Nouveau livre', label: 'Nom', placeholder: 'ex. Petit-déj', ok: 'Créer' });
    if (!name) return;
    const b = await saveBook({ name: name.slice(0, 60) });
    sel.add(b.id);
    paint();
  });
  $('[data-ok]', sh.body).addEventListener('click', async () => {
    await setRecipeBooks(cur.id, [...sel].filter(id => bookById(id)));
    sh.close();
    toast('Rangement enregistré.');
  });
}

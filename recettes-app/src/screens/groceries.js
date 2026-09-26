// Onglet Courses : liste générée depuis les recettes (quantités additionnées), rangée par rayon.
import { S, recipeById, saveGroceries } from '../store.js';
import { $, esc, num, str, f0, f2, norm, rid, plural } from '../util.js';
import { icon, actionSheet, askText, confirmSheet, toast, nav } from '../ui.js';
import { lines, foodByName, unitsText } from '../macros.js';
import { shareText } from '../native.js';

const AISLE = {
  'Fruits': 'Fruits & légumes', 'Légumes': 'Fruits & légumes', 'Protéines': 'Viandes, poissons & œufs',
  'Produits laitiers': 'Crèmerie', 'Glucides': 'Féculents, pains & céréales', 'Lipides': 'Huiles, beurres & oléagineux',
  'Épicerie': 'Épicerie', 'Compléments': 'Compléments', 'Autre': 'Autres',
};
const ORDER = ['Fruits & légumes', 'Viandes, poissons & œufs', 'Crèmerie', 'Féculents, pains & céréales', 'Huiles, beurres & oléagineux', 'Épicerie', 'Compléments', 'Autres'];
const SKIP = /^(eau|eau froide|eau chaude|glaçons?)$/;

export function guessCat(name) {
  const n = norm(name);
  if (/(whey|isolat|isolate|creatine|eaa|karbolyn|maltodextrine|proteine en poudre|bcaa)/.test(n)) return 'Compléments';
  if (/(huile|beurre de|cacahuete|amande|noix|noisette|cajou|pistache|graines?)/.test(n)) return 'Lipides';
  if (/(poulet|boeuf|steak|thon|saumon|oeuf|jambon|dinde|cabillaud|crevette|porc|veau|colin|merlu|sardine|truite|lardon|bacon|viande|poisson|surimi)/.test(n)) return 'Protéines';
  if (/(lait|yaourt|fromage|skyr|creme|beurre|mozzarella|emmental|parmesan|feta|cheddar|ricotta|mascarpone|leerdammer|vache qui rit)/.test(n)) return 'Produits laitiers';
  if (/(riz|pate|pain|farine|avoine|semoule|quinoa|pomme de terre|patate|wrap|tortilla|galette|cereale|flocon|boulgour|vermicelle|couscous|granola|biscott)/.test(n)) return 'Glucides';
  if (/(pomme|banane|fraise|myrtille|framboise|mangue|ananas|citron|orange|fruit|kiwi|poire|peche|raisin|cerise|mure|datte)/.test(n)) return 'Fruits';
  if (/(salade|tomate|oignon|ail|carotte|courgette|poivron|brocoli|epinard|champignon|legume|concombre|haricot|poireau|chou|aubergine|cornichon|avocat)/.test(n)) return 'Légumes';
  return 'Épicerie';
}
const itemG = it => (it.parts || []).reduce((s, p) => s + num(p.g), 0) + num(it.extra);

export function addRecipeToGroceries(r, k = 1, { quiet = false } = {}) {
  const G = S.groceries;
  for (const l of lines(r)) {
    const name = str(l.name);
    if (!name || SKIP.test(norm(name))) continue;
    const food = foodByName(name);
    const key = norm(food ? food.name : name);
    const g = num(l.g) * k;
    let it = G.items.find(x => x.key === key && !x.checked) || G.items.find(x => x.key === key);
    if (!it) {
      it = { id: rid(), key, name: food ? food.name : name, cat: food ? food.cat : guessCat(name), parts: [], extra: 0, qty: '', checked: false };
      G.items.push(it);
    }
    it.parts = it.parts || [];
    it.parts.push({ rid: r.id, g });
    if (!(g > 0) && l.qty && !it.qty) it.qty = str(l.qty);
    it.checked = false;
  }
  const e = G.recipes.find(x => x.recipeId === r.id);
  if (e) e.k = num(e.k) + k; else G.recipes.push({ recipeId: r.id, title: r.title, k });
  saveGroceries();
  if (!quiet) toast(`${r.title} ajouté aux courses.`, { action: 'Voir', onAction: () => nav.setTab('courses') });
}

function removeRecipe(recipeId) {
  const G = S.groceries;
  G.items = G.items.filter(it => {
    it.parts = (it.parts || []).filter(p => p.rid !== recipeId);
    return it.parts.length || it.manual;
  });
  G.recipes = G.recipes.filter(x => x.recipeId !== recipeId);
  saveGroceries();
}

function qtyText(it) {
  const g = itemG(it);
  if (g > 0) {
    const u = unitsText(foodByName(it.name), g);
    return `${f0(g)} g${u ? ` · ${u}` : ''}`;
  }
  return it.qty || '';
}

export function renderGroceries(root) {
  const G = S.groceries;
  const todo = G.items.filter(it => !it.checked);
  const got = G.items.filter(it => it.checked);
  const groups = new Map();
  for (const it of todo) {
    const a = AISLE[it.cat] || 'Autres';
    if (!groups.has(a)) groups.set(a, []);
    groups.get(a).push(it);
  }
  const itemHTML = it => `<li class="gi ${it.checked ? 'done' : ''}">
    <label class="gcheck"><input type="checkbox" data-check="${esc(it.id)}" ${it.checked ? 'checked' : ''}><span class="box">${icon('check')}</span></label>
    <button type="button" class="gi-b" data-item="${esc(it.id)}"><span class="gi-n">${esc(it.name)}</span><span class="gi-q">${esc(qtyText(it))}</span></button>
  </li>`;
  const recipes = G.recipes.filter(x => recipeById(x.recipeId) || x.title);
  root.innerHTML = `
    <header class="top"><h1>Courses</h1><button type="button" class="icon-btn" data-act="more" aria-label="Options">${icon('more')}</button></header>
    <div class="shop-bar">
      <button type="button" class="btn small primary" data-act="add">${icon('plus')}Article</button>
      <button type="button" class="btn small ghost" data-act="share" ${todo.length ? '' : 'disabled'}>${icon('share')}Partager</button>
      <span class="muted">${plural(todo.length, 'article', 'articles')} à prendre</span>
    </div>
    ${recipes.length ? `<div class="chips wrap rchips">${recipes.map(x => `<span class="chip static">${esc((recipeById(x.recipeId) || x).title)}${num(x.k) && Math.abs(num(x.k) - 1) > 1e-9 ? ` ×${f2(x.k)}` : ''}<button type="button" data-unrec="${esc(x.recipeId)}" aria-label="Retirer de la liste">${icon('close')}</button></span>`).join('')}</div>` : ''}
    ${!G.items.length ? `<div class="empty-state"><div class="es-ic">${icon('cart')}</div><h2>Liste vide</h2><p>Sur une recette, appuie sur <b>Courses</b>. Ou dans <b>Planning</b>, mets toute ta semaine dans la liste : les quantités s'additionnent.</p></div>` : ''}
    ${ORDER.filter(a => groups.has(a)).map(a => `<section class="gsec"><h2>${esc(a)}</h2><ul class="glist">${groups.get(a).sort((x, y) => x.name.localeCompare(y.name, 'fr')).map(itemHTML).join('')}</ul></section>`).join('')}
    ${got.length ? `<section class="gsec got"><h2>Dans le panier (${got.length})</h2><ul class="glist">${got.map(itemHTML).join('')}</ul></section>` : ''}`;
  root.onclick = async e => {
    const t = e.target;
    const un = t.closest('[data-unrec]');
    if (un) { removeRecipe(un.dataset.unrec); return; }
    const itb = t.closest('[data-item]');
    if (itb) { itemMenu(itb.dataset.item); return; }
    const a = t.closest('[data-act]');
    if (!a) return;
    const act = a.dataset.act;
    if (act === 'add') addManual();
    else if (act === 'share') share();
    else if (act === 'more') {
      actionSheet('Liste de courses', [
        { label: 'Tout décocher', icon: 'check', run: () => { G.items.forEach(i => { i.checked = false; }); saveGroceries(); } },
        { label: 'Retirer les articles cochés', icon: 'trash', run: () => { G.items = G.items.filter(i => !i.checked); saveGroceries(); } },
        { label: 'Tout vider', icon: 'trash', danger: true, run: async () => { if (await confirmSheet({ title: 'Vider la liste ?', ok: 'Vider', danger: true })) { S.groceries = { items: [], recipes: [] }; saveGroceries(); } } },
      ]);
    }
  };
  root.onchange = e => {
    const c = e.target.closest('[data-check]');
    if (!c) return;
    const it = G.items.find(x => x.id === c.dataset.check);
    if (it) { it.checked = c.checked; saveGroceries(); }
  };
}

async function addManual() {
  const v = await askText({ title: 'Ajouter un article', label: 'Article (et quantité)', placeholder: 'ex. Blanc de poulet 500 g', ok: 'Ajouter' });
  if (!v) return;
  const m = v.match(/^(.*?)(?:\s+(\d+(?:[.,]\d+)?)\s*(kg|g))?\s*$/i);
  const name = str(m ? m[1] : v) || v;
  let g = m && m[2] ? num(m[2]) * (/kg/i.test(m[3]) ? 1000 : 1) : 0;
  const food = foodByName(name);
  const key = norm(food ? food.name : name);
  const ex = S.groceries.items.find(x => x.key === key && !x.checked);
  if (ex) { ex.extra = num(ex.extra) + g; ex.manual = true; }
  else S.groceries.items.push({ id: rid(), key, name: food ? food.name : name, cat: food ? food.cat : guessCat(name), parts: [], extra: g, qty: g ? '' : '', checked: false, manual: true });
  saveGroceries();
}

function itemMenu(id) {
  const it = S.groceries.items.find(x => x.id === id);
  if (!it) return;
  actionSheet(`${it.name}${qtyText(it) ? ' · ' + qtyText(it) : ''}`, [
    { label: it.checked ? 'Remettre à prendre' : 'Dans le panier', icon: 'check', run: () => { it.checked = !it.checked; saveGroceries(); } },
    { label: 'Modifier la quantité', icon: 'pen', run: async () => {
      const v = await askText({ title: it.name, label: 'Quantité totale en grammes', value: String(Math.round(itemG(it)) || ''), ok: 'Valider' });
      if (v == null) return;
      const g = num(v);
      it.extra = g - (it.parts || []).reduce((s, p) => s + num(p.g), 0);
      it.manual = true;
      saveGroceries();
    } },
    { label: 'Retirer', icon: 'trash', danger: true, run: () => { S.groceries.items = S.groceries.items.filter(x => x.id !== id); saveGroceries(); } },
  ]);
}

async function share() {
  const todo = S.groceries.items.filter(it => !it.checked);
  const out = ['Liste de courses'];
  const groups = new Map();
  for (const it of todo) { const a = AISLE[it.cat] || 'Autres'; if (!groups.has(a)) groups.set(a, []); groups.get(a).push(it); }
  for (const a of ORDER) if (groups.has(a)) { out.push('', a); for (const it of groups.get(a)) { const q = qtyText(it); out.push(`- ${it.name}${q ? ' : ' + q : ''}`); } }
  try { const how = await shareText('Liste de courses', out.join('\n')); if (how === 'copied') toast('Liste copiée.'); } catch (e) { /* annulé */ }
}


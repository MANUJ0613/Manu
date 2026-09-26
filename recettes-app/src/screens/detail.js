// Fiche recette : photo, macros, balance face au plan, ingrédients, étapes, actions.
import { S, recipeById, saveRecipe, deleteRecipe } from '../store.js';
import { $, esc, f0, f1, f2, sgn, num, arr, clone, norm } from '../util.js';
import { pushPage, icon, actionSheet, confirmSheet, openSheet, toast, thumbHTML } from '../ui.js';
import {
  perPortion, totals, servingsOf, stepFor, bestS, fitAt, fit, bestFit, verdictSub, compareMeals, mealById, hasMacros,
  lines, isSec, scaleQty, macroStatus, STT, STW, calibrate, substitute, lineFromFood, searchFoods, foodByName, unitsText,
} from '../macros.js';
import { openExternal, shareText } from '../native.js';
import { openEditor } from './editor.js';
import { openCook } from './cook.js';
import { openBookPicker } from './book.js';
import { openPlanPicker } from './planner.js';
import { addRecipeToGroceries } from './groceries.js';

const kcalStatus = (e, t) => { const r = Math.abs(e) / Math.max(1, t); return r <= 0.03 ? 'ok' : r <= 0.07 ? 'near' : 'far'; };

export function openRecipe(id, opts = {}) {
  const r = recipeById(id);
  if (!r) return null;
  const bf = bestFit(r);
  const meal = opts.meal && mealById(opts.meal) ? opts.meal : bf ? bf.meal.id : null;
  const m = meal && mealById(meal);
  const f = m ? fit(r, m) : null;
  const page = pushPage({
    cls: 'detail',
    state: { id, meal, s: opts.s ? Number(opts.s) : f ? f.s : 1, sv: servingsOf(r) },
    render,
  });
  page.el.addEventListener('scroll', () => {
    const top = $('.d-top', page.el);
    if (top) top.classList.toggle('solid', page.el.scrollTop > 180);
  }, { passive: true });
  page.el.addEventListener('click', e => onClick(e, page));
  return page;
}

function render(page) {
  const r = recipeById(page.state.id);
  if (!r) { page.close(); return; }
  const st = page.state;
  const n = servingsOf(r);
  const pp = perPortion(r);
  if (st.meal && !mealById(st.meal)) st.meal = compareMeals()[0] ? compareMeals()[0].id : null;
  const k = st.sv / n;
  const src = r.source || {};
  page.el.innerHTML = `
    <div class="d-top ${page.el.scrollTop > 180 ? 'solid' : ''}">
      <button type="button" class="icon-btn glass" data-act="back" aria-label="Retour">${icon('back')}</button>
      <p class="d-top-t">${esc(r.title)}</p>
      <button type="button" class="icon-btn glass" data-act="more" aria-label="Plus d'actions">${icon('more')}</button>
    </div>
    ${thumbHTML(r, 'hero', () => page.refresh())}
    <div class="d-body">
      <h1 class="d-title">${esc(r.title)}</h1>
      ${src.url || src.label ? `<button type="button" class="src-chip" data-act="source" ${src.url ? '' : 'disabled'}>${icon('link')}<span>${esc(src.label || src.url)}</span></button>` : ''}
      ${hasMacros(pp) ? `<div class="d-mac">
        <div class="d-kcal"><b>${f0(pp.kcal)}</b><span>kcal par portion</span></div>
        <div class="d-m"><span class="tp">Protéines</span><b>${f1(pp.p)}<small> g</small></b></div>
        <div class="d-m"><span class="tg">Glucides</span><b>${f1(pp.c)}<small> g</small></b></div>
        <div class="d-m"><span class="tl">Lipides</span><b>${f1(pp.f)}<small> g</small></b></div>
      </div>` : `<p class="warn-box">Macros incomplètes : ajoute les grammes des ingrédients (Modifier).</p>`}
      <div class="d-actions">
        <button type="button" data-act="cook">${icon('play')}<span>Cuisiner</span></button>
        <button type="button" data-act="plan">${icon('calendar')}<span>Planifier</span></button>
        <button type="button" data-act="groceries">${icon('cart')}<span>Courses</span></button>
        <button type="button" data-act="books">${icon('book')}<span>Livres</span></button>
      </div>
      ${balanceHTML(r, st)}
      <section class="d-sec">
        <div class="sec-h">
          <h2>Ingrédients</h2>
          <div class="stepper" aria-label="Nombre de portions">
            <button type="button" class="icon-btn small" data-act="sv-" aria-label="Moins">${icon('minus')}</button>
            <span><b>${f2(st.sv)}</b> ${st.sv >= 2 ? 'portions' : 'portion'}</span>
            <button type="button" class="icon-btn small" data-act="sv+" aria-label="Plus">${icon('plus')}</button>
          </div>
        </div>
        ${Math.abs(st.sv - n) > 1e-9 ? `<p class="hint">Quantités pour ${f2(st.sv)} ${st.sv >= 2 ? 'portions' : 'portion'} (recette d'origine : ${n}). <button type="button" class="linkish" data-act="sv-reset">Revenir à ${n}</button></p>` : ''}
        ${ingredientsHTML(r, k)}
      </section>
      ${arr(r.steps).length ? `<section class="d-sec">
        <div class="sec-h"><h2>Étapes</h2><button type="button" class="btn small ghost" data-act="cook">${icon('play')}Mode cuisine</button></div>
        <ol class="steps">${r.steps.map((s, i) => `<li><span class="sn">${i + 1}</span><p>${esc(s)}</p></li>`).join('')}</ol>
      </section>` : ''}
      ${r.notes ? `<section class="d-sec"><h2>Notes</h2><p class="notes">${esc(r.notes)}</p></section>` : ''}
      <div class="d-foot">
        <button type="button" class="btn ghost" data-act="edit">${icon('pen')}Modifier</button>
        <button type="button" class="btn ghost" data-act="share">${icon('share')}Partager</button>
      </div>
    </div>`;
  centerChip(page);
}

function centerChip(page) {
  const row = $('.bal-meals', page.el);
  const on = row && row.querySelector('[aria-pressed="true"]');
  if (!on) return;
  const a = on.getBoundingClientRect(), b = row.getBoundingClientRect();
  row.scrollLeft = Math.max(0, row.scrollLeft + (a.left - b.left) - (b.width - a.width) / 2);
}

/* ============================================================ balance */
function balanceHTML(r, st) {
  const meals = compareMeals();
  if (!meals.length) {
    return `<section class="bal bal-empty"><h2>${icon('scale')}Balance</h2><p>Ajoute tes meals dans <b>Profil › Mes meals</b> pour comparer cette recette à ton plan.</p></section>`;
  }
  const pp = perPortion(r);
  if (!hasMacros(pp)) return '';
  const m = mealById(st.meal) || meals[0];
  const n = servingsOf(r);
  const fv = fitAt(pp, m, st.s);
  const best = bestS(pp, m, stepFor(r));
  const chips = meals.map(x => {
    const f = fit(r, x);
    return `<button type="button" class="bchip" data-meal="${esc(x.id)}" aria-pressed="${x.id === m.id}"><span class="dot st-${f ? f.status : 'far'}"></span>${esc(x.name)}</button>`;
  }).join('');
  const row = (label, cls, key) => {
    const target = num(m[key]), me = st.s * pp[key], e = fv.e[key];
    const status = key === 'kcal' ? kcalStatus(e, target) : macroStatus(e);
    const pct = target > 0 ? Math.min(100, (me / target) * 100) : 0;
    return `<div class="brow ${cls}">
      <span class="bl">${label}</span><span class="bv">${f1(target)}</span><span class="bv">${f1(me)}</span><span class="be st-${status}">${sgn(e)}</span>
      <span class="bbar"><i style="width:${pct.toFixed(1)}%"></i><em style="left:${target > 0 ? 100 : 0}%"></em></span>
    </div>`;
  };
  return `<section class="bal st-${fv.status}">
    <div class="bal-h"><h2>${icon('scale')}Balance</h2><span class="muted">ta portion face à ton plan</span></div>
    <div class="bal-meals">${chips}</div>
    <p class="verdict">${esc(STT[fv.status])}</p>
    <p class="verdict-sub">${esc(verdictSub(fv))}</p>
    <div class="bal-portion">
      <div class="stepper big">
        <button type="button" class="icon-btn" data-act="s-" aria-label="Moins">${icon('minus')}</button>
        <b>${f2(st.s)}</b>
        <button type="button" class="icon-btn" data-act="s+" aria-label="Plus">${icon('plus')}</button>
      </div>
      <div><p><b>${st.s >= 2 ? 'portions' : 'portion'}</b></p><p class="muted">soit ${f0((st.s / n) * 100)} % de la recette</p></div>
    </div>
    <div class="btable">
      <div class="brow head"><span></span><span class="bv">Cible</span><span class="bv">Toi</span><span class="be">Écart</span></div>
      ${row('Kcal', 'k', 'kcal')}${row('Prot.', 'p', 'p')}${row('Gluc.', 'g', 'c')}${row('Lip.', 'l', 'f')}
    </div>
    <div class="bal-btns">
      ${best != null && Math.abs(best - st.s) > 1e-9 ? `<button type="button" class="btn small ghost" data-act="s-best">Meilleure portion : ${f2(best)}</button>` : ''}
      ${fv.status !== 'ok' || fv.worst > 0.6 ? `<button type="button" class="btn small primary" data-act="calibrate">${icon('target')}Caler sur ${esc(m.name)} au gramme près</button>` : `<span class="okline">${icon('check')}Remplace ton ${esc(m.name)} tel quel.</span>`}
    </div>
  </section>`;
}

/* ============================================================ ingrédients */
function ingredientsHTML(r, k) {
  const items = arr(r.ingredients);
  if (!items.length) return '<p class="muted">Aucun ingrédient.</p>';
  let li = -1;
  const rows = items.map(it => {
    if (isSec(it)) return `<h3 class="ing-sec">${esc(it.section)}</h3>`;
    li++;
    const g = num(it.g);
    const q = scaleQty(it.qty, k);
    return `<button type="button" class="ing" data-ing="${li}">
      <span class="ing-n">${esc(it.name)}${it.est ? '<i class="est" title="Valeurs estimées">≈</i>' : ''}</span>
      <span class="ing-q">${esc(q)}${g > 0 && !/\bg\b/.test(q) ? ` <small>${f0(g * k)} g</small>` : ''}</span>
    </button>`;
  });
  const t = totals(r);
  return `<div class="ings">${rows.join('')}</div>
    <p class="ing-tot">Recette entière (${servingsOf(r)} ${servingsOf(r) >= 2 ? 'portions' : 'portion'}) : <b>${f0(t.kcal)} kcal</b> · P ${f1(t.p)} · G ${f1(t.c)} · L ${f1(t.f)}</p>`;
}

/* ============================================================ actions */
function onClick(e, page) {
  const st = page.state;
  const r = recipeById(st.id);
  if (!r) return;
  const meal = e.target.closest('[data-meal]');
  if (meal) {
    st.meal = meal.dataset.meal;
    const f = fit(r, mealById(st.meal));
    if (f) st.s = f.s;
    page.refresh();
    return;
  }
  const ing = e.target.closest('[data-ing]');
  if (ing) { ingredientMenu(page, r, +ing.dataset.ing); return; }
  const b = e.target.closest('[data-act]');
  if (!b) return;
  const a = b.dataset.act;
  const n = servingsOf(r);
  const step = stepFor(r);
  if (a === 'back') page.close();
  else if (a === 'more') moreMenu(page, r);
  else if (a === 'source') { if (r.source && r.source.url) openExternal(r.source.url); }
  else if (a === 's+' || a === 's-') { st.s = Math.max(step, Math.round((st.s + (a === 's+' ? step : -step)) * 100) / 100); page.refresh(); }
  else if (a === 's-best') { const m = mealById(st.meal); const f = m && fit(r, m); if (f) st.s = f.s; page.refresh(); }
  else if (a === 'sv+' || a === 'sv-') {
    const d = n > 1 ? 1 : 0.5;
    st.sv = Math.max(d, Math.round((st.sv + (a === 'sv+' ? d : -d)) * 100) / 100);
    page.refresh();
  } else if (a === 'sv-reset') { st.sv = n; page.refresh(); }
  else if (a === 'cook') openCook(r, st.sv / n);
  else if (a === 'plan') openPlanPicker(r, st.meal, st.s);
  else if (a === 'groceries') groceriesSheet(r, st);
  else if (a === 'books') openBookPicker(r);
  else if (a === 'edit') openEditor(r);
  else if (a === 'share') shareRecipe(r);
  else if (a === 'calibrate') openCalibrate(page, r, mealById(st.meal));
}

function groceriesSheet(r, st) {
  const n = servingsOf(r);
  let sv = st.sv;
  const sh = openSheet({
    title: 'Ajouter aux courses',
    html: `<p class="lead">${esc(r.title)}</p>
      <div class="stepper big center"><button type="button" class="icon-btn" data-d="-1" aria-label="Moins">${icon('minus')}</button><b id="gs-v">${f2(sv)}</b><button type="button" class="icon-btn" data-d="1" aria-label="Plus">${icon('plus')}</button></div>
      <p class="muted center" id="gs-l">${sv >= 2 ? 'portions' : 'portion'}</p>
      <button type="button" class="btn primary block" data-ok>${icon('cart')}Ajouter les ingrédients</button>`,
  });
  sh.body.addEventListener('click', e => {
    const d = e.target.closest('[data-d]');
    if (d) {
      const step = n > 1 ? 1 : 0.5;
      sv = Math.max(step, sv + step * +d.dataset.d);
      $('#gs-v', sh.body).textContent = f2(sv);
      $('#gs-l', sh.body).textContent = sv >= 2 ? 'portions' : 'portion';
    }
    if (e.target.closest('[data-ok]')) {
      addRecipeToGroceries(r, sv / n);
      sh.close();
    }
  });
}

function moreMenu(page, r) {
  const items = [
    { label: 'Modifier', icon: 'pen', run: () => openEditor(r) },
    { label: 'Dupliquer', icon: 'copy', run: () => duplicate(r) },
    { label: 'Ranger dans un livre', icon: 'book', run: () => openBookPicker(r) },
    { label: 'Partager la recette', icon: 'share', run: () => shareRecipe(r) },
  ];
  if (r.source && r.source.url) items.push({ label: 'Voir la publication d’origine', icon: 'link', run: () => openExternal(r.source.url) });
  items.push({
    label: 'Supprimer', icon: 'trash', danger: true,
    run: async () => {
      if (await confirmSheet({ title: 'Supprimer la recette ?', body: `« ${esc(r.title)} » sera supprimée de ton téléphone.`, ok: 'Supprimer', danger: true })) {
        page.close();
        await deleteRecipe(r.id);
        toast('Recette supprimée.');
      }
    },
  });
  actionSheet(r.title, items);
}

async function duplicate(r) {
  const c = clone(r);
  c.id = ''; c.createdAt = 0; c.title = r.title + ' (copie)';
  const saved = await saveRecipe(c);
  toast('Copie créée.');
  openRecipe(saved.id);
}

export function recipeText(r) {
  const out = [r.title, `${servingsOf(r)} ${servingsOf(r) >= 2 ? 'portions' : 'portion'}`];
  const pp = perPortion(r);
  if (hasMacros(pp)) out.push(`Par portion : ${f0(pp.kcal)} kcal · P ${f1(pp.p)} g · G ${f1(pp.c)} g · L ${f1(pp.f)} g`);
  out.push('', 'Ingrédients :');
  for (const it of arr(r.ingredients)) {
    if (isSec(it)) { out.push(`— ${it.section} —`); continue; }
    const g = num(it.g);
    out.push(`- ${it.qty ? it.qty + ' ' : ''}${it.name}${g > 0 && !/\bg\b/.test(it.qty || '') ? ` (${f0(g)} g)` : ''}`);
  }
  if (arr(r.steps).length) { out.push('', 'Étapes :'); r.steps.forEach((s, i) => out.push(`${i + 1}. ${s}`)); }
  if (r.notes) out.push('', r.notes);
  if (r.source && r.source.url) out.push('', r.source.url);
  return out.join('\n');
}
async function shareRecipe(r) {
  try { const how = await shareText(r.title, recipeText(r)); if (how === 'copied') toast('Recette copiée.'); } catch (e) { /* partage annulé */ }
}

/* ============================================================ recalage au gramme */
function openCalibrate(page, r, meal) {
  if (!meal) return;
  const L = lines(r);
  const movable = L.map((l, i) => (num(l.g) > 0 && num(l.p) + num(l.c) + num(l.f) > 0 ? i : -1)).filter(i => i >= 0);
  let allowed = movable.slice();
  const sh = openSheet({ title: `Caler sur ${meal.name}`, full: true, html: '<div id="cal"></div>' });
  const paint = () => {
    const res = calibrate(r, meal, allowed);
    const box = $('#cal', sh.body);
    const lockList = `<details class="cal-lock"><summary>Ingrédients qui peuvent bouger (${allowed.length}/${movable.length})</summary>
      <div class="lock-list">${movable.map(i => `<label class="check"><input type="checkbox" data-lock="${i}" ${allowed.includes(i) ? 'checked' : ''}> ${esc(L[i].name)}</label>`).join('')}</div></details>`;
    if (!res) {
      box.innerHTML = `<p class="lead">Impossible de caler avec ces ingrédients. Autorise d'autres ingrédients à bouger, ou ajoute une source de protéines, de glucides ou de lipides.</p>${lockList}`;
      return;
    }
    const f = res.fit;
    const cell = (label, key) => `<div class="brow"><span class="bl">${label}</span><span class="bv">${f1(meal[key])}</span><span class="bv">${f1(f.s * perPortion(res.recipe)[key])}</span><span class="be st-${key === 'kcal' ? kcalStatus(f.e[key], num(meal[key])) : macroStatus(f.e[key])}">${sgn(f.e[key])}</span></div>`;
    box.innerHTML = `
      <p class="lead">${res.changes.length ? 'Pour que <b>1 portion</b> remplace ton ' + esc(meal.name) + ' :' : 'La recette est déjà calée.'}</p>
      <ul class="changes">${res.changes.map(c => {
        const food = foodByName(c.name);
        const u = unitsText(food, c.to);
        return `<li><span>${esc(c.name)}</span><b>${f1(c.from)} g → ${f1(c.to)} g</b>${u ? `<small>${esc(u)}</small>` : ''}</li>`;
      }).join('')}</ul>
      <div class="btable light">
        <div class="brow head"><span></span><span class="bv">Cible</span><span class="bv">Recette</span><span class="be">Écart</span></div>
        ${cell('Kcal', 'kcal')}${cell('Prot.', 'p')}${cell('Gluc.', 'c')}${cell('Lip.', 'f')}
      </div>
      <p class="verdict-mini st-${f.status}">${esc(STT[f.status])} · ${esc(verdictSub(f))}</p>
      ${lockList}
      ${res.changes.length ? `<div class="row-btns stack">
        <button type="button" class="btn primary" data-save="replace">Appliquer à cette recette</button>
        <button type="button" class="btn ghost" data-save="copy">Enregistrer en copie</button>
      </div>` : ''}`;
    sh.res = res;
  };
  paint();
  sh.body.addEventListener('change', e => {
    const c = e.target.closest('[data-lock]');
    if (!c) return;
    const i = +c.dataset.lock;
    allowed = c.checked ? [...new Set([...allowed, i])] : allowed.filter(x => x !== i);
    paint();
  });
  sh.body.addEventListener('click', async e => {
    const b = e.target.closest('[data-save]');
    if (!b || !sh.res) return;
    const out = sh.res.recipe;
    if (b.dataset.save === 'copy') {
      out.id = ''; out.createdAt = 0;
      out.title = `${r.title} (${meal.name})`;
      const saved = await saveRecipe(out);
      sh.close();
      toast('Copie calée enregistrée.');
      openRecipe(saved.id, { meal: meal.id });
    } else {
      await saveRecipe(out);
      page.state.s = 1;
      page.state.sv = servingsOf(out);
      sh.close();
      toast(`Recette calée sur ${meal.name}.`);
    }
  });
}

/* ============================================================ remplacer un ingrédient */
function ingredientMenu(page, r, li) {
  const line = lines(r)[li];
  if (!line) return;
  const canSwap = num(line.g) > 0 && num(line.p) + num(line.c) + num(line.f) > 0;
  const items = [];
  if (canSwap) items.push({ label: 'Remplacer (mêmes macros)', icon: 'swap', run: () => openSwap(page, r, li) });
  items.push({ label: 'Modifier la recette', icon: 'pen', run: () => openEditor(r) });
  const g = num(line.g);
  const title = `${line.name}${g > 0 ? ` · ${f0(g)} g` : ''}`;
  if (g > 0) items.unshift({ label: `P ${f1(line.p)} · G ${f1(line.c)} · L ${f1(line.f)} · ${f0(line.kcal)} kcal`, icon: 'scale', run: () => {} });
  actionSheet(title, items);
}

function openSwap(page, r, li) {
  const line = lines(r)[li];
  let q = '';
  const sh = openSheet({
    title: `Remplacer ${line.name}`, full: true,
    html: `<p class="hint">Pour garder <b>P ${f1(line.p)} · G ${f1(line.c)} · L ${f1(line.f)}</b> (${f0(line.g)} g). L'appli calcule la quantité du remplaçant.</p>
      <label class="search">${icon('search')}<input type="search" id="sw-q" placeholder="Chercher un aliment de ta base" autocomplete="off"></label>
      <div id="sw-list"></div>`,
  });
  const paint = () => {
    const pool = q ? searchFoods(q, 60) : S.foods;
    const list = pool.filter(f => norm(f.name) !== norm(line.name))
      .map(f => ({ f, s: substitute(line, f) })).filter(x => x.s && x.s.g > 0)
      .sort((a, b) => a.s.worst - b.s.worst).slice(0, q ? 40 : 12);
    $('#sw-list', sh.body).innerHTML = list.length ? `<div class="menu">${list.map((x, i) => {
      const u = unitsText(x.f, x.s.g);
      return `<button type="button" class="menu-i swap-i" data-i="${i}"><span class="dot st-${x.s.status}"></span><span class="sw-n">${esc(x.f.name)}<small>${f0(x.s.g)} g${u ? ` · ${esc(u)}` : ''}</small></span><span class="sw-e st-${x.s.status}">±${f1(x.s.worst)} g</span></button>`;
    }).join('')}</div>` : '<p class="muted">Aucun aliment trouvé. Ajoute-le dans Profil › Mes aliments.</p>';
    sh.list = list;
  };
  paint();
  const inp = $('#sw-q', sh.body);
  inp.addEventListener('input', () => { q = inp.value; paint(); });
  sh.body.addEventListener('click', async e => {
    const b = e.target.closest('[data-i]');
    if (!b) return;
    const x = sh.list[+b.dataset.i];
    const out = clone(r);
    const L = lines(out);
    const nl = lineFromFood(x.f, x.s.g);
    Object.assign(L[li], nl, { est: false });
    delete L[li].est;
    await saveRecipe(out);
    sh.close();
    const m = mealById(page.state.meal);
    const f = m && fit(out, m);
    if (m && f && f.status !== 'ok') {
      toast(`${x.f.name} : ${f0(x.s.g)} g. Il reste un petit écart.`, { action: 'Caler', onAction: () => openCalibrate(page, recipeById(out.id), m) });
    } else toast(`${line.name} remplacé par ${f0(x.s.g)} g de ${x.f.name}.`);
  });
}


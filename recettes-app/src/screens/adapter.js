// « Adapter à ton plan » : à quelle meal la recette colle le mieux, et comment la changer au gramme près.
import { S, recipeById, saveRecipe } from '../store.js';
import { $, esc, f0, f1, f2, sgn, num, arr, str, clone, norm, portionWord } from '../util.js';
import { pushPage, icon, toast, actionSheet, confirmSheet } from '../ui.js';
import {
  lines, isSec, perFromLine, applyPer, qtyForGrams, lineFromFood, unitsText, foodByName, scaleQty,
  compareMeals, mealById, fit, bestFit, perPortion, hasMacros, macroStatus, servingsOf, MACN,
} from '../macros.js';
import { adaptAll, adaptFor, TOL_EXACT } from '../adapt.js';

export const LEVEL = {
  none: 'Rien à changer',
  light: 'Petits ajustements',
  medium: 'Ajustements moyens',
  strong: 'Gros changements',
  impossible: 'Impossible sans dénaturer la recette',
};
const LEVEL_DOT = { none: 'ok', light: 'ok', medium: 'near', strong: 'near', impossible: 'far' };

/* ============================================================ calcul (mis en cache par recette) */
const CACHE = new WeakMap();
const planKey = () => JSON.stringify(compareMeals().map(m => [m.id, m.kcal, m.p, m.c, m.f, str(m.note).length]))
  + S.foods.map(f => `${f.id}:${f.p}:${f.c}:${f.f}`).join(',');
export function adaptOptions(r) {
  const key = planKey();
  const hit = CACHE.get(r);
  if (hit && hit.key === key) return hit.list;
  let list = [];
  try { list = adaptAll(r, compareMeals(), S.foods); } catch (e) { console.error(e); }
  CACHE.set(r, { key, list });
  return list;
}

/** Changements les plus parlants en premier (ajouts, puis les plus gros écarts en kcal). */
function sortedChanges(res) {
  const rows = new Map(res.rows.map(x => [x.idx, x]));
  return res.changes.slice().sort((a, b) => {
    if (a.added !== b.added) return a.added ? -1 : 1;
    const ka = Math.abs(a.to - a.from * res.beta) * ((rows.get(a.idx) || {}).per || { kcal: 100 }).kcal;
    const kb = Math.abs(b.to - b.from * res.beta) * ((rows.get(b.idx) || {}).per || { kcal: 100 }).kcal;
    return kb - ka;
  });
}
const gTxt = g => `${f1(g)} g`;
function changeHTML(c, res) {
  if (c.added) return `<li><span>+ ${esc(c.name)}${res.q > 1 ? `<small>${gTxt(c.to / res.q)} par ${esc(res.meal.name)}</small>` : ''}</span><b class="up">${gTxt(c.to)}</b></li>`;
  return `<li><span>${esc(c.name)}</span><b class="${c.to > c.from * res.beta ? 'up' : 'down'}">${gTxt(c.from)} → ${gTxt(c.to)}</b></li>`;
}
function lotText(res) {
  if (res.n <= 1) return res.beta > 1.05 || res.beta < 0.95 ? 'la recette entière, quantités ajustées' : 'la recette entière';
  const same = res.servings === res.n;
  return `${same ? '' : `${res.servings} portions au lieu de ${res.n} · `}le lot = ${res.q} × ${res.meal.name}`;
}
function missText(res) {
  const k = ['p', 'c', 'f'].reduce((a, b) => (Math.abs(res.e[b]) > Math.abs(res.e[a]) ? b : a));
  const v = res.e[k];
  return v > 0 ? `Même en adaptant, il reste ${f1(v)} g de ${MACN[k]} en trop.` : `Même en adaptant, il manque ${f1(-v)} g de ${MACN[k]}.`;
}

/* ============================================================ carte sur la fiche recette */
export function adaptCardHTML(r) {
  if (!compareMeals().length || !hasMacros(perPortion(r))) return '';
  if (r.adapt && mealById(r.adapt.meal)) {
    const m = mealById(r.adapt.meal);
    const f = fit(r, m);
    if (f && f.status === 'ok') {
      return `<button type="button" class="adapt-done" data-act="adapt-menu">${icon('check')}<span>Adaptée pour <b>${esc(m.name)}</b> : ${f2(f.s)} ${portionWord(f.s)}</span>${icon('next')}</button>`;
    }
  }
  const bf = bestFit(r);
  if (bf && bf.fit.status === 'ok') return '';
  const list = adaptOptions(r);
  const best = list[0];
  if (!best) return '';
  if (best.level === 'impossible') {
    return `<section class="adapt-card">
      <h2>${icon('sparkle')}Adapter à ton plan</h2>
      <p class="ac-sub">Aucune de tes meals ne colle, même en changeant les quantités. ${esc(missText(best))}</p>
      <button type="button" class="btn small ghost" data-adapt="${esc(best.meal.id)}">Voir le détail</button>
    </section>`;
  }
  const ch = sortedChanges(best);
  const others = list.slice(1).filter(x => x.level !== 'impossible').slice(0, 3);
  return `<section class="adapt-card">
    <h2>${icon('sparkle')}Adapter à ton plan</h2>
    <p class="ac-big">${best.k} ${portionWord(best.k)} = ${esc(best.meal.name)}</p>
    <p class="ac-sub"><span class="dot st-${LEVEL_DOT[best.level]}"></span><b>${esc(LEVEL[best.level])}</b> · ${esc(lotText(best))}</p>
    ${ch.length ? `<ul class="ac-list">${ch.slice(0, 3).map(c => changeHTML(c, best)).join('')}</ul>
    ${ch.length > 3 ? `<p class="ac-more-n">+ ${ch.length - 3} autre${ch.length - 3 > 1 ? 's' : ''} changement${ch.length - 3 > 1 ? 's' : ''}</p>` : ''}` : ''}
    <button type="button" class="btn primary block" data-adapt="${esc(best.meal.id)}">${icon('target')}Voir l'adaptation</button>
    ${others.length ? `<div class="ac-others"><span class="muted">Possible aussi :</span>${others.map(x => `<button type="button" class="chip small" data-adapt="${esc(x.meal.id)}"><span class="dot st-${LEVEL_DOT[x.level]}"></span>${esc(x.meal.name)}</button>`).join('')}</div>` : ''}
  </section>`;
}

/** Menu de la puce « Adaptée pour … ». */
export function adaptMenu(r, onChange) {
  const m = r.adapt && mealById(r.adapt.meal);
  const items = [];
  if (m) items.push({ label: `Réadapter (${m.name} ou une autre meal)`, icon: 'target', run: () => openAdapt(r, { meal: m.id, onApplied: onChange }) });
  if (r.adapt && r.adapt.orig) items.push({ label: 'Revenir à la recette d’origine', icon: 'back', run: () => revertAdapt(r, onChange) });
  actionSheet(m ? `Adaptée pour ${m.name}` : 'Recette adaptée', items);
}

export async function revertAdapt(r, onChange) {
  const o = r.adapt && r.adapt.orig;
  if (!o) return;
  const ok = await confirmSheet({ title: 'Revenir à la recette d’origine ?', body: 'Les quantités, les portions, les étapes et les notes reviennent comme avant l’adaptation.', ok: 'Revenir' });
  if (!ok) return;
  const out = clone(r);
  out.ingredients = clone(o.ingredients);
  out.servings = o.servings;
  if (Array.isArray(o.steps)) out.steps = clone(o.steps);
  out.notes = str(o.notes);
  delete out.adapt;
  await saveRecipe(out);
  toast('Recette d’origine rétablie.');
  if (onChange) onChange(out);
}

/* ============================================================ recette adaptée */
const DRY = /farine|flocon|avoine|poudre|whey|isolate|cacao|son d|amande|coco rap|maizena|fecule|levure/;

function updateSteps(steps, from, to, name) {
  // « 100 g de beurre de cacahuète » → « 32 g de beurre de cacahuète » (seulement l'ancienne quantité exacte)
  const word = str(name).split(/[\s'’]+/).find(w => w.length >= 4);
  if (!word || !(from > 0)) return steps;
  const fromTxt = String(from).replace('.', '[.,]');
  const toTxt = f1(to).replace(/\s/g, '');
  const re = new RegExp(`\\b${fromTxt}\\s?(g|gr|grammes?)\\b(\\s+(?:de\\s+|d['’]\\s*)?)(${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
  return steps.map(s => s.replace(re, (m0, u, mid, w) => `${toTxt} g${mid}${w}`));
}

export function buildAdapted(r, res) {
  const out = clone(r);
  const L = lines(out);
  let steps = arr(out.steps).slice();
  const resize = Math.abs(res.beta - 1) > 0.1;
  for (const row of res.rows) {
    const line = L[row.idx];
    if (!line) continue;
    if (row.to == null) { if (resize && line.qty) line.qty = scaleQty(line.qty, res.beta); continue; }
    const old = num(line.g);
    if (Math.abs(row.to - old) < 0.05) continue;
    const per = row.per || perFromLine(line);
    line.qty = qtyForGrams(line, row.to);
    line.g = row.to;
    if (per) applyPer(line, per);
    steps = updateSteps(steps, old, row.to, line.name);
  }
  // Ingrédients ajoutés : la poudre avec les ingrédients secs, le reste à la fin (« À côté » s'il y a des parties).
  const list = out.ingredients;
  const hasSecs = list.some(isSec);
  const tips = [];
  for (const ad of res.added) {
    const nl = lineFromFood(ad.food, ad.g);
    nl.added = ad.type;
    const perMeal = res.q > 1 ? ` (${f1(ad.g / res.q)} g par ${res.meal.name})` : '';
    if (res.sweet && ad.type === 'p') {
      let at = list.findIndex(it => !isSec(it) && DRY.test(norm(it.name)));
      if (at < 0) at = list.findIndex(it => !isSec(it));
      list.splice(at + 1, 0, nl);
      tips.push(`${ad.food.name} ajoutée${perMeal} : mélange-la avec les ingrédients secs (ou prends-la en shake à côté). Si la pâte devient sèche, ajoute un peu d’eau.`);
    } else {
      if (hasSecs && !list.some(it => isSec(it) && /à côté/i.test(it.section))) list.push({ section: 'À côté' });
      list.push(nl);
      tips.push(`${ad.food.name}${perMeal} : ${res.sweet ? 'à ajouter à la recette ou à côté' : 'à servir à côté'}.`);
    }
  }
  const powderUp = res.rows.some(x => x.role === 'powder' && x.to - num(x.from) * res.beta >= 25);
  if (powderUp) tips.push('Beaucoup plus de whey qu’avant : si la pâte est sèche, ajoute un peu d’eau (0 kcal) jusqu’à la bonne texture.');
  out.servings = res.servings;
  out.steps = steps;
  const head = res.n > 1
    ? `Adaptée pour ${res.meal.name} : ${res.k} ${portionWord(res.k)} = 1 ${res.meal.name} (le lot en fait ${res.q}).`
    : `Adaptée pour ${res.meal.name} : la recette entière = 1 ${res.meal.name}.`;
  // Les lignes qui donnent l'ancien nombre de portions ou les anciennes macros ne sont plus vraies.
  const stale = /^Adaptée pour |ajoutée? \(|: à servir à côté\.|: à ajouter à la recette ou à côté\.|\d\s*portions?\b|\bmacros\b|\d\s*kcal\b/i;
  const kept = str(r.notes).split('\n').filter(x => !stale.test(x));
  out.notes = [head, ...tips, ...kept].filter(Boolean).join('\n');
  out.adapt = {
    meal: res.meal.id, mealName: res.meal.name, q: res.q, k: res.k, at: Date.now(),
    orig: (r.adapt && r.adapt.orig) || { servings: r.servings, ingredients: clone(r.ingredients), steps: clone(arr(r.steps)), notes: str(r.notes) },
  };
  return out;
}

/* ============================================================ écran d'adaptation */
export function openAdapt(r0, opts = {}) {
  const st = { id: r0.id, meal: opts.meal || null, q: null, locked: [], pick: {}, noAdd: [] };
  let res = null;
  const page = pushPage({ cls: 'adapt', live: false, render: p => paint(p) });

  function compute() {
    const r = recipeById(st.id) || r0;
    const meal = mealById(st.meal) || (adaptOptions(r)[0] || {}).meal || compareMeals()[0];
    if (!meal) return null;
    st.meal = meal.id;
    const o = { locked: st.locked, pick: st.pick, noAdd: st.noAdd };
    if (st.q) o.q = st.q;
    return adaptFor(r, meal, S.foods, o);
  }

  function rowsHTML(r) {
    const byIdx = new Map(res.rows.map(x => [x.idx, x]));
    const changed = new Set(res.changes.filter(c => !c.added).map(c => c.idx));
    let li = -1;
    const out = [];
    for (const it of arr(r.ingredients)) {
      if (isSec(it)) { out.push(`<h3 class="ing-sec">${esc(it.section)}</h3>`); continue; }
      li++;
      const row = byIdx.get(li);
      if (!row) continue;
      const lockable = row.role != null;
      const locked = st.locked.includes(li);
      let q;
      if (row.to == null || (row.neutral && Math.abs(row.to - num(row.from)) < 0.05)) q = `<span class="muted">${esc(it.qty || (row.to ? gTxt(row.to) : 'sans grammes'))}</span>`;
      else if (Math.abs(row.to - num(row.from)) >= 0.5) q = `<s>${gTxt(row.from)}</s> <b class="${changed.has(li) ? (row.to > row.from * res.beta ? 'up' : 'down') : ''}">${gTxt(row.to)}</b>`;
      else q = `<span>${gTxt(row.to)}</span>`;
      const food = foodByName(it.name);
      const u = row.to != null ? unitsText(food, row.to) : '';
      out.push(`<div class="ad-row ${changed.has(li) ? 'changed' : 'same'} ${locked ? 'locked' : ''}">
        <span class="ad-n">${esc(it.name)}${u ? `<small>${esc(u)}</small>` : ''}</span>
        <span class="ad-q">${q}</span>
        ${lockable ? `<button type="button" class="icon-btn small ad-lock" data-lock="${li}" aria-pressed="${locked}" aria-label="${locked ? 'Débloquer' : 'Ne pas toucher'} ${esc(it.name)}">${icon(locked ? 'lock' : 'unlock')}</button>` : '<span></span>'}
      </div>`);
    }
    for (const ad of res.added) {
      const u = unitsText(ad.food, ad.g);
      const alts = (res.choices[ad.type] || []).slice(0, 4);
      out.push(`<div class="ad-row added">
        <span class="ad-n">+ ${esc(ad.food.name)}<small>ajouté${res.q > 1 ? ` · ${gTxt(ad.g / res.q)} par ${esc(res.meal.name)}` : ''}${u ? ` · ${esc(u)}` : ''}</small></span>
        <span class="ad-q"><b class="up">${gTxt(ad.g)}</b></span><span></span>
      </div>
      <div class="ad-alts" role="group" aria-label="Autre choix pour les ${esc(MACN[ad.type])}">
        ${alts.map(f => `<button type="button" class="chip small" data-pick="${ad.type}:${esc(f.id)}" aria-pressed="${f.id === ad.food.id}">${esc(f.name)}</button>`).join('')}
        <button type="button" class="chip small" data-noadd="${ad.type}">Sans ajout</button>
      </div>`);
    }
    for (const t of st.noAdd) {
      out.push(`<div class="ad-alts"><span class="muted">Sans ajout de ${esc(MACN[t])}.</span><button type="button" class="chip small" data-readd="${t}">Autoriser un ajout</button></div>`);
    }
    return out.join('');
  }

  function tableHTML() {
    const m = res.meal;
    const row = (label, cls, key) => {
      const target = num(m[key]), e = res.e[key], me = target + e;
      const status = key === 'kcal' ? 'info' : macroStatus(e);
      return `<div class="brow ${cls}"><span class="bl">${label}</span><span class="bv">${f1(target)}</span><span class="bv">${f1(me)}</span><span class="be st-${status}">${sgn(e)}</span></div>`;
    };
    return `<div class="btable">
      <div class="brow head"><span></span><span class="bv">Cible</span><span class="bv">Adaptée</span><span class="be">Écart</span></div>
      ${row('Prot.', 'p', 'p')}${row('Gluc.', 'g', 'c')}${row('Lip.', 'l', 'f')}${row('Kcal', 'k', 'kcal')}
    </div>`;
  }

  function paint(p) {
    const r = recipeById(st.id) || r0;
    res = compute();
    const list = adaptOptions(r);
    const byMeal = new Map(list.map(x => [x.meal.id, x]));
    const chips = compareMeals().map(m => {
      const x = byMeal.get(m.id);
      const lvl = x ? x.level : 'impossible';
      const star = list[0] && list[0].meal.id === m.id && lvl !== 'impossible' ? `<span class="star">${icon('sparkle')}</span>` : '';
      return `<button type="button" class="bchip" data-meal="${esc(m.id)}" aria-pressed="${m.id === st.meal}"><span class="dot st-${LEVEL_DOT[lvl]}"></span>${esc(m.name)}${star}</button>`;
    }).join('');
    let body;
    if (!res) {
      body = '<p class="muted">Ajoute les grammes des ingrédients pour pouvoir adapter la recette.</p>';
    } else {
      const ok = res.level !== 'impossible';
      const kcalNote = ok && Math.abs(res.e.kcal) > 0.03 * num(res.meal.kcal)
        ? `<p class="hint">Kcal : ${f0(res.kcal)} au lieu de ${f0(res.meal.kcal)}. Les kcal des étiquettes ne tombent pas pile sur les macros (fibres, arrondis) : c'est normal quand protéines, glucides et lipides sont identiques.</p>` : '';
      const lotStep = res.n > 1 ? `<div class="ad-lot">
          <span>Le lot fait</span>
          <div class="stepper"><button type="button" class="icon-btn small" data-q="-1" aria-label="Moins" ${res.q <= 1 ? 'disabled' : ''}>${icon('minus')}</button><span><b>${res.q}</b> × ${esc(res.meal.name)}</span><button type="button" class="icon-btn small" data-q="1" aria-label="Plus">${icon('plus')}</button></div>
        </div>` : '';
      body = `
        <section class="bal ad-panel st-${ok ? 'ok' : 'far'}">
          <p class="verdict">${ok ? `${res.k} ${portionWord(res.k)} = ${esc(res.meal.name)}` : 'Impossible'}</p>
          <p class="verdict-sub">${ok ? `${esc(LEVEL[res.level])} · ${esc(lotText(res))}` : esc(missText(res))}</p>
          ${lotStep}
          ${tableHTML()}
        </section>
        ${kcalNote}
        ${ok ? `<section class="d-sec">
          <div class="sec-h"><h2>Ingrédients</h2><span class="muted small">${icon('lock')} = ne pas toucher</span></div>
          <div class="ad-rows">${rowsHTML(r)}</div>
        </section>` : `<div class="ad-help">
          <p>${st.locked.length ? 'Des ingrédients sont bloqués : débloque-les pour laisser plus de marge.' : st.noAdd.length ? 'Autorise un ajout (whey, riz…) pour compléter ce qui manque.' : 'Cette recette est trop loin de ce meal : choisis une autre meal au-dessus.'}</p>
          ${st.locked.length ? '<button type="button" class="btn small ghost" data-unlock-all>Tout débloquer</button>' : ''}
          ${st.noAdd.map(t => `<button type="button" class="btn small ghost" data-readd="${t}">Autoriser un ajout de ${esc(MACN[t])}</button>`).join('')}
        </div>`}
        ${ok && res.level !== 'none' ? `<div class="row-btns stack">
          <button type="button" class="btn primary" data-save="replace">${icon('check')}Appliquer à la recette</button>
          <button type="button" class="btn ghost" data-save="copy">${icon('copy')}Enregistrer une copie adaptée</button>
        </div>` : ok ? `<p class="okline dark">${icon('check')}Telle quelle : ${res.k} ${portionWord(res.k)} = ${esc(res.meal.name)}.</p>` : ''}`;
    }
    p.el.innerHTML = `
      <div class="p-top"><button type="button" class="icon-btn" data-act="back" aria-label="Retour">${icon('back')}</button><h1>Adapter à ton plan</h1><span></span></div>
      <div class="p-body">
        <p class="ad-title">${esc(r.title)}</p>
        <p class="flabel">Pour quelle meal ?</p>
        <div class="bal-meals ad-meals">${chips}</div>
        ${body}
      </div>`;
    const on = $('.ad-meals [aria-pressed="true"]', p.el);
    if (on) on.scrollIntoView({ block: 'nearest', inline: 'center' });
  }

  page.el.addEventListener('click', async e => {
    const t = e.target;
    const mb = t.closest('[data-meal]');
    if (mb) { st.meal = mb.dataset.meal; st.q = null; st.locked = []; st.pick = {}; st.noAdd = []; page.refresh(); return; }
    const lk = t.closest('[data-lock]');
    if (lk) { const i = +lk.dataset.lock; st.locked = st.locked.includes(i) ? st.locked.filter(x => x !== i) : [...st.locked, i]; page.refresh(); return; }
    const pk = t.closest('[data-pick]');
    if (pk) { const [type, id] = pk.dataset.pick.split(':'); st.pick = { ...st.pick, [type]: id }; page.refresh(); return; }
    const na = t.closest('[data-noadd]');
    if (na) { st.noAdd = [...new Set([...st.noAdd, na.dataset.noadd])]; page.refresh(); return; }
    if (t.closest('[data-unlock-all]')) { st.locked = []; page.refresh(); return; }
    const ra = t.closest('[data-readd]');
    if (ra) { st.noAdd = st.noAdd.filter(x => x !== ra.dataset.readd); page.refresh(); return; }
    const qb = t.closest('[data-q]');
    if (qb && res) { st.q = Math.max(1, res.q + +qb.dataset.q); page.refresh(); return; }
    const sv = t.closest('[data-save]');
    if (sv && res) {
      const r = recipeById(st.id) || r0;
      const out = buildAdapted(r, res);
      if (sv.dataset.save === 'copy') {
        out.id = ''; out.createdAt = 0;
        out.title = `${r.title} (${res.meal.name})`.slice(0, 140);
      }
      const saved = await saveRecipe(out);
      page.close();
      toast(`${sv.dataset.save === 'copy' ? 'Copie adaptée' : 'Recette adaptée'} : ${res.k} ${portionWord(res.k)} = ${res.meal.name}.`);
      if (opts.onApplied) opts.onApplied(saved, res, sv.dataset.save === 'copy');
      return;
    }
    if (t.closest('[data-act="back"]')) page.close();
  });
  return page;
}

// Onglet Planning : la semaine, meal par meal, avec les recettes qui remplacent le plan du coach.
import { S, recipeById, savePlanning } from '../store.js';
import { $, esc, num, f0, f1, f2, norm, todayISO, addDays, weekStart, dayShort, dayNum, monthShort, dateLabel } from '../util.js';
import { icon, openSheet, actionSheet, confirmSheet, toast, thumbHTML, nav } from '../ui.js';
import { planMeals, compareMeals, mealById, perPortion, fit, fitAt, servingsOf, stepFor, STW, hasMacros } from '../macros.js';
import { openRecipe } from './detail.js';
import { addRecipeToGroceries } from './groceries.js';

let sel = todayISO();
let rerender = () => {};
export function showDay(iso) { sel = iso; }

const slotOf = (iso, mid) => (S.planning[iso] && S.planning[iso][mid]) || null;

function dayTotals(iso) {
  const t = { kcal: 0, p: 0, c: 0, f: 0 }, plan = { kcal: 0, p: 0, c: 0, f: 0 };
  let planned = 0;
  for (const m of planMeals()) {
    for (const k of ['kcal', 'p', 'c', 'f']) plan[k] += num(m[k]);
    const sl = slotOf(iso, m.id);
    const r = sl && recipeById(sl.recipeId);
    if (r) {
      const pp = perPortion(r);
      for (const k of ['kcal', 'p', 'c', 'f']) t[k] += pp[k] * num(sl.s || 1);
      planned++;
    } else for (const k of ['kcal', 'p', 'c', 'f']) t[k] += num(m[k]);
  }
  return { t, plan, planned };
}

export function renderPlanner(root) {
  rerender = () => renderPlanner(root);
  const meals = planMeals();
  const ws = weekStart(sel);
  const days = [...Array(7)].map((_, i) => addDays(ws, i));
  const today = todayISO();
  const head = `<header class="top"><h1>Planning</h1><button type="button" class="icon-btn" data-act="more" aria-label="Options">${icon('more')}</button></header>
    <div class="week">
      <button type="button" class="icon-btn small" data-act="prev-w" aria-label="Semaine précédente">${icon('back')}</button>
      <div class="wdays">${days.map(d => {
        const n = S.planning[d] ? Object.keys(S.planning[d]).length : 0;
        return `<button type="button" class="wday ${d === today ? 'today' : ''}" data-day="${d}" aria-pressed="${d === sel}"><span>${dayShort(d)}</span><b>${dayNum(d)}</b><i class="${n ? 'on' : ''}"></i></button>`;
      }).join('')}</div>
      <button type="button" class="icon-btn small" data-act="next-w" aria-label="Semaine suivante">${icon('next')}</button>
    </div>`;
  if (!meals.length) {
    root.innerHTML = `${head}<div class="empty-state"><div class="es-ic">${icon('calendar')}</div><h2>Ajoute d'abord ton plan</h2><p>Mets tes meals et leurs macros dans <b>Profil › Mes meals</b> (ou restaure ta sauvegarde). Tu pourras ensuite choisir une recette pour chaque meal.</p><button type="button" class="btn primary" data-act="go-meals">Mes meals</button></div>`;
    bind(root);
    return;
  }
  const { t, plan, planned } = dayTotals(sel);
  const slots = meals.map(m => {
    const sl = slotOf(sel, m.id);
    const r = sl && recipeById(sl.recipeId);
    let bodyHTML;
    if (r) {
      const pp = perPortion(r);
      const fv = hasMacros(pp) ? fitAt(pp, m, num(sl.s || 1)) : null;
      bodyHTML = `<button type="button" class="slot-r" data-openr="${esc(r.id)}" data-mid="${esc(m.id)}">
          ${thumbHTML(r, 'sthumb', () => rerender())}
          <span class="slot-rb"><b>${esc(r.title)}</b>
          <span class="fitl st-${fv ? fv.status : 'far'}"><span class="dot"></span><span class="nw">${f2(sl.s || 1)} ${num(sl.s) >= 2 ? 'portions' : 'portion'}</span>${fv ? `<span class="muted nw">${esc(STW[fv.status])}</span>` : ''}</span></span>
        </button>
        <button type="button" class="icon-btn small" data-slot="${esc(m.id)}" aria-label="Options">${icon('more')}</button>`;
    } else {
      bodyHTML = `<div class="slot-plan"><p>${esc(m.note || 'Plan du coach')}</p>
        ${m.compare !== false ? `<button type="button" class="btn small ghost" data-pick="${esc(m.id)}">${icon('plus')}Choisir une recette</button>` : ''}</div>`;
    }
    return `<li class="slot ${r ? 'has' : ''}">
      <div class="slot-t"><b>${esc(m.time || '')}</b></div>
      <div class="slot-b">
        <div class="slot-h"><h3>${esc(m.name)}</h3><span class="muted">${f0(m.kcal)} kcal · P ${f0(m.p)} · G ${f0(m.c)} · L ${f0(m.f)}</span></div>
        <div class="slot-c">${bodyHTML}</div>
      </div>
    </li>`;
  }).join('');
  const dk = t.kcal - plan.kcal;
  root.innerHTML = `${head}
    <div class="day-h"><h2>${esc(dateLabel(sel))}</h2><span class="muted">${planned ? `${planned} recette${planned > 1 ? 's' : ''}` : 'Plan du coach'}</span></div>
    <div class="day-sum">
      <div><b>${f0(t.kcal)}</b><span>kcal</span></div>
      <div><b>${f0(t.p)}</b><span>prot.</span></div>
      <div><b>${f0(t.c)}</b><span>gluc.</span></div>
      <div><b>${f0(t.f)}</b><span>lip.</span></div>
      <p class="muted">Plan : ${f0(plan.kcal)} kcal · P ${f0(plan.p)} · G ${f0(plan.c)} · L ${f0(plan.f)}${planned ? ` · écart ${dk >= 0 ? '+' : '−'}${f0(Math.abs(dk))} kcal` : ''}</p>
    </div>
    <ol class="slots">${slots}</ol>
    <div class="row-btns stack">
      <button type="button" class="btn ghost" data-act="week-shop">${icon('cart')}Mettre la semaine dans les courses</button>
      <button type="button" class="btn ghost" data-act="copy-day">${icon('copy')}Copier ce jour sur…</button>
    </div>`;
  bind(root);
}

function bind(root) {
  root.onclick = async e => {
    const t = e.target;
    const d = t.closest('[data-day]');
    if (d) { sel = d.dataset.day; rerender(); return; }
    const op = t.closest('[data-openr]');
    if (op) { const sl = slotOf(sel, op.dataset.mid); openRecipe(op.dataset.openr, { meal: op.dataset.mid, s: sl && sl.s }); return; }
    const pk = t.closest('[data-pick]');
    if (pk) { pickRecipe(sel, pk.dataset.pick); return; }
    const so = t.closest('[data-slot]');
    if (so) { slotMenu(sel, so.dataset.slot); return; }
    const a = t.closest('[data-act]');
    if (!a) return;
    const act = a.dataset.act;
    if (act === 'prev-w') { sel = addDays(sel, -7); rerender(); }
    else if (act === 'next-w') { sel = addDays(sel, 7); rerender(); }
    else if (act === 'go-meals') nav.setTab('profil', 'meals');
    else if (act === 'week-shop') weekToGroceries();
    else if (act === 'copy-day') copyDay();
    else if (act === 'more') {
      actionSheet('Planning', [
        { label: "Revenir à aujourd'hui", icon: 'calendar', run: () => { sel = todayISO(); rerender(); } },
        { label: 'Vider ce jour', icon: 'trash', danger: true, run: async () => { if (S.planning[sel] && await confirmSheet({ title: 'Vider ce jour ?', body: 'Les meals reviennent au plan du coach.', ok: 'Vider', danger: true })) { delete S.planning[sel]; await savePlanning(); } } },
      ]);
    }
  };
}

function slotMenu(iso, mid) {
  const sl = slotOf(iso, mid);
  const r = sl && recipeById(sl.recipeId);
  const m = mealById(mid);
  if (!sl || !m) return;
  actionSheet(`${m.name} · ${r ? r.title : ''}`, [
    { label: 'Changer de recette', icon: 'swap', run: () => pickRecipe(iso, mid) },
    { label: 'Ajuster la portion', icon: 'scale', run: () => portionSheet(iso, mid) },
    { label: 'Revenir au plan du coach', icon: 'close', danger: true, run: async () => { delete S.planning[iso][mid]; await savePlanning(); } },
  ]);
}

function portionSheet(iso, mid) {
  const sl = slotOf(iso, mid);
  const r = sl && recipeById(sl.recipeId);
  const m = mealById(mid);
  if (!r || !m) return;
  const step = stepFor(r);
  let s = num(sl.s || 1);
  const sh = openSheet({ title: 'Portion', html: '<div id="ps"></div>' });
  const paint = () => {
    const fv = fitAt(perPortion(r), m, s);
    $('#ps', sh.body).innerHTML = `<p class="lead">${esc(r.title)} pour ${esc(m.name)}</p>
      <div class="stepper big center"><button type="button" class="icon-btn" data-d="-1" aria-label="Moins">${icon('minus')}</button><b>${f2(s)}</b><button type="button" class="icon-btn" data-d="1" aria-label="Plus">${icon('plus')}</button></div>
      <p class="fitl center st-${fv.status}"><span class="dot"></span>${esc(STW[fv.status])} · écart max ${f1(fv.worst)} g</p>
      <button type="button" class="btn primary block" data-ok>Enregistrer</button>`;
  };
  paint();
  sh.body.addEventListener('click', async e => {
    const d = e.target.closest('[data-d]');
    if (d) { s = Math.max(step, Math.round((s + step * +d.dataset.d) * 100) / 100); paint(); return; }
    if (e.target.closest('[data-ok]')) { S.planning[iso][mid].s = s; await savePlanning(); sh.close(); }
  });
}

function pickRecipe(iso, mid) {
  const m = mealById(mid);
  if (!m) return;
  let q = '';
  const sh = openSheet({
    title: `${m.name} · ${dateLabel(iso)}`, full: true,
    html: `<p class="hint">Cible : ${f0(m.kcal)} kcal · P ${f1(m.p)} · G ${f1(m.c)} · L ${f1(m.f)}. Les recettes les plus proches d'abord.</p>
      <label class="search">${icon('search')}<input type="search" id="pk-q" placeholder="Rechercher" autocomplete="off"></label>
      <div id="pk-list" class="pick-list"></div>`,
  });
  const paint = () => {
    const w = norm(q).split(' ').filter(Boolean);
    const list = S.recipes.filter(r => { const h = norm(r.title); return w.every(x => h.includes(x)); })
      .map(r => ({ r, f: fit(r, m) })).sort((a, b) => (a.f ? a.f.score : 99) - (b.f ? b.f.score : 99));
    $('#pk-list', sh.body).innerHTML = list.length ? list.map(({ r, f }) => `<button type="button" class="pick" data-r="${esc(r.id)}">
        ${thumbHTML(r, 'sthumb')}
        <span class="pick-b"><b>${esc(r.title)}</b>${f ? `<span class="fitl st-${f.status}"><span class="dot"></span><span class="nw">${esc(STW[f.status])}</span><span class="muted nw">${f2(f.s)} ${f.s >= 2 ? 'portions' : 'portion'}</span></span>` : '<span class="muted">Macros à compléter</span>'}</span>
      </button>`).join('') : '<p class="muted">Aucune recette. Ajoute-en avec le bouton +.</p>';
  };
  paint();
  $('#pk-q', sh.body).addEventListener('input', e => { q = e.target.value; paint(); });
  sh.body.addEventListener('click', async e => {
    const b = e.target.closest('[data-r]');
    if (!b) return;
    const r = recipeById(b.dataset.r);
    const f = r && fit(r, m);
    S.planning[iso] = S.planning[iso] || {};
    S.planning[iso][mid] = { recipeId: r.id, s: f ? f.s : 1 };
    await savePlanning();
    sh.close();
    toast(`${r.title} planifié pour ${m.name}.`);
  });
}

/** Depuis une fiche recette : choisir le jour et le meal. */
export function openPlanPicker(r, mealId, s0) {
  const meals = compareMeals();
  if (!meals.length) { toast('Ajoute d’abord tes meals dans Profil › Mes meals.'); return; }
  let day = todayISO();
  let mid = mealId && mealById(mealId) ? mealId : meals[0].id;
  let s = s0 || 1;
  const days = [...Array(14)].map((_, i) => addDays(todayISO(), i));
  const sh = openSheet({ title: 'Planifier', html: '<div id="pp"></div>' });
  const paint = () => {
    const m = mealById(mid);
    const fv = hasMacros(perPortion(r)) ? fitAt(perPortion(r), m, s) : null;
    const taken = slotOf(day, mid);
    const takenR = taken && recipeById(taken.recipeId);
    $('#pp', sh.body).innerHTML = `<p class="lead">${esc(r.title)}</p>
      <p class="flabel">Jour</p>
      <div class="chips">${days.map((d, i) => `<button type="button" class="chip" data-day="${d}" aria-pressed="${d === day}">${i === 0 ? "Aujourd'hui" : i === 1 ? 'Demain' : `${dayShort(d)} ${dayNum(d)} ${monthShort(d)}`}</button>`).join('')}</div>
      <p class="flabel">Meal</p>
      <div class="chips wrap">${meals.map(x => { const f = fit(r, x); return `<button type="button" class="chip" data-mid="${esc(x.id)}" aria-pressed="${x.id === mid}"><span class="dot st-${f ? f.status : 'far'}"></span>${esc(x.name)}</button>`; }).join('')}</div>
      <div class="stepper big center"><button type="button" class="icon-btn" data-d="-1" aria-label="Moins">${icon('minus')}</button><b>${f2(s)}</b><button type="button" class="icon-btn" data-d="1" aria-label="Plus">${icon('plus')}</button></div>
      ${fv ? `<p class="fitl center st-${fv.status}"><span class="dot"></span>${esc(STW[fv.status])} · écart max ${f1(fv.worst)} g</p>` : ''}
      ${takenR && takenR.id !== r.id ? `<p class="hint center">Remplacera « ${esc(takenR.title)} ».</p>` : ''}
      <button type="button" class="btn primary block" data-ok>${icon('calendar')}Planifier</button>`;
    const on = $('#pp .chips [aria-pressed="true"]', sh.body);
    if (on && on.scrollIntoView) on.scrollIntoView({ inline: 'center', block: 'nearest' });
  };
  paint();
  sh.body.addEventListener('click', async e => {
    const dd = e.target.closest('[data-day]');
    if (dd) { day = dd.dataset.day; paint(); return; }
    const mm = e.target.closest('[data-mid]');
    if (mm) { mid = mm.dataset.mid; const f = fit(r, mealById(mid)); if (f) s = f.s; paint(); return; }
    const d = e.target.closest('[data-d]');
    if (d) { const step = stepFor(r); s = Math.max(step, Math.round((s + step * +d.dataset.d) * 100) / 100); paint(); return; }
    if (e.target.closest('[data-ok]')) {
      S.planning[day] = S.planning[day] || {};
      S.planning[day][mid] = { recipeId: r.id, s };
      await savePlanning();
      sh.close();
      const m = mealById(mid);
      toast(`Planifié : ${m.name}, ${dateLabel(day).toLowerCase()}.`, { action: 'Voir', onAction: () => { sel = day; nav.setTab('planning'); } });
    }
  });
}

async function weekToGroceries() {
  const ws = weekStart(sel);
  const entries = [];
  for (let i = 0; i < 7; i++) {
    const d = addDays(ws, i);
    for (const [, sl] of Object.entries(S.planning[d] || {})) {
      const r = sl && recipeById(sl.recipeId);
      if (r) entries.push({ r, k: num(sl.s || 1) / servingsOf(r) });
    }
  }
  if (!entries.length) { toast('Aucune recette planifiée cette semaine.'); return; }
  const ok = await confirmSheet({ title: 'Courses de la semaine', body: `${entries.length} repas planifiés seront ajoutés à ta liste de courses.`, ok: 'Ajouter' });
  if (!ok) return;
  const merged = new Map();
  for (const { r, k } of entries) merged.set(r.id, { r, k: (merged.has(r.id) ? merged.get(r.id).k : 0) + k });
  for (const { r, k } of merged.values()) addRecipeToGroceries(r, k, { quiet: true });
  toast('Ajouté aux courses.', { action: 'Voir', onAction: () => nav.setTab('courses') });
}

function copyDay() {
  const src = S.planning[sel];
  if (!src || !Object.keys(src).length) { toast('Rien de planifié ce jour.'); return; }
  const days = [...Array(14)].map((_, i) => addDays(sel, i + 1));
  const picked = new Set();
  const sh = openSheet({
    title: 'Copier ce jour sur…',
    html: `<div class="chips wrap">${days.map(d => `<button type="button" class="chip" data-day="${d}" aria-pressed="false">${dayShort(d)} ${dayNum(d)} ${monthShort(d)}</button>`).join('')}</div>
      <button type="button" class="btn primary block" data-ok>Copier</button>`,
  });
  sh.body.addEventListener('click', async e => {
    const dd = e.target.closest('[data-day]');
    if (dd) { const d = dd.dataset.day; if (picked.has(d)) picked.delete(d); else picked.add(d); dd.setAttribute('aria-pressed', picked.has(d)); return; }
    if (e.target.closest('[data-ok]')) {
      if (!picked.size) { toast('Choisis au moins un jour.'); return; }
      for (const d of picked) S.planning[d] = JSON.parse(JSON.stringify(src));
      await savePlanning();
      sh.close();
      toast(`Copié sur ${picked.size} jour${picked.size > 1 ? 's' : ''}.`);
    }
  });
}

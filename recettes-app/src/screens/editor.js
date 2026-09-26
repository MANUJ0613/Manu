// Éditeur de recette (création, modification, relecture après import).
import { S, saveRecipe, savePhoto, deletePhoto, photoURL } from '../store.js';
import { $, $$, esc, num, str, arr, clone, f0, f1, round1, gOrNull, resizeImage } from '../util.js';
import { pushPage, icon, actionSheet, confirmSheet, toast } from '../ui.js';
import { isSec, perFromLine, lookupPer, foodByName, foodPer, applyPer, perPortion, bestFit, STW, hasMacros } from '../macros.js';
import { openRecipe } from './detail.js';

export function openEditor(rec, opts = {}) {
  const base = opts.draft ? clone(opts.draft) : rec ? clone(rec) : {
    title: '', servings: 1, ingredients: [], steps: [], notes: '', source: { url: '', label: '' }, books: [], photo: null,
  };
  const d = {
    id: (rec && rec.id) || '',
    title: str(base.title),
    servings: Math.max(1, Math.round(num(base.servings)) || 1),
    source: { url: str(base.source && base.source.url), label: str(base.source && base.source.label) },
    books: arr(base.books).slice(),
    notes: str(base.notes),
    steps: arr(base.steps).map(str),
    photo: base.photo || null,
    lines: arr(base.ingredients).map(it => (isSec(it)
      ? { sec: str(it.section) }
      : { name: str(it.name), qty: str(it.qty), g: num(it.g) > 0 ? round1(it.g) : '', per: perFromLine(it) || lookupPer(it.name) || null, est: !!it.est, open: false })),
  };
  if (!d.lines.length) d.lines.push({ name: '', qty: '', g: '', per: null, est: false, open: false });
  if (!d.steps.length) d.steps.push('');
  let photoBlob = opts.photoBlob || null;
  let photoPreview = photoBlob ? URL.createObjectURL(photoBlob) : null;
  let dropPhoto = false;
  let dirty = !!opts.draft;
  let saving = false;

  let page = null;
  page = pushPage({
    cls: 'editor',
    live: false,
    render: p => { page = p; paint(); },
    onClose: () => { if (photoPreview) URL.revokeObjectURL(photoPreview); },
    onBack: () => { tryClose(); return true; },
  });

  async function tryClose() {
    collect();
    if (dirty && !(await confirmSheet({ title: 'Quitter sans enregistrer ?', body: 'Tes modifications seront perdues.', ok: 'Quitter', danger: true }))) return;
    page.close();
  }

  function lineMacros(l) {
    const g = num(l.g);
    if (!l.per || !(g > 0)) return null;
    return { kcal: l.per.kcal * g / 100, p: l.per.p * g / 100, c: l.per.c * g / 100, f: l.per.f * g / 100 };
  }
  function toIngredient(l) {
    const out = { name: str(l.name), qty: str(l.qty), g: gOrNull(l.g), kcal: 0, p: 0, c: 0, f: 0 };
    if (out.g && l.per) applyPer(out, l.per);
    if (l.est && out.g) out.est = true;
    return out;
  }
  function draftRecipe() {
    return {
      servings: d.servings,
      ingredients: d.lines.filter(l => l.sec == null && str(l.name)).map(toIngredient),
    };
  }

  function lineHTML(l, i) {
    if (l.sec != null) {
      return `<div class="ln sec" data-li="${i}">
        <input class="sec-name" value="${esc(l.sec)}" placeholder="Nom de la partie (ex. Crème)" aria-label="Partie de la recette">
        <button type="button" class="icon-btn small" data-lact="menu" aria-label="Options">${icon('more')}</button>
      </div>`;
    }
    const m = lineMacros(l);
    const food = foodByName(l.name);
    const tag = !str(l.name) ? '' : food ? `<span class="tag ok">${icon('check')}Ma base</span>` : l.per ? `<span class="tag est">≈ estimé</span>` : `<span class="tag warn">macros ?</span>`;
    return `<div class="ln" data-li="${i}">
      <div class="ln-top">
        <input class="ln-name" list="foodnames" value="${esc(l.name)}" placeholder="Ingrédient" aria-label="Ingrédient" autocomplete="off">
        <button type="button" class="icon-btn small" data-lact="menu" aria-label="Options">${icon('more')}</button>
      </div>
      <div class="ln-row">
        <input class="ln-qty" value="${esc(l.qty)}" placeholder="Quantité (ex. 2 tranches)" aria-label="Quantité">
        <label class="ln-gw"><input class="ln-g" inputmode="decimal" value="${esc(l.g)}" placeholder="0" aria-label="Grammes"><span>g</span></label>
      </div>
      <div class="ln-mac">
        <span class="ln-m">${m ? `P ${f1(m.p)} · G ${f1(m.c)} · L ${f1(m.f)} · ${f0(m.kcal)} kcal` : num(l.g) > 0 ? 'Valeurs manquantes' : 'Sans grammes : compté à 0'}</span>
        ${tag}
        <button type="button" class="linkish" data-lact="per">${l.open ? 'Masquer' : 'Pour 100 g'}</button>
      </div>
      ${l.open ? `<div class="ln-per">
        <label>kcal<input data-per="kcal" inputmode="decimal" value="${l.per ? round1(l.per.kcal) : ''}"></label>
        <label>P<input data-per="p" inputmode="decimal" value="${l.per ? round1(l.per.p) : ''}"></label>
        <label>G<input data-per="c" inputmode="decimal" value="${l.per ? round1(l.per.c) : ''}"></label>
        <label>L<input data-per="f" inputmode="decimal" value="${l.per ? round1(l.per.f) : ''}"></label>
      </div>` : ''}
    </div>`;
  }

  function totalsHTML() {
    const r = draftRecipe();
    const pp = perPortion(r);
    if (!hasMacros(pp)) return '<p class="muted">Ajoute les grammes pour voir les macros.</p>';
    const bf = bestFit(r);
    return `<p class="tot-l"><b>${f0(pp.kcal)} kcal</b> par portion · P ${f1(pp.p)} · G ${f1(pp.c)} · L ${f1(pp.f)}</p>
      ${bf ? `<p class="fitl st-${bf.fit.status}"><span class="dot"></span>${esc(bf.meal.name)} : ${esc(STW[bf.fit.status])} (${f1(bf.fit.s)} portion)</p>` : ''}`;
  }

  function photoHTML() {
    const url = dropPhoto ? null : photoPreview || (d.photo ? photoURL(d.photo, paint) : null);
    return `<div class="ed-photo ${url ? 'has' : ''}">
      ${url ? `<img src="${url}" alt="">` : `<span>${icon('image')}Ajouter une photo</span>`}
      <input type="file" accept="image/*" id="ed-file" aria-label="Choisir une photo">
      ${url ? `<button type="button" class="icon-btn glass small" data-act="photo-del" aria-label="Retirer la photo">${icon('trash')}</button>` : ''}
    </div>`;
  }

  function paint() {
    const booksHTML = S.books.length ? `<div class="field"><span class="flabel">Livres de recettes</span><div class="chips wrap">${S.books.map(b => `<button type="button" class="chip" data-book="${esc(b.id)}" aria-pressed="${d.books.includes(b.id)}">${esc(b.name)}</button>`).join('')}</div></div>` : '';
    page.el.innerHTML = `
      <div class="ed-top">
        <button type="button" class="btn ghost small" data-act="cancel">Annuler</button>
        <p>${d.id ? 'Modifier' : opts.fromImport ? 'Vérifier la recette' : 'Nouvelle recette'}</p>
        <button type="button" class="btn primary small" data-act="save">Enregistrer</button>
      </div>
      <div class="ed-body">
        ${opts.fromImport ? `<div class="banner">${icon('sparkle')}<p>Lue par Gemini. Vérifie les grammes : <b>≈ estimé</b> = valeurs moyennes. Tape le nom exact d'un aliment de ta base pour avoir tes valeurs.</p></div>` : ''}
        ${photoHTML()}
        <div class="field"><label for="ed-title">Titre</label><input id="ed-title" value="${esc(d.title)}" placeholder="ex. Croque-monsieur XXL" autocomplete="off"></div>
        <div class="grid2">
          <div class="field"><label for="ed-serv">Portions</label><input id="ed-serv" inputmode="numeric" value="${d.servings}"></div>
          <div class="field"><label for="ed-src">Source</label><input id="ed-src" value="${esc(d.source.label)}" placeholder="@compte, site…" autocomplete="off"></div>
        </div>
        <div class="field"><label for="ed-url">Lien de la publication</label><input id="ed-url" type="url" inputmode="url" value="${esc(d.source.url)}" placeholder="https://…" autocomplete="off"></div>
        ${booksHTML}
        <h2 class="ed-h">Ingrédients</h2>
        <div class="lns">${d.lines.map(lineHTML).join('')}</div>
        <div class="row-btns left"><button type="button" class="btn small ghost" data-act="add-line">${icon('plus')}Ingrédient</button><button type="button" class="btn small ghost" data-act="add-sec">${icon('plus')}Partie</button></div>
        <div class="ed-tot" id="ed-tot">${totalsHTML()}</div>
        <h2 class="ed-h">Étapes</h2>
        <div class="sts">${d.steps.map((s, i) => `<div class="st" data-si="${i}"><span class="sn">${i + 1}</span><textarea rows="2" placeholder="Étape ${i + 1}">${esc(s)}</textarea><button type="button" class="icon-btn small" data-sact="del" aria-label="Supprimer l'étape">${icon('close')}</button></div>`).join('')}</div>
        <div class="row-btns left"><button type="button" class="btn small ghost" data-act="add-step">${icon('plus')}Étape</button></div>
        <div class="field"><label for="ed-notes">Notes</label><textarea id="ed-notes" rows="4" placeholder="Astuces, conservation, variantes…">${esc(d.notes)}</textarea></div>
        <button type="button" class="btn primary block" data-act="save">Enregistrer la recette</button>
      </div>
      <datalist id="foodnames">${S.foods.map(f => `<option value="${esc(f.name)}">`).join('')}</datalist>`;
    $$('.st textarea', page.el).forEach(autoGrow);
  }
  function autoGrow(ta) { ta.style.height = 'auto'; ta.style.height = Math.min(320, ta.scrollHeight + 2) + 'px'; }

  function collect() {
    const v = id => { const el = $(id, page.el); return el ? el.value : ''; };
    if (!$('#ed-title', page.el)) return;
    d.title = str(v('#ed-title'));
    d.servings = Math.min(50, Math.max(1, Math.round(num(v('#ed-serv'))) || 1));
    d.source = { url: str(v('#ed-url')), label: str(v('#ed-src')) };
    d.notes = v('#ed-notes');
    d.steps = $$('.st textarea', page.el).map(t => t.value);
    $$('.ln', page.el).forEach(el => {
      const l = d.lines[+el.dataset.li];
      if (!l) return;
      if (l.sec != null) { l.sec = $('.sec-name', el).value; return; }
      l.name = $('.ln-name', el).value;
      l.qty = $('.ln-qty', el).value;
      const g = $('.ln-g', el).value.trim();
      l.g = g === '' ? '' : num(g);
      const per = $$('[data-per]', el);
      if (per.length) {
        const o = {};
        per.forEach(p => { o[p.dataset.per] = num(p.value); });
        if (o.kcal + o.p + o.c + o.f > 0) {
          const old = l.per;
          if (!old || ['kcal', 'p', 'c', 'f'].some(k => Math.abs(round1(old[k]) - round1(o[k])) > 0.05)) { l.per = o; l.est = false; }
        }
      }
    });
  }

  function refreshLine(el) {
    const i = +el.dataset.li;
    const l = d.lines[i];
    if (!l || l.sec != null) return;
    const tmp = document.createElement('div');
    tmp.innerHTML = lineHTML(l, i);
    const fresh = tmp.firstElementChild;
    $('.ln-mac', el).replaceWith($('.ln-mac', fresh));
  }
  function refreshTotals() { const t = $('#ed-tot', page.el); if (t) t.innerHTML = totalsHTML(); }

  page.el.addEventListener('input', e => {
    dirty = true;
    const t = e.target;
    if (t.matches('.st textarea')) autoGrow(t);
    const ln = t.closest('.ln');
    if (ln && (t.matches('.ln-g') || t.matches('[data-per]'))) { collect(); refreshLine(ln); refreshTotals(); }
    if (t.id === 'ed-serv') { collect(); refreshTotals(); }
  });
  page.el.addEventListener('change', e => {
    const t = e.target;
    if (t.id === 'ed-file') {
      const f = t.files && t.files[0];
      if (!f) return;
      collect();
      if (photoPreview) URL.revokeObjectURL(photoPreview);
      photoBlob = f; photoPreview = URL.createObjectURL(f); dropPhoto = false; dirty = true;
      paint();
      return;
    }
    const ln = t.closest('.ln');
    if (ln && t.matches('.ln-name')) {
      collect();
      const l = d.lines[+ln.dataset.li];
      const food = foodByName(l.name);
      if (food) {
        l.per = foodPer(food); l.est = false;
        if ((l.g === '' || !num(l.g)) && food.unitG) {
          const m = String(l.qty).match(/^\s*(\d+(?:[.,]\d+)?)/);
          if (m) l.g = round1(num(m[1]) * num(food.unitG));
        }
        if (!str(l.qty) && food.unit) l.qty = `1 ${food.unit}`;
        if (l.g === '' && food.unitG) l.g = num(food.unitG);
        const ge = $('.ln-g', ln); if (ge) ge.value = l.g;
        const qe = $('.ln-qty', ln); if (qe) qe.value = l.qty;
      } else {
        const per = lookupPer(l.name, null);
        if (per && !l.per) l.per = per;
      }
      refreshLine(ln);
      refreshTotals();
    }
  });

  page.el.addEventListener('click', async e => {
    const t = e.target;
    const bk = t.closest('[data-book]');
    if (bk) {
      const id = bk.dataset.book;
      d.books = d.books.includes(id) ? d.books.filter(x => x !== id) : [...d.books, id];
      bk.setAttribute('aria-pressed', d.books.includes(id));
      dirty = true;
      return;
    }
    const la = t.closest('[data-lact]');
    if (la) {
      collect();
      const i = +la.closest('.ln').dataset.li;
      const l = d.lines[i];
      if (la.dataset.lact === 'per') { l.open = !l.open; paint(); return; }
      actionSheet(l.sec != null ? 'Partie' : (str(l.name) || 'Ingrédient'), [
        { label: 'Monter', icon: 'up', run: () => { if (i > 0) { [d.lines[i - 1], d.lines[i]] = [d.lines[i], d.lines[i - 1]]; dirty = true; paint(); } } },
        { label: 'Descendre', icon: 'down', run: () => { if (i < d.lines.length - 1) { [d.lines[i + 1], d.lines[i]] = [d.lines[i], d.lines[i + 1]]; dirty = true; paint(); } } },
        ...(l.sec == null ? [{ label: 'Insérer une partie au-dessus', icon: 'plus', run: () => { d.lines.splice(i, 0, { sec: '' }); dirty = true; paint(); } }] : []),
        { label: 'Supprimer', icon: 'trash', danger: true, run: () => { d.lines.splice(i, 1); dirty = true; paint(); } },
      ]);
      return;
    }
    const sa = t.closest('[data-sact]');
    if (sa) { collect(); const i = +sa.closest('.st').dataset.si; d.steps.splice(i, 1); if (!d.steps.length) d.steps.push(''); dirty = true; paint(); return; }
    const b = t.closest('[data-act]');
    if (!b) return;
    const a = b.dataset.act;
    if (a === 'cancel') tryClose();
    else if (a === 'save') save();
    else if (a === 'add-line') { collect(); d.lines.push({ name: '', qty: '', g: '', per: null, est: false, open: false }); paint(); const all = $$('.ln-name', page.el); if (all.length) all[all.length - 1].focus(); }
    else if (a === 'add-sec') { collect(); d.lines.push({ sec: '' }); paint(); const all = $$('.sec-name', page.el); if (all.length) all[all.length - 1].focus(); }
    else if (a === 'add-step') { collect(); d.steps.push(''); paint(); const all = $$('.st textarea', page.el); if (all.length) all[all.length - 1].focus(); }
    else if (a === 'photo-del') { collect(); dropPhoto = true; if (photoPreview) { URL.revokeObjectURL(photoPreview); photoPreview = null; } photoBlob = null; dirty = true; paint(); }
  });

  async function save() {
    if (saving) return;
    collect();
    if (!d.title) { toast('Donne un titre à la recette.'); const t = $('#ed-title', page.el); if (t) t.focus(); return; }
    saving = true;
    try {
      const ingredients = [];
      for (const l of d.lines) {
        if (l.sec != null) { if (str(l.sec)) ingredients.push({ section: str(l.sec).slice(0, 60) }); continue; }
        if (!str(l.name)) continue;
        ingredients.push(toIngredient(l));
      }
      const oldPhoto = rec && rec.photo;
      let photo = dropPhoto ? null : d.photo;
      if (photoBlob) photo = await savePhoto(await resizeImage(photoBlob, 1400, 0.84));
      const out = Object.assign(rec ? clone(rec) : {}, {
        id: d.id, title: d.title.slice(0, 140), servings: d.servings, ingredients,
        steps: d.steps.map(str).filter(Boolean), notes: str(d.notes), source: d.source, books: d.books, photo,
      });
      const saved = await saveRecipe(out);
      if (oldPhoto && oldPhoto !== photo && !S.recipes.some(x => x.photo === oldPhoto)) deletePhoto(oldPhoto);
      dirty = false;
      page.close();
      toast(rec ? 'Recette modifiée.' : 'Recette enregistrée.');
      if (!rec) openRecipe(saved.id);
      if (opts.onSaved) opts.onSaved(saved);
    } catch (e) {
      console.error(e);
      toast('Enregistrement impossible : ' + (e && e.message ? e.message : 'erreur'));
    } finally {
      saving = false;
    }
  }
  return page;
}

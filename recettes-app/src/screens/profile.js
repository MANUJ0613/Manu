// Onglet Profil : clé Gemini, mes meals (plan du coach), mes aliments, sauvegarde, accueil.
import { S, savePlan, saveFood, deleteFood, saveSettings, exportBackup, importBackup } from '../store.js';
import { $, $$, esc, num, str, arr, f0, f1, norm, rid, pos1, gOrNull, todayISO, plural, resizeImage, blobToB64 } from '../util.js';
import { pushPage, icon, openSheet, actionSheet, confirmSheet, askText, toast, busyHTML } from '../ui.js';
import { setKey, hasKey, KEY_URL, generateJSON, imagePart, textPart } from '../ai.js';
import { readLabel } from '../importers.js';
import { searchFoods } from '../macros.js';
import { openExternal, shareFile, appVersion } from '../native.js';
import { openHowTo } from './add.js';
import { guessCat } from './groceries.js';

const CATS = ['Protéines', 'Produits laitiers', 'Glucides', 'Fruits', 'Légumes', 'Lipides', 'Épicerie', 'Compléments', 'Autre'];
let version = '';
appVersion().then(v => { version = v; });

/* ============================================================ onglet */
export function renderProfile(root) {
  const meals = arr(S.plan.meals);
  const tot = meals.reduce((t, m) => ({ kcal: t.kcal + num(m.kcal), p: t.p + num(m.p), c: t.c + num(m.c), f: t.f + num(m.f) }), { kcal: 0, p: 0, c: 0, f: 0 });
  const model = S.settings.lastModel || (arr(S.settings.models)[0] || '');
  root.innerHTML = `
    <header class="top"><h1>Profil</h1></header>
    <section class="card key-card ${hasKey() ? 'ok' : 'todo'}">
      <div class="kc-h">${icon('sparkle')}<div><h2>Lecture automatique</h2><p>${hasKey() ? `Clé Gemini active${model ? ` · ${esc(model)}` : ''}` : 'Ajoute ta clé Gemini gratuite pour importer depuis Insta, TikTok, une photo…'}</p></div></div>
      <button type="button" class="btn ${hasKey() ? 'ghost' : 'primary'} small" data-go="key">${icon('key')}${hasKey() ? 'Changer la clé' : 'Ajouter ma clé'}</button>
    </section>
    <nav class="rows">
      <button type="button" class="rowb" data-go="meals">${icon('target')}<span><b>Mes meals</b><small>${meals.length ? `${esc(S.plan.name || 'Mon plan')} · ${plural(meals.length, 'meal', 'meals')} · ${f0(tot.kcal)} kcal` : 'Le plan de ton coach, meal par meal'}</small></span>${icon('next')}</button>
      <button type="button" class="rowb" data-go="foods">${icon('scale')}<span><b>Mes aliments</b><small>${plural(S.foods.length, 'aliment', 'aliments')} · valeurs pour 100 g</small></span>${icon('next')}</button>
    </nav>
    <nav class="rows">
      <button type="button" class="rowb" data-go="export">${icon('download')}<span><b>Sauvegarder mes données</b><small>Un fichier à garder sur Drive ou WhatsApp</small></span>${icon('next')}</button>
      <button type="button" class="rowb" data-go="import">${icon('upload')}<span><b>Restaurer une sauvegarde</b><small>Plan, recettes, aliments, photos</small></span>${icon('next')}</button>
    </nav>
    <nav class="rows">
      <button type="button" class="rowb" data-go="howto">${icon('shareIn')}<span><b>Importer depuis Insta ou TikTok</b><small>Le bouton Partager, pas à pas</small></span>${icon('next')}</button>
    </nav>
    <p class="foot-note">Tes recettes restent sur ce téléphone. Pense à sauvegarder de temps en temps.${version ? `<br>Version ${esc(version)}` : ''}</p>`;
  root.onclick = e => {
    const b = e.target.closest('[data-go]');
    if (!b) return;
    const g = b.dataset.go;
    if (g === 'key') openKeySetup();
    else if (g === 'meals') openMeals();
    else if (g === 'foods') openFoods();
    else if (g === 'export') doExport();
    else if (g === 'import') doImport();
    else if (g === 'howto') openHowTo();
  };
}
export function openProfileSection(what) {
  if (what === 'meals') openMeals();
  else if (what === 'foods') openFoods();
  else if (what === 'key') openKeySetup();
}

/* ============================================================ clé Gemini */
export function openKeySetup(onDone) {
  const sh = openSheet({
    title: 'Clé Gemini gratuite',
    html: `<ol class="howto">
        <li>Ouvre <b>Google AI Studio</b> et connecte-toi avec ton compte Google.<br><button type="button" class="btn small ghost" data-open>${icon('link')}Ouvrir AI Studio</button></li>
        <li>Appuie sur <b>Create API key</b> (Créer une clé API), puis <b>copie</b> la clé.</li>
        <li>Reviens ici et colle-la :</li>
      </ol>
      <div class="field"><label for="k-in">Clé API</label><input id="k-in" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="AIza…" value=""></div>
      <div id="k-msg"></div>
      <button type="button" class="btn primary block" data-ok>${icon('check')}Vérifier et enregistrer</button>
      <p class="hint">Gratuit, sans carte bancaire. Avec l'offre gratuite, Google peut utiliser ce que tu lui envoies (recettes, captures) pour améliorer ses produits : n'y envoie rien de personnel.</p>
      ${hasKey() ? '<button type="button" class="btn ghost block danger-text" data-del>Retirer ma clé</button>' : ''}`,
  });
  $('[data-open]', sh.body).addEventListener('click', () => openExternal(KEY_URL));
  const del = $('[data-del]', sh.body);
  if (del) del.addEventListener('click', async () => { await saveSettings({ geminiKey: '', models: [], lastModel: '' }); sh.close(); toast('Clé retirée.'); });
  const ok = $('[data-ok]', sh.body);
  ok.addEventListener('click', async () => {
    const k = str($('#k-in', sh.body).value).replace(/\s+/g, '');
    const msg = $('#k-msg', sh.body);
    if (k.length < 20) { msg.innerHTML = '<p class="err">Colle la clé en entier (elle commence souvent par AIza).</p>'; return; }
    ok.disabled = true; sh.busy = true;
    msg.innerHTML = busyHTML('Vérification auprès de Google…');
    try {
      const models = await setKey(k);
      sh.busy = false;
      sh.close();
      toast(`Clé enregistrée · ${models[0]}`);
      if (onDone) onDone();
    } catch (e) {
      msg.innerHTML = `<p class="err">${esc(e.message || 'Clé refusée.')}</p>`;
    } finally {
      ok.disabled = false; sh.busy = false;
    }
  });
  setTimeout(() => $('#k-in', sh.body).focus(), 300);
}

/* ============================================================ mes meals */
function openMeals() {
  const page = pushPage({ cls: 'meals-page', render });
  function render(p) {
    const meals = arr(S.plan.meals);
    const tot = meals.reduce((t, m) => ({ kcal: t.kcal + num(m.kcal), p: t.p + num(m.p), c: t.c + num(m.c), f: t.f + num(m.f) }), { kcal: 0, p: 0, c: 0, f: 0 });
    p.el.innerHTML = `<div class="p-top"><button type="button" class="icon-btn" data-act="back" aria-label="Retour">${icon('back')}</button><h1>Mes meals</h1><button type="button" class="icon-btn" data-act="more" aria-label="Options">${icon('more')}</button></div>
      <div class="p-body">
        <button type="button" class="plan-name" data-act="rename"><span class="muted">Plan</span><b>${esc(S.plan.name || 'Mon plan')}</b>${icon('pen')}</button>
        ${meals.length ? `<div class="day-sum small"><div><b>${f0(tot.kcal)}</b><span>kcal</span></div><div><b>${f0(tot.p)}</b><span>prot.</span></div><div><b>${f0(tot.c)}</b><span>gluc.</span></div><div><b>${f0(tot.f)}</b><span>lip.</span></div></div>` : ''}
        <ol class="meal-list">${meals.map((m, i) => `<li><button type="button" class="meal" data-i="${i}">
          <span class="m-t">${esc(m.time || '—')}</span>
          <span class="m-b"><b>${esc(m.name)}</b>${m.compare === false ? '<em>hors comparaison</em>' : ''}<small>${f0(m.kcal)} kcal · P ${f1(m.p)} · G ${f1(m.c)} · L ${f1(m.f)}</small>${m.note ? `<small class="m-note">${esc(m.note)}</small>` : ''}</span>
        </button></li>`).join('')}</ol>
        ${!meals.length ? '<div class="empty-state small"><p>Aucun meal. Ajoute-les un par un, ou fais lire les captures du plan de ton coach.</p></div>' : ''}
        <div class="row-btns stack">
          <button type="button" class="btn primary" data-act="scan">${icon('image')}Lire mon plan depuis des captures</button>
          <button type="button" class="btn ghost" data-act="add">${icon('plus')}Ajouter un meal</button>
        </div>
      </div>`;
  }
  page.el.addEventListener('click', async e => {
    const it = e.target.closest('[data-i]');
    if (it) { mealEditor(+it.dataset.i); return; }
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const a = b.dataset.act;
    if (a === 'back') page.close();
    else if (a === 'add') mealEditor(-1);
    else if (a === 'scan') scanPlan();
    else if (a === 'rename') { const n = await askText({ title: 'Nom du plan', value: S.plan.name || '', placeholder: 'ex. Plan 4657 kcal' }); if (n != null) await savePlan({ ...S.plan, name: n }); }
    else if (a === 'more') actionSheet('Mes meals', [
      { label: 'Lire mon plan depuis des captures', icon: 'image', run: scanPlan },
      { label: 'Tout effacer', icon: 'trash', danger: true, run: async () => { if (await confirmSheet({ title: 'Effacer tous les meals ?', ok: 'Effacer', danger: true })) await savePlan({ name: '', meals: [] }); } },
    ]);
  });
  return page;
}

function mealEditor(i) {
  const meals = arr(S.plan.meals).slice();
  const m = i >= 0 ? { ...meals[i] } : { id: 'm' + rid(), name: `Meal ${meals.length + 1}`, time: '', kcal: '', p: '', c: '', f: '', compare: true, note: '' };
  const sh = openSheet({
    title: i >= 0 ? m.name : 'Nouveau meal',
    html: `<div class="grid2">
        <div class="field"><label for="me-n">Nom</label><input id="me-n" value="${esc(m.name)}" autocomplete="off"></div>
        <div class="field"><label for="me-t">Heure</label><input id="me-t" type="time" value="${esc(m.time || '')}"></div>
      </div>
      <div class="grid4">
        <div class="field"><label for="me-k">kcal</label><input id="me-k" inputmode="decimal" value="${esc(m.kcal)}"></div>
        <div class="field"><label for="me-p" class="tp">Prot.</label><input id="me-p" inputmode="decimal" value="${esc(m.p)}"></div>
        <div class="field"><label for="me-c" class="tg">Gluc.</label><input id="me-c" inputmode="decimal" value="${esc(m.c)}"></div>
        <div class="field"><label for="me-f" class="tl">Lip.</label><input id="me-f" inputmode="decimal" value="${esc(m.f)}"></div>
      </div>
      <div class="field"><label for="me-note">Composition du coach</label><textarea id="me-note" rows="3" placeholder="ex. 165 g de poulet, 300 g de riz…">${esc(m.note || '')}</textarea></div>
      <label class="check"><input type="checkbox" id="me-cmp" ${m.compare !== false ? 'checked' : ''}> Comparer mes recettes à ce meal</label>
      <div class="row-btns">${i >= 0 ? `<button type="button" class="btn ghost danger-text" data-del>Supprimer</button>` : ''}<button type="button" class="btn primary" data-ok>Enregistrer</button></div>
      ${i >= 0 ? `<div class="row-btns"><button type="button" class="btn small ghost" data-mv="-1">${icon('up')}Monter</button><button type="button" class="btn small ghost" data-mv="1">${icon('down')}Descendre</button></div>` : ''}`,
  });
  const v = id => $(id, sh.body).value;
  sh.body.addEventListener('click', async e => {
    if (e.target.closest('[data-ok]')) {
      const k = num(v('#me-k')), p = num(v('#me-p')), c = num(v('#me-c')), f = num(v('#me-f'));
      const out = { ...m, name: str(v('#me-n')) || m.name, time: str(v('#me-t')), kcal: pos1(k || p * 4 + c * 4 + f * 9), p: pos1(p), c: pos1(c), f: pos1(f), note: str(v('#me-note')), compare: $('#me-cmp', sh.body).checked };
      if (i >= 0) meals[i] = out; else meals.push(out);
      await savePlan({ ...S.plan, meals });
      sh.close();
      toast('Meal enregistré.');
    } else if (e.target.closest('[data-del]')) {
      if (!(await confirmSheet({ title: `Supprimer ${m.name} ?`, ok: 'Supprimer', danger: true }))) return;
      meals.splice(i, 1);
      await savePlan({ ...S.plan, meals });
      sh.close();
    } else {
      const mv = e.target.closest('[data-mv]');
      if (!mv) return;
      const j = i + +mv.dataset.mv;
      if (j < 0 || j >= meals.length) return;
      [meals[i], meals[j]] = [meals[j], meals[i]];
      await savePlan({ ...S.plan, meals });
      sh.close();
    }
  });
}

function scanPlan() {
  const inp = document.createElement('input');
  inp.type = 'file'; inp.accept = 'image/*'; inp.multiple = true; inp.style.display = 'none';
  document.body.appendChild(inp);
  inp.addEventListener('change', async () => {
    const files = Array.from(inp.files || []).slice(0, 8);
    inp.remove();
    if (!files.length) return;
    if (!hasKey()) { openKeySetup(); return; }
    const sh = openSheet({ title: 'Lecture du plan', full: true, html: busyHTML('Gemini lit ton plan…') });
    sh.busy = true;
    try {
      const parts = [];
      for (const f of files) parts.push(imagePart(await blobToB64(await resizeImage(f, 1800, 0.86))));
      parts.push(textPart(`Ces images sont mon plan alimentaire (diète de musculation) donné par mon coach. Extrais chaque repas dans l'ordre.
Réponds avec UNIQUEMENT un objet JSON : {"name": "Plan 4657 kcal", "meals": [{"name": "Meal 1", "time": "", "kcal": 0, "p": 0, "c": 0, "f": 0, "note": "3 œufs, 250 g de blancs d'œufs, 100 g de myrtilles", "supplement": false}]}
- Garde les noms des repas tels qu'écrits (Meal 1, Collation 1, Pré-training…) et leur ordre.
- kcal, p (protéines), c (glucides), f (lipides) = totaux du repas s'ils sont écrits ; sinon calcule-les à partir des aliments et des quantités.
- note = les aliments avec leurs quantités, en une ligne.
- supplement = true pour un repas qui n'est qu'un shaker de compléments (pré ou intra-training).
- time = l'heure si elle est écrite, sinon "".
- name du plan = le titre ou les calories totales s'ils sont écrits.`));
      const j = await generateJSON(parts);
      sh.busy = false;
      const got = arr(j.meals).filter(x => x && x.name).map(x => ({
        name: str(x.name).slice(0, 40), time: str(x.time).slice(0, 5), kcal: pos1(x.kcal), p: pos1(x.p), c: pos1(x.c), f: pos1(x.f),
        note: str(x.note).slice(0, 400), compare: !x.supplement,
      }));
      if (!got.length) { sh.body.innerHTML = '<p class="err">Aucun repas trouvé sur ces images.</p>'; return; }
      const tot = got.reduce((t, m) => t + m.kcal, 0);
      sh.body.innerHTML = `<p class="lead">${got.length} repas lus · ${f0(tot)} kcal au total. Vérifie avant de remplacer ton plan.</p>
        <ol class="meal-list">${got.map(m => `<li><div class="meal static"><span class="m-t">${esc(m.time || '—')}</span><span class="m-b"><b>${esc(m.name)}</b>${m.compare ? '' : '<em>hors comparaison</em>'}<small>${f0(m.kcal)} kcal · P ${f1(m.p)} · G ${f1(m.c)} · L ${f1(m.f)}</small>${m.note ? `<small class="m-note">${esc(m.note)}</small>` : ''}</span></div></li>`).join('')}</ol>
        <div class="row-btns stack"><button type="button" class="btn primary" data-ok>Remplacer mon plan</button><button type="button" class="btn ghost" data-close>Annuler</button></div>`;
      $('[data-ok]', sh.body).addEventListener('click', async () => {
        const old = arr(S.plan.meals);
        const meals = got.map(m => {
          const prev = old.find(o => norm(o.name) === norm(m.name));
          return { id: prev ? prev.id : 'm' + rid(), ...m, time: m.time || (prev && prev.time) || '' };
        });
        await savePlan({ name: str(j.name) || S.plan.name || `Plan ${f0(tot)} kcal`, meals });
        sh.close();
        toast('Plan mis à jour. Toutes les recettes sont recomparées.');
      });
    } catch (e) {
      sh.busy = false;
      sh.body.innerHTML = `<p class="err">${esc(e.message || 'Lecture impossible.')}</p><button type="button" class="btn ghost block" data-close>Fermer</button>`;
    }
  });
  inp.click();
}

/* ============================================================ mes aliments */
function openFoods() {
  let q = '';
  const page = pushPage({ cls: 'foods-page', render });
  function listHTML() {
    const list = q ? searchFoods(q, 200) : S.foods.slice().sort((a, b) => (CATS.indexOf(a.cat) - CATS.indexOf(b.cat)) || a.name.localeCompare(b.name, 'fr'));
    let cat = null;
    return list.map(f => {
      const h = !q && f.cat !== cat ? `<h3 class="f-cat">${esc(f.cat || 'Autre')}</h3>` : '';
      cat = f.cat;
      return `${h}<button type="button" class="food" data-f="${esc(f.id)}"><span class="fn">${esc(f.name)}${f.unit && f.unitG ? `<small>1 ${esc(f.unit)} = ${f1(f.unitG)} g</small>` : ''}</span><span class="fv">${f0(f.kcal)} kcal<small>P ${f1(f.p)} · G ${f1(f.c)} · L ${f1(f.f)}</small></span></button>`;
    }).join('') || '<p class="muted">Aucun aliment.</p>';
  }
  function render(p) {
    p.el.innerHTML = `<div class="p-top"><button type="button" class="icon-btn" data-act="back" aria-label="Retour">${icon('back')}</button><h1>Mes aliments</h1><button type="button" class="icon-btn" data-act="add" aria-label="Ajouter un aliment">${icon('plus')}</button></div>
      <div class="p-body">
        <label class="search">${icon('search')}<input type="search" id="fd-q" placeholder="Rechercher un aliment" autocomplete="off" value="${esc(q)}"></label>
        <div class="row-btns"><button type="button" class="btn small ghost" data-act="scan">${icon('label')}Scanner une étiquette</button><button type="button" class="btn small ghost" data-act="add">${icon('plus')}Ajouter</button></div>
        <p class="hint">Valeurs pour 100 g. Les recettes utilisent ces valeurs quand le nom de l'ingrédient est identique.</p>
        <div id="fd-list" class="foods">${listHTML()}</div>
      </div>`;
    $('#fd-q', p.el).addEventListener('input', e => { q = e.target.value; $('#fd-list', p.el).innerHTML = listHTML(); });
  }
  page.el.addEventListener('click', e => {
    const f = e.target.closest('[data-f]');
    if (f) { foodEditor(S.foods.find(x => x.id === f.dataset.f)); return; }
    const b = e.target.closest('[data-act]');
    if (!b) return;
    if (b.dataset.act === 'back') page.close();
    else if (b.dataset.act === 'add') foodEditor(null);
    else if (b.dataset.act === 'scan') scanLabel();
  });
  return page;
}

function foodEditor(food, prefill = {}) {
  const f = food ? { ...food } : { name: '', cat: 'Autre', kcal: '', p: '', c: '', f: '', unit: '', unitG: '', aliases: [], ...prefill };
  const sh = openSheet({
    title: food ? food.name : 'Nouvel aliment',
    html: `<div class="field"><label for="fe-n">Nom</label><input id="fe-n" value="${esc(f.name)}" autocomplete="off" placeholder="ex. Skyr nature 0 %"></div>
      <div class="field"><label for="fe-cat">Rayon</label><select id="fe-cat">${CATS.map(c => `<option ${c === f.cat ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></div>
      <p class="flabel">Pour 100 g</p>
      <div class="grid4">
        <div class="field"><label for="fe-k">kcal</label><input id="fe-k" inputmode="decimal" value="${esc(f.kcal)}"></div>
        <div class="field"><label for="fe-p" class="tp">Prot.</label><input id="fe-p" inputmode="decimal" value="${esc(f.p)}"></div>
        <div class="field"><label for="fe-c" class="tg">Gluc.</label><input id="fe-c" inputmode="decimal" value="${esc(f.c)}"></div>
        <div class="field"><label for="fe-f" class="tl">Lip.</label><input id="fe-f" inputmode="decimal" value="${esc(f.f)}"></div>
      </div>
      <div class="grid2">
        <div class="field"><label for="fe-u">Unité (facultatif)</label><input id="fe-u" value="${esc(f.unit || '')}" placeholder="tranche, œuf, dose…" autocomplete="off"></div>
        <div class="field"><label for="fe-ug">Poids d'une unité (g)</label><input id="fe-ug" inputmode="decimal" value="${esc(f.unitG || '')}"></div>
      </div>
      <div class="field"><label for="fe-al">Autres noms (séparés par des virgules)</label><input id="fe-al" value="${esc(arr(f.aliases).join(', '))}" autocomplete="off" placeholder="ex. fromage blanc 0%, fb 0"></div>
      <div class="row-btns">${food ? '<button type="button" class="btn ghost danger-text" data-del>Supprimer</button>' : ''}<button type="button" class="btn primary" data-ok>Enregistrer</button></div>`,
  });
  const v = id => $(id, sh.body).value;
  sh.body.addEventListener('click', async e => {
    if (e.target.closest('[data-ok]')) {
      const name = str(v('#fe-n'));
      if (!name) { toast('Donne un nom à l’aliment.'); return; }
      const p = num(v('#fe-p')), c = num(v('#fe-c')), fat = num(v('#fe-f'));
      const out = {
        ...f, id: f.id || norm(name).replace(/\s+/g, '-').slice(0, 40) + '-' + rid().slice(-4), name, cat: v('#fe-cat'),
        kcal: pos1(num(v('#fe-k')) || p * 4 + c * 4 + fat * 9), p: pos1(p), c: pos1(c), f: pos1(fat),
        unit: str(v('#fe-u')).slice(0, 20), unitG: gOrNull(v('#fe-ug')),
        aliases: str(v('#fe-al')).split(',').map(s => s.trim()).filter(Boolean).slice(0, 8),
      };
      if (!out.unit || !out.unitG) { delete out.unit; delete out.unitG; }
      await saveFood(out);
      sh.close();
      toast('Aliment enregistré.');
    } else if (e.target.closest('[data-del]')) {
      if (await confirmSheet({ title: `Supprimer ${f.name} ?`, body: 'Les recettes gardent leurs valeurs actuelles.', ok: 'Supprimer', danger: true })) { await deleteFood(f.id); sh.close(); }
    }
  });
}

function scanLabel() {
  if (!hasKey()) { openKeySetup(); return; }
  const inp = document.createElement('input');
  inp.type = 'file'; inp.accept = 'image/*'; inp.style.display = 'none';
  document.body.appendChild(inp);
  inp.addEventListener('change', async () => {
    const file = inp.files && inp.files[0];
    inp.remove();
    if (!file) return;
    const sh = openSheet({ title: 'Lecture de l’étiquette', html: busyHTML('Gemini lit l’étiquette…') });
    sh.busy = true;
    try {
      const pre = await readLabel(file);
      sh.busy = false;
      sh.close();
      foodEditor(null, { name: pre.name, cat: guessCat(pre.name), kcal: pre.kcal, p: pre.p, c: pre.c, f: pre.f, unit: pre.unit, unitG: pre.unitG || '' });
      toast('Vérifie les valeurs avant d’enregistrer.');
    } catch (e) {
      sh.busy = false;
      sh.body.innerHTML = `<p class="err">${esc(e.message || 'Lecture impossible.')}</p><button type="button" class="btn ghost block" data-close>Fermer</button>`;
    }
  });
  inp.click();
}

/* ============================================================ sauvegarde */
async function doExport() {
  try {
    const data = await exportBackup();
    await shareFile(`au-gramme-pres-${todayISO()}.json`, JSON.stringify(data));
    await saveSettings({ lastBackup: Date.now() });
  } catch (e) {
    if (e && /cancel/i.test(String(e.message))) return;
    toast('Sauvegarde impossible : ' + (e && e.message ? e.message : 'erreur'));
  }
}

export function doImport(onDone) {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.accept = '*/*';
  inp.style.display = 'none';
  document.body.appendChild(inp);
  inp.addEventListener('change', async () => {
    const file = inp.files && inp.files[0];
    inp.remove();
    if (!file) return;
    let data;
    try { data = JSON.parse(await file.text()); } catch (e) { toast('Ce fichier n’est pas une sauvegarde valide.'); return; }
    const nR = arr(data.recipes).length, nM = arr(data.plan && data.plan.meals).length;
    const ok = await confirmSheet({
      title: 'Restaurer cette sauvegarde ?',
      body: `${plural(nR, 'recette', 'recettes')}${nM ? `, un plan de ${nM} meals (remplace le plan actuel)` : ''}, tes aliments et tes livres. Les recettes déjà présentes avec le même identifiant sont remplacées.`,
      ok: 'Restaurer',
    });
    if (!ok) return;
    try {
      const res = await importBackup(data);
      toast(`Restauré : ${plural(res.recipes, 'recette', 'recettes')}${res.plan ? ' et ton plan' : ''}.`);
      if (onDone) onDone(res);
    } catch (e) {
      toast('Restauration impossible : ' + (e && e.message ? e.message : 'erreur'));
    }
  });
  inp.click();
}

/* ============================================================ accueil (premier lancement) */
export function openOnboarding() {
  const page = pushPage({ cls: 'onboard', live: true, render, onBack: () => { finish(); return true; } });
  function render(p) {
    const hasData = S.recipes.length || arr(S.plan.meals).length;
    p.el.innerHTML = `<div class="ob">
      <div class="ob-logo">${icon('scale')}</div>
      <h1>Au gramme près</h1>
      <p class="ob-sub">Tes recettes d'Insta, TikTok et d'ailleurs, calées sur ton plan au gramme près.</p>
      <div class="ob-step ${hasData ? 'done' : ''}">
        <span class="ob-n">${hasData ? icon('check') : '1'}</span>
        <div><h2>Ton plan et tes recettes</h2><p>${hasData ? `${plural(S.recipes.length, 'recette', 'recettes')} · ${plural(arr(S.plan.meals).length, 'meal', 'meals')}` : 'Tu as un fichier de sauvegarde (.json) ? Restaure-le. Sinon, ajoute tes meals dans Profil.'}</p>
        ${hasData ? '' : `<button type="button" class="btn small primary" data-act="restore">${icon('upload')}Restaurer une sauvegarde</button>`}</div>
      </div>
      <div class="ob-step ${hasKey() ? 'done' : ''}">
        <span class="ob-n">${hasKey() ? icon('check') : '2'}</span>
        <div><h2>Clé Gemini gratuite</h2><p>${hasKey() ? 'Clé active : la lecture automatique marche.' : 'Pour lire les recettes toute seule (liens, captures, texte). 2 minutes, sans carte bancaire.'}</p>
        ${hasKey() ? '' : `<button type="button" class="btn small primary" data-act="key">${icon('key')}Ajouter ma clé</button>`}</div>
      </div>
      <div class="ob-step">
        <span class="ob-n">3</span>
        <div><h2>Partage depuis Insta ou TikTok</h2><p>Bouton Partager, puis <b>Au gramme près</b>.</p>
        <button type="button" class="btn small ghost" data-act="howto">Voir comment</button></div>
      </div>
      <button type="button" class="btn primary block big" data-act="go">C'est parti</button>
    </div>`;
  }
  async function finish() { await saveSettings({ onboarded: true }); page.close(); }
  page.el.addEventListener('click', e => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const a = b.dataset.act;
    if (a === 'restore') doImport(() => page.refresh());
    else if (a === 'key') openKeySetup(() => page.refresh());
    else if (a === 'howto') openHowTo();
    else if (a === 'go') finish();
  });
  return page;
}


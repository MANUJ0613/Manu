// Ajout de recettes : feuille « Ajouter une recette », écrans d'import, partage entrant.
import { S } from '../store.js';
import { $, esc, str, arr, firstUrl, dataUrlToBlob, hostOf } from '../util.js';
import { pushPage, openSheet, icon, toast, nav } from '../ui.js';
import { importFromLink, importFromText, importFromImages, detectPlatform, PLATFORM_LABEL } from '../importers.js';
import { hasKey } from '../ai.js';
import { openEditor } from './editor.js';
import { openKeySetup } from './profile.js';

/* ============================================================ feuille d'ajout */
export function openAddSheet() {
  const sh = openSheet({
    title: 'Ajouter une recette',
    html: `
      <button type="button" class="add-social" data-add="social">
        <span class="soc-ic">${icon('shareIn')}</span>
        <span><b>Importer depuis les réseaux</b><small>Instagram, TikTok, YouTube, Facebook : appuie sur Partager puis choisis Au gramme près</small></span>
      </button>
      <div class="add-grid">
        <button type="button" data-add="photo"><span class="add-ic">${icon('image')}</span><b>Depuis une photo</b><small>Captures, livre, carnet</small></button>
        <button type="button" data-add="text"><span class="add-ic">${icon('text')}</span><b>Depuis du texte</b><small>Description copiée</small></button>
        <button type="button" data-add="link"><span class="add-ic">${icon('link')}</span><b>Depuis un lien</b><small>Site, blog, post</small></button>
        <button type="button" data-add="write"><span class="add-ic">${icon('pen')}</span><b>Écrire à partir de zéro</b><small>Ta propre recette</small></button>
      </div>
      ${hasKey() ? '' : `<p class="hint">${icon('key')}Pour la lecture automatique, ajoute ta clé Gemini gratuite. <button type="button" class="linkish" data-add="key">Ajouter ma clé</button></p>`}`,
  });
  sh.body.addEventListener('click', e => {
    const b = e.target.closest('[data-add]');
    if (!b) return;
    const a = b.dataset.add;
    if (a === 'photo') { pickImages(files => { sh.close(); openPhotoSheet(files); }); return; }
    sh.close();
    setTimeout(() => {
      if (a === 'social') openLinkSheet({ social: true });
      else if (a === 'text') openTextSheet();
      else if (a === 'link') openLinkSheet();
      else if (a === 'write') openEditor(null);
      else if (a === 'key') openKeySetup();
    }, 150);
  });
}

function pickImages(cb) {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.accept = 'image/*';
  inp.multiple = true;
  inp.style.display = 'none';
  document.body.appendChild(inp);
  inp.addEventListener('change', () => {
    const files = Array.from(inp.files || []);
    inp.remove();
    if (files.length) cb(files);
  });
  inp.click();
}

function openLinkSheet({ social = false, url = '' } = {}) {
  const sh = openSheet({
    title: social ? 'Depuis les réseaux' : 'Depuis un lien',
    html: `${social ? `<ol class="howto small">
        <li>Ouvre la recette dans <b>Instagram</b>, <b>TikTok</b> ou <b>YouTube</b>.</li>
        <li>Appuie sur <b>Partager</b> (l'avion en papier ou la flèche), puis <b>Plus</b> si besoin.</li>
        <li>Choisis <b>Au gramme près</b> : la recette arrive toute seule ici.</li>
      </ol><p class="flabel">Ou colle le lien :</p>` : ''}
      <div class="field"><label for="lk-url">Lien</label><input id="lk-url" type="url" inputmode="url" autocomplete="off" placeholder="https://www.instagram.com/reel/…" value="${esc(url)}"></div>
      <div class="field"><label for="lk-note">Précisions (facultatif)</label><input id="lk-note" autocomplete="off" placeholder="ex. la recette est pour 4 personnes"></div>
      <button type="button" class="btn primary block" data-go>${icon('sparkle')}Importer la recette</button>`,
  });
  const go = () => {
    const u = firstUrl($('#lk-url', sh.body).value) || str($('#lk-url', sh.body).value);
    if (!/^https?:\/\//i.test(u)) { toast('Colle un lien qui commence par https://'); $('#lk-url', sh.body).focus(); return; }
    const note = str($('#lk-note', sh.body).value);
    sh.close();
    startImport({ kind: 'link', url: u, note });
  };
  $('[data-go]', sh.body).addEventListener('click', go);
  $('#lk-url', sh.body).addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  if (!social) setTimeout(() => $('#lk-url', sh.body).focus(), 280);
}

export function openTextSheet(prefill = '', url = '') {
  const sh = openSheet({
    title: 'Depuis du texte',
    html: `<div class="field"><label for="tx-t">Texte de la recette</label><textarea id="tx-t" rows="9" placeholder="Colle ici la description de la vidéo ou la recette…">${esc(prefill)}</textarea></div>
      <div class="field"><label for="tx-u">Lien ou compte (facultatif)</label><input id="tx-u" autocomplete="off" placeholder="https://… ou @compte" value="${esc(url)}"></div>
      <div class="field"><label for="tx-n">Précisions (facultatif)</label><input id="tx-n" autocomplete="off" placeholder="ex. la recette est pour 2 pots"></div>
      <button type="button" class="btn primary block" data-go>${icon('sparkle')}Lire la recette</button>`,
  });
  $('[data-go]', sh.body).addEventListener('click', () => {
    const text = str($('#tx-t', sh.body).value);
    if (text.length < 15) { toast('Colle d’abord le texte de la recette.'); $('#tx-t', sh.body).focus(); return; }
    const u = str($('#tx-u', sh.body).value);
    const note = str($('#tx-n', sh.body).value);
    sh.close();
    startImport({ kind: 'text', text, url: /^https?:/i.test(u) ? u : '', label: /^https?:/i.test(u) ? '' : u, note });
  });
  if (!prefill) setTimeout(() => $('#tx-t', sh.body).focus(), 280);
}

function openPhotoSheet(files, text = '') {
  const urls = files.map(f => URL.createObjectURL(f));
  const sh = openSheet({
    title: 'Depuis une photo',
    onClose: () => setTimeout(() => urls.forEach(u => URL.revokeObjectURL(u)), 500),
    html: `<div class="thumbs">${urls.map(u => `<img src="${u}" alt="">`).join('')}</div>
      <label class="check"><input type="checkbox" id="ph-keep" checked> Garder la première image comme photo de la recette</label>
      <div class="field"><label for="ph-n">Précisions (facultatif)</label><input id="ph-n" autocomplete="off" placeholder="ex. la recette est pour 2 pots"></div>
      <button type="button" class="btn primary block" data-go>${icon('sparkle')}Lire la recette</button>`,
  });
  $('[data-go]', sh.body).addEventListener('click', () => {
    const keep = $('#ph-keep', sh.body).checked;
    const note = str($('#ph-n', sh.body).value);
    sh.close();
    startImport({ kind: 'images', images: files.slice(0, 6), keepPhoto: keep, note, text });
  });
}

/* ============================================================ écran d'import */
export function startImport(input) {
  let cancelled = false;
  let t0 = Date.now();
  let timer = null;
  const page = pushPage({
    cls: 'importing',
    live: false,
    state: { msg: 'Préparation…', err: null },
    render: p => paint(p),
    onClose: () => { cancelled = true; clearInterval(timer); },
  });

  function sourceHTML() {
    if (input.kind === 'link') {
      const pf = detectPlatform(input.url);
      return `<p class="imp-src">${icon('link')}<span>${esc(PLATFORM_LABEL[pf])} · ${esc(hostOf(input.url))}</span></p>`;
    }
    if (input.kind === 'images') return `<p class="imp-src">${icon('image')}<span>${input.images.length} image${input.images.length > 1 ? 's' : ''}</span></p>`;
    return `<p class="imp-src">${icon('text')}<span>Texte collé</span></p>`;
  }
  function paint(p) {
    const st = p.state;
    if (st.err) {
      const e = st.err;
      const keyIssue = e.code === 'nokey' || e.code === 'key';
      p.el.innerHTML = `<div class="imp">
        <button type="button" class="icon-btn imp-x" data-act="close" aria-label="Fermer">${icon('close')}</button>
        <div class="imp-card">
          ${sourceHTML()}
          <h2>${keyIssue ? 'Clé Gemini nécessaire' : 'Import impossible'}</h2>
          <p class="imp-err">${esc(e.message || 'Erreur inconnue.')}</p>
          <div class="row-btns stack">
            ${keyIssue ? `<button type="button" class="btn primary" data-act="key">${icon('key')}Ajouter ma clé Gemini</button>` : `<button type="button" class="btn primary" data-act="retry">Réessayer</button>`}
            <button type="button" class="btn ghost" data-act="photo">${icon('image')}Importer une capture à la place</button>
            <button type="button" class="btn ghost" data-act="text">${icon('text')}Coller le texte</button>
            <button type="button" class="btn ghost" data-act="write">${icon('pen')}L'écrire moi-même</button>
          </div>
        </div>
      </div>`;
      return;
    }
    p.el.innerHTML = `<div class="imp">
      <button type="button" class="icon-btn imp-x" data-act="close" aria-label="Annuler">${icon('close')}</button>
      <div class="imp-card">
        ${sourceHTML()}
        <div class="loader" aria-hidden="true"><span></span><span></span><span></span></div>
        <p class="imp-msg" aria-live="polite">${esc(st.msg)}</p>
        <p class="imp-t">${Math.round((Date.now() - t0) / 1000)} s</p>
        <button type="button" class="btn ghost" data-act="close">Annuler</button>
      </div>
    </div>`;
  }
  const setMsg = m => { page.state.msg = m; const el = $('.imp-msg', page.el); if (el) el.textContent = m; };

  async function run() {
    page.state.err = null;
    t0 = Date.now();
    page.refresh();
    clearInterval(timer);
    timer = setInterval(() => { const el = $('.imp-t', page.el); if (el) el.textContent = Math.round((Date.now() - t0) / 1000) + ' s'; }, 1000);
    const common = { note: input.note || '', onStep: setMsg, isCancelled: () => cancelled };
    try {
      let res;
      if (input.kind === 'link') res = await importFromLink(input.url, { ...common, sharedText: input.text || '' });
      else if (input.kind === 'text') res = await importFromText(input.text, { ...common, url: input.url || '' });
      else res = await importFromImages(input.images, { ...common, text: input.text || '' });
      if (cancelled) return;
      clearInterval(timer);
      const recipe = res.recipe;
      if (!recipe.isRecipe) {
        page.state.err = { code: 'norecipe', message: 'Pas de recette trouvée dans ce contenu. Si elle est seulement dite dans la vidéo, fais des captures des ingrédients.' };
        page.refresh();
        return;
      }
      if (input.label && !recipe.source.label) recipe.source.label = input.label;
      let photo = res.photo || null;
      if (!photo && input.kind === 'images' && input.keepPhoto !== false) photo = input.images[0];
      page.close();
      openEditor(null, { draft: recipe, photoBlob: photo, fromImport: true });
    } catch (e) {
      if (cancelled || (e && e.code === 'cancelled')) return;
      clearInterval(timer);
      console.error(e);
      page.state.err = { code: (e && e.code) || 'error', message: (e && e.message) || 'Erreur inconnue.', text: '' };
      page.refresh();
    }
  }

  page.el.addEventListener('click', e => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const a = b.dataset.act;
    if (a === 'close') { page.close(); return; }
    if (a === 'retry') { run(); return; }
    if (a === 'key') { openKeySetup(() => run()); return; }
    page.close();
    setTimeout(() => {
      if (a === 'photo') pickImages(files => openPhotoSheet(files, input.url || ''));
      else if (a === 'text') openTextSheet(input.kind === 'text' ? input.text : '', input.url || '');
      else if (a === 'write') openEditor(null, input.url ? { draft: { title: '', servings: 1, ingredients: [], steps: [], notes: '', source: { url: input.url, label: PLATFORM_LABEL[detectPlatform(input.url)] }, books: [] } } : {});
    }, 150);
  });
  run();
  return page;
}

/* ============================================================ partage entrant */
const seen = new Set();
export function handleShared(d) {
  if (!d || seen.has(d.id)) return;
  seen.add(d.id);
  const images = arr(d.images).map(dataUrlToBlob).filter(Boolean);
  const text = [str(d.subject), str(d.text)].filter(Boolean).join('\n').trim();
  if (nav.tab !== 'recettes') nav.setTab('recettes');
  if (images.length) { startImport({ kind: 'images', images, text, keepPhoto: true }); return; }
  const url = firstUrl(text);
  if (url) { startImport({ kind: 'link', url, text: text.replace(url, '').trim() }); return; }
  if (text.length >= 15) { startImport({ kind: 'text', text }); return; }
  toast('Rien à importer dans ce partage.');
}

/* ============================================================ aide */
export function openHowTo() {
  const page = pushPage({
    cls: 'howto-page',
    live: false,
    render: p => {
      p.el.innerHTML = `<div class="p-top"><button type="button" class="icon-btn" data-act="back" aria-label="Retour">${icon('back')}</button><h1>Importer une recette</h1><span></span></div>
      <div class="p-body prose">
        <h2>Depuis Instagram</h2>
        <ol class="howto"><li>Sur le post ou le reel, appuie sur l'<b>avion en papier</b>.</li><li>Fais défiler et appuie sur <b>Partager vers…</b> (ou <b>Plus</b>).</li><li>Choisis <b>Au gramme près</b>.</li></ol>
        <h2>Depuis TikTok</h2>
        <ol class="howto"><li>Appuie sur <b>Partager</b> (la flèche).</li><li>Appuie sur <b>Plus</b> (ou <b>Autres</b>).</li><li>Choisis <b>Au gramme près</b>.</li></ol>
        <h2>Depuis YouTube, un site ou Facebook</h2>
        <ol class="howto"><li>Appuie sur <b>Partager</b>.</li><li>Choisis <b>Au gramme près</b>.</li></ol>
        <h2>Avec des captures</h2>
        <ol class="howto"><li>Fais une ou plusieurs captures (ingrédients, légende, étapes).</li><li>Dans la galerie, sélectionne-les, <b>Partager</b> › <b>Au gramme près</b>.</li></ol>
        <div class="tip">${icon('bolt')}<p><b>Astuce :</b> dans le menu Partager, fais un appui long sur <b>Au gramme près</b> puis <b>Épingler</b> : l'appli restera en haut de la liste.</p></div>
        <div class="tip">${icon('help')}<p>Si la recette est seulement <b>dite à l'oral</b> dans une vidéo Insta ou TikTok, l'appli ne peut pas l'entendre : fais des captures des ingrédients. Les vidéos <b>YouTube</b>, elles, sont regardées en entier par Gemini.</p></div>
      </div>`;
    },
  });
  page.el.addEventListener('click', e => { if (e.target.closest('[data-act="back"]')) page.close(); });
  return page;
}

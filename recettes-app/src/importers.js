// Import de recettes : liens (Instagram, TikTok, YouTube, Facebook, sites), texte, photos.
import { http, MOBILE_UA } from './native.js';
import { S } from './store.js';
import { generateJSON, textPart, imagePart, youtubePart, AIError } from './ai.js';
import { lookupPer, applyPer } from './macros.js';
import { arr, str, num, pos1, gOrNull, hostOf, firstUrl, b64ToBlob, blobToB64, resizeImage, f1 } from './util.js';

export const PLATFORM_LABEL = { instagram: 'Instagram', tiktok: 'TikTok', youtube: 'YouTube', facebook: 'Facebook', pinterest: 'Pinterest', web: 'Site web' };

export function detectPlatform(url) {
  const h = hostOf(url);
  if (/(^|\.)instagram\.com$/.test(h) || h === 'instagr.am') return 'instagram';
  if (/(^|\.)tiktok\.com$/.test(h)) return 'tiktok';
  if (/(^|\.)youtube\.com$/.test(h) || h === 'youtu.be') return 'youtube';
  if (/(^|\.)facebook\.com$/.test(h) || h === 'fb.watch' || h === 'fb.com') return 'facebook';
  if (/(^|\.)pinterest\./.test(h) || h === 'pin.it') return 'pinterest';
  return 'web';
}

/* ============================================================ outils HTML */
const HDRS = { 'User-Agent': MOBILE_UA, 'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.7', Accept: 'text/html,application/xhtml+xml,*/*;q=0.8' };
const unescapeJSON = s => { try { return JSON.parse('"' + s + '"'); } catch (e) { return s; } };
const decodeEntities = s => { const t = document.createElement('textarea'); t.innerHTML = s; return t.value; };
function parseHTML(html) { return new DOMParser().parseFromString(String(html || ''), 'text/html'); }
function meta(doc, ...names) {
  for (const n of names) {
    const el = doc.querySelector(`meta[property="${n}"], meta[name="${n}"]`);
    const v = el && el.getAttribute('content');
    if (v && v.trim()) return v.trim();
  }
  return '';
}
function textOf(el) {
  if (!el) return '';
  const c = el.cloneNode(true);
  c.querySelectorAll('br').forEach(b => b.replaceWith('\n'));
  c.querySelectorAll('p, div, li, h1, h2, h3, h4, tr').forEach(b => b.append('\n'));
  return (c.textContent || '').replace(/[ \t ]+/g, ' ').replace(/\n\s*\n\s*\n+/g, '\n\n').trim();
}
async function get(url, extra = {}) {
  const r = await http({ url, headers: { ...HDRS, ...extra }, timeout: 25000 });
  return { status: r.status, html: typeof r.data === 'string' ? r.data : JSON.stringify(r.data || ''), url: r.url || url };
}

/* ---------- JSON-LD « Recipe » (Marmiton, 750g, blogs…) */
function findRecipeLD(doc) {
  const out = [];
  const visit = o => {
    if (!o || typeof o !== 'object') return;
    if (Array.isArray(o)) { o.forEach(visit); return; }
    const t = o['@type'];
    if (t === 'Recipe' || (Array.isArray(t) && t.includes('Recipe'))) out.push(o);
    if (o['@graph']) visit(o['@graph']);
    if (o.mainEntity) visit(o.mainEntity);
  };
  doc.querySelectorAll('script[type="application/ld+json"]').forEach(s => { try { visit(JSON.parse(s.textContent)); } catch (e) { /* bloc invalide */ } });
  return out[0] || null;
}
function ldText(r) {
  const lines = [];
  if (r.name) lines.push(`Titre : ${decodeEntities(str(r.name))}`);
  if (r.recipeYield) lines.push(`Portions : ${arr(r.recipeYield).length ? arr(r.recipeYield).join(' / ') : r.recipeYield}`);
  if (r.description) lines.push(`Description : ${decodeEntities(str(r.description))}`);
  const ing = arr(r.recipeIngredient || r.ingredients);
  if (ing.length) lines.push('Ingrédients :', ...ing.map(x => '- ' + decodeEntities(str(x))));
  const steps = [];
  const walk = s => {
    if (!s) return;
    if (typeof s === 'string') { steps.push(decodeEntities(s)); return; }
    if (Array.isArray(s)) { s.forEach(walk); return; }
    if (s['@type'] === 'HowToSection') { if (s.name) steps.push(`[${decodeEntities(str(s.name))}]`); walk(s.itemListElement); return; }
    if (s.text) steps.push(decodeEntities(str(s.text)));
    else if (s.name) steps.push(decodeEntities(str(s.name)));
  };
  walk(r.recipeInstructions);
  if (steps.length) lines.push('Préparation :', ...steps.map((x, i) => `${i + 1}. ${x.replace(/<[^>]+>/g, ' ')}`));
  return lines.join('\n');
}
function ldImage(r) {
  const im = r.image;
  if (!im) return '';
  if (typeof im === 'string') return im;
  if (Array.isArray(im)) return typeof im[0] === 'string' ? im[0] : (im[0] && im[0].url) || '';
  return im.url || '';
}
function pageText(doc) {
  const d = doc.cloneNode(true);
  d.querySelectorAll('script, style, noscript, svg, nav, header, footer, aside, form, iframe, button').forEach(e => e.remove());
  const main = d.querySelector('[itemtype*="Recipe"], .wprm-recipe-container, .tasty-recipes, article, main') || d.body;
  return textOf(main).slice(0, 16000);
}

/* ============================================================ plateformes */
async function fromTikTok(url, onStep) {
  let final = url;
  if (!/\/video\/\d+/.test(url)) {
    onStep('Ouverture du lien TikTok…');
    try { const r = await get(url); final = r.url || url; } catch (e) { /* on garde le lien court */ }
  }
  onStep('Lecture de la description TikTok…');
  const o = await http({ url: `https://www.tiktok.com/oembed?url=${encodeURIComponent(final)}`, headers: { 'User-Agent': MOBILE_UA }, responseType: 'json', timeout: 20000 }).catch(() => null);
  const d = o && o.status === 200 && o.data && typeof o.data === 'object' ? o.data : null;
  if (d && (d.title || d.author_name)) {
    return {
      url: final, text: str(d.title), title: '',
      author: d.author_unique_id ? '@' + d.author_unique_id : str(d.author_name),
      image: str(d.thumbnail_url),
    };
  }
  const p = await get(final);
  const m = p.html.match(/"desc":"((?:[^"\\]|\\.)*)"/);
  const doc = parseHTML(p.html);
  return { url: final, text: m ? unescapeJSON(m[1]) : meta(doc, 'og:description', 'description'), title: meta(doc, 'og:title'), author: '', image: meta(doc, 'og:image') };
}

async function fromInstagram(url, onStep) {
  let m = url.match(/instagram\.com\/(?:[^/?#]+\/)?(p|reels?|tv)\/([A-Za-z0-9_-]+)/);
  if (!m) {
    onStep('Ouverture du lien Instagram…');
    const r = await get(url).catch(() => null);
    if (r) m = r.url.match(/instagram\.com\/(?:[^/?#]+\/)?(p|reels?|tv)\/([A-Za-z0-9_-]+)/);
  }
  if (!m) throw new AIError('link', 'Lien Instagram non reconnu.');
  const code = m[2];
  const canon = `https://www.instagram.com/p/${code}/`;
  onStep('Lecture de la légende Instagram…');
  let text = '', author = '', image = '';
  try {
    const e = await get(`https://www.instagram.com/p/${code}/embed/captioned/`);
    const doc = parseHTML(e.html);
    const cap = doc.querySelector('.Caption');
    if (cap) {
      const c = cap.cloneNode(true);
      const u = c.querySelector('.CaptionUsername');
      if (u) { author = '@' + str(u.textContent); u.remove(); }
      c.querySelectorAll('.CaptionComments').forEach(x => x.remove());
      text = textOf(c);
    }
    if (!text) {
      const j = e.html.match(/\\"edge_media_to_caption\\":\{\\"edges\\":\[\{\\"node\\":\{\\"text\\":\\"((?:[^"\\]|\\\\.)*?)\\"/)
        || e.html.match(/"edge_media_to_caption":\{"edges":\[\{"node":\{"text":"((?:[^"\\]|\\.)*)"/);
      if (j) text = unescapeJSON(j[1].replace(/\\\\/g, '\\'));
    }
    if (!author) { const a = doc.querySelector('.UsernameText, .Username'); if (a) author = '@' + str(a.textContent); }
    const img = doc.querySelector('.EmbeddedMediaImage, img.EmbeddedMediaImage');
    image = (img && img.getAttribute('src')) || '';
    if (!image) { const d = e.html.match(/"display_url":"((?:[^"\\]|\\.)*)"/); if (d) image = unescapeJSON(d[1]); }
  } catch (err) { /* on tente la page du post */ }
  if (!text || !image) {
    try {
      const p = await get(canon);
      const doc = parseHTML(p.html);
      const desc = meta(doc, 'og:description', 'description');
      if (!text && desc) {
        const q = desc.match(/:\s*["“](.*)["”]\s*\.?$/s);
        text = q ? q[1] : desc;
        if (!author) { const a = desc.match(/-\s*([A-Za-z0-9._]+)\s+(?:on|le)\s/); if (a) author = '@' + a[1]; }
      }
      if (!image) image = meta(doc, 'og:image');
    } catch (err) { /* rien de plus */ }
  }
  return { url: canon, text, title: '', author, image };
}

function youtubeId(url) {
  const m = url.match(/(?:youtu\.be\/|youtube\.com\/(?:watch\?(?:.*&)?v=|shorts\/|embed\/|live\/))([A-Za-z0-9_-]{11})/);
  return m ? m[1] : '';
}
async function fromYouTube(url, onStep) {
  const id = youtubeId(url);
  if (!id) throw new AIError('link', 'Lien YouTube non reconnu.');
  const watch = `https://www.youtube.com/watch?v=${id}`;
  onStep('Lecture de la vidéo YouTube…');
  let title = '', author = '', image = `https://i.ytimg.com/vi/${id}/hqdefault.jpg`, text = '';
  const o = await http({ url: `https://www.youtube.com/oembed?url=${encodeURIComponent(watch)}&format=json`, responseType: 'json', timeout: 15000 }).catch(() => null);
  if (o && o.status === 200 && o.data && typeof o.data === 'object') { title = str(o.data.title); author = str(o.data.author_name); if (o.data.thumbnail_url) image = o.data.thumbnail_url; }
  try {
    const p = await get(watch, { Cookie: 'SOCS=CAI; CONSENT=YES+1' });
    const m = p.html.match(/"shortDescription":"((?:[^"\\]|\\.)*)"/);
    if (m) text = unescapeJSON(m[1]);
  } catch (e) { /* description facultative */ }
  return { url: watch, text, title, author, image, video: true };
}

async function fromPage(url, onStep, platform) {
  onStep(platform === 'facebook' ? 'Lecture de la publication Facebook…' : 'Lecture de la page…');
  const p = await get(url);
  if (p.status >= 400) throw new AIError('link', `Le site a refusé l'accès (code ${p.status}).`);
  const doc = parseHTML(p.html);
  const ld = findRecipeLD(doc);
  const title = meta(doc, 'og:title') || str(doc.title);
  const image = (ld && ldImage(ld)) || meta(doc, 'og:image', 'twitter:image');
  let text;
  if (ld) text = ldText(ld);
  else if (platform === 'facebook' || platform === 'pinterest') text = [meta(doc, 'og:title'), meta(doc, 'og:description', 'description')].filter(Boolean).join('\n');
  else text = `${title}\n\n${pageText(doc)}`;
  const site = meta(doc, 'og:site_name') || hostOf(p.url);
  return { url: p.url || url, text, title, author: site, image, structured: !!ld };
}

/** Récupère le contenu d'un lien (sans IA). */
export async function fetchLink(url, onStep = () => {}) {
  const platform = detectPlatform(url);
  let res;
  if (platform === 'tiktok') res = await fromTikTok(url, onStep);
  else if (platform === 'instagram') res = await fromInstagram(url, onStep);
  else if (platform === 'youtube') res = await fromYouTube(url, onStep);
  else res = await fromPage(url, onStep, platform);
  return { platform, ...res, text: str(res.text).slice(0, 20000) };
}

/** Télécharge une image distante et la réduit (pour la photo de la recette). */
export async function downloadImage(url) {
  if (!url) return null;
  try {
    const r = await http({ url, headers: { 'User-Agent': MOBILE_UA }, responseType: 'blob', timeout: 20000 });
    if (r.status !== 200 || !r.data) return null;
    const type = /png/i.test(String(r.headers['Content-Type'] || r.headers['content-type'] || '')) ? 'image/png' : 'image/jpeg';
    const blob = typeof r.data === 'string' ? b64ToBlob(r.data, type) : r.data;
    if (!blob || blob.size < 500) return null;
    return await resizeImage(blob, 1200, 0.84);
  } catch (e) {
    return null;
  }
}

/* ============================================================ prompt et normalisation */
export function libText() {
  return S.foods.map(f => `${f.name} : ${f1(f.kcal)}/${f1(f.p)}/${f1(f.c)}/${f1(f.f)}`).join('\n');
}
const IMPORT_RULES = `Réponds avec UNIQUEMENT un objet JSON de cette forme :
{"is_recipe": true, "title": "Nom court", "servings": 1, "ingredients": [{"section": "Crème"}, {"name": "Flocons d'avoine", "qty": "30 g", "g": 30, "kcal": 112.5, "p": 3.8, "c": 20.3, "f": 2.3}], "steps": ["Préchauffe le four à 180 °C."], "notes": "", "author": ""}

Règles :
- is_recipe = false s'il n'y a aucune recette (pas d'ingrédients) ; le reste peut alors rester vide.
- Tout en français (traduis si besoin). Titre court, sans emoji ni hashtag.
- servings = nombre de portions de la recette telle qu'écrite (1 si rien n'est indiqué).
- Garde l'ordre des ingrédients. Si la recette a des parties (base, crème, topping…), mets un objet {"section": "Nom"} avant chaque partie.
- qty = la quantité telle qu'écrite (« 1 banane », « 2 c. à soupe »). g = son poids en grammes : 1 c.à.s ≈ 15 g, 1 c.à.c ≈ 5 g, 1 œuf ≈ 55 g, 1 banane ≈ 118 g, 1 dose de whey ≈ 30 g ; pour « 150 à 200 g », prends le milieu ; convertis cups, oz et ml. g = null seulement pour le sel, le poivre, les épices, l'eau, le café, la levure et l'édulcorant (macros à 0).
- kcal, p (protéines), c (glucides), f (lipides) = valeurs pour ces g, arrondies à 0,1. Si l'aliment est dans MA BASE ci-dessous, reprends son nom EXACT et ses valeurs pour 100 g ; sinon, des valeurs d'étiquette françaises courantes.
- steps : une étape par élément, sans numéro, reformulées avec tes mots en phrases courtes à la 2e personne du singulier.
- notes : astuces, conservation, variantes de la source (reformulées). Si des quantités manquaient et que tu les as estimées, dis-le. author : le compte (@…) ou le site s'il est visible, sinon "".
- N'invente aucun ingrédient absent de la source.`;

function promptFor(kind, { text = '', note = '', url = '', platform = '' } = {}) {
  const src = PLATFORM_LABEL[platform] || 'la source';
  const body = {
    text: `Voici le texte de la recette :\n"""\n${text}\n"""`,
    images: `La recette est dans les images jointes (captures d'écran d'un post, d'une vidéo ou d'une page, ou photo d'un livre). Lis tout le texte visible, légende comprise.${text ? `\nTexte partagé avec les images :\n"""\n${text}\n"""` : ''}`,
    link: `Voici ce que j'ai récupéré du lien ${url} (${src}) : légende ou description du post, titre, texte de la page.\n"""\n${text}\n"""`,
    video: `La recette est dans la vidéo YouTube jointe : regarde-la et écoute-la en entier (ingrédients dits à l'oral ou affichés à l'écran).${text ? `\nDescription de la vidéo :\n"""\n${text}\n"""` : ''}`,
  }[kind];
  return `Tu extrais une recette pour mon carnet de recettes perso. Je suis bodybuilder et je compte mes macros au gramme près.

${body}
${note ? `\nPrécisions de ma part : ${note}\n` : ''}
${IMPORT_RULES}

MA BASE (pour 100 g : kcal/protéines/glucides/lipides) :
${libText()}`;
}

function sanitizeLines(list) {
  const out = [];
  for (const it of arr(list).slice(0, 80)) {
    if (!it || typeof it !== 'object') continue;
    if (typeof it.section === 'string' && !it.name) { const t = str(it.section).slice(0, 60); if (t) out.push({ section: t }); continue; }
    const name = str(it.name).slice(0, 120);
    if (!name) continue;
    const l = { name, qty: str(it.qty).slice(0, 80), g: gOrNull(it.g), kcal: pos1(it.kcal), p: pos1(it.p), c: pos1(it.c), f: pos1(it.f) };
    if (l.g) {
      const per = lookupPer(name, null);
      if (per) applyPer(l, per); else l.est = true;
    } else { l.kcal = 0; l.p = 0; l.c = 0; l.f = 0; }
    out.push(l);
  }
  return out;
}
const cleanStep = s => str(s).replace(/^\s*\d+\s*[.)\-:]\s*/, '').slice(0, 800);

export function normalizeRecipe(j, extra = {}) {
  const o = j && typeof j === 'object' && !Array.isArray(j) ? j : {};
  const ingredients = sanitizeLines(o.ingredients);
  return {
    id: '', title: str(o.title).replace(/#\S+/g, '').trim().slice(0, 140) || 'Nouvelle recette',
    servings: Math.min(50, Math.max(1, Math.round(num(o.servings)) || 1)),
    ingredients,
    steps: arr(o.steps).map(cleanStep).filter(Boolean).slice(0, 60),
    notes: str(o.notes).slice(0, 4000),
    source: { url: str(extra.url), label: str(extra.label || o.author || '') },
    photo: null, books: [],
    isRecipe: o.is_recipe !== false && ingredients.some(l => !l.section),
  };
}

/* ============================================================ points d'entrée */
/** Lien → recette. onStep(message) informe l'écran d'import. */
export async function importFromLink(url, { note = '', sharedText = '', onStep = () => {}, isCancelled = () => false } = {}) {
  const got = await fetchLink(url, onStep);
  if (isCancelled()) throw new AIError('cancelled', 'Annulé.');
  const photoP = downloadImage(got.image);
  const extraText = sharedText && !got.text.includes(sharedText.slice(0, 40)) ? `\n\n(Texte partagé : ${sharedText})` : '';
  const label = [got.author, PLATFORM_LABEL[got.platform]].filter(Boolean).join(' · ');
  let j = null;
  if (got.platform === 'youtube') {
    onStep('Gemini regarde la vidéo…');
    try {
      j = await generateJSON([youtubePart(got.url), textPart(promptFor('video', { text: got.text + extraText, note }))], { isCancelled, onStatus: onStep });
    } catch (e) {
      if (e.code === 'cancelled' || e.code === 'key' || e.code === 'nokey' || e.code === 'net') throw e;
      j = null;
    }
  }
  if (!j || j.is_recipe === false) {
    const text = [got.title, got.text].filter(Boolean).join('\n\n') + extraText;
    if (text.trim().length < 15) {
      throw new AIError('empty', got.platform === 'instagram'
        ? 'Instagram n’a pas donné la légende de ce post. Fais une capture de la recette et partage-la à l’appli.'
        : 'Impossible de lire le texte de ce lien. Fais une capture de la recette ou colle le texte.');
    }
    onStep('Gemini lit la recette…');
    j = await generateJSON([textPart(promptFor('link', { text, note, url: got.url, platform: got.platform }))], { isCancelled, onStatus: onStep });
  }
  const recipe = normalizeRecipe(j, { url: got.url, label });
  const photo = await photoP;
  return { recipe, photo, got };
}

/** Texte collé → recette. */
export async function importFromText(text, { note = '', url = '', onStep = () => {}, isCancelled = () => false } = {}) {
  onStep('Gemini lit la recette…');
  const j = await generateJSON([textPart(promptFor('text', { text: text.slice(0, 20000), note }))], { isCancelled, onStatus: onStep });
  return { recipe: normalizeRecipe(j, { url, label: url ? PLATFORM_LABEL[detectPlatform(url)] : '' }), photo: null };
}

/** Captures / photos (Blob) → recette. La première image peut servir de photo. */
export async function importFromImages(blobs, { note = '', text = '', onStep = () => {}, isCancelled = () => false } = {}) {
  onStep('Préparation des images…');
  const parts = [];
  for (const b of blobs.slice(0, 6)) {
    const small = await resizeImage(b, 1600, 0.85);
    parts.push(imagePart(await blobToB64(small), 'image/jpeg'));
  }
  const url = firstUrl(text);
  parts.push(textPart(promptFor('images', { text, note })));
  onStep('Gemini lit les images…');
  const j = await generateJSON(parts, { isCancelled, onStatus: onStep });
  const label = url ? PLATFORM_LABEL[detectPlatform(url)] : '';
  return { recipe: normalizeRecipe(j, { url, label }), photo: null };
}

/** Étiquette nutritionnelle → aliment (pour 100 g). */
export async function readLabel(blob, { isCancelled = () => false } = {}) {
  const small = await resizeImage(blob, 1600, 0.86);
  const prompt = `Lis cette étiquette nutritionnelle (ou la photo du produit).
Réponds avec UNIQUEMENT un objet JSON : {"name": "Nom du produit avec la marque", "kcal": 0, "p": 0, "c": 0, "f": 0, "unit": "", "unitG": null}
- kcal, p (protéines), c (glucides), f (lipides) POUR 100 g (ou 100 ml). Si l'étiquette ne donne que des valeurs par portion, convertis pour 100 g.
- unit et unitG : la portion indiquée sur l'étiquette (ex. "dose" et 33) s'il y en a une, sinon "" et null.
- Une valeur illisible : null.`;
  const j = await generateJSON([imagePart(await blobToB64(small)), textPart(prompt)], { isCancelled });
  return {
    name: str(j.name).slice(0, 90),
    kcal: j.kcal == null ? '' : pos1(j.kcal), p: j.p == null ? '' : pos1(j.p), c: j.c == null ? '' : pos1(j.c), f: j.f == null ? '' : pos1(j.f),
    unit: str(j.unit).slice(0, 20), unitG: gOrNull(j.unitG),
  };
}


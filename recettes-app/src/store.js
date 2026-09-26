// Données locales (IndexedDB) : tout reste sur le téléphone.
import { FOODS } from './foods.js';
import { arr, str, num, clone, rid, dataUrlToBlob, blobToB64 } from './util.js';

const DB_NAME = 'au-gramme-pres';
const DB_VERSION = 1;
let dbp = null;

function open() {
  if (!dbp) {
    dbp = new Promise((res, rej) => {
      const r = indexedDB.open(DB_NAME, DB_VERSION);
      r.onupgradeneeded = () => {
        const d = r.result;
        for (const s of ['recipes', 'books', 'foods']) if (!d.objectStoreNames.contains(s)) d.createObjectStore(s, { keyPath: 'id' });
        for (const s of ['kv', 'photos']) if (!d.objectStoreNames.contains(s)) d.createObjectStore(s);
      };
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
  }
  return dbp;
}
const req = r => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
const done = t => new Promise((res, rej) => { t.oncomplete = () => res(); t.onerror = () => rej(t.error); t.onabort = () => rej(t.error || new Error('abort')); });

async function getAll(store) { const d = await open(); return req(d.transaction(store).objectStore(store).getAll()); }
async function getKV(key) { const d = await open(); return req(d.transaction('kv').objectStore('kv').get(key)); }
async function put(store, value, key) {
  const d = await open();
  const t = d.transaction(store, 'readwrite');
  if (key === undefined) t.objectStore(store).put(value); else t.objectStore(store).put(value, key);
  return done(t);
}
async function putMany(store, values) {
  const d = await open();
  const t = d.transaction(store, 'readwrite');
  const s = t.objectStore(store);
  for (const v of values) s.put(v);
  return done(t);
}
async function del(store, key) {
  const d = await open();
  const t = d.transaction(store, 'readwrite');
  t.objectStore(store).delete(key);
  return done(t);
}

/* ============================================================ état en mémoire */
export const S = {
  ready: false,
  recipes: [],
  books: [],
  foods: [],
  plan: { name: '', meals: [] },
  settings: { geminiKey: '', models: [], onboarded: false },
  planning: {},            // { 'AAAA-MM-JJ': { mealId: { recipeId, s } } }
  groceries: { items: [], recipes: [] },
  prefs: { sort: 'recent', view: 'grid', libTab: 'all', meal: 'all' },
};

const listeners = new Set();
export const onChange = fn => { listeners.add(fn); return () => listeners.delete(fn); };
function changed(what) { for (const fn of listeners) { try { fn(what); } catch (e) { console.error(e); } } }

export async function loadAll() {
  const [recipes, books, foods, plan, settings, planning, groceries, prefs] = await Promise.all([
    getAll('recipes'), getAll('books'), getAll('foods'),
    getKV('plan'), getKV('settings'), getKV('planning'), getKV('groceries'), getKV('prefs'),
  ]);
  S.recipes = arr(recipes);
  S.books = arr(books);
  S.foods = arr(foods);
  if (!S.foods.length) {
    S.foods = clone(FOODS);
    await putMany('foods', S.foods);
  }
  if (plan && typeof plan === 'object') S.plan = { name: str(plan.name), meals: arr(plan.meals), updatedAt: plan.updatedAt };
  if (settings && typeof settings === 'object') Object.assign(S.settings, settings);
  if (planning && typeof planning === 'object') S.planning = planning;
  if (groceries && typeof groceries === 'object') S.groceries = { items: arr(groceries.items), recipes: arr(groceries.recipes) };
  if (prefs && typeof prefs === 'object') Object.assign(S.prefs, prefs);
  S.ready = true;
  changed('all');
}

/* ---------- recettes */
export const recipeById = id => S.recipes.find(r => r.id === id) || null;
export async function saveRecipe(r) {
  const now = Date.now();
  r.id = r.id || rid();
  r.createdAt = r.createdAt || now;
  r.updatedAt = now;
  const i = S.recipes.findIndex(x => x.id === r.id);
  if (i >= 0) S.recipes[i] = r; else S.recipes.push(r);
  await put('recipes', r);
  changed('recipes');
  return r;
}
export async function deleteRecipe(id) {
  const r = recipeById(id);
  S.recipes = S.recipes.filter(x => x.id !== id);
  await del('recipes', id);
  let planChanged = false;
  for (const day of Object.values(S.planning)) {
    for (const [mid, v] of Object.entries(day)) if (v && v.recipeId === id) { delete day[mid]; planChanged = true; }
  }
  if (planChanged) await put('kv', S.planning, 'planning');
  if (S.groceries.recipes.some(x => x.recipeId === id)) {
    S.groceries.recipes = S.groceries.recipes.filter(x => x.recipeId !== id);
    await put('kv', S.groceries, 'groceries');
  }
  if (r && r.photo && !S.recipes.some(x => x.photo === r.photo)) await deletePhoto(r.photo);
  changed('recipes');
}

/* ---------- livres de recettes */
export const bookById = id => S.books.find(b => b.id === id) || null;
export async function saveBook(b) {
  b.id = b.id || rid();
  b.createdAt = b.createdAt || Date.now();
  if (b.order == null) b.order = S.books.reduce((m, x) => Math.max(m, num(x.order)), 0) + 1;
  const i = S.books.findIndex(x => x.id === b.id);
  if (i >= 0) S.books[i] = b; else S.books.push(b);
  await put('books', b);
  changed('books');
  return b;
}
export async function deleteBook(id) {
  S.books = S.books.filter(b => b.id !== id);
  await del('books', id);
  const touched = S.recipes.filter(r => arr(r.books).includes(id));
  for (const r of touched) r.books = r.books.filter(x => x !== id);
  if (touched.length) await putMany('recipes', touched);
  changed('books');
}
export async function setRecipeBooks(recipeId, ids) {
  const r = recipeById(recipeId);
  if (!r) return;
  r.books = ids.slice();
  r.updatedAt = Date.now();
  await put('recipes', r);
  changed('recipes');
}

/* ---------- aliments */
export async function saveFood(f) {
  f.id = f.id || rid();
  const i = S.foods.findIndex(x => x.id === f.id);
  if (i >= 0) S.foods[i] = f; else S.foods.push(f);
  await put('foods', f);
  changed('foods');
  return f;
}
export async function deleteFood(id) {
  S.foods = S.foods.filter(f => f.id !== id);
  await del('foods', id);
  changed('foods');
}

/* ---------- plan, réglages, planning, courses */
export async function savePlan(plan) {
  S.plan = { name: str(plan.name), meals: arr(plan.meals), updatedAt: new Date().toISOString() };
  await put('kv', S.plan, 'plan');
  changed('plan');
}
export async function saveSettings(patch) {
  Object.assign(S.settings, patch);
  await put('kv', S.settings, 'settings');
  changed('settings');
}
export async function savePlanning() {
  for (const [k, v] of Object.entries(S.planning)) if (!v || !Object.keys(v).length) delete S.planning[k];
  await put('kv', S.planning, 'planning');
  changed('planning');
}
export async function saveGroceries() {
  await put('kv', S.groceries, 'groceries');
  changed('groceries');
}
let prefsTimer = null;
export function savePrefs() {
  clearTimeout(prefsTimer);
  prefsTimer = setTimeout(() => put('kv', S.prefs, 'prefs').catch(() => {}), 300);
}

/* ---------- photos (Blob) */
const photoURLs = new Map();
export async function savePhoto(blob) {
  const id = 'ph_' + rid();
  await put('photos', blob, id);
  return id;
}
export async function deletePhoto(id) {
  if (!id) return;
  const u = photoURLs.get(id);
  if (u && u !== 'pending') URL.revokeObjectURL(u);
  photoURLs.delete(id);
  await del('photos', id).catch(() => {});
}
async function getPhoto(id) { const d = await open(); return req(d.transaction('photos').objectStore('photos').get(id)); }
/** URL affichable d'une photo : synchrone si déjà chargée, sinon null et `cb` est appelé quand elle l'est. */
export function photoURL(id, cb) {
  if (!id) return null;
  const u = photoURLs.get(id);
  if (u && u !== 'pending' && u !== 'missing') return u;
  if (!u) {
    photoURLs.set(id, 'pending');
    getPhoto(id).then(b => {
      if (b instanceof Blob) { photoURLs.set(id, URL.createObjectURL(b)); if (cb) cb(); }
      else photoURLs.set(id, 'missing');
    }).catch(() => photoURLs.set(id, 'missing'));
  }
  return null;
}
export async function preloadPhotos(ids) {
  await Promise.all(ids.filter(Boolean).map(async id => {
    if (photoURLs.has(id) && photoURLs.get(id) !== 'pending') return;
    const b = await getPhoto(id).catch(() => null);
    photoURLs.set(id, b instanceof Blob ? URL.createObjectURL(b) : 'missing');
  }));
}

/* ============================================================ sauvegarde */
export async function exportBackup() {
  const photos = {};
  for (const r of S.recipes) {
    if (!r.photo || photos[r.photo]) continue;
    const b = await getPhoto(r.photo).catch(() => null);
    if (b instanceof Blob) photos[r.photo] = `data:${b.type || 'image/jpeg'};base64,${await blobToB64(b)}`;
  }
  return {
    app: 'au-gramme-pres', version: 2, exportedAt: new Date().toISOString(),
    plan: S.plan, foods: S.foods, books: S.books, recipes: S.recipes,
    planning: S.planning, groceries: S.groceries, photos,
  };
}

function sourceOf(v) {
  if (v && typeof v === 'object') return { url: str(v.url), label: str(v.label) };
  const s = str(v);
  if (/^https?:\/\//i.test(s)) { let h = ''; try { h = new URL(s).hostname.replace(/^www\./, ''); } catch (e) { /* lien invalide */ } return { url: s, label: h }; }
  return { url: '', label: s };
}

/** Lit une sauvegarde (de cette appli ou de la version web) et fusionne. Retourne un résumé. */
export async function importBackup(data, { replacePlan = true } = {}) {
  if (!data || typeof data !== 'object') throw new Error('Fichier illisible.');
  const books = arr(data.books || data.carnets).filter(b => b && b.id && b.name)
    .map(b => ({ id: String(b.id), name: str(b.name).slice(0, 60), order: num(b.order), createdAt: num(b.createdAt) || Date.now() }));
  const photoMap = {};
  for (const [id, url] of Object.entries(data.photos || {})) {
    const blob = dataUrlToBlob(url);
    if (blob) { await put('photos', blob, id); photoMap[id] = true; }
  }
  const recipes = arr(data.recipes).filter(r => r && r.id && (r.title || r.name)).map(r => ({
    id: String(r.id),
    title: str(r.title || r.name).slice(0, 140),
    servings: Math.max(1, Math.round(num(r.servings)) || 1),
    ingredients: arr(r.ingredients),
    steps: arr(r.steps).map(str).filter(Boolean),
    notes: str(r.notes),
    source: sourceOf(r.source),
    photo: r.photo && (photoMap[r.photo] || typeof r.photo === 'string') ? r.photo : null,
    books: arr(r.books || r.carnets).map(String),
    createdAt: num(r.createdAt) || Date.now(),
    updatedAt: num(r.updatedAt) || Date.now(),
  }));
  const foods = arr(data.foods).filter(f => f && f.id && f.name);
  if (books.length) { await putMany('books', books); for (const b of books) { const i = S.books.findIndex(x => x.id === b.id); if (i >= 0) S.books[i] = b; else S.books.push(b); } }
  if (recipes.length) { await putMany('recipes', recipes); for (const r of recipes) { const i = S.recipes.findIndex(x => x.id === r.id); if (i >= 0) S.recipes[i] = r; else S.recipes.push(r); } }
  if (foods.length) { await putMany('foods', foods); for (const f of foods) { const i = S.foods.findIndex(x => x.id === f.id); if (i >= 0) S.foods[i] = f; else S.foods.push(f); } }
  let plan = false;
  if (replacePlan && data.plan && arr(data.plan.meals).length) {
    S.plan = { name: str(data.plan.name), meals: arr(data.plan.meals), updatedAt: data.plan.updatedAt || new Date().toISOString() };
    await put('kv', S.plan, 'plan');
    plan = true;
  }
  if (data.planning && typeof data.planning === 'object') { Object.assign(S.planning, data.planning); await put('kv', S.planning, 'planning'); }
  if (data.groceries && arr(data.groceries.items).length && !S.groceries.items.length) { S.groceries = { items: arr(data.groceries.items), recipes: arr(data.groceries.recipes) }; await put('kv', S.groceries, 'groceries'); }
  changed('all');
  return { recipes: recipes.length, books: books.length, foods: foods.length, plan };
}

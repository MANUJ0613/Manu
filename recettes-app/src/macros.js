// Calculs de macros : totaux, comparaison aux meals du plan, recalage au gramme, remplacements.
import { S } from './store.js';
import { num, arr, norm, pos1, round1, f1, f2 } from './util.js';

export const TOL_OK = 2, TOL_NEAR = 5;
export const MACN = { p: 'protéines', c: 'glucides', f: 'lipides' };
export const STW = { ok: "à l'identique", near: 'presque', far: 'loin' };
export const STT = { ok: "À l'identique", near: 'Presque', far: 'Trop loin' };

export const isSec = it => !!it && typeof it.section === 'string' && !it.name;
export const lines = r => arr(r && r.ingredients).filter(it => it && !isSec(it));
export const servingsOf = r => Math.min(50, Math.max(1, Math.round(num(r && r.servings)) || 1));
export const stepFor = r => (servingsOf(r) > 1 ? 0.25 : 0.05);

export function totals(r) {
  const t = { kcal: 0, p: 0, c: 0, f: 0, g: 0 };
  for (const it of lines(r)) { t.kcal += num(it.kcal); t.p += num(it.p); t.c += num(it.c); t.f += num(it.f); t.g += num(it.g); }
  return t;
}
export function perPortion(r) {
  const t = totals(r), n = servingsOf(r);
  return { kcal: t.kcal / n, p: t.p / n, c: t.c / n, f: t.f / n };
}
export const hasMacros = pp => pp.p + pp.c + pp.f > 0;
export const planMeals = () => arr(S.plan && S.plan.meals);
export const compareMeals = () => planMeals().filter(m => m && m.compare !== false);
export const mealById = id => planMeals().find(m => m.id === id) || null;

export function bestS(pp, m, step) {
  const a = pp.p * num(m.p) + pp.c * num(m.c) + pp.f * num(m.f);
  const b = pp.p * pp.p + pp.c * pp.c + pp.f * pp.f;
  if (b <= 0) return null;
  let s = Math.min(24, Math.max(step, a / b));
  s = Math.round(s / step) * step;
  return Math.max(step, Math.round(s * 100) / 100);
}
export function macroStatus(e) { const a = Math.abs(e); return a <= TOL_OK ? 'ok' : a <= TOL_NEAR ? 'near' : 'far'; }
export function fitAt(pp, m, s) {
  const e = { kcal: s * pp.kcal - num(m.kcal), p: s * pp.p - num(m.p), c: s * pp.c - num(m.c), f: s * pp.f - num(m.f) };
  const worst = Math.max(Math.abs(e.p), Math.abs(e.c), Math.abs(e.f));
  const status = worst <= TOL_OK ? 'ok' : worst <= TOL_NEAR ? 'near' : 'far';
  const score = Math.sqrt((e.p * e.p + e.c * e.c + e.f * e.f) / 3);
  return { s, e, worst, status, score };
}
export function fit(r, m) {
  const pp = perPortion(r);
  if (!hasMacros(pp)) return null;
  const s = bestS(pp, m, stepFor(r));
  return s == null ? null : fitAt(pp, m, s);
}
export function bestFit(r) {
  let best = null;
  for (const m of compareMeals()) {
    const f = fit(r, m);
    if (f && (!best || f.score < best.fit.score)) best = { meal: m, fit: f };
  }
  return best;
}
export function verdictSub(fv) {
  if (fv.status === 'ok') return `Écart maximum : ${f1(fv.worst)} g sur les macros.`;
  const k = ['p', 'c', 'f'].reduce((a, b) => (Math.abs(fv.e[b]) > Math.abs(fv.e[a]) ? b : a));
  const v = fv.e[k];
  return v > 0 ? `${f1(v)} g de ${MACN[k]} en trop.` : `Il manque ${f1(-v)} g de ${MACN[k]}.`;
}
export function shares(pp) {
  const kp = pp.p * 4, kc = pp.c * 4, kf = pp.f * 9, t = kp + kc + kf || 1;
  return { p: kp / t, c: kc / t, f: kf / t };
}
export function domVar(pp) {
  if (!hasMacros(pp)) return 'var(--sunk)';
  const sh = shares(pp);
  return sh.p >= sh.c && sh.p >= sh.f ? 'var(--p)' : sh.c >= sh.f ? 'var(--g)' : 'var(--l)';
}

/* ============================================================ base d'aliments */
let FOODIX = new Map(), LINEIX = new Map();
export function perFromLine(l) {
  const g = num(l && l.g);
  if (!(g > 0)) return null;
  return { kcal: num(l.kcal) * 100 / g, p: num(l.p) * 100 / g, c: num(l.c) * 100 / g, f: num(l.f) * 100 / g };
}
export function rebuildIndex() {
  FOODIX = new Map();
  for (const f of S.foods) {
    for (const n of [f.name, ...arr(f.aliases)]) { const k = norm(n); if (k && !FOODIX.has(k)) FOODIX.set(k, f); }
  }
  LINEIX = new Map();
  for (const r of S.recipes) for (const l of lines(r)) {
    const k = norm(l.name);
    if (!k || FOODIX.has(k) || LINEIX.has(k)) continue;
    const per = perFromLine(l);
    if (per && per.kcal + per.p + per.c + per.f > 0) LINEIX.set(k, per);
  }
}
export const foodPer = f => ({ kcal: num(f.kcal), p: num(f.p), c: num(f.c), f: num(f.f) });
export const foodByName = name => FOODIX.get(norm(name)) || null;
export function lookupPer(name, ref) {
  const k = norm(name);
  if (!k) return null;
  if (ref) for (const l of lines(ref)) if (norm(l.name) === k) { const p = perFromLine(l); if (p) return p; }
  const f = FOODIX.get(k);
  if (f) return foodPer(f);
  return LINEIX.get(k) || null;
}
export function applyPer(line, per) {
  const g = num(line.g);
  line.kcal = pos1(per.kcal * g / 100); line.p = pos1(per.p * g / 100);
  line.c = pos1(per.c * g / 100); line.f = pos1(per.f * g / 100);
}
/** Recherche d'aliments par mots (pour l'autocomplétion et les remplacements). */
export function searchFoods(q, limit = 40) {
  const t = norm(q).split(' ').filter(Boolean);
  const list = S.foods.map(f => {
    const hay = norm([f.name, ...arr(f.aliases)].join(' '));
    let score = 0;
    for (const w of t) { if (hay.includes(w)) score += hay.startsWith(w) || hay.includes(' ' + w) ? 2 : 1; else return null; }
    return { f, score };
  }).filter(Boolean);
  list.sort((a, b) => b.score - a.score || a.f.name.localeCompare(b.f.name, 'fr'));
  return list.slice(0, limit).map(x => x.f);
}

/* ============================================================ quantités */
const FRAC = { '½': 0.5, '¼': 0.25, '¾': 0.75, '⅓': 1 / 3, '⅔': 2 / 3 };
export function parseLead(q) {
  const s = String(q || '');
  let m = s.match(/^(\s*(?:≈\s*)?)(\d+)\s*\/\s*(\d+)/);
  if (m && +m[3]) return { v: +m[2] / +m[3], pre: m[1], len: m[0].length };
  m = s.match(/^(\s*(?:≈\s*)?)(\d+(?:[.,]\d+)?)(?:\s+([½¼¾⅓⅔]))?/);
  if (m) return { v: parseFloat(m[2].replace(',', '.')) + (m[3] ? FRAC[m[3]] : 0), pre: m[1], len: m[0].length };
  m = s.match(/^(\s*(?:≈\s*)?)([½¼¾⅓⅔])/);
  if (m) return { v: FRAC[m[2]], pre: m[1], len: m[0].length };
  return null;
}
export function scaleQty(q, k) {
  if (!q || Math.abs(k - 1) < 1e-9) return q || '';
  const L = parseLead(q);
  if (!L) return q;
  const v = L.v * k;
  const r = v < 10 ? Math.round(v * 4) / 4 : Math.round(v);
  return L.pre + (r > 0 ? f2(r) : f2(v)) + q.slice(L.len);
}
const pluralUnit = (u, v) => (v >= 2 && /[a-zà-ÿœ]$/i.test(u) && !/[sx]$/i.test(u) && !/[ .]/.test(u) ? u + 's' : u);
/** « ≈ 2,5 tranches » à partir d'un poids, si l'aliment a une unité. */
export function unitsText(food, g) {
  if (!food || !food.unit || !(num(food.unitG) > 0) || !(g > 0)) return '';
  const v = Math.round(g / num(food.unitG) * 2) / 2;
  if (v <= 0) return '';
  return `≈ ${f2(v)} ${pluralUnit(food.unit, v)}`;
}
/** Réécrit le texte de quantité après un changement de grammes. */
export function qtyForGrams(line, g) {
  const q = String(line.qty || '');
  const mg = q.match(/^\s*(≈\s*)?(\d+(?:[.,]\d+)?)\s*g\b/i);
  if (mg || !q) return `${f0g(g)} g`;
  const food = foodByName(line.name);
  const u = unitsText(food, g);
  if (u) return u;
  const old = num(line.g);
  const L = parseLead(q);
  if (L && old > 0) return scaleQty(q.startsWith('≈') ? q : '≈ ' + q, g / old);
  return `${f0g(g)} g`;
}
const f0g = g => (g >= 10 ? String(Math.round(g)) : f1(g));

/* ============================================================ remplacer un ingrédient */
/** Grammes d'un aliment qui reproduisent au mieux les macros d'une ligne. */
export function substitute(line, food) {
  const t = [num(line.p), num(line.c), num(line.f)];
  const a = [num(food.p) / 100, num(food.c) / 100, num(food.f) / 100];
  const aa = a[0] * a[0] + a[1] * a[1] + a[2] * a[2];
  if (aa <= 0) return null;
  const g = Math.max(0, (a[0] * t[0] + a[1] * t[1] + a[2] * t[2]) / aa);
  if (!(g > 0)) return null;
  const gr = g >= 20 ? Math.round(g) : Math.round(g * 2) / 2;
  const e = { p: a[0] * gr - t[0], c: a[1] * gr - t[1], f: a[2] * gr - t[2] };
  const worst = Math.max(Math.abs(e.p), Math.abs(e.c), Math.abs(e.f));
  return { g: gr, e, worst, status: worst <= TOL_OK ? 'ok' : worst <= TOL_NEAR ? 'near' : 'far' };
}
export function lineFromFood(food, g) {
  const line = { name: food.name, qty: '', g: round1(g), kcal: 0, p: 0, c: 0, f: 0 };
  applyPer(line, foodPer(food));
  line.qty = unitsText(food, g) || `${f0g(g)} g`;
  return line;
}

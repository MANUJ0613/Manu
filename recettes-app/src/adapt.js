// Adapter une recette à un meal du plan.
// On garde la recette la plus proche possible de l'originale (mêmes proportions autant que possible),
// on peut ajouter une source de protéines, de glucides ou de lipides de ta base,
// et on choisit combien de portions font un meal (ex. 3 barres = Collation 1).
// Module sans DOM : testable tel quel sous Node.
import { num, arr, norm } from './util.js';

const isSecLine = it => !!it && typeof it.section === 'string' && !it.name;
const linesOf = r => arr(r && r.ingredients).filter(it => it && !isSecLine(it));
const servOf = r => Math.min(50, Math.max(1, Math.round(num(r && r.servings)) || 1));
const MK = ['p', 'c', 'f'];
const mkcal = o => 4 * num(o.p) + 4 * num(o.c) + 9 * num(o.f);

export const TOL_EXACT = 0.6; // écart accepté après arrondi, en g par meal (comme « Remplace tel quel »)
const RHO = 400; // poids des cibles de macros (écart en g par meal, au carré)

/* ============================================================ sucré / salé */
const SWEET = /sucr|sirop|miel|chocolat|cacao|confiture|caramel|vanill|cannelle|biscuit|speculoos|cookie|brownie|gateau|cake|muffin|pancake|crepe|gaufre|tiramisu|snickers|barre|dessert|porridge|overnight|granola|compote|banane|fraise|framboise|myrtille|mangue|ananas|datte|agave|erable|whey|isolate|pudding|flan|nutella|praline|energy ball|bounty|twix|kinder|confiserie|glace/;
const SAVORY = /poulet|boeuf|steak|jambon|dinde|thon|saumon|poisson|cabillaud|crevette|oignon|\bail\b|poivre|herbes?\b|moutarde|ketchup|mayo|emmental|cheddar|parmesan|mozzarella|\briz basmati|\briz jasmin|\briz complet|pates|spaghetti|nouilles|pommes? de terre|frites?|legume|tomate|salade|wrap|burger|pizza|quiche|gratin|curry|soja|paprika|cumin|bouillon|lardon|chorizo|saucisse|kebab|tacos|croque|omelette|oeufs? brouilles|\boeufs?\b|oeuf entier/;

function sweetScore(title, names) {
  const t = norm(title);
  let s = 0;
  if (SWEET.test(t)) s += 2;
  if (SAVORY.test(t)) s -= 2;
  for (const raw of names) {
    const n = norm(raw);
    if (SWEET.test(n)) s += 1;
    if (SAVORY.test(n)) s -= 1;
  }
  return s;
}
export const isSweet = r => sweetScore(r && r.title, linesOf(r).map(l => l.name)) > 0;
/** +1 sucré, −1 salé, 0 mixte ou inconnu, d'après le nom du meal et la note du coach. */
const TASTE = new WeakMap();
export function mealTaste(m) {
  if (m && typeof m === 'object') {
    const hit = TASTE.get(m);
    if (hit && hit.k === `${m.name}|${m.note}`) return hit.v;
    const v = mealTaste0(m);
    TASTE.set(m, { k: `${m.name}|${m.note}`, v });
    return v;
  }
  return mealTaste0(m);
}
function mealTaste0(m) {
  const note = String((m && m.note) || '').replace(/^plan du coach\s*:/i, '');
  const parts = note.split(/[,.;+]| et /).map(s => s.trim()).filter(Boolean);
  const s = sweetScore(m && m.name, parts);
  return s >= 2 ? 1 : s <= -2 ? -1 : 0; // un meal mixte (œufs + myrtilles…) reste neutre
}

/* ============================================================ rôle de chaque ingrédient */
const perOfLine = l => {
  const g = num(l && l.g);
  if (!(g > 0)) return null;
  return { kcal: num(l.kcal) * 100 / g, p: num(l.p) * 100 / g, c: num(l.c) * 100 / g, f: num(l.f) * 100 / g };
};
const sharesOf = per => {
  const k = Math.max(1e-9, per.p * 4 + per.c * 4 + per.f * 9);
  return { p: per.p * 4 / k, c: per.c * 4 / k, f: per.f * 9 / k };
};
export const isPowder = (name, per) => (per.p >= 55 && sharesOf(per).p >= 0.7) || (/whey|isolate|proteine en poudre|caseine|protein/.test(norm(name)) && per.p >= 40);

// lo / hi : bornes de la quantité d'un ingrédient, relatives à la recette d'origine mise à l'échelle.
// wMul : difficulté à changer cet ingrédient (petit = on le bouge volontiers).
const ROLES = {
  powder: { wMul: 1, lo: 0, hi: 40 }, // whey, isolate : coût selon les grammes ajoutés (voir solveOne)
  leanp: { wMul: 0.5, lo: 0.3, hi: 3 }, // viande maigre, blancs d'œufs, fromage blanc 0 %
  carb: { wMul: 0.6, lo: 0.25, hi: 3 }, // riz, pâtes, sucre, sirop, fruits
  fat: { wMul: 0.5, lo: 0.1, hi: 3 }, // huile, beurre
  other: { wMul: 1, lo: 0.3, hi: 2.2 }, // le reste (beurre de cacahuète, chocolat, farine…)
};
export function roleOf(name, per) {
  if (isPowder(name, per)) return 'powder';
  const sh = sharesOf(per);
  if (sh.p >= 0.6) return 'leanp';
  if (sh.f >= 0.9) return 'fat';
  if (sh.c >= 0.75) return 'carb';
  return 'other';
}

/* ============================================================ ingrédients qu'on peut ajouter */
const BOOST_IDS = {
  p: { sweet: ['whey-critical', 'isolate-dymatize', 'skyr', 'fromage-blanc-0', 'yaourt-grec-0', 'blancs-oeufs'], savory: ['poulet-cuit', 'blancs-oeufs', 'thon-naturel', 'dinde-hachee', 'boeuf-hache-extra-maigre', 'cabillaud'] },
  c: { sweet: ['flocons-avoine', 'miel', 'banane', 'creme-riz', 'galette-riz'], savory: ['riz-basmati-cuit', 'pdt-four', 'patate-douce', 'couscous-cuit', 'riz-jasmin-cuit', 'quinoa-cuit'] },
  f: { sweet: ['beurre-cacahuete', 'beurre-amande', 'amandes', 'noix-cajou'], savory: ['huile-olive'] },
};
const NOT_FOR_COOKING = /\beaa\b|bcaa|acides? amines?|creatine|karbolyn|maltodextrine|eau de coco|comptes? a 0|citrulline|electrolyte/;
const foodPer = f => ({ kcal: num(f.kcal), p: num(f.p), c: num(f.c), f: num(f.f) });
const stems = s => new Set(norm(s).split(' ').filter(w => w.length >= 4).map(w => w.replace(/s$/, '')));

/** Aliments de ta base qui peuvent compléter une macro (du plus naturel au moins naturel). */
const CHOICES = new WeakMap();
export function boosterChoices(type, sweet, foods, recipe) {
  const key = `${type}|${sweet}|${arr(foods).length}`;
  let memo = recipe && typeof recipe === 'object' ? CHOICES.get(recipe) : null;
  if (memo && memo.foods === foods && memo.map.has(key)) return memo.map.get(key);
  const out = boosterChoices0(type, sweet, foods, recipe);
  if (recipe && typeof recipe === 'object') {
    if (!memo || memo.foods !== foods) { memo = { foods, map: new Map() }; CHOICES.set(recipe, memo); }
    memo.map.set(key, out);
  }
  return out;
}
function boosterChoices0(type, sweet, foods, recipe) {
  const L = linesOf(recipe);
  const inRecipe = new Set(L.map(l => norm(l.name)));
  const words = new Set();
  L.forEach(l => stems(l.name).forEach(w => words.add(w)));
  stems(recipe && recipe.title).forEach(w => words.add(w));
  const ok = f => {
    const n = norm(f.name);
    if (!n || inRecipe.has(n) || NOT_FOR_COOKING.test(n)) return false;
    const p = foodPer(f);
    return p.p + p.c + p.f > 0;
  };
  const list = arr(foods).filter(ok);
  const ids = BOOST_IDS[type][sweet ? 'sweet' : 'savory'];
  const out = [];
  for (const id of ids) { const f = list.find(x => x.id === id); if (f) out.push(f); }
  // Base personnalisée : on complète avec les aliments les plus « purs » dans la macro.
  const purity = f => sharesOf(foodPer(f))[type];
  const extra = list.filter(f => !out.includes(f) && purity(f) >= (type === 'p' ? 0.7 : type === 'c' ? 0.8 : 0.85))
    .filter(f => type !== 'p' || !sweet || isPowder(f.name, foodPer(f)) || /yaourt|skyr|fromage blanc/.test(norm(f.name)))
    .sort((a, b) => purity(b) - purity(a));
  const all = [...out, ...extra];
  // Un aliment qui rappelle la recette passe devant (blancs d'œufs pour une omelette…).
  if (sweet) return all.slice(0, 5);
  const near = f => [...stems(f.name)].some(w => words.has(w));
  return [...all.filter(near), ...all.filter(f => !near(f))].slice(0, 5);
}

/* ============================================================ petit solveur (moindres carrés + bornes) */
function gauss(A, b) {
  const n = b.length;
  const M = A.map((row, i) => [...row, b[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    if (Math.abs(M[p][c]) < 1e-12) return null;
    if (p !== c) [M[c], M[p]] = [M[p], M[c]];
    for (let r = c + 1; r < n; r++) {
      const k = M[r][c] / M[c][c];
      if (k) for (let j = c; j <= n; j++) M[r][j] -= k * M[c][j];
    }
  }
  const x = new Array(n).fill(0);
  for (let i = n - 1; i >= 0; i--) {
    let s = M[i][n];
    for (let j = i + 1; j < n; j++) s -= M[i][j] * x[j];
    x[i] = s / M[i][i];
  }
  return x;
}

/**
 * min ½ zᵀHz + fᵀz  avec  lo ≤ z ≤ hi  (bornes simples ; lo = hi pour une variable bloquée).
 * Ensemble actif primal à partir d'un point z0 admissible. H symétrique définie positive.
 */
function boxqp(H, f, lo, hi, z0) {
  const N = f.length;
  const z = z0.map((v, i) => Math.min(hi[i], Math.max(lo[i], v)));
  // 0 : libre, -1 : sur la borne basse, 1 : sur la borne haute, 2 : bloquée
  const at = z.map((v, i) => (hi[i] - lo[i] < 1e-12 ? 2 : v <= lo[i] + 1e-12 ? -1 : v >= hi[i] - 1e-12 ? 1 : 0));
  for (let it = 0; it < 200; it++) {
    const F = [];
    for (let i = 0; i < N; i++) if (at[i] === 0) F.push(i);
    if (F.length) {
      const A = F.map(i => F.map(j => H[i][j]));
      const b = F.map(i => { let s = -f[i]; for (let j = 0; j < N; j++) if (at[j] !== 0) s -= H[i][j] * z[j]; return s; });
      const x = gauss(A, b);
      if (!x) return z;
      let alpha = 1, block = -1, side = 0;
      F.forEach((i, k) => {
        const d = x[k] - z[i];
        if (d < -1e-15 && z[i] + d < lo[i]) { const t = (lo[i] - z[i]) / d; if (t < alpha) { alpha = t; block = i; side = -1; } }
        if (d > 1e-15 && z[i] + d > hi[i]) { const t = (hi[i] - z[i]) / d; if (t < alpha) { alpha = t; block = i; side = 1; } }
      });
      F.forEach((i, k) => { z[i] += Math.max(0, alpha) * (x[k] - z[i]); });
      if (block >= 0) { at[block] = side; z[block] = side < 0 ? lo[block] : hi[block]; continue; }
    }
    // minimum avec les bornes actives : faut-il en relâcher une ?
    let rel = -1, worst = 1e-9;
    for (let i = 0; i < N; i++) {
      if (at[i] !== -1 && at[i] !== 1) continue;
      let g = f[i];
      for (let j = 0; j < N; j++) g += H[i][j] * z[j];
      const v = at[i] === -1 ? -g : g; // > 0 : la variable voudrait quitter sa borne
      if (v > worst) { worst = v; rel = i; }
    }
    if (rel < 0) return z;
    at[rel] = 0;
  }
  return z;
}

/* ============================================================ une adaptation (meal, taille du lot, ingrédients ajoutés) */
function solveOne(ctx, meal, q, boosters) {
  const { items } = ctx;
  const T = MK.map(k => num(meal[k]) * q);
  const beta = (q * mkcal(meal)) / Math.max(1, ctx.M0); // échelle de la recette pour ce lot
  const free = items.filter(x => x.free);
  const m = free.length, N = m + boosters.length;
  const H = Array.from({ length: N }, () => new Array(N).fill(0));
  const f = new Array(N).fill(0);
  free.forEach((x, i) => {
    // whey déjà présente : réglée comme un ajout (coût selon les grammes ajoutés, pas selon le facteur)
    const w = x.role === 'powder' ? 0.06 * (x.g0 / Math.max(5, (REF_MACRO.p * q) / Math.max(0.01, x.per.p / 100))) ** 2 : x.w;
    x.wq = w;
    H[i][i] += 2 * w; f[i] -= 2 * w * beta;
  });
  boosters.forEach((b, j) => { H[m + j][m + j] += 2 * b.c / (b.ref * b.ref); });
  const rq = RHO / (q * q);
  MK.forEach((k, kk) => {
    const a = new Array(N).fill(0);
    free.forEach((x, i) => { a[i] = x.M[kk]; });
    boosters.forEach((b, j) => { a[m + j] = b.per[k] / 100; });
    for (let i = 0; i < N; i++) {
      if (!a[i]) continue;
      f[i] -= 2 * rq * T[kk] * a[i];
      for (let j = 0; j < N; j++) if (a[j]) H[i][j] += 2 * rq * a[i] * a[j];
    }
  });
  // quantité imposée : ingrédient bloqué (proportion d'origine) ou arrondi à l'unité (3 œufs, 2 bananes)
  const fixed = x => (x.fixG != null ? x.fixG / x.g0 : x.locked ? beta : null);
  const lo = [...free.map(x => fixed(x) ?? x.lo * beta), ...boosters.map(() => 0)];
  const hi = [...free.map(x => fixed(x) ?? x.hi * beta), ...boosters.map(() => MAX_ADD_PER_MEAL * q)]; // pas plus de 350 g ajoutés par meal
  // départ admissible : la recette telle quelle à la bonne échelle, rien d'ajouté
  const z0 = [...free.map(x => fixed(x) ?? beta), ...boosters.map(() => 0)];
  const z = boxqp(H, f, lo, hi, z0);
  if (!z || z.some(v => !Number.isFinite(v))) return null;
  const grams = new Map();
  free.forEach((x, i) => grams.set(x, Math.max(0, z[i] * x.g0)));
  return { beta, grams, bgr: boosters.map((b, j) => Math.max(0, z[m + j])) };
}

const stepOf = g => (g >= 20 ? 1 : 0.5);
const roundG = g => (g >= 20 ? Math.round(g) : Math.round(g * 2) / 2);

function finish(ctx, meal, q, boosters, raw, polish = true) {
  const { items, n } = ctx;
  const beta = raw.beta;
  // Arrondi au gramme puis retouche pour coller au plus près des cibles.
  const rows = items.map(x => {
    if (x.free) return { x, g: roundG(raw.grams.get(x)), cont: raw.grams.get(x), per: x.per };
    // eau, sel, épices : on ne les retouche que si toute la recette change vraiment de taille
    if (x.g0 > 0) { const g = Math.abs(beta - 1) > 0.1 ? roundG(x.g0 * beta) : x.g0; return { x, g, cont: g, per: x.per, neutral: true }; }
    return { x, g: null, cont: null, per: null, neutral: true };
  });
  const brows = boosters.map((b, j) => ({ b, g: roundG(raw.bgr[j]), cont: raw.bgr[j], per: b.per }));
  const target = MK.map(k => num(meal[k]));
  const all = [...rows.filter(r => r.per && !r.neutral), ...brows];
  const err = () => MK.map((k, kk) => all.reduce((s, r) => s + (r.per[k] * (r.g || 0)) / 100, 0) / q - target[kk]);
  const worstOf = e => Math.max(...e.map(Math.abs));
  let e = err(), worst = worstOf(e);
  for (let it = 0; polish && it < 60 && worst > 0.05; it++) {
    let bestMove = null, bestScore = worst;
    for (const r of all) {
      if (r.x && (r.x.locked || r.x.fixG != null)) continue;
      if (!(r.g > 0) && !(r.b && r.cont >= 0.25)) continue;
      const st = stepOf(r.g || 0);
      for (const d of [st, -st]) {
        const g2 = (r.g || 0) + d;
        if (g2 < 0) continue;
        const k = d / 100 / q;
        const w2 = Math.max(Math.abs(e[0] + r.per.p * k), Math.abs(e[1] + r.per.c * k), Math.abs(e[2] + r.per.f * k));
        const drift = (Math.abs(g2 - r.cont) / Math.max(5, r.cont || 0)) * 0.02;
        if (w2 + drift < bestScore - 1e-4) { bestScore = w2 + drift; bestMove = { r, g2, k }; }
      }
    }
    if (!bestMove) break;
    const { r, g2, k } = bestMove;
    r.g = g2;
    e = [e[0] + r.per.p * k, e[1] + r.per.c * k, e[2] + r.per.f * k];
    worst = worstOf(e);
  }
  e = err(); worst = worstOf(e);
  const kcalMeal = all.reduce((s, r) => s + (r.per.kcal * (r.g || 0)) / 100, 0) / q;
  // « Combien on a touché à la recette » (sans les cibles).
  let dist = 0;
  for (const r of rows) {
    if (!r.x.free || !(r.x.g0 > 0)) continue;
    const u = (r.g || 0) / r.x.g0;
    dist += (r.x.wq ?? r.x.w) * (u - beta) * (u - beta);
  }
  const usedB = brows.filter(r => r.g > 0);
  for (const r of usedB) dist += r.b.c * (r.g / r.b.ref) ** 2 + 0.03;
  const changes = [];
  for (const r of rows) {
    if (!r.x.free) continue;
    const ref = r.x.g0 * beta;
    const d = (r.g || 0) - ref;
    if (Math.abs(d) >= Math.max(2, 0.1 * ref)) changes.push({ idx: r.x.idx, name: r.x.name, from: r.x.g0, to: r.g || 0 });
  }
  for (const r of usedB) changes.push({ name: r.b.food.name, from: 0, to: r.g, added: true });
  const k = n > 1 ? Math.max(1, Math.round(n / q)) : 1;
  const servings = n > 1 ? k * q : 1;
  const status = worst <= 2 ? 'ok' : worst <= 5 ? 'near' : 'far';
  const taste = mealTaste(meal);
  // retouches : tout écart au-delà de l'arrondi, même petit (les « changes » ne gardent que les notables)
  const tweaks = rows.filter(r => r.x.free && Math.abs((r.g || 0) - r.x.g0 * beta) >= Math.max(1, 0.03 * r.x.g0 * beta)).length + usedB.length;
  const level = status !== 'ok' || worst > TOL_EXACT ? 'impossible' : !changes.length && !tweaks ? 'none' : dist < 0.03 ? 'light' : dist < 0.1 ? 'medium' : 'strong';
  // Recette sucrée pour un meal salé (ou l'inverse) : un peu moins naturel, sauf si rien ne change.
  const clash = level !== 'none' && taste && taste === (ctx.sweet ? -1 : 1) ? 0.1 : 0;
  const cost = dist + clash + (n > 1 ? 0.08 * Math.log(beta) ** 2 + (0.02 * Math.abs(servings - n)) / n : 0) + (worst > TOL_EXACT ? 2 + worst : 0);
  return {
    meal, q, k, servings, beta,
    rows: rows.map(r => ({ idx: r.x.idx, name: r.x.name, from: r.x.g0 || null, to: r.g, role: r.x.role || null, locked: !!r.x.locked, neutral: !!r.neutral, per: r.per })),
    added: usedB.map(r => ({ type: r.b.type, food: r.b.food, g: r.g, per: r.per })),
    e: { p: e[0], c: e[1], f: e[2], kcal: kcalMeal - num(meal.kcal) }, worst, status,
    kcal: kcalMeal, dist, clash: !!clash, cost, changes, level,
  };
}

/* ============================================================ préparation */
// Aliments qui se comptent à l'unité : on préfère 3 œufs à « 2,6 œufs » (demi-unité pour les fruits).
const COUNTABLE = /^(oeuf|banane|pomme|oignon|steak|tranche|pita|pain|wrap|galette|portion|boite|cornichon|biscuit|pot)s?$/;
const HALVES = /^(banane|pomme|oignon)s?$/;
const FIDX = new WeakMap();
function foodIndex(foods) {
  const key = arr(foods);
  let m = FIDX.get(key);
  if (!m) {
    m = new Map();
    for (const f of key) for (const n of [f.name, ...arr(f.aliases)]) { const k = norm(n); if (k && !m.has(k)) m.set(k, f); }
    if (Array.isArray(foods)) FIDX.set(foods, m);
  }
  return m;
}

function context(r, locked, foods) {
  const fx = foodIndex(foods);
  const L = linesOf(r);
  const K0 = L.reduce((s, l) => s + num(l.kcal), 0);
  const M0 = L.reduce((s, l) => s + mkcal(l), 0);
  const n = servOf(r);
  const items = L.map((l, idx) => {
    const per = perOfLine(l);
    const g0 = num(l.g);
    const macros = per ? per.p + per.c + per.f : 0;
    const free = !!per && g0 > 0 && macros > 0.5;
    const it = { idx, name: l.name, g0, per, free, locked: locked.has(idx) };
    if (free) {
      it.role = roleOf(l.name, per);
      const R = ROLES[it.role];
      const share = M0 > 0 ? mkcal(l) / M0 : 0;
      it.w = Math.max(0.06, share) * R.wMul;
      it.lo = R.lo; it.hi = R.hi;
      it.M = MK.map(k => num(l[k]));
      const food = fx.get(norm(l.name));
      if (food && num(food.unitG) > 0 && COUNTABLE.test(norm(food.unit))) { it.unitG = num(food.unitG); it.half = HALVES.test(norm(food.unit)); }
    }
    return it;
  });
  return { r, L, K0, M0, n, items, sweet: isSweet(r) };
}

export function lotCandidates(r, meal) {
  const n = servOf(r);
  if (n <= 1) return [1];
  const M0 = linesOf(r).reduce((s, l) => s + mkcal(l), 0);
  const qa = M0 / Math.max(1, mkcal(meal));
  const lo = Math.max(1, Math.floor(qa)), hi = Math.max(1, Math.ceil(qa));
  return [...new Set([lo, hi])];
}

// Quantité « normale » d'une macro ajoutée par meal (une dose de whey, une portion de riz…).
const REF_MACRO = { p: 40, c: 80, f: 15 };
const MAX_ADD_PER_MEAL = 350;
function boosterSpec(type, food, q) {
  const per = foodPer(food);
  const dens = per[type] / 100; // g de macro par g d'aliment
  return { type, food, per, c: 0.06, ref: Math.max(5, (REF_MACRO[type] * q) / Math.max(0.01, dens)) };
}

/**
 * Meilleure adaptation d'une recette pour un meal.
 * opts.q : taille du lot imposée (nombre de meals) ; opts.locked : indices d'ingrédients à ne pas toucher ;
 * opts.pick : { p: foodId, c: foodId, f: foodId } aliment choisi pour compléter une macro ;
 * opts.noAdd : true (n'ajoute rien) ou liste de macros à ne pas compléter (['p']…).
 */
export function adaptFor(r, meal, foods, opts = {}) {
  const locked = new Set(arr(opts.locked));
  const ctx = context(r, locked, foods);
  if (!meal || !ctx.items.some(x => x.free) || !(ctx.M0 > 0) || !(mkcal(meal) > 0)) return null;
  const choices = {};
  // On n'ajoute pas ce que la recette a déjà : plus de whey plutôt qu'un 2e ingrédient protéiné, etc.
  const has = role => ctx.items.some(x => x.free && !x.locked && x.role === role);
  const skip = { p: has('powder'), c: has('carb'), f: has('fat') };
  for (const t of MK) {
    const off = opts.noAdd === true || arr(opts.noAdd).includes(t);
    const list = off || skip[t] ? [] : boosterChoices(t, ctx.sweet, foods, r);
    const want = opts.pick && opts.pick[t];
    const first = want && list.find(f => f.id === want);
    choices[t] = first ? [first, ...list.filter(f => f !== first)] : list;
  }
  const qs = opts.q ? [Math.max(1, Math.round(opts.q))] : lotCandidates(r, meal);
  let best = null;
  const keep = res => { if (res && (!best || res.cost < best.cost - 1e-9)) best = res; return res; };
  const run = (q, set) => {
    if (set.some(t => !choices[t].length)) return null;
    const boosters = set.map(t => boosterSpec(t, choices[t][0], q));
    const raw = solveOne(ctx, meal, q, boosters);
    if (!raw) return null;
    const res = finish(ctx, meal, q, boosters, raw);
    res.cost += 0.015 * set.length;
    res.specs = boosters;
    return keep(res);
  };
  const fits = res => res && res.level !== 'impossible';
  for (const q of qs) {
    // Telle quelle, juste à la bonne échelle : si ça colle déjà, rien à changer.
    const beta = (q * mkcal(meal)) / ctx.M0;
    const asIs = finish(ctx, meal, q, [], { beta, grams: new Map(ctx.items.filter(x => x.free).map(x => [x, x.g0 * beta])), bgr: [] }, false);
    if (asIs.level === 'none') { keep(asIs); continue; }
    const r0 = run(q, []);
    if (fits(r0) && r0.dist < 0.03) continue; // petits ajustements suffisent : pas besoin d'ajouter quoi que ce soit
    const singles = MK.map(t => run(q, [t]));
    if (fits(r0) || singles.some(fits)) continue;
    for (const set of [['p', 'c'], ['p', 'f'], ['c', 'f']]) run(q, set);
  }
  if (best && best.level !== 'impossible' && best.level !== 'none') best = snapCountables(ctx, meal, best);
  if (best) {
    best.sweet = ctx.sweet;
    best.choices = choices;
    best.lots = lotCandidates(r, meal);
    best.n = ctx.n;
  }
  return best;
}

/** Arrondit œufs, bananes, tranches… à l'unité (ou à la demie) puis recalcule le reste, si ça reste exact. */
function snapCountables(ctx, meal, best) {
  const specs = best.specs || [];
  const cands = ctx.items.filter(x => x.free && !x.locked && x.unitG > 0).sort((a, b) => b.g0 - a.g0).slice(0, 3);
  if (!cands.length) return best;
  // pour chaque ingrédient : l'unité (ou demie) juste en dessous et juste au-dessus
  const options = cands.map(x => {
    const row = best.rows.find(rw => rw.idx === x.idx);
    const g = (row && row.to) || 0;
    const step = x.half ? 0.5 : 1;
    const u = g / x.unitG / step;
    const outs = [...new Set([Math.floor(u), Math.ceil(u)])].map(v => Math.max(1, v) * step)
      .map(v => Math.round(v * x.unitG * 10) / 10)
      .filter(gs => g > 0 && Math.abs(gs - g) <= 0.4 * g);
    return outs.length ? outs : [null];
  });
  let pick = best;
  const combo = (i, acc) => {
    if (i === cands.length) {
      if (acc.every(v => v == null)) return;
      cands.forEach((x, k) => { x.fixG = acc[k]; });
      const raw = solveOne(ctx, meal, best.q, specs);
      const res = raw && finish(ctx, meal, best.q, specs, raw);
      if (res && res.level !== 'impossible' && res.dist <= best.dist + 0.06 && (pick === best || res.dist < pick.dist)) {
        res.cost += 0.015 * specs.length;
        res.specs = specs;
        res.fixes = acc.slice();
        pick = res;
      }
      return;
    }
    for (const v of options[i]) combo(i + 1, [...acc, v]);
  };
  combo(0, []);
  cands.forEach((x, k) => { x.fixG = pick.fixes ? pick.fixes[k] : null; });
  return pick;
}

/** Toutes les meals, de la plus naturelle à la moins naturelle. */
export function adaptAll(r, meals, foods, opts = {}) {
  const out = [];
  for (const m of arr(meals)) {
    const a = adaptFor(r, m, foods, opts);
    if (a) out.push(a);
  }
  out.sort((a, b) => a.cost - b.cost);
  return out;
}

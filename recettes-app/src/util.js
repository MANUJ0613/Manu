// Petits outils partagés (formatage, texte, dates, images).

export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

export const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const num = v => {
  if (typeof v === 'number') return Number.isFinite(v) ? v : 0;
  if (typeof v === 'string') { const n = parseFloat(v.replace(',', '.').replace(/\s/g, '')); return Number.isFinite(n) ? n : 0; }
  return 0;
};
export const arr = v => (Array.isArray(v) ? v : []);
export const str = v => (v === null || v === undefined ? '' : String(v).trim());
export const round1 = v => { const n = Math.round(num(v) * 10) / 10; return Object.is(n, -0) ? 0 : n; };
export const pos1 = v => Math.max(0, round1(v));
export const gOrNull = v => { const n = num(v); return n > 0 ? round1(n) : null; };
export const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
export const rid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
export const clone = o => JSON.parse(JSON.stringify(o ?? null));
export const sleep = ms => new Promise(r => setTimeout(r, ms));

const NF0 = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });
const NF1 = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 });
const NF2 = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 2 });
const z = n => (Object.is(n, -0) ? 0 : n);
export const f0 = n => NF0.format(z(Math.round(num(n))));
export const f1 = n => NF1.format(z(Math.round(num(n) * 10) / 10));
export const f2 = n => NF2.format(z(Math.round(num(n) * 100) / 100));
export const sgn = (n, d = 1) => {
  const r = z(Math.round(num(n) * 10 ** d) / 10 ** d);
  if (r === 0) return '0';
  return (r > 0 ? '+' : '−') + (d ? NF1 : NF0).format(Math.abs(r));
};
export const norm = s => String(s || '').toLowerCase().replace(/œ/g, 'oe').replace(/æ/g, 'ae')
  .normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
export const portionWord = s => (s >= 2 ? 'portions' : 'portion');
export const plural = (n, one, many) => `${f0(n)} ${n >= 2 ? many : one}`;

export function debounce(fn, ms) {
  let t = null;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

/* ---------- dates (jour local, format AAAA-MM-JJ) */
const pad = n => String(n).padStart(2, '0');
export const isoOf = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const dateOf = iso => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); };
export const todayISO = () => isoOf(new Date());
export const addDays = (iso, n) => { const d = dateOf(iso); d.setDate(d.getDate() + n); return isoOf(d); };
export const weekStart = iso => { const d = dateOf(iso); const k = (d.getDay() + 6) % 7; d.setDate(d.getDate() - k); return isoOf(d); };
const DAYS = ['dim.', 'lun.', 'mar.', 'mer.', 'jeu.', 'ven.', 'sam.'];
const DAYS_L = ['dimanche', 'lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi'];
const MONTHS = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];
export const dayShort = iso => DAYS[dateOf(iso).getDay()];
export const dayLong = iso => DAYS_L[dateOf(iso).getDay()];
export const dayNum = iso => dateOf(iso).getDate();
export const monthShort = iso => MONTHS[dateOf(iso).getMonth()];
export const dateLabel = iso => {
  const t = todayISO();
  if (iso === t) return "Aujourd'hui";
  if (iso === addDays(t, 1)) return 'Demain';
  if (iso === addDays(t, -1)) return 'Hier';
  const d = dateOf(iso);
  return `${DAYS_L[d.getDay()].replace(/^./, c => c.toUpperCase())} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
};

/* ---------- liens */
export const hostOf = url => { try { return new URL(url).hostname.replace(/^www\./, ''); } catch (e) { return ''; } };
export function firstUrl(text) {
  const m = String(text || '').match(/https?:\/\/[^\s<>"'«»]+/i);
  return m ? m[0].replace(/[),.;!?]+$/, '') : '';
}

/* ---------- images */
export function b64ToBlob(b64, type = 'image/jpeg') {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type });
}
export function dataUrlToBlob(url) {
  const m = String(url).match(/^data:([^;,]+)?(;base64)?,(.*)$/);
  if (!m) return null;
  return m[2] ? b64ToBlob(m[3], m[1] || 'application/octet-stream') : new Blob([decodeURIComponent(m[3])], { type: m[1] || 'text/plain' });
}
export function blobToB64(blob) {
  return new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(String(fr.result).split(',')[1] || '');
    fr.onerror = () => rej(fr.error);
    fr.readAsDataURL(blob);
  });
}
export async function resizeImage(blob, max = 1400, q = 0.84) {
  try {
    const bmp = await createImageBitmap(blob);
    const k = Math.min(1, max / Math.max(bmp.width, bmp.height));
    const w = Math.max(1, Math.round(bmp.width * k)), h = Math.max(1, Math.round(bmp.height * k));
    const cv = document.createElement('canvas');
    cv.width = w; cv.height = h;
    const ctx = cv.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(bmp, 0, 0, w, h);
    if (bmp.close) bmp.close();
    return await new Promise(res => cv.toBlob(b => res(b || blob), 'image/jpeg', q));
  } catch (e) {
    return blob;
  }
}

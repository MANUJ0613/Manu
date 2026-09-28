// Accès au téléphone (réseau sans CORS, bouton Partager, fichiers, écran allumé),
// avec des repli web pour les tests dans un navigateur.
import { Capacitor, CapacitorHttp, registerPlugin } from '@capacitor/core';
import { App } from '@capacitor/app';
import { Filesystem, Directory, Encoding } from '@capacitor/filesystem';
import { Share } from '@capacitor/share';
import { KeepAwake } from '@capacitor-community/keep-awake';
import { blobToB64 } from './util.js';

export const isNative = Capacitor.isNativePlatform();

export const MOBILE_UA = 'Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36';

/**
 * Requête HTTP. Sur le téléphone elle passe par le réseau natif (pas de blocage CORS).
 * responseType : 'text' | 'json' | 'blob' (blob → base64 dans data).
 */
export async function http({ url, method = 'GET', headers = {}, data, responseType = 'text', timeout = 30000, redirects = true }) {
  if (!isNative && window.__agpMockHttp) return window.__agpMockHttp({ url, method, headers, data, responseType, redirects });
  if (isNative) {
    const r = await CapacitorHttp.request({
      url, method, headers, data, responseType,
      connectTimeout: 15000, readTimeout: timeout, disableRedirects: !redirects,
    });
    return { status: r.status, data: r.data, url: r.url || url, headers: r.headers || {} };
  }
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeout);
  try {
    const body = data == null ? undefined : typeof data === 'string' ? data : JSON.stringify(data);
    const res = await fetch(url, { method, headers, body, signal: ctl.signal, redirect: redirects ? 'follow' : 'manual' });
    const ct = res.headers.get('content-type') || '';
    let out;
    if (responseType === 'blob') out = await blobToB64(await res.blob());
    else if (ct.includes('json') || responseType === 'json') { const txt = await res.text(); try { out = JSON.parse(txt); } catch (e) { out = txt; } }
    else out = await res.text();
    return { status: res.status, data: out, url: res.url || url, headers: Object.fromEntries(res.headers.entries()) };
  } finally {
    clearTimeout(t);
  }
}

/* ---------- partage entrant (Instagram, TikTok, galerie → l'appli) */
const ShareInNative = isNative ? registerPlugin('ShareIn') : null;
export function onShared(cb) {
  if (!ShareInNative) return;
  ShareInNative.addListener('shared', d => cb(d));
  ShareInNative.getPending().then(r => { if (r && r.data) cb(r.data); }).catch(() => {});
}
export function clearShared() { if (ShareInNative) ShareInNative.clear().catch(() => {}); }

/* ---------- navigateur caché (lit une page comme Chrome, avec les comptes connectés) */
const WebReaderNative = isNative ? registerPlugin('WebReader') : null;
export const hasWebReader = () => !!WebReaderNative || !!(typeof window !== 'undefined' && window.__agpMockWebRead);
export async function webRead(url, { wait = 3500, timeout = 25000 } = {}) {
  if (!isNative && window.__agpMockWebRead) return window.__agpMockWebRead(url);
  if (!WebReaderNative) return null;
  try { return await WebReaderNative.read({ url, wait, timeout }); } catch (e) { return null; }
}
export async function webLogin(url, title) {
  if (!isNative && window.__agpMockWebLogin) return window.__agpMockWebLogin(url);
  if (!WebReaderNative) return null;
  try { return await WebReaderNative.open({ url, title }); } catch (e) { return null; }
}
export async function webCookies(url) {
  if (!isNative && window.__agpMockCookies) return window.__agpMockCookies(url) || '';
  if (!WebReaderNative) return '';
  try { const r = await WebReaderNative.cookies({ url }); return (r && r.cookies) || ''; } catch (e) { return ''; }
}
export async function webLogout(urls) {
  if (!WebReaderNative) return;
  try { await WebReaderNative.logout({ urls }); } catch (e) { /* rien */ }
}

/* ---------- bouton retour Android */
export function onBack(handler) {
  if (!isNative) return;
  App.addListener('backButton', () => { if (!handler()) App.minimizeApp().catch(() => App.exitApp()); });
}
export function onResume(cb) { if (isNative) App.addListener('resume', cb); }

/* ---------- ouvrir un lien dans l'appli concernée (Instagram, TikTok…) ou le navigateur */
export function openExternal(url) {
  if (!url) return;
  if (isNative) { window.location.href = url; return; }
  window.open(url, '_blank', 'noopener');
}

/* ---------- partager un texte ou un fichier vers d'autres applis */
export async function shareText(title, text) {
  if (isNative) { await Share.share({ title, text, dialogTitle: title }); return 'shared'; }
  if (navigator.share) { await navigator.share({ title, text }); return 'shared'; }
  await navigator.clipboard.writeText(text);
  return 'copied';
}
export async function shareFile(name, text) {
  if (isNative) {
    const w = await Filesystem.writeFile({ path: name, data: text, directory: Directory.Cache, encoding: Encoding.UTF8 });
    await Share.share({ title: name, files: [w.uri], dialogTitle: 'Enregistrer la sauvegarde' });
    return;
  }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

/* ---------- écran allumé (mode cuisine) */
export async function keepAwake(on) {
  try {
    if (isNative) { if (on) await KeepAwake.keepAwake(); else await KeepAwake.allowSleep(); return true; }
    if (on && navigator.wakeLock) { keepAwake.lock = await navigator.wakeLock.request('screen'); return true; }
    if (!on && keepAwake.lock) { await keepAwake.lock.release(); keepAwake.lock = null; }
    return true;
  } catch (e) {
    return false;
  }
}

/* ---------- version de l'appli */
export async function appVersion() {
  if (!isNative) return 'web';
  try { const i = await App.getInfo(); return `${i.version} (${i.build})`; } catch (e) { return ''; }
}

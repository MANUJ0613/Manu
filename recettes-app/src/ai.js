// Client Gemini (Google AI Studio, offre gratuite) : lit les recettes et les étiquettes.
import { http } from './native.js';
import { S, saveSettings } from './store.js';
import { sleep } from './util.js';

const BASE = 'https://generativelanguage.googleapis.com/v1beta';
const DEFAULT_CHAIN = ['gemini-3.8-flash', 'gemini-flash-latest', 'gemini-3.5-flash', 'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite'];
export const KEY_URL = 'https://aistudio.google.com/apikey';

export class AIError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}
export const hasKey = () => !!(S.settings.geminiKey || '').trim();

/** Liste les modèles « flash » accessibles avec la clé, du plus récent au plus léger. */
export async function listModels(key) {
  let r;
  try {
    r = await http({ url: `${BASE}/models?pageSize=1000`, headers: { 'x-goog-api-key': key }, responseType: 'json', timeout: 20000 });
  } catch (e) {
    throw new AIError('net', 'Pas de connexion internet.');
  }
  const d = r.data && typeof r.data === 'object' ? r.data : {};
  if (r.status === 400 || r.status === 401 || r.status === 403) throw new AIError('key', 'Google refuse cette clé. Vérifie qu’elle est copiée en entier.');
  if (r.status !== 200) throw new AIError('net', `Google ne répond pas (code ${r.status}).`);
  const gen = (d.models || [])
    .filter(m => (m.supportedGenerationMethods || []).includes('generateContent'))
    .map(m => String(m.name || '').replace(/^models\//, ''));
  const flash = gen.map(n => {
    const m = n.match(/^gemini-(\d+(?:\.\d+)?)-flash(-lite)?$/);
    return m ? { n, v: parseFloat(m[1]), lite: m[2] ? 1 : 0 } : null;
  }).filter(Boolean);
  flash.sort((a, b) => a.lite - b.lite || b.v - a.v);
  const chain = flash.map(x => x.n);
  if (gen.includes('gemini-flash-latest')) chain.splice(Math.min(1, chain.length), 0, 'gemini-flash-latest');
  if (!chain.length) chain.push(...gen.filter(n => /flash/.test(n) && !/(tts|image|live|audio|embedding)/.test(n)).slice(0, 4));
  return [...new Set(chain)].slice(0, 6);
}

/** Enregistre et vérifie une clé. */
export async function setKey(key) {
  const k = String(key || '').trim();
  const models = await listModels(k);
  if (!models.length) throw new AIError('models', 'Aucun modèle Gemini disponible avec cette clé.');
  await saveSettings({ geminiKey: k, models, lastModel: '' });
  return models;
}

function extractText(d) {
  const c = d && d.candidates && d.candidates[0];
  if (!c) return { text: '', reason: (d && d.promptFeedback && d.promptFeedback.blockReason) || 'EMPTY' };
  const parts = (c.content && c.content.parts) || [];
  const text = parts.filter(p => p && typeof p.text === 'string' && !p.thought).map(p => p.text).join('');
  return { text, reason: c.finishReason || '' };
}
export function parseJSONLoose(t) {
  if (!t) return null;
  let s = String(t).trim().replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/, '');
  try { return JSON.parse(s); } catch (e) { /* on tente l'objet englobant */ }
  const a = s.indexOf('{'), b = s.lastIndexOf('}');
  if (a >= 0 && b > a) { try { return JSON.parse(s.slice(a, b + 1)); } catch (e) { /* illisible */ } }
  return null;
}

/**
 * Envoie des « parts » Gemini ({text} | {inlineData} | {fileData}) et renvoie l'objet JSON produit.
 * Essaie les modèles un par un si l'un est saturé ou indisponible.
 */
export async function generateJSON(parts, { isCancelled = () => false, onStatus = () => {} } = {}) {
  const key = (S.settings.geminiKey || '').trim();
  if (!key) throw new AIError('nokey', 'Ajoute ta clé Gemini (gratuite) dans Profil pour lire les recettes automatiquement.');
  let chain = S.settings.models && S.settings.models.length ? S.settings.models.slice() : DEFAULT_CHAIN.slice();
  const last = S.settings.lastModel;
  if (last && chain.includes(last)) chain = [last, ...chain.filter(m => m !== last)];
  const noThink = Object.assign({}, S.settings.noThinking || {});
  let lastErr = null;
  for (const model of chain) {
    for (let attempt = 0; attempt < 3; attempt++) {
      if (isCancelled()) throw new AIError('cancelled', 'Annulé.');
      const body = { contents: [{ role: 'user', parts }], generationConfig: { responseMimeType: 'application/json' } };
      if (!noThink[model]) body.generationConfig.thinkingConfig = { thinkingLevel: 'low' };
      let r;
      try {
        r = await http({
          url: `${BASE}/models/${model}:generateContent`, method: 'POST',
          headers: { 'Content-Type': 'application/json', 'x-goog-api-key': key },
          data: body, responseType: 'json', timeout: 150000,
        });
      } catch (e) {
        throw new AIError('net', 'Pas de connexion internet (ou Google ne répond pas).');
      }
      if (isCancelled()) throw new AIError('cancelled', 'Annulé.');
      const d = r.data && typeof r.data === 'object' ? r.data : parseJSONLoose(r.data) || {};
      const msg = String((d.error && d.error.message) || '');
      if (r.status === 200) {
        const { text, reason } = extractText(d);
        const j = parseJSONLoose(text);
        if (j && typeof j === 'object') {
          if (S.settings.lastModel !== model) saveSettings({ lastModel: model }).catch(() => {});
          return j;
        }
        if (/SAFETY|RECITATION|BLOCK|PROHIBITED/i.test(reason)) lastErr = new AIError('blocked', 'Gemini a refusé de lire ce contenu. Essaie avec une capture ou le texte collé.');
        else lastErr = new AIError('format', 'Réponse de Gemini illisible.');
        break;
      }
      if (r.status === 400 && /thinking/i.test(msg) && !noThink[model]) {
        noThink[model] = true;
        saveSettings({ noThinking: noThink }).catch(() => {});
        continue;
      }
      if ((r.status === 400 || r.status === 401 || r.status === 403) && /api key|api_key|unregistered callers/i.test(msg)) {
        throw new AIError('key', 'Google refuse ta clé Gemini. Remets-la dans Profil.');
      }
      if (r.status === 400 && /(fetch|youtube|file_uri|fileuri|video|url)/i.test(msg)) {
        throw new AIError('media', 'Gemini ne peut pas ouvrir ce média.');
      }
      if (r.status === 404 || r.status === 403 || (r.status === 400 && /model/i.test(msg))) {
        lastErr = new AIError('model', 'Modèle indisponible.');
        break;
      }
      if (r.status === 429) {
        lastErr = new AIError('quota', 'Limite gratuite de Gemini atteinte pour le moment. Réessaie dans une minute.');
        onStatus('Limite atteinte, essai d’un autre modèle…');
        break;
      }
      if (r.status >= 500) {
        if (attempt < 2) { onStatus('Google est chargé, nouvel essai…'); await sleep(1500 * (attempt + 1)); continue; }
        lastErr = new AIError('busy', 'Google est surchargé. Réessaie dans un moment.');
        break;
      }
      lastErr = new AIError('api', msg ? `Gemini : ${msg}` : `Erreur Gemini (${r.status}).`);
      break;
    }
  }
  throw lastErr || new AIError('api', 'Aucun modèle Gemini disponible.');
}

export const textPart = text => ({ text });
export const imagePart = (b64, mimeType = 'image/jpeg') => ({ inlineData: { mimeType, data: b64 } });
export const youtubePart = url => ({ fileData: { fileUri: url } });

/* ----------------------------------------------------------------------------
   Rédacteur de commentaires — 100 % local, aucune publication automatique.
   Le but : extraire les infos d'un post collé et proposer des brouillons
   VARIÉS et NATURELS en français. L'utilisateur copie/colle lui-même.
---------------------------------------------------------------------------- */

(function () {
  'use strict';

  // -------------------------------------------------------------------------
  // Petit générateur pseudo-aléatoire avec graine (pour "Régénérer" reproductible
  // dans un run, mais différent à chaque clic). Évite Math.random non maîtrisé.
  // -------------------------------------------------------------------------
  function makeRng(seed) {
    let s = seed >>> 0 || 1;
    return function () {
      // xorshift32
      s ^= s << 13; s >>>= 0;
      s ^= s >> 17;
      s ^= s << 5;  s >>>= 0;
      return s / 4294967296;
    };
  }
  let rng = makeRng(Date.now ? (Date.now() & 0xffffffff) : 12345);

  function pick(arr) { return arr[Math.floor(rng() * arr.length)]; }
  function chance(p) { return rng() < p; }
  function shuffle(arr) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(rng() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  // -------------------------------------------------------------------------
  // Extraction des infos depuis le texte collé.
  // -------------------------------------------------------------------------
  const KNOWN_STORES = [
    'Micromania', 'Amazon', 'Fnac', 'Darty', 'Cdiscount', 'Boulanger',
    'Leclerc', 'Carrefour', 'Auchan', 'Action', 'Lidl', 'Aliexpress',
    'King Jouet', 'JouéClub', 'PicWic', 'Maxi Toys', 'Decathlon',
    'Leroy Merlin', 'Castorama', 'Ikea', 'Zalando', 'Shein', 'Temu',
    'Rakuten', 'Gamecash', 'La Grande Récré', 'Intermarché', 'Lego',
  ];

  function parsePrices(text) {
    // Capture les montants type "39,99 €", "39.99€", "149 €", "€39.99"
    const re = /(?:€\s*)?(\d{1,4}(?:[.,]\d{1,2})?)\s*€|€\s*(\d{1,4}(?:[.,]\d{1,2})?)/g;
    const found = [];
    let m;
    while ((m = re.exec(text)) !== null) {
      const raw = (m[1] || m[2] || '').replace('.', ',');
      const val = parseFloat(raw.replace(',', '.'));
      if (!isNaN(val)) found.push({ label: raw + ' €', value: val, index: m.index });
    }
    return found;
  }

  function extract(text) {
    const out = { subject: '', price: '', oldPrice: '', store: '', link: '', info: '' };
    const clean = text.replace(/ /g, ' ').trim();
    if (!clean) return out;

    // Lien
    const link = clean.match(/https?:\/\/[^\s)>\]]+/i);
    if (link) out.link = link[0];

    // Boutique : nom connu, sinon domaine du lien
    for (const s of KNOWN_STORES) {
      const re = new RegExp('\\b' + s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b', 'i');
      if (re.test(clean)) { out.store = s; break; }
    }
    if (!out.store && out.link) {
      const dom = out.link.match(/https?:\/\/(?:www\.)?([^/.]+)/i);
      if (dom) out.store = dom[1].charAt(0).toUpperCase() + dom[1].slice(1);
    }

    // Prix : on cherche "au lieu de", sinon le plus petit = prix actuel,
    // le plus grand = prix barré.
    const prices = parsePrices(clean);
    if (prices.length === 1) {
      out.price = prices[0].label;
    } else if (prices.length >= 2) {
      const auLieu = clean.match(/(\d{1,4}(?:[.,]\d{1,2})?)\s*€[^0-9]{0,18}?(?:au\s*lieu\s*de|aulieu|→|->|contre)\s*(\d{1,4}(?:[.,]\d{1,2})?)\s*€/i);
      if (auLieu) {
        out.price = auLieu[1].replace('.', ',') + ' €';
        out.oldPrice = auLieu[2].replace('.', ',') + ' €';
      } else {
        const sorted = prices.slice().sort((a, b) => a.value - b.value);
        out.price = sorted[0].label;
        out.oldPrice = sorted[sorted.length - 1].label;
      }
    }

    // Pourcentage de réduction
    const pct = clean.match(/-?\s*(\d{1,3})\s*%/);
    if (pct) out.info = '-' + pct[1] + ' %';

    // Mentions utiles (stock, expiration…)
    const flags = [];
    if (/stock\s*limit|derni[eè]re?s?\s*pi[eè]ces?|rupture/i.test(clean)) flags.push('stock limité');
    if (/erreur\s*de\s*prix|prix\s*err/i.test(clean)) flags.push('erreur de prix');
    if (/(fin|expire|jusqu'?au|valable)/i.test(clean) && /\b(\d{1,2}\/\d{1,2}|aujourd|demain|minuit|ce soir)/i.test(clean)) flags.push('offre limitée dans le temps');
    if (flags.length) out.info = [out.info, ...flags].filter(Boolean).join(', ');

    // Sujet : on prend la 1re ligne qui ressemble à un VRAI titre produit,
    // en sautant les en-têtes pur emoji/majuscules ("🔥 ERREUR DE PRIX 🔥").
    const HEADER_RE = /^[\s\W]*(?:erreur\s*de\s*prix|bon\s*plan|deal|promo|vente\s*flash|alerte|exclusif|affaire\s*du\s*jour|lien\s*amazon|rémunéré)[\s\W0-9€:!]*$/i;
    const storeRe = out.store
      ? new RegExp('\\s*\\bchez\\s+' + out.store.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b', 'gi')
      : null;
    const cleanLine = (l) => {
      let c = l
        .replace(/https?:\/\/\S+/gi, '')
        .replace(/(?:€\s*)?\d{1,4}(?:[.,]\d{1,2})?\s*€/g, '')
        .replace(/-?\s*\d{1,3}\s*%/g, '')
        .replace(/\b(au lieu de|bon plan|deal|promo|vente flash|erreur de prix)\b/gi, '')
        .replace(/[🔥‼️⚡️🚨💥✅👉]/gu, '');
      if (storeRe) c = c.replace(storeRe, '');
      return c
        .replace(/\s{2,}/g, ' ')
        // prépositions/connecteurs devenus orphelins (en fin ou avant rien)
        .replace(/\s+(?:à|au|aux|de|des|du|pour|chez|seulement|que|à\s+seulement)\s*$/i, '')
        .replace(/^[\p{Extended_Pictographic}☀-➿️\s•\-–—*:]+/u, '')
        .replace(/[\p{Extended_Pictographic}☀-➿️\s•\-–—*:]+$/u, '')
        .trim();
    };

    const lines = clean.split(/\n/).map(l => l.trim()).filter(Boolean);
    const isJustStore = (c) => out.store && c.toLowerCase() === out.store.toLowerCase();
    // Candidats = lignes "produit" plausibles. On garde la plus descriptive
    // (souvent la plus longue : le titre produit), pas juste la première.
    const candidates = [];
    for (const l of lines) {
      const c = cleanLine(l);
      if (c && /[a-zàâäéèêëîïôöùûüç]{3,}/i.test(c) && !HEADER_RE.test(l) && c.length >= 3 && !isJustStore(c)) {
        candidates.push(c);
      }
    }
    let subject = '';
    if (candidates.length) {
      subject = candidates.reduce((best, c) => (c.length > best.length ? c : best), candidates[0]);
    } else if (lines.length) {
      subject = cleanLine(lines[0]);
    }
    // Titre catalogue "Marque - détail - détail - …" → on garde le nom court
    // (2 premiers segments), bien plus naturel dans un commentaire.
    if (subject.includes(' - ') || subject.includes(' – ')) {
      const segs = subject.split(/\s+[-–]\s+/).filter(Boolean);
      subject = segs.slice(0, 2).join(' - ');
    }
    if (subject.length > 60) subject = subject.slice(0, 57).trim() + '…';
    out.subject = subject;

    return out;
  }

  // -------------------------------------------------------------------------
  // Génération de commentaires naturels.
  // Stratégie : on assemble openers + corps + closers depuis des banques
  // par ton, on injecte les infos quand elles existent, on varie les emojis
  // (souvent zéro), et on garantit 3 sorties différentes.
  // -------------------------------------------------------------------------
  function capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

  function subjectPhrase(d) {
    if (d.subject) {
      return chance(0.5) ? d.subject.toLowerCase() : 'ce ' + (looksPlural(d.subject) ? 'lot' : 'truc');
    }
    return 'ce deal';
  }
  function looksPlural(s) { return /s\b/.test(s.split(' ').slice(-1)[0] || ''); }

  function priceBit(d) {
    if (d.price && d.oldPrice) return `${d.price} au lieu de ${d.oldPrice}`;
    if (d.price) return `à ${d.price}`;
    return '';
  }

  const EMOJI_RE = /\p{Extended_Pictographic}/u;
  // N'ajoute un emoji que si la phrase n'en contient pas déjà (évite les doublons).
  function withEmoji(txt, pool) {
    if (EMOJI_RE.test(txt)) return txt;
    return chance(0.45) ? txt + ' ' + pick(pool) : txt;
  }

  // --- Bank: UTILE (apporte une info, factuel, posé) ---
  function genUtile(d) {
    const subj = d.subject ? d.subject.toLowerCase() : 'le produit';
    const price = priceBit(d);
    const store = d.store ? `chez ${d.store}` : '';
    const openers = [
      `Bon plan confirmé`,
      `Repéré aussi de mon côté`,
      `Pour ceux qui hésitent`,
      `Petit rappel`,
      `Effectivement`,
    ];
    const bodies = [
      price ? `${capitalize(subj)} ${price}${store ? ' ' + store : ''}, c'est une vraie baisse.`
            : `${capitalize(subj)} à ce prix${store ? ' ' + store : ''}, ça vaut le coup.`,
      d.info ? `${capitalize(subj)} ${price || ''} — et en plus ${d.info.toLowerCase()}.`.replace('  ', ' ')
             : `${capitalize(subj)} ${price || 'à ce tarif'}, difficile de trouver mieux en ce moment.`,
      `J'ai comparé vite fait, ${price || 'ce prix'} c'est bien le meilleur ${store ? store + ' ' : ''}actuellement.`,
    ];
    const closers = [
      `Merci pour le partage.`,
      `À prendre si le besoin est là.`,
      `Pensez à vérifier le stock avant.`,
      `Foncez tant que c'est dispo.`,
      ``,
    ];
    let txt = `${pick(openers)} : ${pick(bodies)}`;
    const c = pick(closers);
    if (c) txt += ' ' + c;
    txt = withEmoji(txt, ['👍', '✅', '💪']);
    return tidy(txt);
  }

  // --- Bank: QUESTION (engage une discussion, donc actif sans spammer) ---
  function genQuestion(d) {
    const subj = subjectPhrase(d);
    const price = priceBit(d);
    const store = d.store ? d.store : 'la boutique';
    const qs = [
      `Quelqu'un l'a déjà pris${looksPlural(d.subject) ? 's' : ''} ? ${price ? `${capitalize(price)},` : ''} ça tient la route niveau qualité ?`,
      `${d.subject ? capitalize(d.subject) + ' :' : ''} la livraison est rapide chez ${store} en ce moment ?`,
      `Ça vaut vraiment le coup à ${d.price || 'ce prix'} ou il y a mieux ailleurs ?`,
      `Vous savez si l'offre tient jusqu'à quand ? J'hésite à craquer.`,
      `Y'a un code promo à ajouter ou le prix affiché est déjà le bon ?`,
      `${price ? capitalize(price) + ', ' : ''}c'est vraiment le prix plancher ou ça baisse encore parfois ?`,
    ];
    let txt = pick(qs);
    txt = withEmoji(txt, ['🤔', '🙏', '😅']);
    return tidy(txt);
  }

  // --- Bank: ENTHOUSIASTE (réaction vivante, variée, pas robotique) ---
  function genEnthousiaste(d) {
    const subj = subjectPhrase(d);
    const price = priceBit(d);
    const reacts = [
      `Ah là c'est cadeau`,
      `Belle prise`,
      `Excellent ça`,
      `Énorme`,
      `Imbattable`,
      `Ça pique (dans le bon sens)`,
    ];
    const middles = [
      price ? `${price}, je dis pas non.` : `je dis pas non.`,
      d.subject ? `${capitalize(d.subject.toLowerCase())} à ce tarif, fallait le voir passer.` : `fallait le voir passer.`,
      price ? `${capitalize(price)} c'est du vol… mais dans l'autre sens 😄` : `franchement rien à redire.`,
      `merci, je file regarder avant que ça parte.`,
      `direct dans le panier celui-là.`,
    ];
    const enders = [
      `Top ce groupe pour ça`,
      `Continuez comme ça`,
      `Vous gérez`,
      ``,
    ];
    let txt = `${pick(reacts)} ! ${capitalize(pick(middles))}`;
    const e = pick(enders);
    if (e && chance(0.5)) txt += ' ' + e + '.';
    txt = withEmoji(txt, ['🔥', '😍', '🤑', '🙌']);
    return tidy(txt);
  }

  function tidy(s) {
    return s
      .replace(/\s{2,}/g, ' ')
      .replace(/\s+([,.!?])/g, '$1')
      .replace(/([,.!?]){2,}/g, '$1')
      .replace(/\s+/g, ' ')
      .trim();
  }

  const GENERATORS = {
    utile: { label: 'Utile', fn: genUtile },
    question: { label: 'Question', fn: genQuestion },
    enthousiaste: { label: 'Enthousiaste', fn: genEnthousiaste },
  };

  // Produit 3 commentaires distincts selon le ton choisi.
  function generate(d, tone) {
    const out = [];
    const seen = new Set();
    let order;
    if (tone === 'mix') {
      order = shuffle(['utile', 'question', 'enthousiaste']);
    } else {
      order = [tone, tone, tone];
    }
    let guard = 0;
    while (out.length < 3 && guard < 40) {
      guard++;
      const t = order[out.length] || tone;
      const gen = GENERATORS[t] || GENERATORS.utile;
      const text = gen.fn(d);
      const key = text.toLowerCase().slice(0, 40);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ tone: gen.label, text });
    }
    // Score "meilleur" : on privilégie un commentaire qui cite une vraie info
    // (prix/sujet) et de longueur raisonnable — plus crédible.
    out.forEach(c => {
      let score = 0;
      if (d.price && c.text.includes(d.price)) score += 3;
      if (d.subject && c.text.toLowerCase().includes(d.subject.toLowerCase().slice(0, 10))) score += 2;
      const len = c.text.length;
      if (len >= 40 && len <= 160) score += 2;
      if (!/[🔥😍🤑🙌🤔🙏😅👍✅💪]/.test(c.text)) score += 1; // sans emoji = souvent plus crédible
      c.score = score;
    });
    const bestIdx = out.reduce((bi, c, i, a) => (c.score > a[bi].score ? i : bi), 0);
    out.forEach((c, i) => (c.best = i === bestIdx));
    return out;
  }

  // -------------------------------------------------------------------------
  // État / historique local
  // -------------------------------------------------------------------------
  const STORE_KEY = 'redacteur_stats_v1';
  function loadStats() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY)) || { posts: 0, likes: 0, comments: 0 }; }
    catch { return { posts: 0, likes: 0, comments: 0 }; }
  }
  function saveStats(s) { try { localStorage.setItem(STORE_KEY, JSON.stringify(s)); } catch {} }
  let stats = loadStats();
  function renderStats() {
    $('#statPosts').textContent = stats.posts;
    $('#statLikes').textContent = stats.likes;
    $('#statComments').textContent = stats.comments;
  }

  // -------------------------------------------------------------------------
  // DOM helpers + wiring
  // -------------------------------------------------------------------------
  function $(sel) { return document.querySelector(sel); }

  let currentTone = 'utile';
  let lastData = null;

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => toast('Copié !')).catch(() => fallbackCopy(text));
    } else {
      fallbackCopy(text);
    }
  }
  function fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    try { document.execCommand('copy'); toast('Copié !'); } catch { toast('Copie impossible'); }
    document.body.removeChild(ta);
  }

  let toastTimer = null;
  function toast(msg) {
    const t = $('#toast');
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), 1600);
  }

  function fillFields(d) {
    $('#fSubject').value = d.subject || '';
    $('#fPrice').value = d.price || '';
    $('#fOldPrice').value = d.oldPrice || '';
    $('#fStore').value = d.store || '';
    $('#fLink').value = d.link || '';
    $('#fInfo').value = d.info || '';
  }
  function readFields() {
    return {
      subject: $('#fSubject').value.trim(),
      price: $('#fPrice').value.trim(),
      oldPrice: $('#fOldPrice').value.trim(),
      store: $('#fStore').value.trim(),
      link: $('#fLink').value.trim(),
      info: $('#fInfo').value.trim(),
    };
  }

  function renderComments(list) {
    const ul = $('#comments');
    ul.innerHTML = '';
    list.forEach((c, i) => {
      const li = document.createElement('li');
      li.className = 'comment' + (c.best ? ' is-best' : '');
      const meta = document.createElement('div');
      meta.className = 'comment-meta';
      meta.innerHTML =
        `<span class="badge ${c.best ? 'best' : ''}">${c.tone}${c.best ? ' · meilleur' : ''}</span>` +
        `<span>${c.text.length} caractères</span>`;
      const txt = document.createElement('div');
      txt.className = 'comment-text';
      txt.textContent = c.text;
      const actions = document.createElement('div');
      actions.className = 'comment-actions';
      const copyBtn = document.createElement('button');
      copyBtn.className = 'btn btn-ghost btn-sm';
      copyBtn.type = 'button';
      copyBtn.textContent = '📋 Copier';
      copyBtn.addEventListener('click', () => copyText(c.text));
      actions.appendChild(copyBtn);
      li.appendChild(meta);
      li.appendChild(txt);
      li.appendChild(actions);
      ul.appendChild(li);
    });
  }

  let lastComments = [];
  function runGeneration(reseed) {
    if (reseed) rng = makeRng(((rng() * 1e9) | 0) ^ (lastComments.length + 7));
    const d = readFields();
    lastData = d;
    lastComments = generate(d, currentTone);
    renderComments(lastComments);
    $('#resultCard').hidden = false;
  }

  function prepare() {
    const text = $('#postText').value;
    if (!text.trim()) { toast('Colle d\'abord un post 🙂'); return; }
    const d = extract(text);
    fillFields(d);
    $('#extractCard').hidden = false;
    stats.posts += 1; saveStats(stats); renderStats();
    runGeneration(false);
    $('#resultCard').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // -------------------------------------------------------------------------
  // Listeners (avec addEventListener => fiable aussi sur mobile, contrairement
  // à onclick inline qui peut échouer selon les navigateurs).
  // -------------------------------------------------------------------------
  function init() {
    renderStats();

    $('#prepareBtn').addEventListener('click', prepare);
    // Sécurité mobile : déclenche aussi sur touchend si le click tarde.
    $('#prepareBtn').addEventListener('touchend', function (e) {
      e.preventDefault();
      prepare();
    }, { passive: false });

    $('#clearBtn').addEventListener('click', function () {
      $('#postText').value = '';
      $('#extractCard').hidden = true;
      $('#resultCard').hidden = true;
    });

    $('#regenBtn').addEventListener('click', function () { runGeneration(true); });

    $('#tones').addEventListener('click', function (e) {
      const b = e.target.closest('.tone');
      if (!b) return;
      document.querySelectorAll('.tone').forEach(x => x.classList.remove('is-active'));
      b.classList.add('is-active');
      currentTone = b.dataset.tone;
      if (!$('#resultCard').hidden) runGeneration(true);
    });

    $('#copyBestBtn').addEventListener('click', function () {
      const best = lastComments.find(c => c.best) || lastComments[0];
      if (best) copyText(best.text);
    });

    $('#markLiked').addEventListener('click', function () {
      stats.likes += 1; saveStats(stats); renderStats(); toast('Liké noté 👍');
    });
    $('#markCommented').addEventListener('click', function () {
      stats.comments += 1; saveStats(stats); renderStats(); toast('Commentaire noté 💬');
    });

    $('#resetStats').addEventListener('click', function () {
      stats = { posts: 0, likes: 0, comments: 0 };
      saveStats(stats); renderStats(); toast('Historique réinitialisé');
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

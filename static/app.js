"use strict";

// ------------------------------------------------------------------ utilitaires
const $ = (id) => document.getElementById(id);
const api = async (url, opts = {}) => {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  try {
    return await r.json();
  } catch (e) {
    // Réponse non-JSON (page d'erreur, timeout proxy…) : erreur lisible.
    throw new Error("Réponse invalide du serveur (HTTP " + r.status + ")");
  }
};
function toast(msg, err = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  setTimeout(() => (t.className = "toast"), 2600);
}
async function copier(texte) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(texte);
    } else {
      // Fallback HTTP (mobile) : navigator.clipboard exige HTTPS.
      const ta = document.createElement("textarea");
      ta.value = texte;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
    toast("Copié ✓");
  } catch (e) {
    toast("Copie impossible", true);
  }
}

let dernierProduit = null;
let dernierAnnonce = null;
let dernierChiffrage = null;
let dernierPromptGemini = "";

// ------------------------------------------------------------------ onglets
document.querySelectorAll(".onglet").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".onglet").forEach((x) => x.classList.remove("actif"));
    document.querySelectorAll(".vue").forEach((x) => x.classList.remove("active"));
    b.classList.add("actif");
    $("vue-" + b.dataset.vue).classList.add("active");
    if (b.dataset.vue === "suivi") chargerAnnonces();
    if (b.dataset.vue === "creneaux") { chargerCreneaux(); chargerEtatNtfy(); }
  });
});

// ------------------------------------------------------------------ état config
async function chargerEtat() {
  const e = await api("/api/etat");
  const p = (label, ok) =>
    `<span class="pastille ${ok ? "ok" : "ko"}">${ok ? "●" : "○"} ${label}</span>`;
  $("etat").innerHTML =
    p("Claude" + (e.modele ? " (" + e.modele + ")" : ""), e.claude) +
    p("DataForSEO", e.dataforseo) +
    p("ntfy" + (e.ntfy_topic ? " (" + e.ntfy_topic + ")" : ""), e.ntfy);
}

// ------------------------------------------------------------------ photo -> remplissage auto
// Redimensionne la photo côté navigateur (max 1600 px, JPEG) : upload rapide
// et taille compatible avec l'API vision.
function redimensionnerPhoto(file, maxDim = 1600) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const ratio = Math.min(1, maxDim / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * ratio);
      canvas.height = Math.round(img.height * ratio);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("compression échouée"))),
        "image/jpeg", 0.85);
      URL.revokeObjectURL(img.src);
    };
    img.onerror = () => reject(new Error("image illisible"));
    img.src = URL.createObjectURL(file);
  });
}

async function analyserFichierPhoto(input) {
  const files = Array.prototype.slice.call(input.files || [], 0, 4);
  if (!files.length) return;
  const zone = document.querySelector(".photo-boutons");
  const status = $("photo-status");
  zone.classList.add("charge");
  status.classList.remove("cachee");
  status.textContent = files.length > 1
    ? "⏳ Analyse des " + files.length + " photos par Claude…"
    : "⏳ Analyse de la photo par Claude…";
  try {
    const fd = new FormData();
    for (let i = 0; i < files.length; i++) {
      const blob = await redimensionnerPhoto(files[i]);
      fd.append("photo", blob, "photo" + (i + 1) + ".jpg");
    }
    const r = await fetch("/api/analyser-photo", { method: "POST", body: fd });
    let d;
    try { d = await r.json(); }
    catch (e) { throw new Error("Réponse invalide du serveur (HTTP " + r.status + ")"); }
    if (d.erreur) throw new Error(d.erreur);
    const p = d.produit || {};
    const map = { nom: "nom", marque: "marque", categorie: "categorie", taille: "taille", couleur: "couleur", details: "details" };
    Object.entries(map).forEach(([k, id]) => { if (p[k]) $(id).value = p[k]; });
    if (p.etat) {
      // Boucle indexée : HTMLOptionsCollection n'est pas itérable sur tous les
      // navigateurs mobiles (Samsung Internet, vieux Chromium).
      const sel = $("etat");
      for (let i = 0; i < sel.options.length; i++) {
        if (sel.options[i].value === p.etat) { sel.value = p.etat; break; }
      }
    }
    if (p.mots_cles && p.mots_cles.length) $("mots_cles").value = p.mots_cles.join(", ");
    status.textContent = "✅ Champs remplis (confiance " + (p.confiance || "?") +
      "). Vérifie, ajoute ton prix d'achat, puis Générer.";
    toast("Produit identifié 📸");
    $("prix_achat").focus();
  } catch (e) {
    status.textContent = "❌ " + e.message;
    toast("Analyse échouée", true);
  } finally {
    zone.classList.remove("charge");
    input.value = "";
  }
}

$("photo-input").addEventListener("change", () => analyserFichierPhoto($("photo-input")));
$("galerie-input").addEventListener("change", () => analyserFichierPhoto($("galerie-input")));

// ------------------------------------------------------------------ génération
function lireProduit() {
  return {
    nom: $("nom").value, marque: $("marque").value, categorie: $("categorie").value,
    etat: $("etat").value, taille: $("taille").value, couleur: $("couleur").value,
    details: $("details").value, plateforme: $("plateforme").value,
    prix_achat: $("prix_achat").value, reference_marche: $("reference_marche").value,
    mots_cles: $("mots_cles").value,
  };
}

$("btn-generer").addEventListener("click", async () => {
  const nom = $("nom").value.trim();
  if (!nom) { toast("Renseigne au moins le nom du produit", true); return; }
  const btn = $("btn-generer");
  btn.disabled = true; btn.textContent = "⏳ Génération en cours…";
  try {
    const d = await api("/api/generer", { method: "POST", body: lireProduit() });
    if (d.erreur) { toast(d.erreur, true); return; }
    dernierProduit = d.produit || {};
    afficherAnnonce(d);
    afficherSeo(d.seo);
    afficherPrix(d.chiffrage, dernierProduit.plateforme);
    afficherLiens(d.liens);
  } catch (e) {
    toast("Erreur : " + e, true);
  } finally {
    btn.disabled = false; btn.textContent = "✨ Générer l'annonce";
  }
});

function afficherAnnonce(d) {
  $("resultats").classList.remove("cachee");
  const err = $("erreur-claude");
  if (d.erreur_claude) {
    err.classList.remove("cachee");
    err.textContent = "⚠️ Génération Claude indisponible : " + d.erreur_claude +
      " — le reste (mots-clés, prix, liens) fonctionne quand même.";
  } else { err.classList.add("cachee"); }

  const a = d.annonce || {};
  dernierAnnonce = a;

  // Description commune
  $("desc").textContent = a.description || "—";
  const mots = (a.description || "").trim().split(/\s+/).filter(Boolean).length;
  $("desc-len").textContent = a.description ? mots + " mots" + (mots >= 80 && mots <= 150 ? " ✓" : " (visez 80-150)") : "";

  // Côté Vinted
  $("v-titre").textContent = a.titre_vinted || "—";
  const vlen = (a.titre_vinted || "").length;
  $("v-titre-len").textContent = vlen + " car." + (vlen >= 45 && vlen <= 60 ? " ✓" : " (50-60)");
  $("hashtags").textContent = (a.hashtags || []).slice(0, 5).map((h) => "#" + h.replace(/^#/, "")).join(" ") || "—";

  // Côté Leboncoin
  $("lc-titre").textContent = a.titre_court || "—";
  const len = (a.titre_court || "").length;
  $("lc-titre-len").textContent = len + "/50" + (len > 50 ? " ⚠️" : " ✓");
  $("lc-cat").textContent = (a.attributs && a.attributs.categorie_precise) || "Maison & Jardin > Arts de la table";
  // Leboncoin : 5 mots-clés max, sans le # (autorisés avec ou sans).
  $("lc-tags").textContent = (a.hashtags || []).slice(0, 5).map((h) => h.replace(/^#/, "")).join(" · ") || "—";

  // Affiche le(s) côté(s) selon la plateforme cible
  const plat = (d.produit && d.produit.plateforme) || "les-deux";
  $("cote-vinted").classList.toggle("cachee", plat === "leboncoin");
  $("cote-lbc").classList.toggle("cachee", plat === "vinted");

  afficherAttributs(a.attributs);
}

// Copier l'annonce complète (titre + description + mots-clés) pour une plateforme
function copierAnnonceComplete(plateforme) {
  const a = dernierAnnonce;
  if (!a) return;
  let titre, tags;
  if (plateforme === "leboncoin") {
    titre = a.titre_court || "";
    tags = (a.hashtags || []).slice(0, 5).map((h) => h.replace(/^#/, "")).join(" ");
  } else {
    titre = a.titre_vinted || "";
    tags = (a.hashtags || []).slice(0, 5).map((h) => "#" + h.replace(/^#/, "")).join(" ");
  }
  // Si la description contient déjà les hashtags à la fin, on n'ajoute pas de doublon.
  let desc = a.description || "";
  const bloc = tags && !desc.includes(tags.split(" ")[0]) ? desc + "\n\n" + tags : desc;
  copier(titre + "\n\n" + bloc);
}

function afficherAttributs(attrs) {
  const bloc = $("bloc-attributs");
  if (!attrs) { bloc.classList.add("cachee"); return; }
  const libelles = {
    marque: "Marque", taille: "Taille", couleur: "Couleur",
    matiere: "Matière", etat: "État", categorie_precise: "Catégorie",
  };
  const cases = Object.entries(libelles)
    .filter(([k]) => attrs[k])
    .map(([k, lib]) => `<div class="attr"><span class="k">${lib}</span><span class="v">${attrs[k]}</span></div>`)
    .join("");
  if (!cases) { bloc.classList.add("cachee"); return; }
  $("attributs").innerHTML = `<div class="attributs-grille">${cases}</div>`;
  bloc.classList.remove("cachee");
}

function afficherSeo(seo) {
  seo = seo || { disponible: false, mots_cles: [], erreur: "réponse incomplète" };
  seo.mots_cles = seo.mots_cles || [];
  const bloc = $("bloc-seo");
  bloc.classList.remove("cachee");
  const tbody = $("seo-table").querySelector("tbody");
  tbody.innerHTML = "";
  if (!seo.disponible) {
    $("seo-info").textContent = "DataForSEO non configuré ou indisponible" +
      (seo.erreur ? " (" + seo.erreur + ")" : "") + " — volumes réels désactivés.";
    $("seo-table").style.display = "none";
    return;
  }
  $("seo-table").style.display = "";
  $("seo-info").textContent = "Volumes mensuels Google (France).";
  seo.mots_cles.forEach((m) => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${m.keyword}</td><td class="vol">${(m.volume || 0).toLocaleString("fr-FR")}</td>` +
      `<td>${m.competition || "—"}</td><td>${m.cpc != null ? m.cpc.toFixed(2) + " €" : "—"}</td>`;
    tbody.appendChild(tr);
  });
}

// ------------------------------------------------------------------ prix / marge
function afficherPrix(chiffrage, plateforme) {
  const bloc = $("bloc-prix");
  if (!chiffrage) { bloc.classList.add("cachee"); return; }
  bloc.classList.remove("cachee");
  dernierChiffrage = chiffrage;
  const p = chiffrage.paliers;
  const carte = (titre, c) =>
    `<div class="palier"><h3>${titre}</h3><div class="pv">${c.prix_vente.toFixed(2)} €</div>` +
    `<div class="marge">+${c.marge_euro.toFixed(2)} € net (${c.marge_pct.toFixed(0)} %)</div>` +
    (plateforme === "vinted"
      ? `<small>acheteur paie ~${c.cout_acheteur.toFixed(2)} €</small>`
      : `<small>frais ${c.frais_vendeur.toFixed(2)} €</small>`) + `</div>`;
  $("paliers").innerHTML =
    carte("⚡ Vente rapide", p.vente_rapide) +
    carte("⚖️ Équilibré", p.equilibre) +
    carte("💎 Marge max", p.marge_max);
  majPrixLive();
}

$("marge-slider").addEventListener("input", () => {
  $("marge-val").textContent = $("marge-slider").value;
  majPrixLive();
});

async function majPrixLive() {
  const prixAchat = $("prix_achat").value;
  if (!prixAchat) { $("prix-live").textContent = "Renseigne un prix d'achat pour le calcul."; return; }
  const c = await api("/api/prix", {
    method: "POST",
    body: {
      prix_achat: prixAchat,
      plateforme: $("plateforme").value,
      marge_cible_pct: $("marge-slider").value,
    },
  });
  $("prix-live").innerHTML =
    `Prix conseillé : <strong>${c.prix_vente.toFixed(2)} €</strong> · ` +
    `marge nette <strong style="color:var(--vert)">${c.marge_euro.toFixed(2)} €</strong>`;
}

// ------------------------------------------------------------------ liens externes
function afficherLiens(liens) {
  if (!liens) return;
  $("bloc-liens").classList.remove("cachee");
  $("lien-ebay").href = liens.ebay_vendus;
  $("lien-lens").href = liens.lens;
  $("lien-gemini").href = liens.gemini;
  dernierPromptGemini = liens.gemini_prompt || "";
}
$("btn-copier-prompt").addEventListener("click", () => {
  if (dernierPromptGemini) copier(dernierPromptGemini);
});

// médiane des vendus -> remplit la référence marché
$("btn-mediane").addEventListener("click", async () => {
  const texte = $("vendus-texte").value.trim();
  if (!texte) { toast("Colle des prix d'abord", true); return; }
  const s = await api("/api/mediane", { method: "POST", body: { texte } });
  if (!s.n) { $("mediane-res").textContent = "Aucun prix détecté."; return; }
  $("reference_marche").value = s.mediane;
  if (!$("bloc-prix").classList.contains("cachee")) majPrixLive();
  $("mediane-res").innerHTML =
    `${s.n} prix · médiane <strong>${s.mediane} €</strong> · min ${s.min} / max ${s.max} · ` +
    `conseillé <strong style="color:var(--vert)">${s.conseille} €</strong> (référence remplie).`;
});

// copier les blocs (boutons avec data-cible)
document.querySelectorAll(".copier").forEach((b) => {
  b.addEventListener("click", () => copier($(b.dataset.cible).textContent));
});

// boutons « copier l'annonce complète » (Vinted / Leboncoin)
document.querySelectorAll("[data-full]").forEach((b) => {
  b.addEventListener("click", () => copierAnnonceComplete(b.dataset.full));
});

// clic direct sur un champ copiable = copier son contenu
document.getElementById("resultats").addEventListener("click", (e) => {
  const el = e.target.closest(".copiable.petit");
  if (el && el.textContent && el.textContent !== "—") copier(el.textContent);
});

// ------------------------------------------------------------------ suivre annonce
$("btn-suivre").addEventListener("click", async () => {
  if (!dernierProduit) return;
  const a = document.querySelector("#v-titre").textContent;
  const prixVente = dernierChiffrage ? dernierChiffrage.paliers.equilibre.prix_vente : null;
  await api("/api/annonces", {
    method: "POST",
    body: {
      titre: a && a !== "—" ? a : dernierProduit.nom,
      plateforme: dernierProduit.plateforme === "les-deux" ? "vinted" : dernierProduit.plateforme,
      categorie: dernierProduit.categorie,
      prix: prixVente,
      prix_achat: $("prix_achat").value,
    },
  });
  toast("Annonce ajoutée au suivi 📌");
});

// ------------------------------------------------------------------ vue suivi
async function chargerAnnonces() {
  let d;
  try { d = await api("/api/annonces"); } catch (e) { toast(e.message, true); return; }
  const resume = d.resume || { vert: 0, orange: 0, rouge: 0 };
  const annonces = d.annonces || [];
  $("resume-statuts").innerHTML =
    `<span class="b">🟢 ${resume.vert}</span>` +
    `<span class="b">🟠 ${resume.orange}</span>` +
    `<span class="b">🔴 ${resume.rouge}</span>`;
  const box = $("liste-annonces");
  if (!annonces.length) { box.innerHTML = '<p class="aide">Aucune annonce suivie pour le moment.</p>'; return; }
  box.innerHTML = "";
  annonces.forEach((a) => {
    const el = document.createElement("div");
    el.className = "annonce " + a.statut_couleur;
    const bActive = a.variante_active === "B" && a.titre_b;
    const titreActif = bActive ? a.titre_b : a.titre;
    const badge = a.titre_b ? `<span class="badge">Var. ${a.variante_active}</span>` : "";
    const repub = a.jours_avant_republication;
    const repubTxt = repub <= 0 ? "à republier" : `repub. dans ${repub} j`;
    const cadence = Math.round(a.cadence_jours || 8);
    const opts = [7, 8, 10, 12, 14, 21].map((v) =>
      `<option value="${v}" ${v === cadence ? "selected" : ""}>${v} j</option>`).join("");

    el.innerHTML =
      `<div class="statut">${a.statut_emoji}</div>` +
      `<div class="infos"><div class="titre">${titreActif} ${badge}</div>` +
      `<div class="meta">${a.plateforme} · ${a.age_jours} j · ${a.statut_label} · ${repubTxt}` +
      (a.nb_republications ? ` · ${a.nb_republications}× repub.` : "") + `</div>` +
      `<details class="ab-panel"><summary>⚙️ Cadence &amp; test A/B</summary>` +
      `<div class="ab-row">Republier tous les <select data-cadence="${a.id}">${opts}</select></div>` +
      `<div class="variante ${!bActive ? "on" : ""}">A · ${a.titre} ${a.prix ? "· " + a.prix + " €" : ""}</div>` +
      `<div class="variante ${bActive ? "on" : ""}">B · ` +
      `<input class="ab-in" data-btitre="${a.id}" value="${(a.titre_b || "").replace(/"/g, "&quot;")}" placeholder="titre variante B">` +
      `<input class="ab-in prix" data-bprix="${a.id}" type="number" step="0.5" value="${a.prix_b || ""}" placeholder="€">` +
      `<button data-bsave="${a.id}">💾</button></div>` +
      (a.titre_b ? `<button class="secondaire mini" data-bascule="${a.id}">🔀 Basculer A/B (relister)</button>` : "") +
      `<div class="ab-bilan" data-bilan="${a.id}"></div>` +
      `</details></div>` +
      `<div class="actions">` +
      `<button class="rep" data-rep="${a.id}">Republié</button>` +
      `<button data-vendu="${a.id}">Vendu</button>` +
      `<button data-del="${a.id}">✕</button></div>`;
    box.appendChild(el);
  });

  box.querySelectorAll("[data-rep]").forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.rep;
    let r = await api(`/api/annonces/${id}/republier`, { method: "POST", body: {} });
    if (r.avertissement) {
      if (!confirm(r.avertissement)) return;
      r = await api(`/api/annonces/${id}/republier`, { method: "POST", body: { force: true } });
    }
    toast("Marqué republié 🔄"); chargerAnnonces();
  }));
  box.querySelectorAll("[data-vendu]").forEach((b) => b.addEventListener("click", async () => {
    await api(`/api/annonces/${b.dataset.vendu}/vendu`, { method: "POST", body: {} });
    toast("Vendu ! 🎉 (ajouté à tes stats)"); chargerAnnonces();
  }));
  box.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    await api(`/api/annonces/${b.dataset.del}`, { method: "DELETE" });
    chargerAnnonces();
  }));
  box.querySelectorAll("[data-cadence]").forEach((s) => s.addEventListener("change", async () => {
    await api(`/api/annonces/${s.dataset.cadence}`, { method: "PATCH", body: { cadence_jours: s.value } });
    toast("Cadence mise à jour"); chargerAnnonces();
  }));
  box.querySelectorAll("[data-bsave]").forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.bsave;
    const titre_b = box.querySelector(`[data-btitre="${id}"]`).value.trim();
    const prix_b = box.querySelector(`[data-bprix="${id}"]`).value;
    await api(`/api/annonces/${id}`, { method: "PATCH", body: { titre_b, prix_b } });
    toast("Variante B enregistrée"); chargerAnnonces();
  }));
  box.querySelectorAll("[data-bascule]").forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.bascule;
    let r = await api(`/api/annonces/${id}/variante`, { method: "POST", body: {} });
    if (r.avertissement) { if (!confirm(r.avertissement)) return;
      r = await api(`/api/annonces/${id}/variante`, { method: "POST", body: { force: true } }); }
    toast("Variante " + (r.variante_active || "") + " en ligne 🔀"); chargerAnnonces();
  }));
  // bilan A/B (chargé à l'ouverture du panneau)
  box.querySelectorAll("details.ab-panel").forEach((det) => det.addEventListener("toggle", async () => {
    if (!det.open) return;
    const cible = det.querySelector("[data-bilan]");
    const id = cible.dataset.bilan;
    const bilan = await api(`/api/annonces/${id}/ab`);
    cible.textContent = (bilan.A || bilan.B)
      ? `Bilan ventes — A : ${bilan.A} · B : ${bilan.B}`
      : "Bilan A/B : aucune vente encore.";
  }));
}

// ------------------------------------------------------------------ vue créneaux
async function chargerCreneaux() {
  let d;
  try { d = await api("/api/creneaux"); } catch (e) { toast(e.message, true); return; }
  $("creneaux-info").textContent = d.source === "stats"
    ? `Basé sur tes ${d.total_ventes} ventes.`
    : `Créneaux par défaut (grand trafic). Enregistre au moins 8 ventes pour personnaliser (${d.total_ventes || 0} pour l'instant).`;
  const box = $("liste-creneaux");
  box.innerHTML = "";
  (d.creneaux || []).forEach((c, i) => {
    const el = document.createElement("div");
    el.className = "creneau";
    el.innerHTML =
      `<div class="rang">${i + 1}</div>` +
      `<div class="quand">${c.jour_nom} ${String(c.heure).padStart(2, "0")}h</div>` +
      `<div class="part">${c.ventes ? c.ventes + " ventes · " + c.part_pct + " %" : "recommandé"}</div>`;
    box.appendChild(el);
  });
}

$("btn-vente").addEventListener("click", async () => {
  const dt = $("v-date").value;
  const body = {
    montant: $("v-montant").value || null,
    plateforme: $("v-plateforme").value,
  };
  if (dt) body.date_vente = Math.floor(new Date(dt).getTime() / 1000);
  await api("/api/ventes", { method: "POST", body });
  toast("Vente enregistrée ✓"); chargerCreneaux();
});

async function chargerEtatNtfy() {
  const e = await api("/api/etat");
  $("ntfy-info").textContent = e.ntfy
    ? `Alertes actives sur le topic « ${e.ntfy_topic} ». Abonne-toi via l'appli ntfy.`
    : "ntfy non configuré : ajoute NTFY_TOPIC dans le .env pour recevoir les alertes.";
}
$("btn-test-ntfy").addEventListener("click", async () => {
  const r = await api("/api/tester-ntfy", { method: "POST" });
  toast(r.envoye ? "Notif envoyée 🔔" : "Échec (ntfy configuré ?)", !r.envoye);
});

// premier chargement de prix live quand on change le prix d'achat
$("prix_achat").addEventListener("input", () => {
  if (!$("bloc-prix").classList.contains("cachee")) majPrixLive();
});

chargerEtat();

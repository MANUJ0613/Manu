"use strict";

// ------------------------------------------------------------------ utilitaires
const $ = (id) => document.getElementById(id);
const api = async (url, opts = {}) => {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  return r.json();
};
function toast(msg, err = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  setTimeout(() => (t.className = "toast"), 2200);
}
async function copier(texte) {
  try { await navigator.clipboard.writeText(texte); toast("Copié ✓"); }
  catch { toast("Copie impossible", true); }
}

let dernierProduit = null;
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
    dernierProduit = d.produit;
    afficherAnnonce(d);
    afficherSeo(d.seo);
    afficherPrix(d.chiffrage, d.produit.plateforme);
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
  $("lc-titre").textContent = a.titre_court || "—";
  const len = (a.titre_court || "").length;
  $("lc-titre-len").textContent = len + "/50" + (len > 50 ? " ⚠️" : "");
  $("v-titre").textContent = a.titre_vinted || "—";
  $("desc").textContent = a.description || "—";
  $("hashtags").textContent = (a.hashtags || []).map((h) => "#" + h.replace(/^#/, "")).join(" ") || "—";
}

function afficherSeo(seo) {
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

// copier les blocs
document.querySelectorAll(".copier").forEach((b) => {
  b.addEventListener("click", () => copier($(b.dataset.cible).textContent));
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
  const d = await api("/api/annonces");
  $("resume-statuts").innerHTML =
    `<span class="b">🟢 ${d.resume.vert}</span>` +
    `<span class="b">🟠 ${d.resume.orange}</span>` +
    `<span class="b">🔴 ${d.resume.rouge}</span>`;
  const box = $("liste-annonces");
  if (!d.annonces.length) { box.innerHTML = '<p class="aide">Aucune annonce suivie pour le moment.</p>'; return; }
  box.innerHTML = "";
  d.annonces.forEach((a) => {
    const el = document.createElement("div");
    el.className = "annonce " + a.statut_couleur;
    el.innerHTML =
      `<div class="statut">${a.statut_emoji}</div>` +
      `<div class="infos"><div class="titre">${a.titre}</div>` +
      `<div class="meta">${a.plateforme} · ${a.age_jours} j · ${a.statut_label}` +
      (a.nb_republications ? ` · ${a.nb_republications}× republié` : "") + `</div></div>` +
      `<div class="actions">` +
      `<button class="rep" data-rep="${a.id}">Republié</button>` +
      `<button data-vendu="${a.id}">Vendu</button>` +
      `<button data-del="${a.id}">✕</button></div>`;
    box.appendChild(el);
  });
  box.querySelectorAll("[data-rep]").forEach((b) => b.addEventListener("click", async () => {
    await api(`/api/annonces/${b.dataset.rep}/republier`, { method: "POST" });
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
}

// ------------------------------------------------------------------ vue créneaux
async function chargerCreneaux() {
  const d = await api("/api/creneaux");
  $("creneaux-info").textContent = d.source === "stats"
    ? `Basé sur tes ${d.total_ventes} ventes.`
    : `Créneaux par défaut (grand trafic). Enregistre au moins 8 ventes pour personnaliser (${d.total_ventes} pour l'instant).`;
  const box = $("liste-creneaux");
  box.innerHTML = "";
  d.creneaux.forEach((c, i) => {
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

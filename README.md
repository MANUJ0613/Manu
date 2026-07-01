# Outils de veille e-commerce

Ce dépôt contient trois outils indépendants :

1. **[Analyseur de demande Vinted](#analyseur-de-demande-vinted-)** 🛍️ — savoir
   ce qui est le plus recherché sur Vinted (favoris/vues) et à quel prix ça se
   revend.
2. **[Micromania deals watcher](#micromania-deals-watcher-)** 🔥 — alerte sur les
   grosses réductions / erreurs de prix sur micromania.fr.
3. **[Revente SEO (Leboncoin & Vinted)](#revente-seo-leboncoin--vinted-)** ✍️ —
   serveur web qui génère tes annonces (titre + description via Claude), cale le
   prix/marge, et t'alerte quand republier au meilleur créneau.

---

# Revente SEO (Leboncoin & Vinted) ✍️

Un **serveur Flask 24/7** (pensé pour un VPS) qui t'aide à revendre plus vite.
Tu décris ton produit dans l'interface web, et l'outil :

- **génère titre + description optimisés** via l'**API Claude** (titre court
  Leboncoin ≤ 50 caractères, titre Vinted riche en mots-clés, description,
  hashtags) ;
- récupère les **vrais volumes de recherche Google** de tes mots-clés via
  **DataForSEO** (pour prioriser ceux que les acheteurs tapent vraiment) ;
- calcule le **prix conseillé et la marge nette** (frais Vinted/Leboncoin
  intégrés) avec 3 paliers (vente rapide / équilibré / marge max) et un curseur ;
- propose des boutons **Google Lens / eBay (prix vendus) / Gemini** pour caler
  le prix ;
- **suit tes annonces** avec un statut de fraîcheur **🟢🟠🔴** et t'envoie une
  **alerte push ntfy** aux meilleurs créneaux pour les republier ;
- **détecte TES meilleurs créneaux** à partir de tes propres ventes (repli sur
  des créneaux grand trafic tant que tu n'as pas assez de données).

### Démarrage rapide (local)

```bash
pip install -r requirements-web.txt
export ANTHROPIC_API_KEY=sk-ant-...      # génération d'annonce
export NTFY_TOPIC=revente-secret         # alertes push (abonne-toi via l'appli ntfy)
export DATAFORSEO_LOGIN=... DATAFORSEO_PASSWORD=...   # optionnel : volumes réels
python annonces_seo.py                    # http://localhost:8000
```

L'appli **démarre même sans clés** : la génération Claude se désactive
proprement, le reste (mots-clés saisis, prix/marge, liens, suivi) continue.

### Déploiement VPS (systemd)

```bash
sudo bash deploy/install-annonces.sh      # clone, venv, service systemd
sudo nano /etc/annonces-seo.env           # remplis Claude / ntfy / DataForSEO
sudo systemctl start annonces-seo
```

Toute la config est dans **`deploy/annonces-seo.env.example`** (frais des
plateformes, seuils 🟠/🔴, modèle Claude, etc.). Mets un reverse-proxy nginx +
HTTPS devant en production.

### Comment ça marche

| Élément | Détail |
|---|---|
| Serveur | `annonces_seo.py` (Flask) + planificateur d'alertes en thread de fond |
| Logique | `seo_tools/` : `claude_client`, `dataforseo`, `pricing`, `slots`, `notify`, `links`, `db` |
| Stockage | SQLite `state/annonces.db` (annonces, ventes, réglages) — non versionné |
| Statuts | 🟢 fraîche · 🟠 à republier bientôt (72 h) · 🔴 republie maintenant (168 h) |
| Créneaux | histogramme (jour × heure) de tes ventes → alerte ntfy au bon moment |
| Modèle | `claude-opus-4-8` par défaut (surcharge via `ANNONCES_MODEL`) |

---

# Analyseur de demande Vinted 🛍️

> 🤖 **Bot Discord interactif** : tu peux aussi taper **`/vinted iphone 13`**
> directement dans ton salon et recevoir la fiche en réponse — voir
> [Bot Discord interactif](#bot-discord-interactif-).

Trois modes (`MODE`) :

- **`categories` (par défaut)** — scanne **toutes les catégories Vinted sauf les
  vêtements** et liste les **produits récents (postés ≤ 7 jours) qui ont le plus
  de favoris**. C'est *le* mode pour repérer ce qui buzz en ce moment, par
  catégorie, sans rien présupposer.
- **`brands`** — classe les **marques** les plus demandées d'une/des catégorie(s)
  (favoris cumulés, nombre d'annonces, favoris/annonce). Par défaut sur **toute
  l'offre active** ; mets `BRAND_DAYS_WINDOW=7` pour les marques qui montent **ces
  7 derniers jours**. Ex : `MODE=brands VINTED_CATEGORIES=1499` (jouets).
- **`deals`** — **scanner d'affaires** : pour chaque catégorie, calcule le prix
  de marché par **marque + modèle** et alerte (Discord/Telegram) sur les annonces
  récentes **nettement sous le marché** (deals à sniper pour la revente). Filtre
  les cassés / pièces / boîtes vides / téléphones bloqués, et ne compare jamais
  un accessoire (coque, housse…) à l'appareil complet. Ex :
  `MODE=deals DEAL_THRESHOLD=0.40`.
- **`watchlist`** — **vérif revente d'un produit** : tu donnes un ou des
  mot(s)-clé(s) (`VINTED_QUERIES`), le bot renvoie pour chacun le **nombre
  d'annonces** (l'offre), les **favoris/annonce** (la demande), le **prix médian**
  de revente, et un **verdict** 🟢 ACHÈTE / 🟡 PRUDENCE / 🔴 ÉVITE. Pratique pour
  décider d'acheter ou non avant de sourcer. Workflow dédié
  [`vinted-check.yml`](.github/workflows/vinted-check.yml) : entre ton produit
  dans **Actions → Vinted check produit → Run workflow** et reçois la fiche sur
  Discord.

> ⏱️ **Dates des favoris.** Le `favourite_count` d'une annonce est **cumulé
> depuis sa mise en ligne** (tout l'historique de l'annonce), pas une fenêtre
> glissante. Le mode `categories` ne garde que les annonces postées ≤ `DAYS_WINDOW`
> jours ; le mode `brands` compte par défaut **toute l'offre active** (mets
> `BRAND_DAYS_WINDOW` pour restreindre au récent).

Mesure de demande = nombre de **favoris (likes)** ; les **vues** ne sont
récupérables qu'avec une session connectée (voir plus bas).

## Mode `categories` — ce que ça donne

Un classement **par catégorie › sous-catégorie**, avec les **15 produits** de
chaque sous-catégorie qui **montent le plus vite** (favoris/jour), postés sur la
fenêtre :

```
15 PRODUITS / CATÉGORIE — postés ≤ 7j, classés par favoris/jour, hors vêtements

▸ Électronique › Jeux vidéo et consoles
    1.  302/j  ❤163   12h   95,00 €  Nintendo switch 1
    2.  108/j  ❤54     4h  210,00 €  Nintendo switch 2
    ...
▸ Loisirs et collections › Cartes à collectionner
    1.   62/j  ❤31     9h    1,00 €  Lot de cartes Pokémon
    ...
```

- **`/jour` (favoris/jour)** = vitesse à laquelle l'article accumule des likes →
  repère les **tendances fraîches** mieux que le total brut.
- **`âge`** = depuis quand l'article est en ligne (heure de référence = horloge
  du **serveur Vinted**, donc fiable même si l'horloge locale est décalée).
- Sur Discord : **un message par sous-catégorie** (liens cliquables). Détail
  complet aussi en **JSON** + **CSV** pour analyse dans un tableur.

## Comment ça marche

1. Récupère l'arbre des catégories Vinted (depuis la home) et retire les
   catégories de **vêtements** (`EXCLUDE_PATTERNS`). Reste : chaussures, sacs,
   accessoires, **électronique**, maison, **collections/cartes**, jeux/jouets,
   sport, beauté…
2. Pour chaque catégorie, lit le catalogue en tri `relevance` (qui remonte les
   articles **récents les plus engageants**) et ne garde que ceux postés depuis
   ≤ `DAYS_WINDOW` jours (date = timestamp de la photo).
3. Déduplique, filtre le bruit (`MIN_FAVOURITES`), classe par **favoris** et
   sort le top global + les tendances qui montent vite + JSON/CSV + digest.

> 📌 **Vues indisponibles en anonyme.** Vinted renvoie `0` vue dans le catalogue
> et `404` sur la fiche sans être connecté. Le classement se base donc sur les
> **favoris** (excellent proxy de demande). Pour débloquer les vues : colle ta
> session via `VINTED_COOKIE` et mets `FETCH_VIEWS=true` (mode watchlist).
>
> ⚠️ Vinted **plafonne ~960 résultats** par requête : sur les très grosses
> catégories on échantillonne les plus pertinents récents, pas l'exhaustivité.

## Détection de tendances dans le temps 📈

À chaque run, le bot enregistre un **instantané** (favoris cumulés par mot-clé,
par sous-catégorie et par annonce) dans `state/vinted_history.json`, puis le
compare au run précédent pour repérer **ce qui MONTE** — avant que ça explose :

```
📈 TENDANCES QUI MONTENT (vs run précédent, il y a 6h)
• Mots-clés en hausse :      +150 (+300%) pokemon → 200 favoris
• Mots-clés ÉMERGENTS :      ✦ sonny (80 favoris)   ← absent avant
• Sous-catégories en hausse : +300 (+150%) Électronique › Téléphones
• Annonces qui décollent :   +85 ❤95  40€  Pokemon Charizard …
```

Un **embed Discord/Telegram dédié** est aussi envoyé. Comme le workflow tourne
toutes les 6 h et committe l'historique, les comparaisons s'enrichissent toutes
seules. Le **premier run** ne fait qu'enregistrer la base (rien à comparer).

## Lancer en local

```bash
pip install -r requirements.txt

# Mode catégories (défaut), à blanc :
DRY_RUN=true python3 vinted_analyzer.py

# Restreindre à quelques catégories (IDs) et élargir la fenêtre :
DRY_RUN=true VINTED_CATEGORIES="2994,4824" DAYS_WINDOW=14 python3 vinted_analyzer.py

# Mode watchlist (recherches précises) :
DRY_RUN=true MODE=watchlist VINTED_QUERIES="jordan 1,stanley cup" python3 vinted_analyzer.py
```

## Automatisation (GitHub Actions)

Le workflow [`.github/workflows/vinted-demand.yml`](.github/workflows/vinted-demand.yml)
lance le scan catégories **toutes les 6 heures**, écrit le rapport dans `state/`
et envoie le digest. Ajoute tes secrets dans **Settings → Secrets and variables
→ Actions** : `DISCORD_WEBHOOK_URL` (ou `TELEGRAM_*`).

## Réglages (variables d'environnement)

| Variable | Défaut | Rôle |
|----------|--------|------|
| `MODE` | `categories` | `categories`, `brands`, `deals` (affaires) ou `watchlist` |
| `DEAL_THRESHOLD` | `0.40` | Mode `deals` : seuil sous le marché (0.40 = -40%) |
| `DEAL_MAX_AGE_DAYS` | `2` | Mode `deals` : n'évaluer que les annonces postées ≤ N jours |
| `DEAL_MIN_COMPARABLES` | `5` | Mode `deals` : nb mini d'annonces comparables pour estimer le marché |
| `DEAL_MIN_SHARED_TOKENS` | `2` | Mode `deals` : mots de modèle communs requis pour comparer |
| `DEAL_MIN_PRICE` | `5` | Mode `deals` : ignore les annonces sous ce prix |
| `BRAND_DAYS_WINDOW` | `0` | Mode `brands` : `0` = offre active, `N` = postées ≤ N jours |
| `TOP_BRANDS` | `40` | Mode `brands` : nombre de marques affichées |
| `BRAND_MIN_LISTINGS` | `3` | Mode `brands` : minimum d'annonces pour retenir une marque |
| `DAYS_WINDOW` | `7` | Fenêtre de fraîcheur : articles postés depuis N jours |
| `RANK_BY` | `hotness` | `hotness` (favoris/jour, le + frais) ou `favourites` (favoris totaux) |
| `TOP_PER_CATEGORY` | `15` | Nombre d'articles listés par sous-catégorie |
| `CATEGORY_MAX_PAGES` | `3` | Pages de 96 articles lues par catégorie |
| `MIN_FAVOURITES` | `3` | Ignore les articles sous ce nombre de favoris |
| `TRACK_TRENDS` | `true` | Suivi des tendances dans le temps (instantané + comparaison) |
| `HISTORY_FILE` | `state/vinted_history.json` | Historique des instantanés |
| `HISTORY_MAX_RUNS` | `60` | Nombre de runs conservés dans l'historique |
| `TOP_TRENDS` | `12` | Nombre de tendances montantes affichées |
| `EXCLUDE_PATTERNS` | `vêtement,…,créateur` | Catégories exclues par titre |
| `VINTED_CATEGORIES` | *(auto)* | Forcer des IDs de catégories (sépar. virgules) |
| `TOP_ITEMS` | `30` | Taille du top produits affiché |
| `VINTED_DOMAIN` | `www.vinted.fr` | Domaine Vinted ciblé |
| `VINTED_QUERIES` / `WATCHLIST_FILE` | *(vide)* / `watchlist.txt` | Recherches (mode watchlist) |
| `FETCH_VIEWS` + `VINTED_COOKIE` | `false` | Récupérer les vues (session requise) |
| `PRICE_FROM` / `PRICE_TO` | *(vide)* | Filtre de prix |
| `CONCURRENCY` | `4` | Requêtes parallèles |
| `PROXY` | *(vide)* | Proxy (idéalement résidentiel) pour DataDome sur VPS |
| `LOOP_ENABLED` / `LOOP_INTERVAL_SECONDS` | `false` / `3600` | Boucle continue (systemd) |
| `REPORT_JSON` / `REPORT_CSV` | `state/vinted_report.*` | Chemins des rapports |
| `DRY_RUN` | `false` | N'envoie aucun digest |

> ⚠️ Comme Micromania, Vinted est derrière **DataDome**. Sur une IP datacenter
> (VPS), installe `curl_cffi` (déjà dans `requirements.txt`) et/ou utilise un
> **proxy résidentiel** (`PROXY=...`). Sur GitHub Actions, l'IP passe sans proxy.

## Bot Discord interactif 🤖

[`vinted_bot.py`](vinted_bot.py) est un **vrai bot** qui ÉCOUTE ton salon : tu
tapes une commande et il répond, en direct.

```
/vinted iphone 13      → fiche revente (annonces, favoris, prix, verdict)
/marques 1499          → top marques d'une catégorie (1499 = jouets)
!vinted stanley cup    → idem en préfixe (si tu préfères)
```

> Le **webhook** (utilisé par les workflows) est à sens unique. Pour *taper* dans
> le salon et obtenir une réponse, il faut ce bot, qui doit tourner **en continu**
> (VPS, PC allumé, hébergeur). GitHub Actions ne peut pas écouter en temps réel.

### Installation (VPS, ~5 min)

1. **Créer le bot** sur https://discord.com/developers/applications → *New
   Application* → onglet **Bot** → *Reset Token* → copie le **token**.
2. Toujours dans **Bot**, active **MESSAGE CONTENT INTENT**.
3. **Inviter le bot** : onglet *OAuth2 → URL Generator* → scopes **`bot`** +
   **`applications.commands`** → permissions **Send Messages** + **Embed Links**
   → ouvre l'URL générée et ajoute-le à ton serveur.
4. Sur le VPS :
   ```bash
   pip install -r requirements-bot.txt
   DISCORD_BOT_TOKEN="ton_token" python3 vinted_bot.py
   ```
   (optionnel : `DISCORD_GUILD_ID="id_serveur"` pour que les commandes `/`
   apparaissent instantanément.)
5. **En service permanent** (redémarre tout seul) :
   ```bash
   sudo cp deploy/vinted-bot.service /etc/systemd/system/
   echo 'DISCORD_BOT_TOKEN=ton_token' | sudo tee -a /etc/vinted-demand.env
   sudo systemctl enable --now vinted-bot
   sudo journalctl -u vinted-bot -f   # voir les logs
   ```

Dépendances : [`requirements-bot.txt`](requirements-bot.txt) (`discord.py` +
`curl_cffi`).

---

# Micromania deals watcher 🔥

Détecteur automatique de **grosses réductions et erreurs de prix** sur
[micromania.fr](https://www.micromania.fr). Quand un produit **neuf** dont le
prix de référence dépasse **50 €** est bradé d'au moins **50 %**, tu reçois une
alerte sur **Discord** et/ou **Telegram** — pour ne plus rater un bon plan façon
Dealabs (comme la réplique casque Doom à 39,99 € au lieu de 149,99 €).

## Comment ça marche

1. Lit l'index des sitemaps Micromania et récupère les sitemaps `*-product`.
2. Grâce au champ `lastmod`, **ne re-scanne que les fiches modifiées depuis le
   dernier passage** → chaque run est rapide et léger.
3. Pour chaque fiche, extrait le **prix actuel** et le **prix de référence
   barré** depuis les données embarquées dans la page.
4. Déclenche une alerte si : produit neuf + prix réf. ≥ 50 € + réduction ≥ 50 %.
5. Mémorise les deals déjà signalés (`state/state.json`) pour ne pas spammer
   (ré-alerte uniquement si le prix baisse encore).

Bibliothèque standard Python 3, plus **`curl_cffi`** (optionnel mais recommandé) :
Micromania est protégé par l'anti-bot **DataDome**, qui bloque en **403** les
requêtes depuis une IP datacenter (VPS). `curl_cffi` imite l'empreinte TLS de
Chrome et, combiné à un warm-up de la page d'accueil (cookie DataDome), permet
de contourner ce blocage. Sans `curl_cffi`, le bot retombe sur `urllib`
(suffisant seulement sur une IP non bloquée, ex. GitHub Actions).

## Lancer en local

```bash
# Test à blanc (n'envoie rien, affiche les deals trouvés) :
DRY_RUN=true FULL_SCAN=true MAX_PRODUCTS=200 python3 micromania_deals.py

# Run réel avec alertes Discord :
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxx/yyy" \
  python3 micromania_deals.py
```

## Automatisation (GitHub Actions)

Le workflow [`.github/workflows/micromania-deals.yml`](.github/workflows/micromania-deals.yml)
tourne **toutes les 30 minutes** sans intervention.

Ajoute tes secrets dans **Settings → Secrets and variables → Actions** :

| Secret | Requis | Description |
|--------|:---:|-------------|
| `DISCORD_WEBHOOK_URL` | au choix | URL de webhook d'un salon Discord |
| `TELEGRAM_BOT_TOKEN`  | au choix | Token d'un bot Telegram (via @BotFather) |
| `TELEGRAM_CHAT_ID`    | au choix | ID du chat/canal Telegram cible |

> Configure au moins **un** canal (Discord *ou* Telegram). Sinon les deals sont
> seulement journalisés dans les logs du run et dans `state/deals.log`.

### Créer un webhook Discord
Salon → *Modifier le salon* → *Intégrations* → *Webhooks* → *Nouveau webhook* →
copie l'URL.

### Créer un bot Telegram
Parle à [@BotFather](https://t.me/BotFather) → `/newbot` → récupère le token.
Pour le `chat_id`, envoie un message au bot puis ouvre
`https://api.telegram.org/bot<TOKEN>/getUpdates`.

## Réglages (variables d'environnement)

| Variable | Défaut | Rôle |
|----------|--------|------|
| `DISCOUNT_THRESHOLD` | `0.50` | Seuil de réduction (0.50 = -50 %) |
| `MIN_REFERENCE_PRICE` | `50` | Prix de référence minimum (€) |
| `INITIAL_WINDOW_HOURS` | `24` | Fenêtre du tout premier run (h) |
| `MAX_PRODUCTS` | `5000` | Garde-fou : fiches max par run |
| `CONCURRENCY` | `8` | Téléchargements parallèles |
| `INCLUDE_USED` | `false` | Inclure les produits d'occasion |
| `INCLUDE_PRECOMMANDE` | `false` | Inclure les précommandes |
| `INCLUDE_UNAVAILABLE` | `false` | Inclure les produits en rupture (« Créer une alerte ») |
| `FULL_SCAN` | `false` | Forcer un scan complet (ignore `lastmod`) |
| `LOOP_ENABLED` | `false` | `true` = boucle à deux vitesses |
| `LOOP_INTERVAL_SECONDS` | `60` | Pause entre deux passages rapides (packs/collectors) |
| `FULL_CATALOG_EVERY_MINUTES` | `30` | Fréquence du scan complet du catalogue (`0` = à chaque passage) |
| `LOOP_MAX_SECONDS` | `19800` | Durée max d'un run en boucle (~5h30) |
| `ALERT_MIN_INTERVAL` | `0.4` | Espacement min. entre 2 alertes (anti rate-limit) |
| `EXTRA_CATEGORIES` | `tous-nos-packs` | Catégories en plus du sitemap (packs hors sitemap), slugs séparés par `,` |
| `CATEGORY_SZ` | `1000` | Nombre de produits demandés par page catégorie |
| `PACK_ID_ENUM` | `true` | Énumérer les packs par ID (`/mbN.html`) — capte les packs éphémères |
| `PACK_ID_MAX` | `0` | ID de pack max à sonder (`0` = auto : max connu + buffer) |
| `PACK_ID_BUFFER` | `40` | Marge d'IDs à sonder au-delà du max connu |
| `DRY_RUN` | `false` | N'envoie aucune alerte, affiche seulement |

## Couverture & limites

- ✅ **Tout produit ayant une fiche `/p/...`** est couvert : jeux, éditions
  collector, accessoires, figurines… y compris les **nouveautés**.
- ✅ On n'alerte que les produits **réellement disponibles** (`dispoweb=1`),
  jamais ceux en rupture / « Créer une alerte ».
- ✅ **Collectors / éditions limitées / exclusivités / collectibles premium** :
  **revérifiés en priorité** à chaque passage rapide via les catégories
  `jeux-video-edition-collector`, `exclusivites-micromania`,
  `exclusivites-premium` et `produits-derives-premium` (statues/figurines
  chères) — `EXTRA_CATEGORIES`. Ce sont les produits qui s'arrachent à la
  revente, donc ceux où une erreur de prix vaut le plus le coup.
- ⚡ **Boucle à deux vitesses** : le `lastmod` du sitemap n'étant pas fiable
  (sitemap régénéré quelques fois/jour seulement), la boucle fait :
  - un **passage RAPIDE** très fréquent (~2 min) sur les sources sensibles —
    **packs (énumérés par ID), collectors, exclusivités** — léger (~95 s) ;
  - un **scan COMPLET du catalogue** toutes les `FULL_CATALOG_EVERY_MINUTES`
    (30 min par défaut, ~6-7 min à `CONCURRENCY=24`), sans dépendre du `lastmod`.
- ✅ **Packs hors sitemap, y compris éphémères** : les **PACKS** (en
  `/...-mbNNN.html`) ne sont pas dans le sitemap, et certains packs flash ne
  sont **listés nulle part**. On les capte de deux façons :
  1. en scannant la/les catégorie(s) `EXTRA_CATEGORIES` (`tous-nos-packs`) ;
  2. surtout, par **énumération d'IDs** (`PACK_ID_ENUM`) : `/mbN.html` redirige
     vers la fiche du pack, donc on sonde toute la plage `mb1..mbMAX` et on
     découvre **tout pack ayant un ID**, même jamais listé / éphémère (c'était
     le cas du « Pack Doom », `mb498`). En pratique cela trouve ~2× plus de
     packs que la catégorie seule.
- ❌ **Limite** : les pages « Bonnes Affaires » de Micromania sont **curées
  manuellement** et ne listent pas tous les produits réellement remisés — on ne
  peut donc pas s'en servir comme raccourci ; le scan des fiches reste nécessaire.

> ⚖️ **Compromis charge/vitesse** : scanner tout le catalogue en continu = ~20-25
> requêtes/s en permanence vers Micromania. C'est le prix d'une détection rapide
> et exhaustive. Pour alléger, augmente `LOOP_INTERVAL_SECONDS` ou passe en
> `LOOP_INCREMENTAL=true` (plus léger mais moins réactif).

Pour rendre la détection plus stricte (uniquement les erreurs de prix
évidentes), monte le seuil : `DISCOUNT_THRESHOLD=0.70`.

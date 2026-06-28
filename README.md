# Outils de veille e-commerce

Ce dépôt contient deux outils indépendants :

1. **[Analyseur de demande Vinted](#analyseur-de-demande-vinted-)** 🛍️ — savoir
   ce qui est le plus recherché sur Vinted (favoris/vues) et à quel prix ça se
   revend.
2. **[Micromania deals watcher](#micromania-deals-watcher-)** 🔥 — alerte sur les
   grosses réductions / erreurs de prix sur micromania.fr.

---

# Analyseur de demande Vinted 🛍️

Pour une liste de recherches (produits, marques, modèles…), interroge le
catalogue Vinted et **classe ce qui est le plus recherché** — mesuré par le
nombre de **favoris (likes)** et, si tu fournis ta session connectée, de
**vues**. Tu vois d'un coup d'œil ce qui s'arrache, à quel **prix médian** ça se
revend, et combien il y a de concurrence (nombre d'annonces).

## Ce que ça donne

```
CLASSEMENT DE LA DEMANDE VINTED (le plus recherché en premier)
 #  recherche                   demande  fav.moy vues.moy annonces   prix méd
 1  stanley cup                    35.3     35.3        —      192    30,50 €
 2  jordan 1                       17.1     17.1        —      192    45,00 €
 3  labubu                         10.4     10.4        —      192     5,00 €

TOP ARTICLES LES PLUS CONVOITÉS (favoris / vues)
 1. ❤241  👁  —   28,00 €  Stanley Cup rosa            https://www.vinted.fr/items/...
```

Le détail complet est aussi écrit en **JSON** (`state/vinted_report.json`) et en
**CSV** (`state/vinted_report.csv`) pour analyse dans un tableur.

## Comment ça marche

1. Pour chaque recherche, lit le catalogue via l'API JSON de Vinted
   (`/api/v2/catalog/items`) — chaque annonce expose son nombre de **favoris**.
2. Agrège par recherche : favoris moyens, **prix médian** (= prix de revente
   réaliste), nombre d'annonces, et un **indice de demande**
   `= favoris + 0,05·vues`.
3. Sort le classement dans la console + JSON + CSV, et (option) un digest
   Discord/Telegram.

> 📌 **À propos des vues.** Vinted n'expose plus les vues en accès anonyme
> (catalogue et fiche renvoient 0 / 404). Le classement se base donc sur les
> **favoris**, qui sont un excellent indicateur de demande. Pour récupérer aussi
> les vues, fournis ta **session connectée** via `VINTED_COOKIE` et mets
> `FETCH_VIEWS=true` (voir réglages).

## Définir ta watchlist

Édite [`watchlist.txt`](watchlist.txt) (une recherche par ligne, `#` = commentaire) :

```
nike air max
jordan 1
stanley cup
labubu
```

Ou passe-les en variable d'environnement : `VINTED_QUERIES="nike air max,jordan 1"`.

## Lancer en local

```bash
pip install -r requirements.txt

# Analyse à blanc (n'envoie aucun digest) :
DRY_RUN=true VINTED_QUERIES="jordan 1,stanley cup" python3 vinted_analyzer.py

# Avec digest Discord :
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxx/yyy" python3 vinted_analyzer.py
```

## Automatisation (GitHub Actions)

Le workflow [`.github/workflows/vinted-demand.yml`](.github/workflows/vinted-demand.yml)
tourne **toutes les 6 heures**, écrit le rapport dans `state/` et envoie le
digest. Ajoute tes secrets dans **Settings → Secrets and variables → Actions** :
`DISCORD_WEBHOOK_URL` (ou `TELEGRAM_*`), et `VINTED_COOKIE` si tu veux les vues.

## Réglages (variables d'environnement)

| Variable | Défaut | Rôle |
|----------|--------|------|
| `VINTED_QUERIES` | *(vide)* | Recherches séparées par des virgules (prioritaire sur le fichier) |
| `WATCHLIST_FILE` | `watchlist.txt` | Fichier de recherches (une par ligne) |
| `VINTED_DOMAIN` | `www.vinted.fr` | Domaine Vinted ciblé |
| `MAX_PAGES` | `3` | Pages de 96 articles lues par recherche |
| `CATALOG_ORDER` | `relevance` | Tri : `relevance` / `newest_first` / `price_low_to_high`… |
| `PRICE_FROM` / `PRICE_TO` | *(vide)* | Filtre de prix appliqué à toutes les recherches |
| `FETCH_VIEWS` | `false` | Récupérer les vues (nécessite `VINTED_COOKIE`) |
| `VINTED_COOKIE` | *(vide)* | En-tête `Cookie` de ta session connectée (débloque les vues) |
| `VINTED_ACCESS_TOKEN` | *(vide)* | Alternative : jeton Bearer de session |
| `TOP_VIEWS` | `20` | Articles enrichis en vues par recherche |
| `VIEW_WEIGHT` | `0.05` | Poids des vues dans l'indice de demande |
| `TOP_ITEMS` | `25` | Taille du top d'articles affiché |
| `CONCURRENCY` | `4` | Requêtes parallèles |
| `PROXY` | *(vide)* | Proxy (idéalement résidentiel) pour contourner DataDome sur VPS |
| `LOOP_ENABLED` | `false` | Boucle continue (service systemd) |
| `LOOP_INTERVAL_SECONDS` | `3600` | Pause entre deux analyses en boucle |
| `REPORT_JSON` / `REPORT_CSV` | `state/vinted_report.*` | Chemins des rapports |
| `DRY_RUN` | `false` | N'envoie aucun digest |

> ⚠️ Comme Micromania, Vinted est derrière **DataDome**. Sur une IP datacenter
> (VPS), installe `curl_cffi` (déjà dans `requirements.txt`) et/ou utilise un
> **proxy résidentiel** (`PROXY=...`). Sur GitHub Actions, l'IP passe sans proxy.

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

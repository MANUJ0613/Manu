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

Aucune dépendance : que de la bibliothèque standard Python 3.

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
| `FULL_SCAN` | `false` | Scanner tout le catalogue (ignore `lastmod`) |
| `FULL_SCAN_EVERY_HOURS` | `6` | En boucle : fréquence d'un scan complet de rattrapage |
| `LOOP_INTERVAL_SECONDS` | `0` | >0 = mode boucle, intervalle entre 2 scans |
| `ALERT_MIN_INTERVAL` | `0.4` | Espacement min. entre 2 alertes (anti rate-limit) |
| `DRY_RUN` | `false` | N'envoie aucune alerte, affiche seulement |

## Couverture & limites

- ✅ **Tout produit ayant une fiche `/p/...`** est couvert : jeux, éditions
  collector, accessoires, figurines… y compris les **nouveautés** (le sitemap
  est re-téléchargé à chaque passage, les nouvelles fiches ont un `lastmod`
  récent et sont donc scannées).
- ✅ Les **erreurs de prix** sur un produit existant sont détectées : soit via
  le `lastmod` mis à jour, soit via le **scan complet périodique**
  (`FULL_SCAN_EVERY_HOURS`) qui balaie tout le catalogue sans dépendre du
  `lastmod`.
- ✅ On n'alerte que les produits **réellement disponibles** (`dispoweb=1`),
  jamais ceux en rupture / « Créer une alerte ».
- ❌ **Limite** : les **packs/bundles construits au panier** (ex. « Pack jeu +
  réplique » à prix combiné) ne sont **pas** des fiches produit et n'existent
  pas dans le sitemap — ils ne peuvent donc pas être détectés par cette
  méthode.

Pour rendre la détection plus stricte (uniquement les erreurs de prix
évidentes), monte le seuil : `DISCOUNT_THRESHOLD=0.70`.

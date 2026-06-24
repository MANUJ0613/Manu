# 🐊 Rédacteur de commentaires — bons plans

Petite application **100 % locale** (HTML/CSS/JS, aucune dépendance, aucun
serveur) qui t'aide à **préparer** un commentaire pour un post de bon plan.

> ⚠️ **Elle ne publie rien automatiquement.** Elle se contente d'analyser le
> texte que tu colles et de te proposer des brouillons. C'est **toi** qui copies
> et colles le commentaire sur Facebook. C'est volontaire : l'automatisation des
> commentaires viole les règles de Facebook et fait bannir les comptes. Ici, tu
> restes humain dans la boucle — juste plus rapide.

## Ce que ça fait

1. Tu colles le texte complet d'un post dans **Texte complet du post**.
2. Tu cliques sur **Préparer mon commentaire**.
3. L'app extrait automatiquement : **sujet**, **prix**, **prix barré**,
   **boutique**, **lien** et **infos utiles** (réduction %, stock limité,
   erreur de prix, offre limitée…).
4. Elle remplit les champs (modifiables à la main si l'extraction se trompe).
5. Elle génère **3 commentaires courts** selon le ton choisi :
   **Utile**, **Question**, **Enthousiaste** ou **Mélange**.
6. **Copier** chaque commentaire, ou **Copier le meilleur** (celui jugé le plus
   crédible : cite une vraie info, longueur naturelle).
7. Tu colles toi-même sur Facebook.
8. Tu peux noter **Marquer liké** / **Marquer commenté**.
9. L'app garde un **historique local** (navigateur) : posts suivis, likes et
   commentaires marqués.

## Pourquoi les commentaires sont « plus naturels »

Plutôt qu'un seul modèle de phrase répété, le générateur :

- assemble aléatoirement **ouvertures + corps + clôtures** depuis des banques
  d'expressions FR par ton ;
- **injecte les vraies infos** (prix, sujet, boutique) quand elles existent,
  pour des commentaires crédibles et non génériques ;
- **varie les emojis** (souvent aucun — plus crédible) ;
- garantit **3 sorties différentes** à chaque fois ;
- le bouton **🔁 Régénérer** repropose 3 variantes inédites.

> Reste toi-même : relis et personnalise avant de poster. Le meilleur moyen de
> devenir VIP d'un groupe, c'est d'apporter de **vrais** retours (un achat, un
> code qui marche, une comparaison) — pas de coller le même message partout.

## Lancer

Aucune installation. Ouvre simplement `index.html` dans ton navigateur
(double-clic), ou sers le dossier :

```bash
cd redacteur-commentaires
python3 -m http.server 8000
# puis ouvre http://localhost:8000
```

## Fichiers

| Fichier | Rôle |
|---------|------|
| `index.html` | structure de la page |
| `styles.css` | mise en forme |
| `app.js` | extraction + génération + historique local |
| `README.md` | ce fichier |

Tout reste dans ton navigateur (`localStorage`). Rien n'est envoyé en ligne.

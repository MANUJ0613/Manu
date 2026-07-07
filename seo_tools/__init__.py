"""Outils SEO pour la revente sur Leboncoin et Vinted.

Ce package regroupe la logique métier de l'application :
- db          : stockage SQLite (annonces, ventes, réglages)
- claude_client : génération de titre + description via l'API Claude
- dataforseo  : vrais volumes de recherche Google (mots-clés)
- pricing     : calcul prix conseillé, marge et frais par plateforme
- notify      : alertes push via ntfy
- slots       : détection des meilleurs créneaux à partir de tes stats
"""

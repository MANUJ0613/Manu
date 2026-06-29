#!/usr/bin/env python3
"""
Bot Discord interactif pour l'analyseur Vinted.

Tu tapes une commande dans ton salon Discord et le bot répond avec la fiche :
    /vinted <produit>     → vérif revente (annonces, favoris, prix, verdict)
    /marques <id_cat>     → top marques d'une catégorie (favoris)
    /deals                → affaires du moment (sous le prix du marché)
ou en préfixe : !vinted <produit>, !marques <id>, !deals

⚠️ Contrairement au webhook (à sens unique), ce bot ÉCOUTE le salon : il doit
donc tourner en CONTINU (VPS, PC allumé, hébergeur type Railway/Render…).

Prérequis :
  1. Créer une application + bot sur https://discord.com/developers/applications
  2. Activer "MESSAGE CONTENT INTENT" (onglet Bot) pour les commandes en !préfixe
  3. Inviter le bot sur ton serveur (OAuth2 → URL Generator → scopes: bot,
     applications.commands ; permissions: Send Messages, Embed Links)
  4. Lancer :  DISCORD_BOT_TOKEN="xxxx" python3 vinted_bot.py
     (dépendances :  pip install -r requirements-bot.txt)

Réutilise toute la logique d'analyse de vinted_analyzer.py.
"""

from __future__ import annotations

import asyncio
import os
import sys

import discord
from discord import app_commands
from discord.ext import commands

import vinted_analyzer as va

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
# Optionnel : ID de serveur (guild) pour une synchro instantanée des commandes /.
GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "").strip()

intents = discord.Intents.default()
intents.message_content = True  # nécessaire pour les commandes en !préfixe
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# --------------------------------------------------------------------------- #
# Helpers : exécuter l'analyse (bloquante) sans figer le bot, et formater.
# --------------------------------------------------------------------------- #

async def _run(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func, *args)


def _color(emoji: str) -> int:
    return (0x2ECC71 if emoji == "🟢"
            else 0xF1C40F if emoji == "🟡" else 0xE74C3C)


def _bar(score: int) -> str:
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)


def check_embed(r: dict) -> discord.Embed:
    v = r["verdict"]
    score = r.get("score", 0)
    e = discord.Embed(
        title=f"{v['emoji']} {r['query']} — {v['label']}"[:256],
        description=f"**Score revente : {score}/100**  `{_bar(score)}`",
        color=_color(v["emoji"]),
    )
    vel = (f"≈ **{r['velocity']:.0f}** annonces/jour\n(rythme de vente estimé)"
           if r.get("velocity") else "indéterminé")
    e.add_field(name="📦 Offre (concurrence)",
                value=f"**{va._fmt_total(r['n_total'])}** annonces\n_{v['note']}_",
                inline=True)
    e.add_field(name="🔁 Écoulement", value=vel, inline=True)
    e.add_field(
        name="❤️ Demande",
        value=(f"**{(r['avg_favourites'] or 0):.1f}** favoris/annonce\n"
               f"max {r['max_favourites']} · {r.get('pct_hot', 0)}% à >10 ❤"),
        inline=True,
    )
    e.add_field(
        name="💰 Prix : achat → revente",
        value=(f"Achat malin **{va._euro(r.get('p25_price'))}**\n"
               f"Revente (médian) **{va._euro(r['median_price'])}**\n"
               f"Haut de marché {va._euro(r.get('p75_price'))}\n"
               f"➡️ marge potentielle **~{va._euro(r.get('margin'))}**"),
        inline=False,
    )
    conds = r.get("conditions") or {}
    if conds:
        e.add_field(
            name="📦 Par état",
            value="\n".join(
                f"**{c}** — {d['n']} ann. · méd. {va._euro(d['median'])}"
                for c, d in list(conds.items())[:4]
            )[:1024],
            inline=False,
        )
    top = sorted(r.get("all_items") or [], key=lambda i: i["favourites"], reverse=True)[:5]
    if top:
        e.add_field(
            name="🔝 Les plus likées (la cote du marché)",
            value="\n".join(
                f"❤{it['favourites']} · {va._euro(it['price'])} — "
                f"[{it['title'][:38]}]({it['url']})" for it in top
            )[:1024],
            inline=False,
        )
    e.set_footer(text="Vinted — vérif revente · score = demande + écoulement")
    return e


def brands_embed(cat_id: str) -> discord.Embed:
    agg = va.scan_brands_in({"id": cat_id, "title": f"cat {cat_id}"})
    rows = sorted(
        ((b, n, f) for b, (n, f) in agg.items() if n >= va.BRAND_MIN_LISTINGS),
        key=lambda x: -x[2],
    )[:20]
    if not rows:
        return discord.Embed(title="Aucune marque trouvée", color=0xE74C3C)
    body = "\n".join(
        f"**{i}.** {b} — ❤{f} ({n} ann.)" for i, (b, n, f) in enumerate(rows, 1)
    )
    return discord.Embed(title=f"🏷️ Top marques — catégorie {cat_id}",
                         description=body[:4000], color=0x09B1BA)


# --------------------------------------------------------------------------- #
# Commandes SLASH (/vinted …)
# --------------------------------------------------------------------------- #

@bot.tree.command(name="vinted", description="Vérif revente d'un produit sur Vinted")
@app_commands.describe(produit="Le produit à vérifier (ex: iphone 13)")
async def vinted_slash(interaction: discord.Interaction, produit: str):
    await interaction.response.defer(thinking=True)
    r = await _run(va.analyze_query, produit)
    if not r:
        await interaction.followup.send(f"Aucun résultat pour « {produit} ».")
        return
    await interaction.followup.send(embed=check_embed(r))


@bot.tree.command(name="marques", description="Top marques d'une catégorie Vinted")
@app_commands.describe(categorie="ID de catégorie Vinted (ex: 1499 = jouets)")
async def marques_slash(interaction: discord.Interaction, categorie: str):
    await interaction.response.defer(thinking=True)
    e = await _run(brands_embed, categorie)
    await interaction.followup.send(embed=e)


# --------------------------------------------------------------------------- #
# Commandes PRÉFIXE (!vinted …) — repli si tu préfères taper sans slash.
# --------------------------------------------------------------------------- #

@bot.command(name="vinted")
async def vinted_cmd(ctx: commands.Context, *, produit: str):
    async with ctx.typing():
        r = await _run(va.analyze_query, produit)
    if not r:
        await ctx.reply(f"Aucun résultat pour « {produit} ».")
        return
    await ctx.reply(embed=check_embed(r))


@bot.command(name="marques")
async def marques_cmd(ctx: commands.Context, categorie: str):
    async with ctx.typing():
        e = await _run(brands_embed, categorie)
    await ctx.reply(embed=e)


@bot.event
async def on_ready():
    try:
        if GUILD_ID:
            g = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=g)
            await bot.tree.sync(guild=g)
        else:
            await bot.tree.sync()
    except Exception as err:  # noqa: BLE001
        print(f"[sync] échec: {err}", file=sys.stderr)
    print(f"✅ Bot connecté en tant que {bot.user} — tape /vinted <produit>")


def main() -> int:
    if not TOKEN:
        print("DISCORD_BOT_TOKEN manquant. Crée un bot sur "
              "https://discord.com/developers/applications puis exporte le token.",
              file=sys.stderr)
        return 1
    bot.run(TOKEN)
    return 0


if __name__ == "__main__":
    sys.exit(main())

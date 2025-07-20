### Główny plik main.py z integracją Supabase, logiką matchmakingu, rewanżem i obsługą Render ###

import discord
from discord.ext import commands
from discord import app_commands, ui, Interaction
import os
from dotenv import load_dotenv
from typing import Optional
from supabase_stats import get_player_stats, update_player_stats, get_all_stats
import asyncio
import aiohttp

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

active_matches = {}  # user_id: opponent_id
pending_results = {}  # match_key: wynik
confirmed_matches = set()  # para potwierdzonych meczy

### === MODAL: WPROWADZENIE WYNIKU === ###
class ScoreModal(ui.Modal, title="Wpisz wynik meczu"):
    score1 = ui.TextInput(label="Gole pierwszego gracza", style=discord.TextStyle.short)
    score2 = ui.TextInput(label="Gole drugiego gracza", style=discord.TextStyle.short)

    def __init__(self, match_info):
        super().__init__()
        self.match_info = match_info

    async def on_submit(self, interaction: Interaction):
        p1 = self.match_info["player1"]
        p2 = self.match_info["player2"]
        match_key = tuple(sorted((p1, p2)))

        if match_key in pending_results:
            await interaction.response.send_message("❌ Wynik już zgłoszony.", ephemeral=True)
            return

        try:
            s1 = int(self.score1.value)
            s2 = int(self.score2.value)
        except ValueError:
            await interaction.response.send_message("❌ Gole muszą być liczbami.", ephemeral=True)
            return

        if interaction.user.id not in (p1, p2):
            await interaction.response.send_message("❌ Nie jesteś graczem tego meczu.", ephemeral=True)
            return

        pending_results[match_key] = {
            "player1": p1,
            "player2": p2,
            "score1": s1,
            "score2": s2,
            "reported_by": interaction.user.id,
        }

        view = ConfirmView(pending_results[match_key])
        await interaction.response.send_message(
            f"Wynik zgłoszony: {s1} - {s2}\nDrugi gracz proszony o potwierdzenie.",
            view=view
        )

### === PRZYCISK POTWIERDZENIA WYNIKU === ###
class ConfirmView(ui.View):
    def __init__(self, match):
        super().__init__(timeout=None)
        self.match = match

    @ui.button(label="Potwierdź wynik", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id == self.match["reported_by"]:
            await interaction.response.send_message("❌ Musi potwierdzić drugi gracz.", ephemeral=True)
            return

        p1 = str(self.match["player1"])
        p2 = str(self.match["player2"])
        s1 = self.match["score1"]
        s2 = self.match["score2"]

        match_key = tuple(sorted((self.match["player1"], self.match["player2"])))
        pending_results.pop(match_key, None)
        confirmed_matches.add(match_key)

        update_player_stats(p1, goals_scored=s1, goals_conceded=s2)
        update_player_stats(p2, goals_scored=s2, goals_conceded=s1)

        if s1 > s2:
            update_player_stats(p1, wins=1)
            update_player_stats(p2, losses=1)
            msg = f"<@{p1}> wygrał z <@{p2}> {s1}-{s2}!"
        elif s2 > s1:
            update_player_stats(p2, wins=1)
            update_player_stats(p1, losses=1)
            msg = f"<@{p2}> wygrał z <@{p1}> {s2}-{s1}!"
        else:
            update_player_stats(p1, draws=1)
            update_player_stats(p2, draws=1)
            msg = f"Remis {s1}-{s2} między <@{p1}> a <@{p2}>."

        view = RematchView(player1=int(p1), player2=int(p2))
        await interaction.response.send_message(f"✅ Wynik potwierdzony! {msg}\nKliknij, aby zagrać rewanż:", view=view)

### === PRZYCISK REWANŻU Z AKCEPTACJĄ === ###
class RematchView(ui.View):
    def __init__(self, player1, player2):
        super().__init__(timeout=60)
        self.player1 = player1
        self.player2 = player2

    @ui.button(label="Rewanż", style=discord.ButtonStyle.blurple)
    async def rematch(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id not in (self.player1, self.player2):
            await interaction.response.send_message("❌ Tylko gracze meczu mogą zainicjować rewanż.", ephemeral=True)
            return

        opponent = self.player2 if interaction.user.id == self.player1 else self.player1

        # Wyślij prośbę o akceptację rewanżu do przeciwnika
        view = RematchAcceptView(challenger=interaction.user.id, opponent=opponent)
        await interaction.response.send_message(
            f"<@{opponent}>, <@{interaction.user.id}> zaproponował rewanż. Kliknij, aby zaakceptować.",
            view=view,
            ephemeral=False
        )

### === PRZYCISK AKCEPTACJI REWANŻU === ###
class RematchAcceptView(ui.View):
    def __init__(self, challenger: int, opponent: int, timeout=60):
        super().__init__(timeout=timeout)
        self.challenger = challenger
        self.opponent = opponent

    @ui.button(label="Akceptuj rewanż", style=discord.ButtonStyle.success)
    async def accept_rematch(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.opponent:
            await interaction.response.send_message("❌ Tylko przeciwnik może zaakceptować rewanż.", ephemeral=True)
            return

        # Dodaj do aktywnych meczów
        active_matches[self.challenger] = self.opponent
        active_matches[self.opponent] = self.challenger

        await interaction.response.send_message(
            f"🎮 Rewanż między <@{self.challenger}> a <@{self.opponent}> został zaakceptowany!\n"
            "Możecie wpisać wynik meczu.",
            view=ResultView(self.challenger, self.opponent)
        )

### === WIDOK WPISYWANIA WYNIKU === ###
class ResultView(ui.View):
    def __init__(self, p1, p2):
        super().__init__(timeout=None)
        self.match_info = {"player1": p1, "player2": p2}

    @ui.button(label="Wpisz wynik", style=discord.ButtonStyle.primary)
    async def enter_score(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_modal(ScoreModal(self.match_info))

### === KOMENDA /GRAM === ###
@bot.tree.command(name="gram", description="Szukaj przeciwnika")
@app_commands.describe(czas="Czas oczekiwania w minutach (domyślnie 3)")
async def gram(interaction: Interaction, czas: Optional[int] = 3):
    role = discord.utils.get(interaction.guild.roles, name="Gracz")
    if role is None:
        await interaction.response.send_message("Nie znaleziono roli 'Gracz'.", ephemeral=True)
        return

    view = ResultView(interaction.user.id, 0)  # Zastępowane potem po zaakceptowaniu
    await interaction.response.send_message(
        f"{role.mention}\n<@{interaction.user.id}> szuka przeciwnika! Kliknij przycisk, aby zaakceptować mecz.",
        view=MatchAcceptView(interaction.user.id, timeout=czas * 60)
    )

@ui.button(label="Potwierdź wynik", style=discord.ButtonStyle.green)
async def confirm_button(self, interaction: Interaction, button: ui.Button):
    if interaction.user.id == self.match["reported_by"]:
        await interaction.response.send_message("❌ Musi potwierdzić drugi gracz.", ephemeral=True)
        return

    p1 = str(self.match["player1"])
    p2 = str(self.match["player2"])
    s1 = self.match["score1"]
    s2 = self.match["score2"]

    match_key = tuple(sorted((self.match["player1"], self.match["player2"])))
    pending_results.pop(match_key, None)
    confirmed_matches.add(match_key)

    # **Tutaj dodajemy await!**
    await update_player_stats(p1, goals_scored=s1, goals_conceded=s2)
    await update_player_stats(p2, goals_scored=s2, goals_conceded=s1)

    if s1 > s2:
        await update_player_stats(p1, wins=1)
        await update_player_stats(p2, losses=1)
        msg = f"<@{p1}> wygrał z <@{p2}> {s1}-{s2}!"
    elif s2 > s1:
        await update_player_stats(p2, wins=1)
        await update_player_stats(p1, losses=1)
        msg = f"<@{p2}> wygrał z <@{p1}> {s2}-{s1}!"
    else:
        await update_player_stats(p1, draws=1)
        await update_player_stats(p2, draws=1)
        msg = f"Remis {s1}-{s2} między <@{p1}> a <@{p2}>."

    view = RematchView(player1=int(p1), player2=int(p2))
    await interaction.response.send_message(f"✅ Wynik potwierdzony! {msg}\nKliknij, aby zagrać rewanż:", view=view)


### === KOMENDY /STATYSTYKI I /RANKING === ###
@bot.tree.command(name="statystyki", description="Sprawdź swoje statystyki")
async def statystyki(interaction: Interaction):
    stats = await get_player_stats(str(interaction.user.id))  # <--- tu await
    embed = discord.Embed(title=f"Statystyki {interaction.user.display_name}", color=discord.Color.blue())
    embed.add_field(name="Wygrane", value=str(stats["wins"]))
    embed.add_field(name="Przegrane", value=str(stats["losses"]))
    embed.add_field(name="Remisy", value=str(stats["draws"]))
    embed.add_field(name="Gole zdobyte", value=str(stats["goals_scored"]))
    embed.add_field(name="Gole stracone", value=str(stats["goals_conceded"]))
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ranking", description="Wyświetl ranking")
async def ranking(interaction: Interaction):
    data = await get_all_stats()  # pamiętaj, że get_all_stats jest async!
    # Sortujemy według wskaźnika wygranych (win ratio)
    def win_ratio(player):
        total_games = player["wins"] + player["losses"] + player["draws"]
        return player["wins"] / total_games if total_games > 0 else 0

    sorted_players = sorted(data, key=win_ratio, reverse=True)

    embed = discord.Embed(title="🏆 Ranking Graczy", color=discord.Color.gold())
    for i, player in enumerate(sorted_players[:10], 1):
        user = await bot.fetch_user(int(player["player_id"]))
        ratio = win_ratio(player)
        embed.add_field(
            name=f"#{i} {user.name}",
            value=f"✅ {player['wins']} 🟥 {player['losses']} 🤝 {player['draws']} | 🎯 {ratio:.1%}",
            inline=False
        )
    await interaction.response.send_message(embed=embed)
### === BOT ONLINE I SERWER DLA RENDERA === ###
@bot.event
async def on_ready():
    print(f"Zalogowano jako {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Zsynchronizowano {len(synced)} komend")
    except Exception as e:
        print(f"Błąd synchronizacji komend: {e}")

if __name__ == "__main__":
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        print("Błąd: Brak tokena w .env")
        exit(1)
    
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class DummyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'Discord bot is running.')

    def start_web_server():
        port = int(os.environ.get("PORT", 10000))
        server = HTTPServer(("0.0.0.0", port), DummyHandler)
        print(f"Fake web server running on port {port}")
        server.serve_forever()

    threading.Thread(target=start_web_server, daemon=True).start()

    bot.run(TOKEN)

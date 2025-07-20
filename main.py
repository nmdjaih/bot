import discord
from discord.ext import commands
from discord import app_commands, ui, Interaction
import os
import json
from dotenv import load_dotenv
from typing import Optional, cast
from flask import Flask
import threading

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Dane globalne ---
active_matches = {}
pending_results = {}
confirmed_matches = set()
stats_file = "stats.json"
stats = {}

def load_stats():
    global stats
    try:
        with open(stats_file, "r") as f:
            stats = json.load(f)
    except FileNotFoundError:
        stats = {}

def save_stats():
    with open(stats_file, "w") as f:
        json.dump(stats, f, indent=4)

load_stats()

def update_player_stats(user_id: str, wins=0, losses=0, draws=0, goals_scored=0, goals_conceded=0):
    s = stats.get(user_id, {
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "goals_scored": 0,
        "goals_conceded": 0,
    })
    s["wins"] += wins
    s["losses"] += losses
    s["draws"] += draws
    s["goals_scored"] += goals_scored
    s["goals_conceded"] += goals_conceded
    stats[user_id] = s

def get_player_stats(user_id: str):
    return stats.get(user_id, {
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "goals_scored": 0,
        "goals_conceded": 0,
    })

# --- UI Komponenty ---

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
            await interaction.response.send_message("❌ Wynik dla tego meczu jest już zgłoszony i czeka na potwierdzenie.", ephemeral=True)
            return

        try:
            s1 = int(self.score1.value)
            s2 = int(self.score2.value)
        except ValueError:
            await interaction.response.send_message("❌ Gole muszą być liczbą całkowitą.", ephemeral=True)
            return

        if interaction.user.id not in (p1, p2):
            await interaction.response.send_message("❌ Tylko gracze w meczu mogą wpisać wynik.", ephemeral=True)
            return

        pending_results[match_key] = {
            "player1": p1,
            "player2": p2,
            "score1": s1,
            "score2": s2,
            "reported_by": interaction.user.id,
            "confirmed_by": None,
        }

        view = ConfirmView(pending_results[match_key])
        await interaction.response.send_message(
            f"Wynik zgłoszony przez {interaction.user.mention}: {s1} - {s2}\nDrugi gracz, proszę potwierdź wynik klikając poniższy przycisk.",
            view=view,
            ephemeral=False,
        )

class EnterScoreButton(ui.Button):
    def __init__(self, match_info):
        super().__init__(label="Wpisz wynik", style=discord.ButtonStyle.primary)
        self.match_info = match_info

    async def callback(self, interaction: Interaction):
        if interaction.user.id not in (self.match_info["player1"], self.match_info["player2"]):
            await interaction.response.send_message("❌ Tylko gracze w meczu mogą wpisać wynik.", ephemeral=True)
            return

        modal = ScoreModal(self.match_info)
        await interaction.response.send_modal(modal)

class ConfirmView(ui.View):
    def __init__(self, match):
        super().__init__(timeout=None)
        self.match = match

    @ui.button(label="Potwierdź wynik", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id == self.match["reported_by"]:
            await interaction.response.send_message("❌ To ty zgłosiłeś wynik, musi potwierdzić drugi gracz.", ephemeral=True)
            return

        if interaction.user.id not in (self.match["player1"], self.match["player2"]):
            await interaction.response.send_message("❌ Nie bierzesz udziału w tym meczu.", ephemeral=True)
            return

        p1 = str(self.match["player1"])
        p2 = str(self.match["player2"])
        s1 = self.match["score1"]
        s2 = self.match["score2"]

        match_key = tuple(sorted((self.match["player1"], self.match["player2"])))

        if match_key in pending_results:
            del pending_results[match_key]

        confirmed_matches.add(match_key)

        update_player_stats(p1, goals_scored=s1, goals_conceded=s2)
        update_player_stats(p2, goals_scored=s2, goals_conceded=s1)

        if s1 > s2:
            update_player_stats(p1, wins=1)
            update_player_stats(p2, losses=1)
            result_text = f"<@{p1}> wygrał z <@{p2}> {s1}-{s2}!"
        elif s2 > s1:
            update_player_stats(p2, wins=1)
            update_player_stats(p1, losses=1)
            result_text = f"<@{p2}> wygrał z <@{p1}> {s2}-{s1}!"
        else:
            update_player_stats(p1, draws=1)
            update_player_stats(p2, draws=1)
            result_text = f"Remis {s1}-{s2} pomiędzy <@{p1}> a <@{p2}>."

        save_stats()

        if p1 in active_matches:
            del active_matches[p1]
        if p2 in active_matches:
            del active_matches[p2]

        await interaction.response.send_message(f"✅ Wynik potwierdzony i zapisany!\n{result_text}")
        self.stop()

class AcceptMatchView(ui.View):
    def __init__(self, challenger: discord.Member, timeout: float):
        super().__init__(timeout=timeout)
        self.challenger = challenger
        self.message: Optional[discord.Message] = None

    @ui.button(label="Akceptuj mecz", style=discord.ButtonStyle.success)
    async def accept_button(self, interaction: Interaction, button: ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
            return

        if interaction.user.id == self.challenger.id:
            await interaction.response.send_message("🙃 Nie możesz zagrać sam ze sobą!", ephemeral=True)
            return

        active_matches[str(self.challenger.id)] = {"opponent": str(interaction.user.id)}
        active_matches[str(interaction.user.id)] = {"opponent": str(self.challenger.id)}

        match_info = {
            "player1": self.challenger.id,
            "player2": interaction.user.id,
        }

        view = ui.View(timeout=None)
        view.add_item(EnterScoreButton(match_info))

        await interaction.response.send_message(
            f"✅ Mecz gotowy! <@{self.challenger.id}> vs <@{interaction.user.id}> 🔥\nKliknij przycisk poniżej, aby wpisać wynik po zakończeniu meczu.",
            view=view,
        )

        if str(self.challenger.id) in active_matches:
            if active_matches[str(self.challenger.id)].get("searching"):
                del active_matches[str(self.challenger.id)]

        self.stop()

    async def on_timeout(self):
        if self.message:
            for child in self.children:
                button = cast(ui.Button, child)
                button.disabled = True
            await self.message.edit(content="⌛ Czas na znalezienie przeciwnika minął.", view=self)

        if str(self.challenger.id) in active_matches:
            if active_matches[str(self.challenger.id)].get("searching"):
                del active_matches[str(self.challenger.id)]

@bot.tree.command(name="gram", description="Szukaj przeciwnika")
@app_commands.describe(czas="Czas oczekiwania w minutach (domyślnie 3)")
async def gram(interaction: Interaction, czas: Optional[int] = 3):
    user_id = str(interaction.user.id)
    if user_id in active_matches:
        await interaction.response.send_message("❌ Masz już aktywny lub oczekujący mecz.", ephemeral=True)
        return

    view = AcceptMatchView(interaction.user, timeout=czas * 60)
    message = await interaction.response.send_message(
        f"<@{user_id}> szuka przeciwnika! Kliknij przycisk aby zaakceptować mecz. Czas oczekiwania: {czas} minut.",
        view=view,
        ephemeral=False,
    )
    view.message = await interaction.original_response()
    active_matches[user_id] = {"searching": True}

@bot.tree.command(name="statystyki", description="Sprawdź swoje statystyki")
async def statystyki(interaction: Interaction):
    user_id = str(interaction.user.id)
    s = get_player_stats(user_id)
    embed = discord.Embed(title=f"Statystyki gracza {interaction.user.display_name}", color=discord.Color.blue())
    embed.add_field(name="Wygrane", value=str(s["wins"]))
    embed.add_field(name="Przegrane", value=str(s["losses"]))
    embed.add_field(name="Remisy", value=str(s["draws"]))
    embed.add_field(name="Gole zdobyte", value=str(s["goals_scored"]))
    embed.add_field(name="Gole stracone", value=str(s["goals_conceded"]))
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_ready():
    print(f"Zalogowano jako {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Zsynchronizowano {len(synced)} komend")
    except Exception as e:
        print(f"Błąd synchronizacji komend: {e}")

# Serwer Flask do oszukania Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot Discord dziala!"

def run_web():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web).start()

if __name__ == "__main__":
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        print("Błąd: Brak tokena w .env")
        exit(1)
    bot.run(TOKEN)

import discord
from discord.ext import commands
from discord import app_commands, ui, Interaction
import os
from typing import Optional, cast
import asyncio

# SUPABASE importy i setup
from supabase_stats import get_player_stats, upsert_player_stats

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Dane globalne ---
active_matches = {}  # user_id (int) -> {'opponent': user_id (int), 'searching': bool}
pending_results = {}  # tuple(player1, player2) -> wynik
confirmed_matches = set()  # set of tuples (player1, player2)

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
            await interaction.response.send_message(
                "❌ Wynik dla tego meczu jest już zgłoszony i czeka na potwierdzenie.",
                ephemeral=True,
            )
            return

        try:
            s1 = int(self.score1.value)
            s2 = int(self.score2.value)
        except ValueError:
            await interaction.response.send_message(
                "❌ Gole muszą być liczbą całkowitą.", ephemeral=True
            )
            return

        if interaction.user.id not in (p1, p2):
            await interaction.response.send_message(
                "❌ Tylko gracze w meczu mogą wpisać wynik.", ephemeral=True
            )
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
            f"Wynik zgłoszony przez {interaction.user.mention}: {s1} - {s2}\n"
            "Drugi gracz, proszę potwierdź wynik klikając poniższy przycisk.",
            view=view,
            ephemeral=False,
        )

class EnterScoreButton(ui.Button):
    def __init__(self, match_info):
        super().__init__(label="Wpisz wynik", style=discord.ButtonStyle.primary)
        self.match_info = match_info

    async def callback(self, interaction: Interaction):
        if interaction.user.id not in (self.match_info["player1"], self.match_info["player2"]):
            await interaction.response.send_message(
                "❌ Tylko gracze w meczu mogą wpisać wynik.", ephemeral=True
            )
            return

        modal = ScoreModal(self.match_info)
        await interaction.response.send_modal(modal)

class ConfirmView(ui.View):
    def __init__(self, match):
        super().__init__(timeout=None)
        self.match = match
        self.rematch_button = ui.Button(label="Zagraj rewanż", style=discord.ButtonStyle.secondary)
        self.rematch_button.callback = self.rematch_callback
        self.add_item(self.rematch_button)
        self.rematch_button.disabled = True  # na start wyłączony

    @ui.button(label="Potwierdź wynik", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id == self.match["reported_by"]:
            await interaction.response.send_message(
                "❌ To ty zgłosiłeś wynik, musi potwierdzić drugi gracz.",
                ephemeral=True,
            )
            return

        if interaction.user.id not in (self.match["player1"], self.match["player2"]):
            await interaction.response.send_message(
                "❌ Nie bierzesz udziału w tym meczu.", ephemeral=True
            )
            return

        p1 = str(self.match["player1"])
        p2 = str(self.match["player2"])
        s1 = self.match["score1"]
        s2 = self.match["score2"]

        match_key = tuple(sorted((self.match["player1"], self.match["player2"])))

        if match_key in pending_results:
            del pending_results[match_key]

        confirmed_matches.add(match_key)

        # --- SUPABASE: aktualizacja statystyk ---
        await upsert_player_stats(p1, goals_scored=s1, goals_conceded=s2)
        await upsert_player_stats(p2, goals_scored=s2, goals_conceded=s1)

        if s1 > s2:
            await upsert_player_stats(p1, wins=1)
            await upsert_player_stats(p2, losses=1)
            result_text = f"<@{p1}> wygrał z <@{p2}> {s1}-{s2}!"
        elif s2 > s1:
            await upsert_player_stats(p2, wins=1)
            await upsert_player_stats(p1, losses=1)
            result_text = f"<@{p2}> wygrał z <@{p1}> {s2}-{s1}!"
        else:
            await upsert_player_stats(p1, draws=1)
            await upsert_player_stats(p2, draws=1)
            result_text = f"Remis {s1}-{s2} pomiędzy <@{p1}> a <@{p2}>."

        # Usuń z aktywnych meczy
        if self.match["player1"] in active_matches:
            del active_matches[self.match["player1"]]
        if self.match["player2"] in active_matches:
            del active_matches[self.match["player2"]]

        # Odblokuj przycisk rewanżu
        self.rematch_button.disabled = False
        await interaction.response.edit_message(
            content=f"✅ Wynik potwierdzony i zapisany!\n{result_text}",
            view=self,
        )

    async def rematch_callback(self, interaction: Interaction):
        user_id = interaction.user.id
        p1 = self.match["player1"]
        p2 = self.match["player2"]

        if user_id not in (p1, p2):
            await interaction.response.send_message(
                "❌ Tylko gracze w meczu mogą rozpocząć rewanż.", ephemeral=True
            )
            return

        if p1 in active_matches or p2 in active_matches:
            await interaction.response.send_message(
                "❌ Jeden z graczy już szuka meczu.", ephemeral=True
            )
            return

        # Ustaw obaj jako aktywnych z przeciwnikiem i searching False, czyli mecz gotowy
        active_matches[p1] = {"searching": False, "opponent": p2}
        active_matches[p2] = {"searching": False, "opponent": p1}

        await interaction.response.send_message(
            f"🔁 <@{p1}> i <@{p2}> rozpoczęli rewanż! Powodzenia! 🔥", ephemeral=False
        )

        # Zablokuj przycisk rewanżu
        self.rematch_button.disabled = True
        await interaction.message.edit(view=self)

class AcceptMatchView(ui.View):
    def __init__(self, challenger: discord.Member, timeout: float):
        super().__init__(timeout=timeout)
        self.challenger = challenger
        self.message: Optional[discord.Message] = None

    @ui.button(label="Akceptuj mecz", style=discord.ButtonStyle.success)
    async def accept_button(self, interaction: Interaction, button: ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Ta komenda działa tylko na serwerze.", ephemeral=True
            )
            return

        if interaction.user.id == self.challenger.id:
            await interaction.response.send_message(
                "🙃 Nie możesz zagrać sam ze sobą!", ephemeral=True
            )
            return

        match_info = {
            "player1": self.challenger.id,
            "player2": interaction.user.id,
        }

        view = ui.View(timeout=None)
        view.add_item(EnterScoreButton(match_info))

        await interaction.response.send_message(
            f"✅ Mecz gotowy! <@{self.challenger.id}> vs <@{interaction.user.id}> 🔥\n"
            "Kliknij przycisk poniżej, aby wpisać wynik po zakończeniu meczu.",
            view=view,
        )

        self.stop()

    async def on_timeout(self):
        if self.message:
            for child in self.children:
                button = cast(ui.Button, child)
                button.disabled = True
            await self.message.edit(content="⌛ Czas na znalezienie przeciwnika minął.", view=self)

# --- Komendy ---

@bot.tree.command(name="gram", description="Znajdź przeciwnika do meczu")
async def gram(interaction: discord.Interaction):
    user_id = interaction.user.id

    if user_id in active_matches and active_matches[user_id]["searching"]:
        await interaction.response.send_message("Jesteś już w kolejce na mecz.", ephemeral=True)
        return

    # Szukaj przeciwnika, który też szuka
    opponent_id = None
    for uid, info in active_matches.items():
        if info["searching"] and uid != user_id:
            opponent_id = uid
            break

    if opponent_id:
        # Para graczy znalezionych, tworzymy mecz
        active_matches[user_id] = {"searching": False, "opponent": opponent_id}
        active_matches[opponent_id] = {"searching": False, "opponent": user_id}

        await interaction.response.send_message(
            f"🎉 Znaleziono przeciwnika: <@{opponent_id}>! Mecz gotowy, powodzenia! 🔥"
        )
    else:
        # Nikt nie szuka, dodaj siebie do kolejki
        active_matches[user_id] = {"searching": True, "opponent": None}
        view = AcceptMatchView(challenger=interaction.user, timeout=60)
        await interaction.response.send_message(
            "⏳ Szukam przeciwnika... (lub ktoś może zaakceptować twój mecz)",
            view=view,
        )
        view.message = await interaction.original_response()

@bot.tree.command(name="ranking", description="Pokaż ranking graczy")
async def ranking(interaction: discord.Interaction):
    stats = await get_player_stats()
    if not stats:
        await interaction.response.send_message("Brak statystyk w bazie.", ephemeral=True)
        return

    # Posortuj wg zwycięstw malejąco, potem remisów itd.
    sorted_stats = sorted(
        stats,
        key=lambda x: (x["wins"], x["draws"], -x["losses"]),
        reverse=True,
    )
    lines = []
    for i, player in enumerate(sorted_stats, start=1):
        lines.append(
            f"{i}. <@{player['player_id']}> - W: {player['wins']}, P: {player['losses']}, R: {player['draws']}, "
            f"Gole: {player['goals_scored']}-{player['goals_conceded']}"
        )

    await interaction.response.send_message("🏆 Ranking graczy:\n" + "\n".join(lines))

@bot.tree.command(name="statystyki", description="Pokaż swoje statystyki")
async def statystyki(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    stats = await get_player_stats(user_id)
    if not stats:
        await interaction.response.send_message("Nie masz jeszcze żadnych statystyk.", ephemeral=True)
        return

    p = stats[0]
    text = (
        f"📊 Statystyki dla <@{user_id}>:\n"
        f"Wygrane: {p['wins']}\n"
        f"Przegrane: {p['losses']}\n"
        f"Remisy: {p['draws']}\n"
        f"Gole zdobyte: {p['goals_scored']}\n"
        f"Gole stracone: {p['goals_conceded']}"
    )
    await interaction.response.send_message(text, ephemeral=True)

# --- Run ---
@bot.event
async def on_ready():
    print(f"Bot zalogowany jako {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Slash commands synced: {len(synced)}")
    except Exception as e:
        print(f"Błąd podczas synca slash commands: {e}")

if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    bot.run(TOKEN)

import discord
from discord.ext import commands
from discord import app_commands, ui, Interaction
import os
import json
from dotenv import load_dotenv
from typing import Optional
from keep_alive import keep_alive


load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Musi być włączone, żeby działał on_member_join

bot = commands.Bot(command_prefix="!", intents=intents)

active_request: Optional[discord.Member] = None
pending_match = None
pending_matches = {
}  # Tu trzymamy zgłoszone wyniki oczekujące na potwierdzenie
confirmed_matches = set()  # Tu można trzymać zatwierdzone mecze (opcjonalnie)
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


def get_player_stats(user_id: str):
    s = stats.get(user_id, {})
    wins = s.get("wins", s.get("wygrane", 0))
    losses = s.get("losses", s.get("przegrane", 0))
    draws = s.get("draws", 0)
    goals_scored = s.get("goals_scored", s.get("gole", 0))
    goals_conceded = s.get("goals_conceded", 0)

    return {
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "goals_scored": goals_scored,
        "goals_conceded": goals_conceded
    }


def update_player_stats(user_id: str,
                        wins=0,
                        losses=0,
                        draws=0,
                        goals_scored=0,
                        goals_conceded=0):
    s = stats.get(user_id, {
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "goals_scored": 0,
        "goals_conceded": 0
    })
    s["wins"] = s.get("wins", 0) + wins
    s["losses"] = s.get("losses", 0) + losses
    s["draws"] = s.get("draws", 0) + draws
    s["goals_scored"] = s.get("goals_scored", 0) + goals_scored
    s["goals_conceded"] = s.get("goals_conceded", 0) + goals_conceded
    stats[user_id] = s


class ScoreModal(ui.Modal, title="Wpisz wynik meczu"):
    score1 = ui.TextInput(label="Gole pierwszego gracza",
                          style=discord.TextStyle.short)
    score2 = ui.TextInput(label="Gole drugiego gracza",
                          style=discord.TextStyle.short)

    def __init__(self, match_info):
        super().__init__()
        self.match_info = match_info

    async def on_submit(self, interaction: Interaction):
        global pending_match, pending_matches

        p1 = self.match_info["player1"]
        p2 = self.match_info["player2"]

        match_key = tuple(sorted((p1, p2)))

        # Sprawdzamy, czy wynik dla tego meczu jest już zgłoszony i czeka na potwierdzenie
        if match_key in pending_matches:
            await interaction.response.send_message(
                "❌ Wynik dla tego meczu jest już zgłoszony i czeka na potwierdzenie. "
                "Poczekaj, aż drugi gracz potwierdzi ten wynik.",
                ephemeral=True)
            return

        try:
            s1 = int(self.score1.value)
            s2 = int(self.score2.value)
        except ValueError:
            await interaction.response.send_message(
                "❌ Gole muszą być liczbą całkowitą.", ephemeral=True)
            return

        if interaction.user.id not in (p1, p2):
            await interaction.response.send_message(
                "❌ Tylko gracze w meczu mogą wpisać wynik.", ephemeral=True)
            return

        pending_match = {
            "player1": p1,
            "player2": p2,
            "score1": s1,
            "score2": s2,
            "reported_by": interaction.user.id,
            "confirmed_by": None,
        }

        # Dodajemy zgłoszenie wyniku do pending_matches
        pending_matches[match_key] = pending_match

        view = ConfirmView(pending_match)
        await interaction.response.send_message(
            f"Wynik zgłoszony przez {interaction.user.mention}: {s1} - {s2}\n"
            "Drugi gracz, proszę potwierdź wynik klikając poniższy przycisk.",
            view=view)


class EnterScoreButton(ui.Button):

    def __init__(self, match_info):
        super().__init__(label="Wpisz wynik",
                         style=discord.ButtonStyle.primary)
        self.match_info = match_info

    async def callback(self, interaction: Interaction):
        if interaction.user.id not in (self.match_info["player1"],
                                       self.match_info["player2"]):
            await interaction.response.send_message(
                "❌ Tylko gracze w meczu mogą wpisać wynik.", ephemeral=True)
            return

        modal = ScoreModal(self.match_info)
        await interaction.response.send_modal(modal)


class ConfirmView(ui.View):

    def __init__(self, match):
        super().__init__(timeout=None)
        self.match = match

    @ui.button(label="Potwierdź wynik", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: Interaction,
                             button: ui.Button):
        global pending_match, pending_matches, confirmed_matches

        if interaction.user.id == self.match["reported_by"]:
            await interaction.response.send_message(
                "❌ To ty zgłosiłeś wynik, musi potwierdzić drugi gracz.",
                ephemeral=True)
            return

        if interaction.user.id not in (self.match["player1"],
                                       self.match["player2"]):
            await interaction.response.send_message(
                "❌ Nie bierzesz udziału w tym meczu.", ephemeral=True)
            return

        p1 = str(self.match["player1"])
        p2 = str(self.match["player2"])
        s1 = self.match["score1"]
        s2 = self.match["score2"]

        match_key = tuple(
            sorted((self.match["player1"], self.match["player2"])))

        # Usuwamy wynik z oczekujących zgłoszeń
        if match_key in pending_matches:
            del pending_matches[match_key]

        # Możesz dodać do confirmed_matches, jeśli chcesz (opcjonalne)
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
        pending_match = None

        await interaction.response.send_message(
            f"✅ Wynik potwierdzony i zapisany!\n{result_text}")
        self.stop()


class AkceptujView(ui.View):
    msg: Optional[discord.Message] = None

    def __init__(self, user: discord.Member, timeout: float):
        super().__init__(timeout=timeout)
        self.user = user

    @ui.button(label="Akceptuj mecz", style=discord.ButtonStyle.success)
    async def accept_button(self, interaction: Interaction, button: ui.Button):
        global active_request

        if interaction.user.id == self.user.id:
            await interaction.response.send_message(
                "🙃 Nie możesz zagrać sam ze sobą!", ephemeral=True)
            return

        match_info = {
            "player1": self.user.id,
            "player2": interaction.user.id,
        }

        view = ui.View(timeout=None)
        view.add_item(EnterScoreButton(match_info))

        await interaction.response.send_message(
            f"✅ Mecz gotowy! <@{self.user.id}> vs <@{interaction.user.id}> 🔥\n"
            "Kliknij przycisk poniżej, aby wpisać wynik po zakończeniu meczu.",
            view=view)

        active_request = None
        self.stop()

    async def on_timeout(self):
        global active_request
        if self.msg is not None:
            try:
                await self.msg.edit(
                    content="⌛ Czas na znalezienie przeciwnika minął.",
                    view=None)
            except Exception:
                pass
        active_request = None
        self.stop()


@bot.tree.command(name="gram", description="Szukaj przeciwnika")
@app_commands.describe(czas="Czas aktywności zapytania w minutach (1-5)")
async def gram(interaction: Interaction, czas: app_commands.Range[int, 1, 5]):
    global active_request

    if not interaction.guild:
        await interaction.response.send_message(
            "❌ Komenda działa tylko na serwerze.", ephemeral=True)
        return

    member = interaction.guild.get_member(interaction.user.id)
    if not member or not any(role.name == "Gracz" for role in member.roles):
        await interaction.response.send_message(
            "❌ Tylko osoby z rangą 'Gracz' mogą korzystać z tej komendy.",
            ephemeral=True)
        return

    if active_request is not None:
        await interaction.response.send_message(
            "⚠️ Ktoś już czeka na przeciwnika!", ephemeral=True)
        return

    active_request = member
    view = AkceptujView(member, timeout=czas * 60)

    # Znajdź rolę o nazwie "Gracz"
    role = discord.utils.get(interaction.guild.roles, name="Gracz")

    # Wyślij wiadomość pingując rolę
    await interaction.response.send_message(
        f"🎮 {member.mention} szuka przeciwnika! {role.mention}, kliknij przycisk poniżej, aby dołączyć.",
        view=view)

    view.msg = await interaction.original_response()


@bot.tree.command(name="statystyki", description="Sprawdź swoje statystyki")
async def statystyki(interaction: Interaction):
    user_id = str(interaction.user.id)
    s = get_player_stats(user_id)

    embed = discord.Embed(
        title=f"Statystyki gracza {interaction.user.display_name}",
        color=discord.Color.green())
    embed.add_field(name="Wygrane", value=str(s["wins"]))
    embed.add_field(name="Przegrane", value=str(s["losses"]))
    embed.add_field(name="Remisy", value=str(s["draws"]))
    embed.add_field(name="Gole zdobyte", value=str(s["goals_scored"]))
    embed.add_field(name="Gole stracone", value=str(s["goals_conceded"]))

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ranking",
                  description="Pokaż ranking graczy według win ratio")
async def ranking(interaction: Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    if not stats:
        await interaction.response.send_message("Brak danych o statystykach.",
                                                ephemeral=True)
        return

    ranking_list = []
    for user_id, s in stats.items():
        wins = s.get("wins", 0)
        losses = s.get("losses", 0)
        draws = s.get("draws", 0)
        total = wins + losses + draws
        if total == 0:
            continue
        win_ratio = wins / total
        ranking_list.append((user_id, win_ratio, wins, total))

    if not ranking_list:
        await interaction.response.send_message("Brak rozegranych meczów.",
                                                ephemeral=True)
        return

    ranking_list.sort(key=lambda x: (x[1], x[2]), reverse=True)

    top_n = 10
    top = ranking_list[:top_n]

    embed = discord.Embed(
        title="🏆 Ranking graczy (Win Ratio)",
        description=
        "Top 10 graczy według stosunku wygranych do rozegranych meczów",
        color=discord.Color.gold())

    for i, (user_id, ratio, wins, total) in enumerate(top, start=1):
        member = interaction.guild.get_member(
            int(user_id)) if interaction.guild else None
        name = member.display_name if member else f"<Nieznany gracz {user_id}>"
        embed.add_field(
            name=f"{i}. {name}",
            value=f"Win ratio: {ratio:.2%} | Wygrane: {wins} | Mecze: {total}",
            inline=False)

    await interaction.response.send_message(embed=embed)


# **TU DODAJĘ KOMENDĘ /clear Z WYMAGANIEM ROLI Admin**


@bot.tree.command(name="clear",
                  description="Usuń wiadomości (wymaga roli Admin)")
@app_commands.describe(liczba="Ilość wiadomości do usunięcia")
async def clear(interaction: discord.Interaction, liczba: int):
    if not interaction.guild:
        await interaction.response.send_message(
            "Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    member = interaction.guild.get_member(interaction.user.id)
    if not member or not any(role.name == "Admin" for role in member.roles):
        await interaction.response.send_message(
            "Nie masz uprawnień (rola Admin wymagana).", ephemeral=True)
        return

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "Ta komenda działa tylko w kanałach tekstowych.", ephemeral=True)
        return

    deleted = await channel.purge(limit=liczba + 1)
    await interaction.response.send_message(
        f"Usunięto {len(deleted)} wiadomości.", ephemeral=True)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Bot działa jako {bot.user}")


# Event on_member_join do nadawania roli "Gracz"
@bot.event
async def on_member_join(member: discord.Member):
    role_name = "Gracz"
    role = discord.utils.get(member.guild.roles, name=role_name)
    if role is None:
        print(
            f"❌ Nie znaleziono roli '{role_name}' na serwerze {member.guild.name}"
        )
        return

    try:
        await member.add_roles(role)
        print(f"✅ Nadano rolę '{role_name}' użytkownikowi {member.name}")
    except Exception as e:
        print(f"❌ Błąd nadawania roli: {e}")
keep_alive()


token = os.getenv("TOKEN")
if token is None:
    raise ValueError("Brak TOKEN w pliku .env lub w zmiennych środowiskowych!")
bot.run(token)

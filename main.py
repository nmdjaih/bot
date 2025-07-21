### Główny plik main.py z integracją Supabase, logiką matchmakingu, rewanżem i obsługą Render ###

import discord
from discord.ext import commands
from discord import app_commands, ui, Interaction
from discord.ui import View
import os
from dotenv import load_dotenv
from typing import Optional
from supabase_stats import get_player_stats, update_player_stats, get_all_stats
import asyncio
import aiohttp
from typing import Optional
from typing import cast
from datetime import timedelta

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

active_matches = {}  # user_id: opponent_id
pending_results = {}  # match_key: wynik
confirmed_matches = set()  # para potwierdzonych meczy
tournaments = {}  # message_id: {name, limit, players}



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

        # Nie dodawaj więcej niż raz do pending_results
        if match_key in pending_results:
            await interaction.response.send_message("❌ Wynik już zgłoszony. Czekamy na potwierdzenie.", ephemeral=True)
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

        # Zapisz dane do pending_results tylko RAZ
        pending_results[match_key] = {
            "player1": p1,
            "player2": p2,
            "score1": s1,
            "score2": s2,
            "confirmed": False
        }

        view = ConfirmView(p1, p2, s1, s2, match_key)
        await interaction.response.send_message(
            f"Wynik zgłoszony: {s1} - {s2}. Drugi gracz proszony o potwierdzenie.",
            view=view
        )

#turniej#
class SignupView(discord.ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="Zapisz się", style=discord.ButtonStyle.primary)
    async def signup(self, interaction: Interaction, button: discord.ui.Button):
        message_id = self.message_id
        if message_id not in tournaments:
            await interaction.response.send_message("❌ Ten turniej nie istnieje lub został usunięty.", ephemeral=True)
            return

        tournament = tournaments[message_id]
        user_id = interaction.user.id

        if user_id in tournament["players"]:
            await interaction.response.send_message("⚠️ Już jesteś zapisany do tego turnieju.", ephemeral=True)
            return

        if len(tournament["players"]) >= tournament["limit"]:
            await interaction.response.send_message("⛔ Turniej jest już pełny.", ephemeral=True)
            return

        tournament["players"].append(user_id)
        remaining = tournament["limit"] - len(tournament["players"])
        zapisani = "\n".join(f"<@{uid}>" for uid in tournament["players"])

        embed = discord.Embed(
            title=f"🎮 Turniej: {tournament['name']}",
            description=f"Zapisani gracze:\n{zapisani}\n\nPozostało miejsc: **{remaining}**",
            color=discord.Color.green()
        )
        await interaction.message.edit(embed=embed)

        if remaining == 0:
            # Wyłącz przycisk
            button.disabled = True
            await interaction.message.edit(view=self)

            # Wyślij wiadomość o rozpoczęciu turnieju
            await interaction.channel.send(
                f"🏁 Zapisy do turnieju **{tournament['name']}** zostały zakończone! Turniej rozpoczyna się teraz!"
            )
        
        await interaction.response.send_message("✅ Zapisano do turnieju!", ephemeral=True)



class ConfirmView(View):
    def __init__(self, player1, player2, s1, s2, match_key):
        super().__init__(timeout=None)
        self.player1 = player1
        self.player2 = player2
        self.s1 = s1
        self.s2 = s2
        self.match_key = match_key

    @discord.ui.button(label="Potwierdź wynik", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: Interaction, button: discord.ui.Button):
        if interaction.user.id not in [self.player1, self.player2]:
            await interaction.response.send_message("❌ Nie jesteś uczestnikiem tego meczu!", ephemeral=True)
            return

        match_info = pending_results.get(self.match_key)
        if not match_info or match_info.get("confirmed", False):
            await interaction.response.send_message("❌ Ten wynik już został potwierdzony.", ephemeral=True)
            return

        # Aktualizacja statystyk
        p1_stats = {
            "wins": 1 if self.s1 > self.s2 else 0,
            "losses": 1 if self.s1 < self.s2 else 0,
            "draws": 1 if self.s1 == self.s2 else 0,
            "goals_scored": self.s1,
            "goals_conceded": self.s2,
        }
        p2_stats = {
            "wins": 1 if self.s2 > self.s1 else 0,
            "losses": 1 if self.s2 < self.s1 else 0,
            "draws": 1 if self.s2 == self.s1 else 0,
            "goals_scored": self.s2,
            "goals_conceded": self.s1,
        }

        await update_player_stats(str(self.player1), **p1_stats)
        await update_player_stats(str(self.player2), **p2_stats)
        pending_results[self.match_key]["confirmed"] = True

        if self.s1 > self.s2:
            msg = f"<@{self.player1}> wygrał z <@{self.player2}> {self.s1}-{self.s2}!"
        elif self.s2 > self.s1:
            msg = f"<@{self.player2}> wygrał z <@{self.player1}> {self.s2}-{self.s1}!"
        else:
            msg = f"🤝 Remis {self.s1}-{self.s2} między <@{self.player1}> a <@{self.player2}>."
        pending_results.pop(self.match_key, None)
        view = RematchView(self.player1, self.player2)
        await interaction.response.edit_message(content=msg + "\nKliknij poniżej, aby zagrać rewanż.", view=view)

    @discord.ui.button(label="Odrzuć wynik", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: Interaction, button: discord.ui.Button):
        if interaction.user.id not in [self.player1, self.player2]:
            await interaction.response.send_message("❌ Nie jesteś uczestnikiem tego meczu!", ephemeral=True)
            return

        if self.match_key in pending_results:
            del pending_results[self.match_key]

        view = RematchView(self.player1, self.player2)
        await interaction.response.edit_message(content="❌ Wynik został odrzucony. Możesz zgłosić wynik ponownie.", view=view)

    @discord.ui.button(label="Rewanż", style=discord.ButtonStyle.secondary)
    async def rematch(self, interaction: Interaction, button: discord.ui.Button):
        if interaction.user.id not in [self.player1, self.player2]:
            await interaction.response.send_message("❌ Tylko gracze mogą zainicjować rewanż.", ephemeral=True)
            return

        opponent = self.player2 if interaction.user.id == self.player1 else self.player1

        view = RematchAcceptView(challenger=interaction.user.id, opponent=opponent)
        await interaction.response.send_message(
            f"<@{opponent}>, <@{interaction.user.id}> zaproponował rewanż. Kliknij, aby zaakceptować.",
            view=view,
            ephemeral=False
        )


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

class MatchAcceptView(ui.View):
    def __init__(self, challenger_id: int, timeout: Optional[float] = 60):
        super().__init__(timeout=timeout)
        self.challenger_id = challenger_id
        self.message = None  # <- potrzebne do edytowania wiadomości po czasie

    @ui.button(label="Akceptuj mecz", style=discord.ButtonStyle.green)
    async def accept_match(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id == self.challenger_id:
            await interaction.response.send_message("❌ Nie możesz zaakceptować własnego meczu.", ephemeral=True)
            return

        # Dodajemy do active_matches obie strony
        active_matches[self.challenger_id] = interaction.user.id
        active_matches[interaction.user.id] = self.challenger_id

        # Wyłączamy przyciski po zaakceptowaniu
        for child in self.children:
            child.disabled = True

        # Edytujemy oryginalną wiadomość z widokiem (przyciskami)
        if self.message:
            await self.message.edit(content="✅ Mecz zaakceptowany!", view=self)

        # Odpowiadamy na interakcję i wysyłamy embed + widok do wpisania wyniku
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🏁 Mecz rozpoczęty!",
                description=f"<@{self.challenger_id}> vs <@{interaction.user.id}>. Po meczu kliknij 'Wpisz wynik'."
            ),
            view=ResultView(self.challenger_id, interaction.user.id)
        )

    async def on_timeout(self):
        if self.message:
            for child in self.children:
                button = cast(ui.Button, child)
                button.disabled = True
            await self.message.edit(content="⌛ Czas na znalezienie przeciwnika minął.", view=self)

        # Usuwamy z active_matches jeśli nadal szukał
        entry = active_matches.get(str(self.challenger_id))
        if isinstance(entry, dict) and entry.get("searching"):
            del active_matches[str(self.challenger_id)]

### === KOMENDY /STATYSTYKI I /RANKING === ###

@bot.tree.command(name="sprawdz", description="Pokaż Discord User ID wybranego użytkownika")
@app_commands.describe(uzytkownik="Użytkownik, którego ID chcesz zobaczyć")
async def sprawdz(interaction: discord.Interaction, uzytkownik: discord.User):
    # Sprawdzamy, czy wywołujący ma rolę 'Admin'
    role_names = [role.name for role in interaction.user.roles]
    if "Admin" not in role_names:
        await interaction.response.send_message("❌ Nie masz uprawnień, aby użyć tej komendy.", ephemeral=True)
        return

    # Jeśli jest adminem, pokazujemy user ID
    await interaction.response.send_message(f"User ID użytkownika {uzytkownik.mention} to `{uzytkownik.id}`", ephemeral=True)

@bot.tree.command(name="gram", description="Szukaj przeciwnika")
@app_commands.describe(czas="Czas oczekiwania w minutach (domyślnie 3)")
async def gram(interaction: Interaction, czas: Optional[int] = 3):
    role = discord.utils.get(interaction.guild.roles, name="Gracz")
    if role is None:
        await interaction.response.send_message("Nie znaleziono roli 'Gracz'.", ephemeral=True)
        return

    # Utwórz widok z timeoutem
    view = MatchAcceptView(interaction.user.id, timeout=czas * 60)

    # Wyślij wiadomość z przyciskiem
    msg = await interaction.response.send_message(
        f"{role.mention}\n<@{interaction.user.id}> szuka przeciwnika! Kliknij przycisk, aby zaakceptować mecz.",
        view=view
    )

    # Pobierz wiadomość, żeby przypisać do widoku (potrzebne do on_timeout)
    view.message = await interaction.original_response()

    # Zapisz gracza jako "szukającego"
    active_matches[str(interaction.user.id)] = {"searching": True}


@bot.tree.command(name="statystyki", description="Sprawdź swoje lub cudze statystyki")
@app_commands.describe(uzytkownik="Gracz, którego statystyki chcesz sprawdzić")
async def statystyki(interaction: Interaction, uzytkownik: Optional[discord.User] = None):
    user = uzytkownik or interaction.user
    stats = await get_player_stats(str(user.id))

    total_matches = stats["wins"] + stats["losses"] + stats["draws"]
    win_rate = round((stats["wins"] / total_matches) * 100, 1) if total_matches > 0 else 0.0
    avg_goals_scored = round(stats["goals_scored"] / total_matches, 2) if total_matches > 0 else 0.0
    avg_goals_conceded = round(stats["goals_conceded"] / total_matches, 2) if total_matches > 0 else 0.0

    embed = discord.Embed(
        title=f"📊 Statystyki {user.display_name}",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=user.display_avatar.url)

    embed.add_field(name="✅ Wygrane", value=str(stats["wins"]))
    embed.add_field(name="➗ Remisy", value=str(stats["draws"]))
    embed.add_field(name="❌ Przegrane", value=str(stats["losses"]))
    embed.add_field(name="⚽ Gole zdobyte", value=str(stats["goals_scored"]))
    embed.add_field(name="🛡️ Gole stracone", value=str(stats["goals_conceded"]))
    embed.add_field(name="📊 Mecze łącznie", value=str(total_matches), inline=False)
    embed.add_field(name="📈 Skuteczność", value=f"{win_rate}%", inline=False)
    embed.add_field(name="🎯 Śr. gole zdobyte/mecz", value=str(avg_goals_scored))
    embed.add_field(name="🧱 Śr. gole stracone/mecz", value=str(avg_goals_conceded))

    await interaction.response.send_message(
        embed=embed,
        ephemeral=(uzytkownik is None)
    )


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

@bot.tree.command(name="medale", description="Sprawdź swoje lub czyjeś medale")
@app_commands.describe(user="Użytkownik, którego medale chcesz zobaczyć (opcjonalne)")
async def medale(interaction: Interaction, user: discord.User = None):
    user = user or interaction.user
    stats = await get_player_stats(str(user.id))

    medals = []
    wins = stats.get("wins", 0)
    total = stats["wins"] + stats["losses"] + stats["draws"]
    goals = stats["goals_scored"]
    losses = stats["losses"]
    draws = stats["draws"]
        # Medale za wygrane
    if wins >= 10: medals.append("🏆 Zwycięzca – 10 wygranych")
    if wins >= 50: medals.append("🔥 Wojownik – 50 wygranych")
    if wins >= 100: medals.append("💪 Mistrz – 100 wygranych")
    if wins >= 500: medals.append("👑 Legendarny Mistrz – 500 wygranych")

    if total >= 10: medals.append("🎓 Początkujący Gracz – 10 rozegranych meczów")
    if total >= 50: medals.append("🐢 Maratończyk – 50 rozegranych meczów")
    if total >= 100: medals.append("🧱 Weteran – 100 rozegranych meczów")
    if total >= 500: medals.append("🐉 Legenda Discorda – 500 rozegranych meczów")

    if goals >= 10: medals.append("🎯 Celownik Ustawiony – 10 goli zdobytych")
    if goals >= 50: medals.append("🔥 Snajper – 50 goli zdobytych")
    if goals >= 100: medals.append("💥 Maszyna do goli – 100 goli zdobytych")
    if goals >= 500: medals.append("🚀 Rzeźnik Bramkarzy – 500 goli zdobytych")

    if losses >= 10: medals.append("😬 Uczeń Pokory – 10 porażek")
    if losses >= 50: medals.append("🧹 Zamiatany – 50 porażek")
    if losses >= 100: medals.append("🪦 Król Przegranych – 100 porażek")

    if draws >= 5: medals.append("🤝 Dyplomata – 5 remisów")
    if draws >= 20: medals.append("😐 Wieczny Remis – 20 remisów")
    if draws >= 50: medals.append("💤 Król Nudy – 50 remisów")

    if not medals:
        medals_text = "Brak medali — graj więcej!"
    else:
        medals_text = "\n".join(f"- {m}" for m in medals)

    embed = discord.Embed(
        title=f"🎖️ Medale {user.display_name}",
        description=medals_text,
        color=discord.Color.gold()
    )

    # Jeśli użytkownik sprawdza swoje medale — wiadomość ephemeryczna (ukryta)
    # W przeciwnym wypadku wiadomość jest publiczna na kanale
    ephemeral = (user == interaction.user)

    await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
#turniej#
@bot.tree.command(name="stworz_turniej", description="Stwórz nowy turniej z zapisem")
@app_commands.describe(nazwa="Nazwa turnieju", limit="Ile osób ma się zapisać?")
async def stworz_turniej(interaction: Interaction, nazwa: str, limit: int):
    # ✅ Sprawdzenie, czy użytkownik ma rolę "Turniej"
    role_names = [role.name for role in interaction.user.roles]
    if "Turniej" not in role_names:
        await interaction.response.send_message("❌ Nie masz uprawnień do tworzenia turniejów.", ephemeral=True)
        return

    if limit < 2:
        await interaction.response.send_message("❌ Minimalna liczba graczy to 2.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🎮 Turniej: {nazwa}",
        description=f"Naciśnij przycisk poniżej, aby zapisać się do turnieju.\nLiczba miejsc: **{limit}**",
        color=discord.Color.green()
    )

    # Najpierw wysyłamy wiadomość
    message = await interaction.channel.send(embed=embed)
    
    # Potem tworzymy widok, przekazując message.id
    view = SignupView(message.id)

    # Edytujemy wiadomość, aby dodać przycisk
    await message.edit(view=view)

    # Zapisujemy turniej do słownika
    tournaments[message.id] = {
        "name": nazwa,
        "limit": limit,
        "players": []
    }

    await interaction.response.send_message("✅ Turniej utworzony!", ephemeral=True)

#mute#
@bot.tree.command(name="mute", description="Wycisza użytkownika na określony czas.")
@app_commands.describe(
    user="Użytkownik do wyciszenia",
    time="Czas wyciszenia (np. 10m, 1h, 1d)",
    reason="Powód wyciszenia"
)
async def mute(interaction: Interaction, user: discord.Member, time: str, reason: str = "Brak powodu"):
    # Tylko dla Admin i HELPER
    allowed_roles = ["Admin", "HELPER"]
    user_roles = [role.name for role in interaction.user.roles]
    
    if not any(role in allowed_roles for role in user_roles):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    # Konwersja czasu
    units = {"m": 60, "h": 3600, "d": 86400}
    try:
        unit = time[-1]
        amount = int(time[:-1])
        if unit not in units:
            raise ValueError
        duration = timedelta(seconds=amount * units[unit])
    except ValueError:
        await interaction.response.send_message("❌ Podaj czas w formacie np. `10m`, `1h`, `1d`.", ephemeral=True)
        return

    # Nadanie timeoutu
    try:
        await user.timeout(duration, reason=reason)
        await interaction.response.send_message(
            f"🔇 {user.mention} został wyciszony na **{time}**.\n📄 Powód: {reason}"
        )
    except Exception as e:
        await interaction.response.send_message(f"❌ Nie udało się wyciszyć użytkownika: {e}", ephemeral=True)
)


#unmute#
@bot.tree.command(name="unmute", description="Usuwa wyciszenie z użytkownika.")
@app_commands.describe(user="Użytkownik do odciszenia")
async def unmute(interaction: Interaction, user: discord.Member):
    # Tylko dla Admin i HELPER
    allowed_roles = ["Admin", "HELPER"]
    user_roles = [role.name for role in interaction.user.roles]

    if not any(role in allowed_roles for role in user_roles):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    try:
        await user.timeout(None)
        await interaction.response.send_message(f"🔊 {user.mention} został odciszony.")
    except Exception as e:
        await interaction.response.send_message(f"❌ Nie udało się odciszyć użytkownika: {e}", ephemeral=True)

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

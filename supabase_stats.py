import os
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

async def get_player_stats(user_id: str):
    response = supabase.table("stats").select("*").eq("user_id", user_id).single().execute()
    if response.error:
        # jeśli brak rekordu, zwróć domyślne wartości
        return {
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "goals_scored": 0,
            "goals_conceded": 0,
        }
    return response.data or {
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "goals_scored": 0,
        "goals_conceded": 0,
    }

async def update_player_stats(user_id: str, wins=0, losses=0, draws=0, goals_scored=0, goals_conceded=0):
    # Pobierz obecne statystyki
    existing = supabase.table("stats").select("*").eq("user_id", user_id).single().execute()
    if existing.error or not existing.data:
        # Jeśli nie ma rekordu - dodaj nowy
        stat = {
            "user_id": user_id,
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "goals_scored": goals_scored,
            "goals_conceded": goals_conceded,
        }
        supabase.table("stats").insert(stat).execute()
    else:
        data = existing.data
        stat = {
            "wins": data.get("wins", 0) + wins,
            "losses": data.get("losses", 0) + losses,
            "draws": data.get("draws", 0) + draws,
            "goals_scored": data.get("goals_scored", 0) + goals_scored,
            "goals_conceded": data.get("goals_conceded", 0) + goals_conceded,
        }
        supabase.table("stats").update(stat).eq("user_id", user_id).execute()

async def get_all_stats():
    response = supabase.table("stats").select("*").execute()
    if response.error:
        return []
    return response.data or []

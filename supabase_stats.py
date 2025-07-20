import os
import asyncio
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- synchroniczne funkcje, które wykonują zapytania ---
def get_player_stats_sync(player_id: str) -> dict:
    response = supabase.table("player_stats").select("*").eq("player_id", player_id).execute()
    data = response.data
    if data:
        return data[0]
    else:
        return {
            "player_id": player_id,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "goals_scored": 0,
            "goals_conceded": 0,
        }

def update_player_stats_sync(player_id: str, wins=0, losses=0, draws=0, goals_scored=0, goals_conceded=0):
    response = supabase.table("player_stats").select("*").eq("player_id", player_id).execute()
    data = response.data

    if data:
        stats = data[0]
        updated_stats = {
            "wins": stats.get("wins", 0) + wins,
            "losses": stats.get("losses", 0) + losses,
            "draws": stats.get("draws", 0) + draws,
            "goals_scored": stats.get("goals_scored", 0) + goals_scored,
            "goals_conceded": stats.get("goals_conceded", 0) + goals_conceded,
        }
        supabase.table("player_stats").update(updated_stats).eq("player_id", player_id).execute()
    else:
        new_stats = {
            "player_id": player_id,
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "goals_scored": goals_scored,
            "goals_conceded": goals_conceded,
        }
        supabase.table("player_stats").insert(new_stats).execute()

def get_all_stats_sync():
    response = supabase.table("player_stats").select("*").execute()
    return response.data if response.data else []

# --- async wrappery wywołujące sync funkcje w osobnym wątku ---
async def get_player_stats(player_id: str) -> dict:
    return await asyncio.to_thread(get_player_stats_sync, player_id)

async def update_player_stats(player_id: str, wins=0, losses=0, draws=0, goals_scored=0, goals_conceded=0):
    await asyncio.to_thread(update_player_stats_sync, player_id, wins, losses, draws, goals_scored, goals_conceded)

async def get_all_stats():
    return await asyncio.to_thread(get_all_stats_sync)

"""
FastAPI REST API — exposes dropdown data, simulation trigger, and results.
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from state import app_state
from engine import simulation_engine, fetch_mapbox_route, update_station_road_metrics
import math
import pandas as pd

router = APIRouter()


# ── Pydantic request model ──
class SimulationRequest(BaseModel):
    brand: str
    model: str
    battery_size: str
    battery_pct: int
    target_battery_pct: int
    charger_pref: str  # "Any" | "AC" | "DC"
    user_lat: float
    user_lon: float


# ── Dropdown data ──
@router.get("/brands")
async def get_brands():
    return {"brands": app_state.brands}


@router.get("/models/{brand}")
async def get_models(brand: str):
    models = app_state.models_dict.get(brand, ["No Data"])
    return {"models": models}


@router.get("/battery-sizes/{brand}/{model}")
async def get_battery_sizes(brand: str, model: str):
    sizes = ["No Data"]
    if app_state.ev_df is not None:
        df = app_state.ev_df
        s = df[(df['Brand'] == brand) & (df['Model Name'] == model)]['Battery Size'].dropna().unique()
        if len(s) > 0:
            sizes = sorted([str(x) for x in s])
    return {"sizes": sizes}


@router.get("/stations")
async def get_all_stations():
    # Used for the heatmap layer on the frontend
    if app_state.stations_df is None or app_state.stations_df.empty:
        return {"stations": []}
    
    # We just need lat/lon coordinates
    df = app_state.stations_df.drop_duplicates(subset=['Station ID'])
    
    stations = []
    for _, row in df.iterrows():
        # Ensure we have valid coordinates
        if pd.isna(row['Latitude']) or pd.isna(row['Longitude']):
            continue
            
        stations.append({
            "id": str(row['Station ID']),
            "lat": float(row['Latitude']),
            "lng": float(row['Longitude']),
        })
        
    return {"stations": stations}

# ── Run simulation ──
@router.post("/simulate")
async def run_simulation(req: SimulationRequest):
    # Look up battery capacity
    battery_cap = 0.0
    if app_state.ev_df is not None:
        df = app_state.ev_df
        match = df[
            (df['Brand'] == req.brand) &
            (df['Model Name'] == req.model) &
            (df['Battery Size'] == req.battery_size)
        ]
        if not match.empty:
            battery_cap = float(match.iloc[0]['Value'])

    # Look up consumption
    consumption = 20.0
    if app_state.consumption_df is not None:
        cdf = app_state.consumption_df
        match = cdf[(cdf['Brand'] == req.brand) & (cdf['Model Name'] == req.model)]
        if not match.empty:
            consumption = float(match.iloc[0]['Average Consumption (kWh/100km)'])

    available_energy = battery_cap * (req.battery_pct / 100.0)
    max_range_km = (available_energy / consumption) * 100.0 if consumption > 0 else 0
    target_energy = battery_cap * (req.target_battery_pct / 100.0)

    # Store on app_state for the engine
    app_state.brand = req.brand
    app_state.model = req.model
    app_state.battery_size = req.battery_size
    app_state.battery_pct = req.battery_pct
    app_state.target_battery_pct = req.target_battery_pct
    app_state.battery_cap = battery_cap
    app_state.consumption = consumption
    app_state.available_energy = available_energy
    app_state.target_energy = target_energy
    app_state.max_range_km = max_range_km
    app_state.charger_pref = req.charger_pref
    app_state.user_lat = req.user_lat
    app_state.user_lon = req.user_lon

    # Filter by charger preference
    filtered_df = app_state.stations_df.copy()
    if req.charger_pref != "Any":
        filtered_df['Charger type'] = filtered_df['Charger type'].fillna('Unknown')
        filtered_df = filtered_df[filtered_df['Charger type'].str.upper() == req.charger_pref.upper()]

    current_hour = datetime.now().hour + (datetime.now().minute / 60.0)

    result_df = simulation_engine(filtered_df, current_hour, req.user_lat, req.user_lon, max_range_km)

    # Build JSON response
    if result_df is None or result_df.empty:
        dist = app_state.nearest_station_dist
        if dist < float('inf'):
            msg = f"Not enough battery to reach any station! Closest: {dist:.1f} km — Range: {max_range_km:.1f} km"
        else:
            msg = "No stations found in database."
        return {
            "status": "no_results",
            "message": msg,
            "summary": _build_summary(req, battery_cap),
            "user_lat": req.user_lat,
            "user_lon": req.user_lon,
            "max_range_km": max_range_km,
            "available_energy": available_energy,
        }

    available = result_df[result_df['has_available'] == True].copy()
    if available.empty:
        return {
            "status": "all_occupied",
            "message": "Stations in range, but all chargers are fully occupied!",
            "summary": _build_summary(req, battery_cap),
            "user_lat": req.user_lat,
            "user_lon": req.user_lon,
            "max_range_km": max_range_km,
            "available_energy": available_energy,
        }

    def normalize_sid(sid_val):
        s = str(sid_val)
        if s.endswith('.0'):
            return s[:-2]
        return s

    # Final Guarantee in api.py: Ensure that winners are stably sorted and
    # definitely have their Mapbox road routes fetched and updated.
    for _ in range(5):
        available = result_df[result_df['has_available'] == True]
        if available.empty:
            break
        fastest = available.sort_values(by=['best_total_time', 'Station ID']).head(1)
        closest = available.sort_values(by=['distance_to_user', 'Station ID']).head(1)
        top = pd.concat([fastest, closest]).drop_duplicates(subset=['Station ID'])
        
        needs_fetch = [
            row for _, row in top.iterrows()
            if normalize_sid(row['Station ID']) not in app_state.station_routes
        ]
        if not needs_fetch:
            break
            
        for row in needs_fetch:
            sid = row['Station ID']
            norm_sid = normalize_sid(sid)
            result = fetch_mapbox_route(req.user_lat, req.user_lon, row['Latitude'], row['Longitude'])
            if result:
                road_dist_km, new_drive_time, coords = result
                app_state.station_routes[norm_sid] = coords
                result_df = update_station_road_metrics(result_df, sid, road_dist_km, new_drive_time)

    # Re-calculate top one final time after any potential fetches
    available = result_df[result_df['has_available'] == True]
    fastest = available.sort_values(by=['best_total_time', 'Station ID']).head(1)
    closest = available.sort_values(by=['distance_to_user', 'Station ID']).head(1)
    top = pd.concat([fastest, closest]).drop_duplicates(subset=['Station ID'])

    def safe_float(v):
        if pd.isna(v) or v is None:
            return None
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f

    def safe_round(v, decimals=1):
        sf = safe_float(v)
        return round(sf, decimals) if sf is not None else None

    # Build station cards data
    stations = []
    for _, row in top.iterrows():
        sid = row['Station ID']
        is_fastest = sid in fastest['Station ID'].values
        is_closest = sid in closest['Station ID'].values

        # Route coords
        norm_sid = normalize_sid(sid)
        coords = app_state.station_routes.get(norm_sid)
        
        # Debugging
        if coords is None:
            print(f"[API] Route NOT FOUND for sid: '{norm_sid}' (type {type(sid)}). Available keys: {list(app_state.station_routes.keys())}", flush=True)
        else:
            print(f"[API] Route FOUND for sid: '{norm_sid}' with {len(coords)} points", flush=True)
            
        if coords is None or len(coords) == 0:
            coords = [
                [req.user_lat, req.user_lon],
                [row['Latitude'], row['Longitude']]
            ]

        station = {
            "id": str(sid),
            "name": row['Name'] if pd.notna(row['Name']) else "Unknown",
            "governrate": row['governrate'] if pd.notna(row['governrate']) else "Unknown",
            "lat": safe_float(row['Latitude']),
            "lng": safe_float(row['Longitude']),
            "distance_km": safe_round(row['distance_to_user'], 1),
            "required_kwh": safe_round(row['required_kwh'], 1),
            "is_fastest": bool(is_fastest),
            "is_closest": bool(is_closest),
            "route_coords": coords,
            "ac": None,
            "dc": None,
        }

        if row.get('ac_working', 0) > 0:
            station["ac"] = {
                "working": int(row['ac_working']),
                "available": int(row['ac_avail']),
                "total_time_h": safe_round(row['ac_total_time'], 4),
                "charge_time_h": safe_round(row.get('ac_charge_time', 0), 4),
                "drive_time_h": safe_round(row.get('drive_time_hours', 0), 4),
                "cost_egp": safe_round(row.get('ac_charge_cost', 0), 1),
                "speed_kw": safe_round(row.get('ac_charging_speed', 0), 1),
            }

        if row.get('dc_working', 0) > 0:
            station["dc"] = {
                "working": int(row['dc_working']),
                "available": int(row['dc_avail']),
                "total_time_h": safe_round(row['dc_total_time'], 4),
                "charge_time_h": safe_round(row.get('dc_charge_time', 0), 4),
                "drive_time_h": safe_round(row.get('drive_time_hours', 0), 4),
                "cost_egp": safe_round(row.get('dc_charge_cost', 0), 1),
                "speed_kw": safe_round(row.get('dc_charging_speed', 0), 1),
            }

        stations.append(station)

    return {
        "status": "ok",
        "summary": _build_summary(req, battery_cap),
        "user_lat": req.user_lat,
        "user_lon": req.user_lon,
        "max_range_km": max_range_km,
        "available_energy": available_energy,
        "target_energy": target_energy,
        "battery_cap": battery_cap,
        "stations": stations,
    }


def _build_summary(req, battery_cap):
    return (
        f"🚗 {req.brand} {req.model} ({req.battery_size}, {battery_cap:.0f} kWh) │ "
        f"🔋 {req.battery_pct}% → {req.target_battery_pct}% │ "
        f"📍 {req.user_lat:.4f}, {req.user_lon:.4f}"
    )

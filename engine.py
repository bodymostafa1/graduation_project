"""
Simulation engine — the core probabilistic logic to prepare variables for the Math Model.
Completely decoupled from the UI; reads/writes plain values on the AppState singleton.
"""
import os
import pandas as pd
import numpy as np
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from state import app_state

# Charging prices in Egypt (EGP per kWh)
PRICE_DC_PER_KWH = 7.67
PRICE_AC_PER_KWH = 3.97

# Session re-use for HTTP Keep-Alive
_HTTP_SESSION = requests.Session()

# In-memory route cache keyed by (user_lat, user_lon, s_lat, s_lon)
_ROUTE_CACHE = {}


def normalize_sid(sid_val):
    s = str(sid_val)
    if s.endswith('.0'):
        return s[:-2]
    return s


def fetch_mapbox_route(user_lat, user_lon, s_lat, s_lon, retries=2):
    """Fetch a Mapbox route with retry and in-memory caching. Returns (dist_km, dur_hrs, coords) or None."""
    cache_key = (round(user_lat, 5), round(user_lon, 5), round(s_lat, 5), round(s_lon, 5))
    if cache_key in _ROUTE_CACHE:
        return _ROUTE_CACHE[cache_key]

    MAPBOX_TOKEN = os.getenv("MAPBOX_SECRET_TOKEN", "")
    url = (
        f"https://api.mapbox.com/directions/v5/mapbox/driving/"
        f"{user_lon},{user_lat};{s_lon},{s_lat}"
        f"?overview=full&geometries=geojson&access_token={MAPBOX_TOKEN}"
    )

    for attempt in range(retries + 1):
        try:
            response = _HTTP_SESSION.get(url, timeout=10)
            
            if response.status_code != 200:
                print(f"[Attempt {attempt+1}] Mapbox HTTP Error {response.status_code}: {response.text}", flush=True)
                continue
                
            res = response.json()
            if res.get('code') == 'Ok' and res.get('routes'):
                route = res['routes'][0]
                dist_km = route['distance'] / 1000.0
                dur_hrs = route['duration'] / 3600.0
                coords = [[c[1], c[0]] for c in route['geometry']['coordinates']]
                result = (dist_km, dur_hrs, coords)
                _ROUTE_CACHE[cache_key] = result
                return result
            else:
                print(f"[Attempt {attempt+1}] Mapbox API Error Code: {res.get('code')}, Message: {res.get('message', 'None')}", flush=True)
        except Exception as e:
            print(f"[Attempt {attempt+1}] Exception during route fetch: {repr(e)}", flush=True)
    
    return None


def fetch_mapbox_routes_batch(user_lat, user_lon, targets, max_workers=10):
    """
    Fetch Mapbox routes in parallel using ThreadPoolExecutor.
    targets: list of (key, s_lat, s_lon) tuples, where key is sid or item identifier.
    Returns dict: {key: (dist_km, dur_hrs, coords)}
    """
    results = {}
    if not targets:
        return results

    with ThreadPoolExecutor(max_workers=min(len(targets), max_workers)) as executor:
        future_to_key = {
            executor.submit(fetch_mapbox_route, user_lat, user_lon, s_lat, s_lon): key
            for key, s_lat, s_lon in targets
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            res = future.result()
            if res is not None:
                results[key] = res

    return results


def select_winners(df):
    """Select the fastest and closest available stations from the dataframe using stable sorting."""
    available = df[df['has_available'] == True]
    if available.empty:
        return pd.DataFrame()
    fastest = available.sort_values(by=['best_total_time', 'Station ID']).head(1)
    closest = available.sort_values(by=['distance_to_user', 'Station ID']).head(1)
    return pd.concat([fastest, closest]).drop_duplicates(subset=['Station ID'])


def update_station_road_metrics(df, sid, road_dist_km, new_drive_time):
    """Updates a station's distance, drive time, charge time, total time, and cost in the dataframe."""
    consumption = app_state.consumption
    available_energy = app_state.available_energy
    target_energy = app_state.target_energy if app_state.target_energy > 0 else app_state.battery_cap

    new_req_kwh = road_dist_km * (consumption / 100.0)
    new_rem_kwh = available_energy - new_req_kwh
    new_charge_kwh = max(0, target_energy - new_rem_kwh)

    mask = df['Station ID'] == sid
    if not mask.any():
        return df

    df.loc[mask, 'distance_to_user'] = road_dist_km
    df.loc[mask, 'drive_time_hours'] = new_drive_time
    df.loc[mask, 'required_kwh'] = new_req_kwh

    # Recalculate times and costs with new road values
    ac_mask = mask & (df['ac_working'] > 0)
    dc_mask = mask & (df['dc_working'] > 0)
    if ac_mask.any():
        ac_spd = df.loc[ac_mask, 'ac_charging_speed'].values[0]
        new_ac_ct = new_charge_kwh / ac_spd if ac_spd > 0 else 0
        df.loc[ac_mask, 'ac_charge_time'] = new_ac_ct
        df.loc[ac_mask, 'ac_total_time'] = new_drive_time + new_ac_ct
        df.loc[ac_mask, 'ac_charge_cost'] = new_charge_kwh * PRICE_AC_PER_KWH
    if dc_mask.any():
        dc_spd = df.loc[dc_mask, 'dc_charging_speed'].values[0]
        new_dc_ct = new_charge_kwh / dc_spd if dc_spd > 0 else 0
        df.loc[dc_mask, 'dc_charge_time'] = new_dc_ct
        df.loc[dc_mask, 'dc_total_time'] = new_drive_time + new_dc_ct
        df.loc[dc_mask, 'dc_charge_cost'] = new_charge_kwh * PRICE_DC_PER_KWH

    # Recalculate best_total_time
    row_data = df.loc[mask].iloc[0]
    best = float('inf')
    if row_data['ac_avail'] > 0 and row_data['ac_total_time'] < best:
        best = row_data['ac_total_time']
    if row_data['dc_avail'] > 0 and row_data['dc_total_time'] < best:
        best = row_data['dc_total_time']
    if best < float('inf'):
        df.loc[mask, 'best_total_time'] = best
    
    return df


def simulation_engine(stations_df, current_hour, user_lat, user_lon, max_range_km):
    """The core probabilistic logic to prepare variables for the Math Model."""
    np.random.seed(42)
    app_state.station_routes = {}

    if stations_df.empty:
        return pd.DataFrame()

    # 1. Chargers per station
    stations_df['Charger type'] = stations_df['Charger type'].fillna('Unknown')
    station_stats = stations_df.groupby(['Station ID', 'Name', 'governrate', 'Latitude', 'Longitude', 'Charger type']).agg(
        total_chargers=('charger id', 'count'),
        charging_speed=('charging speed', 'mean')
    ).reset_index()

    # Fill any missing charging speeds with a default 22 kW to avoid NaN math errors
    station_stats['charging_speed'] = station_stats['charging_speed'].fillna(22.0)

    # Filter out stations out of reach using Haversine
    user_lat_rad = np.radians(user_lat)
    user_lon_rad = np.radians(user_lon)
    lat_rad = np.radians(station_stats['Latitude'].values)
    lon_rad = np.radians(station_stats['Longitude'].values)

    dlat = lat_rad - user_lat_rad
    dlon = lon_rad - user_lon_rad

    a = np.sin(dlat / 2)**2 + np.cos(user_lat_rad) * np.cos(lat_rad) * np.sin(dlon / 2)**2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    distances_to_user = 6371.0 * c

    app_state.nearest_station_dist = distances_to_user.min() if len(distances_to_user) > 0 else float('inf')

    station_stats['distance_to_user'] = distances_to_user
    station_stats = station_stats[station_stats['distance_to_user'] <= max_range_km].copy()

    if station_stats.empty:
        return pd.DataFrame()

    # --- PHASE 2: Parallel Mapbox Directions API for top 15 closest unique stations ---
    unique_closest = (
        station_stats.groupby('Station ID')['distance_to_user']
        .min().nsmallest(15)
    )
    top_station_ids = set(unique_closest.index)

    # Prepare targets for batch parallel fetching
    fetch_targets = []
    for sid in top_station_ids:
        rows_mask = station_stats['Station ID'] == sid
        sample = station_stats.loc[rows_mask].iloc[0]
        fetch_targets.append((sid, sample['Latitude'], sample['Longitude']))

    batch_routes = fetch_mapbox_routes_batch(user_lat, user_lon, fetch_targets)

    for sid, (dist_km, dur_hrs, coords) in batch_routes.items():
        rows_mask = station_stats['Station ID'] == sid
        station_stats.loc[rows_mask, 'distance_to_user'] = dist_km
        station_stats.loc[rows_mask, 'drive_time_hours'] = dur_hrs
        app_state.station_routes[normalize_sid(sid)] = coords

    # For rows NOT in the top-15 stations, estimate drive time from Haversine
    non_top_mask = ~station_stats['Station ID'].isin(top_station_ids)
    station_stats.loc[non_top_mask, 'drive_time_hours'] = (
        station_stats.loc[non_top_mask, 'distance_to_user'] / 50.0
    )

    # Set the nearest station distance based on updated road distances (or Haversine fallbacks)
    if not station_stats.empty:
        app_state.nearest_station_dist = station_stats['distance_to_user'].min()

    station_stats = station_stats[station_stats['distance_to_user'] <= max_range_km].copy()
    
    if station_stats.empty:
        return pd.DataFrame()

    # 2. Time Multiplier
    hours = [0, 3, 7, 12, 17, 21, 24]
    traffic_weights = [0.2, 0.05, 0.6, 0.5, 0.95, 0.4, 0.2]
    time_multiplier = np.interp(current_hour, hours, traffic_weights)

    # 3. Density Score (Based on proximity)
    lat_rad = np.radians(station_stats['Latitude'].values)
    lon_rad = np.radians(station_stats['Longitude'].values)
    
    dlat = lat_rad[:, np.newaxis] - lat_rad
    dlon = lon_rad[:, np.newaxis] - lon_rad
    
    a = np.sin(dlat / 2)**2 + np.cos(lat_rad[:, np.newaxis]) * np.cos(lat_rad) * np.sin(dlon / 2)**2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    dist_matrix = 6371.0 * c
    
    proximity_threshold_km = 5.0
    density_score = np.sum(dist_matrix < proximity_threshold_km, axis=1)
    
    max_density = density_score.max() if density_score.max() > 0 else 1
    station_stats['density_score'] = (density_score / max_density) * 10.0

    # 4. Out of Commission
    # 5% chance of a charger being broken
    station_stats['out_of_commission'] = np.random.binomial(n=station_stats['total_chargers'].astype(int), p=0.05)
    station_stats['working_chargers'] = station_stats['total_chargers'] - station_stats['out_of_commission']
    station_stats['working_chargers'] = np.maximum(station_stats['working_chargers'], 1)

    # 5. Activity Meter
    W_t = 0.6
    W_d = 0.4
    station_stats['activity_meter'] = W_t * (10 ** time_multiplier) + W_d * (10.0 - station_stats['density_score'])
    station_stats['activity_meter'] = np.clip(station_stats['activity_meter'], 1.0, 10.0)
    station_stats['activity_meter'] = np.round(station_stats['activity_meter'], 2)

    # 6. Occupied & Available Chargers
    probabilities = station_stats['activity_meter'] / 10.0
    station_stats['occupied_chargers'] = np.random.binomial(n=station_stats['working_chargers'].astype(int), p=probabilities)
    station_stats['available_chargers'] = station_stats['working_chargers'] - station_stats['occupied_chargers']

    # 7. Additional Metrics
    consumption = app_state.consumption
    available_energy = app_state.available_energy
    target_energy = app_state.target_energy if app_state.target_energy > 0 else app_state.battery_cap

    station_stats['required_kwh'] = station_stats['distance_to_user'] * (consumption / 100.0)
    station_stats['remaining_kwh'] = available_energy - station_stats['required_kwh']
    
    if 'drive_time_hours' not in station_stats.columns:
        station_stats['drive_time_hours'] = station_stats['distance_to_user'] / 50.0

    speeds = np.maximum(station_stats['charging_speed'], 1.0)
    station_stats['charge_time_hours'] = np.maximum(0, target_energy - station_stats['remaining_kwh']) / speeds
    station_stats['total_time_hours'] = station_stats['drive_time_hours'] + station_stats['charge_time_hours']

    # 8. Merge AC and DC per station to avoid duplicates in UI
    merged = []
    for (sid, name, gov, lat, lon, dist), group in station_stats.groupby(['Station ID', 'Name', 'governrate', 'Latitude', 'Longitude', 'distance_to_user']):
        station_info = {
            'Station ID': sid,
            'Name': name,
            'governrate': gov,
            'Latitude': lat,
            'Longitude': lon,
            'distance_to_user': dist,
            'required_kwh': group['required_kwh'].iloc[0],
            'drive_time_hours': group['drive_time_hours'].iloc[0],
            'has_available': False,
            'best_total_time': float('inf'),
            'ac_working': 0, 'ac_avail': 0, 'ac_total_time': float('inf'), 'ac_charge_time': 0.0, 'ac_charging_speed': 0.0,
            'dc_working': 0, 'dc_avail': 0, 'dc_total_time': float('inf'), 'dc_charge_time': 0.0, 'dc_charging_speed': 0.0,
            'ac_charge_cost': 0.0, 'dc_charge_cost': 0.0
        }
        
        for _, row in group.iterrows():
            ctype = row['Charger type'].upper()
            avail = row['available_chargers']
            working = row['working_chargers']
            ttime = row['total_time_hours']
            charge_kwh = max(0, target_energy - row['remaining_kwh'])
            charge_time = row['charge_time_hours']

            if avail > 0:
                station_info['has_available'] = True
                if ttime < station_info['best_total_time']:
                    station_info['best_total_time'] = ttime
            
            if ctype == 'DC':
                station_info['dc_working'] += working
                station_info['dc_avail'] += avail
                station_info['dc_charge_cost'] = charge_kwh * PRICE_DC_PER_KWH
                station_info['dc_charging_speed'] = max(row['charging_speed'], 1.0)
                if ttime < station_info['dc_total_time']:
                    station_info['dc_total_time'] = ttime
                    station_info['dc_charge_time'] = charge_time
            else:
                station_info['ac_working'] += working
                station_info['ac_avail'] += avail
                station_info['ac_charge_cost'] = charge_kwh * PRICE_AC_PER_KWH
                station_info['ac_charging_speed'] = max(row['charging_speed'], 1.0)
                if ttime < station_info['ac_total_time']:
                    station_info['ac_total_time'] = ttime
                    station_info['ac_charge_time'] = charge_time
                    
        merged.append(station_info)

    result_df = pd.DataFrame(merged)

    # 9. Backfill routes — batch fetch missing winner routes concurrently
    for _round in range(10):
        candidates = select_winners(result_df)
        if candidates.empty:
            break

        needs_fetch = [
            (row['Station ID'], row['Latitude'], row['Longitude'])
            for _, row in candidates.iterrows()
            if normalize_sid(row['Station ID']) not in app_state.station_routes
        ]
        if not needs_fetch:
            break

        routes_dict = fetch_mapbox_routes_batch(user_lat, user_lon, needs_fetch)
        if not routes_dict:
            break

        for sid, (road_dist_km, new_drive_time, coords) in routes_dict.items():
            app_state.station_routes[normalize_sid(sid)] = coords
            result_df = update_station_road_metrics(result_df, sid, road_dist_km, new_drive_time)

    # 10. Final Guarantee
    final_winners = select_winners(result_df)
    if not final_winners.empty:
        needs_fetch = [
            (row['Station ID'], row['Latitude'], row['Longitude'])
            for _, row in final_winners.iterrows()
            if normalize_sid(row['Station ID']) not in app_state.station_routes
        ]
        if needs_fetch:
            routes_dict = fetch_mapbox_routes_batch(user_lat, user_lon, needs_fetch)
            for sid, (road_dist_km, new_drive_time, coords) in routes_dict.items():
                app_state.station_routes[normalize_sid(sid)] = coords
                result_df = update_station_road_metrics(result_df, sid, road_dist_km, new_drive_time)

    return result_df

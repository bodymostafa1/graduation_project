"""
Simulation engine — the core probabilistic logic to prepare variables for the Math Model.
Completely decoupled from the UI; reads/writes plain values on the AppState singleton.
"""
import os
import pandas as pd
import numpy as np
import requests
from state import app_state


# Charging prices in Egypt (EGP per kWh)
PRICE_DC_PER_KWH = 7.67
PRICE_AC_PER_KWH = 3.97


def normalize_sid(sid_val):
    s = str(sid_val)
    if s.endswith('.0'):
        return s[:-2]
    return s

def simulation_engine(stations_df, current_hour, user_lat, user_lon, max_range_km):
    """The core probabilistic logic to prepare variables for the Math Model."""
    np.random.seed(42)
    app_state.station_routes = {}

    print(f"\n[ENGINE] simulation_engine called with {len(stations_df)} stations", flush=True)
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
        print("[ENGINE] All stations out of range. Returning empty.", flush=True)
        return pd.DataFrame()

    # --- PHASE 2: Mapbox Directions API for top 10 closest unique stations ---
    MAPBOX_TOKEN = os.getenv("MAPBOX_SECRET_TOKEN", "")

    # Pick top 15 unique stations by Haversine (not rows — avoids AC+DC eating slots)
    unique_closest = (
        station_stats.groupby('Station ID')['distance_to_user']
        .min().nsmallest(15)
    )
    top_station_ids = set(unique_closest.index)

    def _fetch_mapbox_route(s_lon, s_lat, retries=2):
        """Fetch a Mapbox route with retry.  Returns (dist_km, dur_hrs, coords) or None."""
        print(f"--> Attempting to fetch Mapbox route to destination ({s_lat}, {s_lon})", flush=True)
        for attempt in range(retries + 1):
            try:
                url = (
                    f"https://api.mapbox.com/directions/v5/mapbox/driving/"
                    f"{user_lon},{user_lat};{s_lon},{s_lat}"
                    f"?overview=full&geometries=geojson&access_token={MAPBOX_TOKEN}"
                )
                response = requests.get(url, timeout=10)
                
                if response.status_code != 200:
                    print(f"[Attempt {attempt+1}] Mapbox HTTP Error {response.status_code}: {response.text}", flush=True)
                    continue
                    
                res = response.json()
                if res.get('code') == 'Ok' and res.get('routes'):
                    route = res['routes'][0]
                    dist_km = route['distance'] / 1000.0
                    dur_hrs = route['duration'] / 3600.0
                    coords = [[c[1], c[0]] for c in route['geometry']['coordinates']]
                    print(f"    SUCCESS: Fetched route, distance={dist_km:.2f}km, duration={dur_hrs:.2f}hrs", flush=True)
                    return dist_km, dur_hrs, coords
                else:
                    print(f"[Attempt {attempt+1}] Mapbox API Error Code: {res.get('code')}, Message: {res.get('message', 'None')}", flush=True)
            except Exception as e:
                print(f"[Attempt {attempt+1}] Exception during route fetch: {repr(e)}", flush=True)
        
        print(f"    FAILED to fetch Mapbox route for dest({s_lat}, {s_lon}) after {retries + 1} attempts, falling back to Haversine.", flush=True)
        return None

    app_state.station_routes = {}

    # Fetch once per unique station, then broadcast to all its rows (AC, DC, etc.)
    for sid in top_station_ids:
        rows_mask = station_stats['Station ID'] == sid
        sample = station_stats.loc[rows_mask].iloc[0]
        result = _fetch_mapbox_route(sample['Longitude'], sample['Latitude'])
        if result:
            dist_km, dur_hrs, coords = result
            station_stats.loc[rows_mask, 'distance_to_user'] = dist_km
            station_stats.loc[rows_mask, 'drive_time_hours'] = dur_hrs
            app_state.station_routes[normalize_sid(sid)] = coords
        # else: keep Haversine fallback values already in station_stats

    # For rows NOT in the top-10 stations, estimate drive time from Haversine
    non_top_mask = ~station_stats['Station ID'].isin(top_station_ids)
    station_stats.loc[non_top_mask, 'drive_time_hours'] = (
        station_stats.loc[non_top_mask, 'distance_to_user'] / 50.0
    )


    station_stats = station_stats[station_stats['distance_to_user'] <= max_range_km].copy()
    
    if station_stats.empty:
        return pd.DataFrame()
        
    app_state.nearest_station_dist = station_stats['distance_to_user'].min()

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

    # 9. Backfill routes — loop until the fastest/closest winners both have
    #    Mapbox routes.  Each iteration may update a station's road distance
    #    (always longer than Haversine), which can shift the winners, so we
    #    keep going until stable (max 10 rounds to avoid infinite loops).
    for _round in range(10):
        available_merged = result_df[result_df['has_available'] == True]
        if available_merged.empty:
            break

        candidates = pd.concat([
            available_merged.nsmallest(1, 'best_total_time'),
            available_merged.nsmallest(1, 'distance_to_user')
        ]).drop_duplicates(subset=['Station ID'])

        # Check if all current winners already have routes
        needs_fetch = [
            cand for _, cand in candidates.iterrows()
            if normalize_sid(cand['Station ID']) not in app_state.station_routes
        ]
        if not needs_fetch:
            break  # All winners have road routes — done

        for cand in needs_fetch:
            sid = cand['Station ID']
            result = _fetch_mapbox_route(cand['Longitude'], cand['Latitude'])
            if result:
                road_dist_km, new_drive_time, coords = result
                app_state.station_routes[normalize_sid(sid)] = coords
                # Update distance/duration/required_kwh with real road values
                new_req_kwh = road_dist_km * (consumption / 100.0)
                new_rem_kwh = available_energy - new_req_kwh
                new_charge_kwh = max(0, target_energy - new_rem_kwh)

                mask = result_df['Station ID'] == sid
                result_df.loc[mask, 'distance_to_user'] = road_dist_km
                result_df.loc[mask, 'drive_time_hours'] = new_drive_time
                result_df.loc[mask, 'required_kwh'] = new_req_kwh

                # Recalculate times and costs with new road values
                ac_mask = mask & (result_df['ac_working'] > 0)
                dc_mask = mask & (result_df['dc_working'] > 0)
                if ac_mask.any():
                    ac_spd = result_df.loc[ac_mask, 'ac_charging_speed'].values[0]
                    new_ac_ct = new_charge_kwh / ac_spd if ac_spd > 0 else 0
                    result_df.loc[ac_mask, 'ac_charge_time'] = new_ac_ct
                    result_df.loc[ac_mask, 'ac_total_time'] = new_drive_time + new_ac_ct
                    result_df.loc[ac_mask, 'ac_charge_cost'] = new_charge_kwh * PRICE_AC_PER_KWH
                if dc_mask.any():
                    dc_spd = result_df.loc[dc_mask, 'dc_charging_speed'].values[0]
                    new_dc_ct = new_charge_kwh / dc_spd if dc_spd > 0 else 0
                    result_df.loc[dc_mask, 'dc_charge_time'] = new_dc_ct
                    result_df.loc[dc_mask, 'dc_total_time'] = new_drive_time + new_dc_ct
                    result_df.loc[dc_mask, 'dc_charge_cost'] = new_charge_kwh * PRICE_DC_PER_KWH
                # Recalculate best_total_time
                row_data = result_df.loc[mask].iloc[0]
                best = float('inf')
                if row_data['ac_avail'] > 0 and row_data['ac_total_time'] < best:
                    best = row_data['ac_total_time']
                if row_data['dc_avail'] > 0 and row_data['dc_total_time'] < best:
                    best = row_data['dc_total_time']
                if best < float('inf'):
                    result_df.loc[mask, 'best_total_time'] = best

    # 10. Final Guarantee: If the loop terminated but the final winners still
    #     don't have Mapbox routes (e.g. hit the 10-round limit), fetch them now.
    available_merged = result_df[result_df['has_available'] == True]
    if not available_merged.empty:
        final_winners = pd.concat([
            available_merged.nsmallest(1, 'best_total_time'),
            available_merged.nsmallest(1, 'distance_to_user')
        ]).drop_duplicates(subset=['Station ID'])
        
        for _, cand in final_winners.iterrows():
            sid = cand['Station ID']
            if normalize_sid(sid) not in app_state.station_routes:
                result = _fetch_mapbox_route(cand['Longitude'], cand['Latitude'])
                if result:
                    road_dist_km, new_drive_time, coords = result
                    app_state.station_routes[normalize_sid(sid)] = coords
                    
                    # Update distance/duration/required_kwh with real road values in result_df
                    new_req_kwh = road_dist_km * (consumption / 100.0)
                    new_rem_kwh = available_energy - new_req_kwh
                    new_charge_kwh = max(0, target_energy - new_rem_kwh)

                    mask = result_df['Station ID'] == sid
                    result_df.loc[mask, 'distance_to_user'] = road_dist_km
                    result_df.loc[mask, 'drive_time_hours'] = new_drive_time
                    result_df.loc[mask, 'required_kwh'] = new_req_kwh

                    # Recalculate times and costs with new road values
                    ac_mask = mask & (result_df['ac_working'] > 0)
                    dc_mask = mask & (result_df['dc_working'] > 0)
                    if ac_mask.any():
                        ac_spd = result_df.loc[ac_mask, 'ac_charging_speed'].values[0]
                        new_ac_ct = new_charge_kwh / ac_spd if ac_spd > 0 else 0
                        result_df.loc[ac_mask, 'ac_charge_time'] = new_ac_ct
                        result_df.loc[ac_mask, 'ac_total_time'] = new_drive_time + new_ac_ct
                        result_df.loc[ac_mask, 'ac_charge_cost'] = new_charge_kwh * PRICE_AC_PER_KWH
                    if dc_mask.any():
                        dc_spd = result_df.loc[dc_mask, 'dc_charging_speed'].values[0]
                        new_dc_ct = new_charge_kwh / dc_spd if dc_spd > 0 else 0
                        result_df.loc[dc_mask, 'dc_charge_time'] = new_dc_ct
                        result_df.loc[dc_mask, 'dc_total_time'] = new_drive_time + new_dc_ct
                        result_df.loc[dc_mask, 'dc_charge_cost'] = new_charge_kwh * PRICE_DC_PER_KWH
                    # Recalculate best_total_time
                    row_data = result_df.loc[mask].iloc[0]
                    best = float('inf')
                    if row_data['ac_avail'] > 0 and row_data['ac_total_time'] < best:
                        best = row_data['ac_total_time']
                    if row_data['dc_avail'] > 0 and row_data['dc_total_time'] < best:
                        best = row_data['dc_total_time']
                    if best < float('inf'):
                        result_df.loc[mask, 'best_total_time'] = best

    return result_df


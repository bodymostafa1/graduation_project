"""
Data loading module. Reads Excel datasets and populates the AppState singleton.
"""
import pandas as pd
from state import app_state


def load_datasets():
    """Loads and prepares necessary Excel datasets."""
    try:
        # Load EV Battery Data
        ev_df = pd.read_excel("EV_Battery_Dataset v2.xlsx", sheet_name="Table1")
        app_state.ev_df = ev_df
        app_state.consumption_df = pd.read_excel("EV_Battery_Dataset v2.xlsx", sheet_name="EV Battery Dataset")

        # Parse available Brands and Models for dropdowns
        brands = ev_df['Brand'].dropna().unique()
        app_state.brands = sorted(list(brands))

        app_state.models_dict = {}
        for brand in app_state.brands:
            models = ev_df[ev_df['Brand'] == brand]['Model Name'].dropna().unique()
            app_state.models_dict[brand] = sorted(list(models))

        # Load Stations Data
        app_state.stations_df = pd.read_excel("Charging stations data v3.xlsx", sheet_name="Sheet1")

        print("Datasets loaded successfully.")

    except Exception as e:
        print(f"Error loading datasets: {e}")

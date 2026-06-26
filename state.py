"""
Centralized application state as a typed Singleton dataclass.
Provides type safety and IDE autocompletion across all modules.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Dict, List
import pandas as pd


@dataclass
class AppState:
    """Singleton state container for the EV Charging Optimization app."""

    # --- Data ---
    stations_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    ev_df: Optional[pd.DataFrame] = None
    consumption_df: Optional[pd.DataFrame] = None
    brands: List[str] = field(default_factory=lambda: ["No Data"])
    models_dict: Dict[str, List[str]] = field(default_factory=lambda: {"No Data": ["No Data"]})

    # --- User Selections (stored as plain values, NOT Tk variables) ---
    brand: str = ""
    model: str = ""
    battery_size: str = ""
    battery_pct: int = 20
    target_battery_pct: int = 80
    charger_pref: str = "Any"
    location_method: str = "Manual (Map)"

    # --- Location ---
    user_lat: Optional[float] = None
    user_lon: Optional[float] = None

    # --- Computed Values ---
    battery_cap: float = 0.0
    consumption: float = 20.0
    available_energy: float = 0.0
    target_energy: float = 0.0
    max_range_km: float = 0.0
    nearest_station_dist: float = float("inf")

    # --- Simulation Results ---
    sim_data: Optional[pd.DataFrame] = None
    station_routes: Dict = field(default_factory=dict)  # {Station ID: [[lat,lon], ...]}

    # Singleton pattern
    _instance: Optional["AppState"] = field(default=None, init=False, repr=False)

    @classmethod
    def instance(cls) -> "AppState":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# Module-level convenience accessor
app_state = AppState.instance()

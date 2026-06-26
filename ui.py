"""
PyQt6 UI — split-pane layout, folium map via QWebEngineView, QThread simulation worker.
"""
import json, folium, geocoder
import pandas as pd
from datetime import datetime
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QSlider, QPushButton, QRadioButton, QButtonGroup, QGroupBox,
    QScrollArea, QFrame, QSplitter, QStackedWidget, QSizePolicy
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage
from state import app_state
from engine import simulation_engine


# ---------------------------------------------------------------------------
#  Web Channel bridge — receives right-click coords from the folium JS map
# ---------------------------------------------------------------------------
from PyQt6.QtCore import QObject, pyqtSlot

class MapBridge(QObject):
    coords_received = pyqtSignal(float, float)
    @pyqtSlot(float, float)
    def setCoords(self, lat, lng):
        self.coords_received.emit(lat, lng)


# ---------------------------------------------------------------------------
#  Background worker for the simulation engine
# ---------------------------------------------------------------------------
class SimulationWorker(QThread):
    finished = pyqtSignal(object)  # emits the result DataFrame (or None)
    error = pyqtSignal(str)

    def __init__(self, filtered_df, current_hour, lat, lon, max_range):
        super().__init__()
        self.filtered_df = filtered_df
        self.current_hour = current_hour
        self.lat = lat
        self.lon = lon
        self.max_range = max_range

    def run(self):
        try:
            result = simulation_engine(
                self.filtered_df, self.current_hour,
                self.lat, self.lon, self.max_range
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
#  Helper: build a folium map and return its HTML string
# ---------------------------------------------------------------------------
def _build_map_html(lat, lon, zoom=11, markers=None, routes=None, user_marker=True, enable_click=False):
    m = folium.Map(location=[lat, lon], zoom_start=zoom, tiles="OpenStreetMap")
    if user_marker:
        folium.Marker([lat, lon], tooltip="Your Location",
                      icon=folium.Icon(color="red", icon="user", prefix="fa")).add_to(m)
    if routes:
        for rt in routes:
            folium.PolyLine(
                locations=rt["coords"],
                color=rt.get("color", "#3388ff"),
                weight=4,
                opacity=0.8,
                tooltip=rt.get("tip", "")
            ).add_to(m)
    if markers:
        for mk in markers:
            folium.Marker([mk["lat"], mk["lng"]], tooltip=mk.get("tip", ""),
                          icon=folium.Icon(color=mk.get("color", "blue"), icon="bolt", prefix="fa")).add_to(m)
    if enable_click:
        # Inject QWebChannel JS for right-click coord capture
        click_js = """
        <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
        <script>
        var bridge = null;
        new QWebChannel(qt.webChannelTransport, function(channel){ bridge = channel.objects.bridge; });
        document.addEventListener('contextmenu', function(e){ e.preventDefault(); });
        var theMap = null;
        document.addEventListener('DOMContentLoaded', function(){
            var checkMap = setInterval(function(){
                var containers = document.querySelectorAll('.folium-map');
                if(containers.length > 0){
                    var mapId = containers[0].id;
                    if(window[mapId]){
                        theMap = window[mapId];
                        theMap.on('contextmenu', function(ev){
                            if(bridge) bridge.setCoords(ev.latlng.lat, ev.latlng.lng);
                        });
                        clearInterval(checkMap);
                    }
                }
            }, 200);
        });
        </script>
        """
        m.get_root().html.add_child(folium.Element(click_js))
    return m.get_root().render()


# ---------------------------------------------------------------------------
#  Helpers to build labelled rows
# ---------------------------------------------------------------------------
def _section(text):
    lbl = QLabel(text); lbl.setObjectName("sectionLabel"); return lbl

def _value_label(text):
    lbl = QLabel(text); lbl.setObjectName("valueLabel"); return lbl


# ---------------------------------------------------------------------------
#  MainWindow
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("⚡ EV Charging Optimization System")
        self.setMinimumSize(1100, 720)
        self.resize(1280, 800)
        self._worker = None

        # Central splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        # --- LEFT: scrollable sidebar ---
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFixedWidth(370)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar_widget = QWidget()
        self.sidebar_layout = QVBoxLayout(sidebar_widget)
        self.sidebar_layout.setContentsMargins(15, 15, 15, 15)
        self.sidebar_layout.setSpacing(10)
        sidebar_scroll.setWidget(sidebar_widget)
        splitter.addWidget(sidebar_scroll)

        # --- RIGHT: stacked (map / results) ---
        self.right_stack = QStackedWidget()
        splitter.addWidget(self.right_stack)
        splitter.setStretchFactor(1, 1)

        # Page 0 — config map
        self.map_view = QWebEngineView()
        self.map_channel = QWebChannel()
        self.map_bridge = MapBridge()
        self.map_channel.registerObject("bridge", self.map_bridge)
        self.map_view.page().setWebChannel(self.map_channel)
        self.map_bridge.coords_received.connect(self._on_map_click)
        self.right_stack.addWidget(self.map_view)

        # Page 1 — results
        self.results_widget = QWidget()
        self.results_layout = QVBoxLayout(self.results_widget)
        self.right_stack.addWidget(self.results_widget)

        self._build_sidebar()
        self._load_config_map(29.84, 31.32)

    # ---------------------------------------------------------------
    #  Sidebar
    # ---------------------------------------------------------------
    def _build_sidebar(self):
        lay = self.sidebar_layout
        title = QLabel("⚡ Trip Configuration"); title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter); lay.addWidget(title)

        # --- Vehicle group ---
        vg = QGroupBox("Vehicle"); vgl = QVBoxLayout(vg)
        vgl.addWidget(_section("Car Brand"))
        self.brand_cb = QComboBox(); self.brand_cb.addItems(app_state.brands)
        self.brand_cb.currentTextChanged.connect(self._on_brand_change); vgl.addWidget(self.brand_cb)

        vgl.addWidget(_section("Car Model"))
        self.model_cb = QComboBox(); self._populate_models(); vgl.addWidget(self.model_cb)
        self.model_cb.currentTextChanged.connect(self._on_model_change)

        vgl.addWidget(_section("Battery Size"))
        self.bsize_cb = QComboBox(); self._populate_battery_sizes(); vgl.addWidget(self.bsize_cb)
        lay.addWidget(vg)

        # --- Battery group ---
        bg = QGroupBox("Battery"); bgl = QVBoxLayout(bg)
        row1 = QHBoxLayout(); row1.addWidget(_section("Current Battery")); self.bat_val = _value_label("20%"); row1.addWidget(self.bat_val); bgl.addLayout(row1)
        self.bat_slider = QSlider(Qt.Orientation.Horizontal); self.bat_slider.setRange(1, 100); self.bat_slider.setValue(20)
        self.bat_slider.valueChanged.connect(self._on_bat_change); bgl.addWidget(self.bat_slider)

        row2 = QHBoxLayout(); row2.addWidget(_section("Target Battery")); self.tgt_val = _value_label("80%"); row2.addWidget(self.tgt_val); bgl.addLayout(row2)
        self.tgt_slider = QSlider(Qt.Orientation.Horizontal); self.tgt_slider.setRange(1, 100); self.tgt_slider.setValue(80)
        self.tgt_slider.valueChanged.connect(self._on_tgt_change); bgl.addWidget(self.tgt_slider)
        lay.addWidget(bg)

        # --- Charger pref ---
        pg = QGroupBox("Charger Preference"); pgl = QVBoxLayout(pg)
        self.pref_cb = QComboBox(); self.pref_cb.addItems(["Any", "AC", "DC"]); pgl.addWidget(self.pref_cb)
        lay.addWidget(pg)

        # --- Location ---
        lg = QGroupBox("Location"); lgl = QVBoxLayout(lg)
        self.loc_group = QButtonGroup(self)
        self.rb_manual = QRadioButton("Manual (Right-click map)"); self.rb_manual.setChecked(True)
        self.rb_auto = QRadioButton("Auto-detect (IP)")
        self.loc_group.addButton(self.rb_manual); self.loc_group.addButton(self.rb_auto)
        lgl.addWidget(self.rb_manual); lgl.addWidget(self.rb_auto)
        self.loc_group.buttonClicked.connect(self._on_loc_method)
        self.loc_status = QLabel("Right-click map to set location"); self.loc_status.setObjectName("statusWarn")
        self.loc_status.setWordWrap(True); lgl.addWidget(self.loc_status)
        lay.addWidget(lg)

        # --- Run button ---
        self.run_btn = QPushButton("⚡  Confirm && Find Stations"); self.run_btn.setObjectName("primaryBtn")
        self.run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_btn.clicked.connect(self._trigger_simulation); lay.addWidget(self.run_btn)

        lay.addStretch()

    # ---------------------------------------------------------------
    #  Dropdown cascading
    # ---------------------------------------------------------------
    def _populate_models(self):
        brand = self.brand_cb.currentText() if hasattr(self, 'brand_cb') else app_state.brands[0]
        models = app_state.models_dict.get(brand, ["No Data"])
        self.model_cb.blockSignals(True); self.model_cb.clear(); self.model_cb.addItems(models); self.model_cb.blockSignals(False)

    def _populate_battery_sizes(self):
        brand = self.brand_cb.currentText() if hasattr(self, 'brand_cb') else ""
        model = self.model_cb.currentText() if hasattr(self, 'model_cb') else ""
        sizes = ["No Data"]
        if app_state.ev_df is not None:
            df = app_state.ev_df
            s = df[(df['Brand'] == brand) & (df['Model Name'] == model)]['Battery Size'].dropna().unique()
            if len(s) > 0: sizes = sorted(list(s))
        if hasattr(self, 'bsize_cb'):
            self.bsize_cb.blockSignals(True); self.bsize_cb.clear(); self.bsize_cb.addItems([str(x) for x in sizes]); self.bsize_cb.blockSignals(False)

    def _on_brand_change(self, _): self._populate_models(); self._populate_battery_sizes()
    def _on_model_change(self, _): self._populate_battery_sizes()

    # ---------------------------------------------------------------
    #  Slider callbacks with mutual constraint
    # ---------------------------------------------------------------
    def _on_bat_change(self, v):
        self.bat_val.setText(f"{v}%")
        if self.tgt_slider.value() < v:
            self.tgt_slider.setValue(v)

    def _on_tgt_change(self, v):
        cur = self.bat_slider.value()
        if v < cur: v = cur; self.tgt_slider.setValue(v)
        self.tgt_val.setText(f"{v}%")

    # ---------------------------------------------------------------
    #  Location
    # ---------------------------------------------------------------
    def _on_loc_method(self, btn):
        if btn is self.rb_auto:
            self.loc_status.setText("Detecting…"); self.loc_status.setObjectName("statusWarn"); self.loc_status.style().polish(self.loc_status)
            try:
                g = geocoder.ip('me')
                if g.ok and g.latlng:
                    app_state.user_lat, app_state.user_lon = g.latlng[0], g.latlng[1]
                    self._load_config_map(app_state.user_lat, app_state.user_lon)
                    self.loc_status.setText(f"Auto-detected ({app_state.user_lat:.3f}, {app_state.user_lon:.3f})")
                    self.loc_status.setObjectName("statusOk"); self.loc_status.style().polish(self.loc_status)
                else:
                    self.loc_status.setText("Detection failed"); self.loc_status.setObjectName("statusError"); self.loc_status.style().polish(self.loc_status)
            except Exception:
                self.loc_status.setText("Detection failed"); self.loc_status.setObjectName("statusError"); self.loc_status.style().polish(self.loc_status)
        else:
            app_state.user_lat = app_state.user_lon = None
            self.loc_status.setText("Right-click map to set location"); self.loc_status.setObjectName("statusWarn"); self.loc_status.style().polish(self.loc_status)
            self._load_config_map(29.84, 31.32)

    def _on_map_click(self, lat, lng):
        if self.rb_manual.isChecked():
            app_state.user_lat, app_state.user_lon = lat, lng
            self._load_config_map(lat, lng)
            self.loc_status.setText(f"Selected ({lat:.3f}, {lng:.3f})")
            self.loc_status.setObjectName("statusOk"); self.loc_status.style().polish(self.loc_status)

    def _load_config_map(self, lat, lon):
        html = _build_map_html(lat, lon, enable_click=True,
                               user_marker=(app_state.user_lat is not None))
        self.map_view.setHtml(html)
        self.right_stack.setCurrentIndex(0)

    # ---------------------------------------------------------------
    #  Simulation trigger → background thread
    # ---------------------------------------------------------------
    def _trigger_simulation(self):
        if app_state.user_lat is None or app_state.user_lon is None:
            self.loc_status.setText("⚠ Set your location first!"); self.loc_status.setObjectName("statusError"); self.loc_status.style().polish(self.loc_status)
            return

        brand = self.brand_cb.currentText(); model = self.model_cb.currentText()
        battery_size = self.bsize_cb.currentText(); battery_pct = self.bat_slider.value()

        battery_cap = 0.0
        if app_state.ev_df is not None:
            df = app_state.ev_df
            match = df[(df['Brand'] == brand) & (df['Model Name'] == model) & (df['Battery Size'] == battery_size)]
            if not match.empty: battery_cap = float(match.iloc[0]['Value'])

        consumption = 20.0
        if app_state.consumption_df is not None:
            cdf = app_state.consumption_df
            match = cdf[(cdf['Brand'] == brand) & (cdf['Model Name'] == model)]
            if not match.empty: consumption = float(match.iloc[0]['Average Consumption (kWh/100km)'])

        available_energy = battery_cap * (battery_pct / 100.0)
        max_range_km = (available_energy / consumption) * 100.0 if consumption > 0 else 0

        target_pct = self.tgt_slider.value()
        target_energy = battery_cap * (target_pct / 100.0)

        app_state.brand = brand; app_state.model = model; app_state.battery_size = battery_size
        app_state.battery_pct = battery_pct; app_state.target_battery_pct = target_pct
        app_state.battery_cap = battery_cap; app_state.consumption = consumption
        app_state.available_energy = available_energy; app_state.target_energy = target_energy
        app_state.max_range_km = max_range_km; app_state.charger_pref = self.pref_cb.currentText()

        pref = app_state.charger_pref; filtered_df = app_state.stations_df.copy()
        if pref != "Any":
            filtered_df['Charger type'] = filtered_df['Charger type'].fillna('Unknown')
            filtered_df = filtered_df[filtered_df['Charger type'].str.upper() == pref.upper()]

        current_hour = datetime.now().hour + (datetime.now().minute / 60.0)

        self.run_btn.setEnabled(False); self.run_btn.setText("⏳  Calculating…")

        self._worker = SimulationWorker(filtered_df, current_hour, app_state.user_lat, app_state.user_lon, max_range_km)
        self._worker.finished.connect(self._on_sim_done)
        self._worker.error.connect(self._on_sim_error)
        self._worker.start()

    def _on_sim_error(self, msg):
        self.run_btn.setEnabled(True); self.run_btn.setText("⚡  Confirm && Find Stations")
        self.loc_status.setText(f"Error: {msg}"); self.loc_status.setObjectName("statusError"); self.loc_status.style().polish(self.loc_status)

    def _on_sim_done(self, result):
        app_state.sim_data = result
        self.run_btn.setEnabled(True); self.run_btn.setText("⚡  Confirm && Find Stations")
        self._show_results()

    # ---------------------------------------------------------------
    #  Results page
    # ---------------------------------------------------------------
    def _show_results(self):
        # Clear old results
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            elif item.layout(): self._clear_layout(item.layout())

        # Header
        hdr = QHBoxLayout()
        title = QLabel("Optimization Results"); title.setObjectName("titleLabel"); hdr.addWidget(title)
        hdr.addStretch()
        back_btn = QPushButton("← Back to Config"); back_btn.setObjectName("secondaryBtn")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor); back_btn.clicked.connect(self._go_back); hdr.addWidget(back_btn)
        hdr_w = QWidget(); hdr_w.setLayout(hdr); self.results_layout.addWidget(hdr_w)

        summary = QLabel(f"🚗 {app_state.brand} {app_state.model} ({app_state.battery_size}, {app_state.battery_cap:.0f} kWh) │ "
                         f"🔋 {app_state.battery_pct}% → {app_state.target_battery_pct}% │ "
                         f"📍 {app_state.user_lat:.4f}, {app_state.user_lon:.4f}")
        summary.setObjectName("summaryLabel"); summary.setWordWrap(True); self.results_layout.addWidget(summary)

        sim_data = app_state.sim_data
        if sim_data is None or sim_data.empty:
            dist = app_state.nearest_station_dist
            if dist < float('inf'):
                msg = f"Not enough battery to reach any station!\nClosest: {dist:.1f} km — Range: {app_state.max_range_km:.1f} km"
            else:
                msg = "No stations found in database."
            err = QLabel(msg); err.setObjectName("errorLabel"); err.setWordWrap(True); err.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.results_layout.addWidget(err)
            
            self.results_map = QWebEngineView()
            self.results_map.setMinimumHeight(320)
            self.results_layout.addWidget(self.results_map)
            self._load_results_map([])
            
            self.right_stack.setCurrentIndex(1); return

        available = sim_data[sim_data['has_available'] == True].copy()
        if available.empty:
            err = QLabel("Stations in range, but all chargers are fully occupied!")
            err.setObjectName("warnLabel"); err.setWordWrap(True); err.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.results_layout.addWidget(err)
            
            self.results_map = QWebEngineView()
            self.results_map.setMinimumHeight(320)
            self.results_layout.addWidget(self.results_map)
            self._load_results_map([])
            
            self.right_stack.setCurrentIndex(1); return

        fastest = available.sort_values('best_total_time').head(1)
        closest = available.sort_values('distance_to_user').head(1)
        top = pd.concat([fastest, closest]).drop_duplicates(subset=['Station ID'])

        markers = [{"lat": r['Latitude'], "lng": r['Longitude'], "tip": r['Name'],
                     "color": "green" if r['Station ID'] in fastest['Station ID'].values else "blue"} for _, r in top.iterrows()]

        # Build route polylines for the map
        routes = []
        for _, r in top.iterrows():
            sid = r['Station ID']
            coords = app_state.station_routes.get(sid)
            # Fallback: straight line if no Mapbox route was cached for this station
            if coords is None or len(coords) == 0:
                coords = [
                    [app_state.user_lat, app_state.user_lon],
                    [r['Latitude'], r['Longitude']]
                ]
            is_fastest = sid in fastest['Station ID'].values
            routes.append({
                "coords": coords,
                "color": "#00d2ff" if is_fastest else "#e94560",
                "tip": f"{'⚡ Fastest' if is_fastest else '📍 Closest'}: {r['Name']}"
            })

        # Map
        self.results_map = QWebEngineView()
        self.results_map.setMinimumHeight(320)
        self.results_layout.addWidget(self.results_map)
        self._load_results_map(markers, routes)

        # Two-column cards
        cols_w = QWidget(); cols_l = QHBoxLayout(cols_w); cols_l.setSpacing(15)

        left_scroll = self._make_card_column("⚡ Fastest Charging", fastest, "#00d2ff")
        right_scroll = self._make_card_column("📍 Closest Distance", closest, "#e94560")
        cols_l.addWidget(left_scroll); cols_l.addWidget(right_scroll)
        self.results_layout.addWidget(cols_w)

        # Pricing reference note
        pricing_note = QLabel("💡 Charging Prices:  DC Fast Charging — 7.67 EGP/kWh  │  AC Slow Charging — 3.97 EGP/kWh")
        pricing_note.setStyleSheet("color: #aaa; font-size: 11px; font-style: italic; padding: 5px 0;")
        pricing_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pricing_note.setWordWrap(True)
        self.results_layout.addWidget(pricing_note)

        self.right_stack.setCurrentIndex(1)

    def _load_results_map(self, markers, routes=None):
        html = _build_map_html(app_state.user_lat, app_state.user_lon, markers=markers, routes=routes, enable_click=False)
        if hasattr(self, 'results_map'):
            self.results_map.setHtml(html)

    def _make_card_column(self, title_text, df, accent):
        container = QWidget(); vl = QVBoxLayout(container); vl.setContentsMargins(0,0,0,0)
        title = QLabel(title_text); title.setStyleSheet(f"color: {accent}; font-size: 16px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter); vl.addWidget(title)

        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget(); il = QVBoxLayout(inner); il.setSpacing(10)

        for _, row in df.iterrows():
            card = QFrame(); card.setObjectName("stationCard"); cl = QVBoxLayout(card); cl.setContentsMargins(12,12,12,12)

            name = QLabel(f"{row['Name']}"); name.setObjectName("cardTitle"); name.setWordWrap(True); cl.addWidget(name)
            gov = QLabel(f"📍 {row['governrate']}"); gov.setStyleSheet("color: #888; font-size: 12px;"); cl.addWidget(gov)

            # AC row
            if row['ac_working'] > 0:
                ac_row = QHBoxLayout()
                ac_badge = QLabel("AC"); ac_badge.setObjectName("badgeAC"); ac_badge.setFixedWidth(30); ac_row.addWidget(ac_badge)
                avail_badge = QLabel(f"{row['ac_avail']}/{row['ac_working']}")
                avail_badge.setObjectName("badgeAvail" if row['ac_avail'] > 0 else "badgeOccupied"); ac_row.addWidget(avail_badge)
                t = row['ac_total_time']
                if t < float('inf'):
                    h, m = int(t), int((t - int(t)) * 60)
                    ac_row.addWidget(QLabel(f"🕐 {h}h {m}m"))
                else:
                    ac_row.addWidget(QLabel("Occupied"))
                cost = row.get('ac_charge_cost', 0)
                cost_lbl = QLabel(f"💰 {cost:.1f} EGP"); cost_lbl.setStyleSheet("color: #ffd700; font-weight: bold; font-size: 12px;"); ac_row.addWidget(cost_lbl)
                ac_row.addStretch(); cl.addLayout(ac_row)
                # Time breakdown sub-row
                if t < float('inf'):
                    drive_t = row['drive_time_hours']
                    charge_t = row.get('ac_charge_time', 0)
                    d_h, d_m = int(drive_t), int((drive_t - int(drive_t)) * 60)
                    c_h, c_m = int(charge_t), int((charge_t - int(charge_t)) * 60)
                    breakdown = QLabel(f"     🚗 Travel: {d_h}h {d_m}m  │  🔌 Charge: {c_h}h {c_m}m")
                    breakdown.setStyleSheet("color: #888; font-size: 11px; padding-left: 34px;"); cl.addWidget(breakdown)

            # DC row
            if row['dc_working'] > 0:
                dc_row = QHBoxLayout()
                dc_badge = QLabel("DC"); dc_badge.setObjectName("badgeDC"); dc_badge.setFixedWidth(30); dc_row.addWidget(dc_badge)
                avail_badge = QLabel(f"{row['dc_avail']}/{row['dc_working']}")
                avail_badge.setObjectName("badgeAvail" if row['dc_avail'] > 0 else "badgeOccupied"); dc_row.addWidget(avail_badge)
                t = row['dc_total_time']
                if t < float('inf'):
                    h, m = int(t), int((t - int(t)) * 60)
                    dc_row.addWidget(QLabel(f"🕐 {h}h {m}m"))
                else:
                    dc_row.addWidget(QLabel("Occupied"))
                cost = row.get('dc_charge_cost', 0)
                cost_lbl = QLabel(f"💰 {cost:.1f} EGP"); cost_lbl.setStyleSheet("color: #ffd700; font-weight: bold; font-size: 12px;"); dc_row.addWidget(cost_lbl)
                dc_row.addStretch(); cl.addLayout(dc_row)
                # Time breakdown sub-row
                if t < float('inf'):
                    drive_t = row['drive_time_hours']
                    charge_t = row.get('dc_charge_time', 0)
                    d_h, d_m = int(drive_t), int((drive_t - int(drive_t)) * 60)
                    c_h, c_m = int(charge_t), int((charge_t - int(charge_t)) * 60)
                    breakdown = QLabel(f"     🚗 Travel: {d_h}h {d_m}m  │  🔌 Charge: {c_h}h {c_m}m")
                    breakdown.setStyleSheet("color: #888; font-size: 11px; padding-left: 34px;"); cl.addWidget(breakdown)

            foot = QLabel(f"Distance: {row['distance_to_user']:.1f} km  │  Req: {row['required_kwh']:.1f} kWh")
            foot.setStyleSheet("color: #888; font-size: 12px; font-style: italic;"); cl.addWidget(foot)
            il.addWidget(card)

        il.addStretch(); scroll.setWidget(inner); vl.addWidget(scroll)
        return container

    def _go_back(self):
        self.right_stack.setCurrentIndex(0)
        if app_state.user_lat and app_state.user_lon:
            self._load_config_map(app_state.user_lat, app_state.user_lon)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            elif item.layout(): self._clear_layout(item.layout())

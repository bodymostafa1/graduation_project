/**
 * EV Charging Optimization System — Advanced Frontend
 */

// ── Global State ──
let map = null;
let userMarker = null;
let userLat = null;
let userLon = null;
let allStationsGeoJSON = null;
let currentMaxRangeKm = 0;
let mapLayers = [];
let stationMarkers = [];
let activePopups = [];

// Charts
let tradeoffChartInst = null;
let batteryChartInst = null;

// ── DOM References ──
const $ = id => document.getElementById(id);

const brandSelect = $('brand-select');
const modelSelect = $('model-select');
const bsizeSelect = $('battery-size-select');
const batSlider = $('bat-slider');
const batVal = $('bat-val');
const tgtSlider = $('tgt-slider');
const tgtVal = $('tgt-val');
const chargerPref = $('charger-pref');
const locStatus = $('loc-status');
const runBtn = $('run-btn');
const spinner = $('spinner');
const sidebar = $('sidebar');
const toggleSidebarBtn = $('toggle-sidebar-btn');
const heatmapToggle = $('heatmap-toggle');
const geolocateBtn = $('geolocate-btn-top');

// ═══════════════════════════════════════════════════════════════
//  INITIALIZATION
// ═══════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', async () => {
    initMap();
    await loadBrands();
    bindEvents();
});

// ── Mapbox GL JS ──
function initMap() {
    mapboxgl.accessToken = window.MAPBOX_TOKEN;

    map = new mapboxgl.Map({
        container: 'map',
        style: 'mapbox://styles/mapbox/navigation-night-v1', // Premium dark style
        center: [31.32, 29.84],  // Egypt
        zoom: 7,
        pitch: 45, // Angled for 3D feel
    });

    map.addControl(new mapboxgl.NavigationControl(), 'top-right');

    map.on('load', () => {
        // Prepare empty source for isochrone
        map.addSource('isochrone', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] }
        });

        map.addLayer({
            id: 'isochrone-fill',
            type: 'fill',
            source: 'isochrone',
            paint: {
                'fill-color': '#06b6d4',
                'fill-opacity': 0.1
            }
        });
        map.addLayer({
            id: 'isochrone-line',
            type: 'line',
            source: 'isochrone',
            paint: {
                'line-color': '#06b6d4',
                'line-width': 2,
                'line-dasharray': [2, 4]
            }
        });

        // Heatmap layer (initially hidden)
        map.addSource('stations-heat', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] }
        });

        map.addLayer({
            id: 'stations-heatmap',
            type: 'heatmap',
            source: 'stations-heat',
            layout: { 'visibility': 'none' },
            paint: {
                'heatmap-weight': 1,
                'heatmap-intensity': 1,
                'heatmap-color': [
                    'interpolate', ['linear'], ['heatmap-density'],
                    0, 'rgba(0, 0, 255, 0)',
                    0.2, 'royalblue',
                    0.4, 'cyan',
                    0.6, 'lime',
                    0.8, 'yellow',
                    1, 'red'
                ],
                'heatmap-radius': 15,
                'heatmap-opacity': 0.7
            }
        });

        // Safe to fetch and set data now that the source exists
        fetchStationsForHeatmap();
    });

    // Geocoder
    const geocoder = new MapboxGeocoder({
        accessToken: mapboxgl.accessToken,
        mapboxgl: mapboxgl,
        marker: false,
        placeholder: 'Search for a location...',
    });

    document.getElementById('geocoder-container').appendChild(geocoder.onAdd(map));

    geocoder.on('result', (e) => {
        userLat = e.result.center[1];
        userLon = e.result.center[0];
        placeUserMarker(userLat, userLon);
        setLocStatus('ok', `Origin: ${e.result.place_name || (userLat.toFixed(3) + ', ' + userLon.toFixed(3))}`);
        updateIsochrone();
    });

    // Right-click to set location
    map.on('contextmenu', async (e) => {
        userLat = e.lngLat.lat;
        userLon = e.lngLat.lng;
        placeUserMarker(userLat, userLon);
        setLocStatus('warn', `Resolving address...`);
        updateIsochrone();
        const address = await reverseGeocode(userLat, userLon);
        setLocStatus('ok', `Origin: ${address}`);
    });
}

function placeUserMarker(lat, lon) {
    if (userMarker) userMarker.remove();

    const el = document.createElement('div');
    el.className = 'user-marker-dot';

    userMarker = new mapboxgl.Marker({ element: el })
        .setLngLat([lon, lat])
        .addTo(map);
}

function setLocStatus(type, text) {
    locStatus.className = 'loc-status ' + type;
    locStatus.textContent = text;
}

async function reverseGeocode(lat, lon) {
    try {
        const url = `https://api.mapbox.com/geocoding/v5/mapbox.places/${lon},${lat}.json?access_token=${mapboxgl.accessToken}&limit=1`;
        const res = await fetch(url);
        if (!res.ok) throw new Error("Reverse geocoding failed");
        const data = await res.json();
        if (data.features && data.features.length > 0) {
            return data.features[0].place_name;
        }
    } catch (e) {
        console.error("Reverse geocoding error:", e);
    }
    return `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
}

// ═══════════════════════════════════════════════════════════════
//  API HELPERS & DROPDOWNS
// ═══════════════════════════════════════════════════════════════

async function api(url, options = {}) {
    const res = await fetch(url, options);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
}

async function loadBrands() {
    const data = await api('/api/brands');
    populateSelect(brandSelect, data.brands);
    await loadModels();
}

async function loadModels() {
    const brand = brandSelect.value;
    const data = await api(`/api/models/${encodeURIComponent(brand)}`);
    populateSelect(modelSelect, data.models);
    await loadBatterySizes();
}

async function loadBatterySizes() {
    const brand = brandSelect.value;
    const model = modelSelect.value;
    const data = await api(`/api/battery-sizes/${encodeURIComponent(brand)}/${encodeURIComponent(model)}`);
    populateSelect(bsizeSelect, data.sizes);
    updateIsochrone(); // Update range estimation
}

function populateSelect(selectEl, items) {
    selectEl.innerHTML = '';
    items.forEach(item => {
        const opt = document.createElement('option');
        opt.value = item;
        opt.textContent = item;
        selectEl.appendChild(opt);
    });
}

// ═══════════════════════════════════════════════════════════════
//  EVENT BINDINGS
// ═══════════════════════════════════════════════════════════════

function bindEvents() {
    brandSelect.addEventListener('change', loadModels);
    modelSelect.addEventListener('change', loadBatterySizes);
    bsizeSelect.addEventListener('change', updateIsochrone);

    batSlider.addEventListener('input', () => {
        batVal.textContent = batSlider.value + '%';
        if (parseInt(tgtSlider.value) < parseInt(batSlider.value)) {
            tgtSlider.value = batSlider.value;
            tgtVal.textContent = batSlider.value + '%';
        }
        updateIsochrone();
    });

    tgtSlider.addEventListener('input', () => {
        if (parseInt(tgtSlider.value) < parseInt(batSlider.value)) {
            tgtSlider.value = batSlider.value;
        }
        tgtVal.textContent = tgtSlider.value + '%';
    });

    if (geolocateBtn) {
        geolocateBtn.addEventListener('click', () => {
            if (!navigator.geolocation) {
                setLocStatus('error', 'Geolocation not supported by browser');
                return;
            }

            setLocStatus('warn', 'Acquiring GPS lock...');
            navigator.geolocation.getCurrentPosition(
                async (position) => {
                    userLat = position.coords.latitude;
                    userLon = position.coords.longitude;
                    placeUserMarker(userLat, userLon);
                    map.flyTo({ center: [userLon, userLat], zoom: 12 });
                    setLocStatus('warn', `Resolving address...`);
                    updateIsochrone();
                    const address = await reverseGeocode(userLat, userLon);
                    setLocStatus('ok', `Origin: ${address}`);
                },
                (error) => {
                    console.error("Geolocation error:", error);
                    setLocStatus('error', 'Could not get location (check permissions)');
                },
                { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
            );
        });
    }



    runBtn.addEventListener('click', runSimulation);

    toggleSidebarBtn.addEventListener('click', () => {
        sidebar.classList.toggle('collapsed');
    });

    heatmapToggle.addEventListener('change', (e) => {
        if (!map.getLayer('stations-heatmap')) return;
        map.setLayoutProperty('stations-heatmap', 'visibility', e.target.checked ? 'visible' : 'none');
    });
}



// ═══════════════════════════════════════════════════════════════
//  GEOSPATIAL INTERACTIONS (Isochrones & Heatmap)
// ═══════════════════════════════════════════════════════════════

async function fetchStationsForHeatmap() {
    try {
        const data = await api('/api/stations');
        const features = data.stations.map(s => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [s.lng, s.lat] }
        }));
        allStationsGeoJSON = { type: 'FeatureCollection', features };

        if (map.getSource('stations-heat')) {
            map.getSource('stations-heat').setData(allStationsGeoJSON);
        }
    } catch (e) { console.error("Heatmap fetch error:", e); }
}

// Fast rough estimation of range based on battery %
function estimateRange() {
    // We don't have the exact consumption via JS before calling `/api/simulate`,
    // so we approximate. ~20 kWh/100km.
    const batStr = bsizeSelect.value;
    if (!batStr || batStr === 'No Data') return 0;

    // Extract number from string like "75 kWh"
    const capMatch = batStr.match(/(\d+(\.\d+)?)/);
    if (!capMatch) return 0;
    const capacity = parseFloat(capMatch[1]);
    const currentPct = parseInt(batSlider.value) / 100;

    const availableKwh = capacity * currentPct;
    return (availableKwh / 20.0) * 100; // rough max range km
}

function updateIsochrone() {
    if (!userLat || !userLon) return;
    const maxRange = estimateRange();

    if (maxRange <= 0) {
        clearIsochrone();
        return;
    }

    // Use Turf to create a polygon representing the range (as a circle)
    const center = [userLon, userLat];
    const options = { steps: 64, units: 'kilometers' };
    const circle = turf.circle(center, maxRange, options);

    if (map.getSource('isochrone')) {
        map.getSource('isochrone').setData(circle);
    }
}

function clearIsochrone() {
    if (map.getSource('isochrone')) {
        map.getSource('isochrone').setData({ type: 'FeatureCollection', features: [] });
    }
}

// ═══════════════════════════════════════════════════════════════
//  SIMULATION
// ═══════════════════════════════════════════════════════════════

async function runSimulation() {
    if (userLat === null || userLon === null) {
        setLocStatus('error', 'Origin required for calculation');
        return;
    }

    runBtn.disabled = true;
    spinner.classList.add('active');

    const payload = {
        brand: brandSelect.value,
        model: modelSelect.value,
        battery_size: bsizeSelect.value,
        battery_pct: parseInt(batSlider.value),
        target_battery_pct: parseInt(tgtSlider.value),
        charger_pref: chargerPref.value,
        user_lat: userLat,
        user_lon: userLon,
    };

    try {
        const result = await api('/api/simulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        // Hide sidebar on mobile or shrink it
        if (window.innerWidth < 900) sidebar.classList.add('collapsed');

        showResults(result);
    } catch (err) {
        alert("Optimization failed: " + err.message);
    } finally {
        runBtn.disabled = false;
        spinner.classList.remove('active');
    }
}

// ═══════════════════════════════════════════════════════════════
//  RESULTS RENDERING & DATA VIZ
// ═══════════════════════════════════════════════════════════════

function showResults(data) {
    clearResultsOnMap();

    if (data.status !== 'ok') {
        alert(data.message);
        return;
    }

    const stations = data.stations;
    drawRoutesOnMap(stations, data.max_range_km);
}

function clearResultsOnMap() {
    // Reverse mapLayers so layers are removed before the sources they depend on
    mapLayers.slice().reverse().forEach(id => {
        if (map.getLayer(id)) map.removeLayer(id);
        if (map.getSource(id)) map.removeSource(id);
    });
    mapLayers = [];

    stationMarkers.forEach(m => m.remove());
    stationMarkers = [];

    activePopups.forEach(p => p.remove());
    activePopups = [];
}

function drawRoutesOnMap(stations, maxRange) {
    const bounds = new mapboxgl.LngLatBounds();
    bounds.extend([userLon, userLat]);

    stations.forEach((st, idx) => {
        const routeCoords = st.route_coords.map(c => [c[1], c[0]]); // to [lng, lat]
        const sourceId = `route-${idx}`;
        const typeStr = st.is_fastest ? 'fastest' : 'closest';

        map.addSource(sourceId, {
            type: 'geojson',
            data: {
                type: 'Feature',
                properties: {},
                geometry: {
                    type: 'LineString',
                    coordinates: routeCoords,
                }
            }
        });
        mapLayers.push(sourceId);

        // Blue for fastest, Red for closest
        const color = st.is_fastest ? '#3b82f6' : '#ef4444';

        map.addLayer({
            id: `route-line-${idx}`,
            type: 'line',
            source: sourceId,
            paint: {
                'line-width': 6,
                'line-color': color
            }
        });
        mapLayers.push(`route-line-${idx}`);

        // Create the popup
        const popup = createStationPopup(st);
        popup.addTo(map);
        activePopups.push(popup);

        // Station marker
        const stEl = document.createElement('div');
        stEl.className = `station-marker ${typeStr}`;
        stEl.innerHTML = st.is_fastest ? '⚡' : '📍';

        stEl.addEventListener('click', () => {
            map.flyTo({ center: [st.lng, st.lat], zoom: 14, pitch: 60 });
            if (!popup.isOpen()) {
                popup.addTo(map);
            }
        });

        const marker = new mapboxgl.Marker({ element: stEl })
            .setLngLat([st.lng, st.lat])
            .addTo(map);

        stationMarkers.push(marker);

        routeCoords.forEach(c => bounds.extend(c));
    });

    map.fitBounds(bounds, { padding: 80, maxZoom: 13 });
}

function formatTime(hours) {
    if (hours === null || hours === undefined) return '--';
    const h = Math.floor(hours);
    const m = Math.round((hours - h) * 60);
    if (h === 0) return `${m}m`;
    return `${h}h ${m}m`;
}

function createStationPopup(st) {
    const badgeHTML = st.is_fastest 
        ? `<div class="popup-category-badge tag-fastest">⚡ Fastest Charging</div>`
        : `<div class="popup-category-badge tag-closest">📍 Closest Distance</div>`;

    const popupHTML = `
        <div class="station-popup-card">
            ${badgeHTML}
            <h4>${st.name}</h4>
            <p class="gov">📍 ${st.governrate}</p>
            <div class="charger-info">
                ${st.ac ? `
                <div class="charger-row">
                    <span class="charger-tag tag-ac">AC</span>
                    <span class="charger-tag tag-avail">${st.ac.available}/${st.ac.working}</span>
                    <div class="charger-time-cost">
                        <span>⏱ ${formatTime(st.ac.total_time_h)}</span>
                        <span class="cost-text">💰 ${st.ac.cost_egp} EGP</span>
                    </div>
                </div>
                <div class="charger-details">
                    🚗 Drive Time: ${formatTime(st.ac.drive_time_h)} (${st.distance_km} km) | 🔌 Charge: ${formatTime(st.ac.charge_time_h)}
                </div>
                ` : ''}
                
                ${st.dc ? `
                <div class="charger-row">
                    <span class="charger-tag tag-dc">DC</span>
                    <span class="charger-tag tag-avail">${st.dc.available}/${st.dc.working}</span>
                    <div class="charger-time-cost">
                        <span>⏱ ${formatTime(st.dc.total_time_h)}</span>
                        <span class="cost-text">💰 ${st.dc.cost_egp} EGP</span>
                    </div>
                </div>
                <div class="charger-details">
                    🚗 Drive Time: ${formatTime(st.dc.drive_time_h)} (${st.distance_km} km) | 🔌 Charge: ${formatTime(st.dc.charge_time_h)}
                </div>
                ` : ''}
            </div>
        </div>
    `;

    // Create Mapbox Popup
    const popup = new mapboxgl.Popup({
        closeOnClick: false,
        closeButton: true,
        anchor: 'bottom',
        offset: 25
    })
    .setLngLat([st.lng, st.lat])
    .setHTML(popupHTML);

    // Setup listener to attach click event when DOM element is rendered
    popup.on('open', () => {
        const element = popup.getElement();
        if (element) {
            const card = element.querySelector('.station-popup-card');
            if (card) {
                card.onclick = () => {
                    window.open(`https://www.google.com/maps/dir/?api=1&destination=${st.lat},${st.lng}`, '_blank');
                };
            }
        }
    });

    return popup;
}

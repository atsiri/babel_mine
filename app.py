# ─────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────
import json
import base64
import pandas as pd
import geopandas as gpd
from shapely import wkt
from shapely.geometry import Point
import streamlit as st
import streamlit.components.v1 as components
import sys
import types

# --- 1. PASSWORD PROTECTION ---
def check_password():
    """Returns True if the user had the correct password."""
    # NOTE: If you just want to run this without password, change this function to return True immediately.
    # return True 
    
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    if st.session_state["password_correct"]:
        return True

    placeholder = st.empty()
    with placeholder.container():
        st.write("## 🔒 Dashboard Login")
        # Defaulting to a simple check if secrets are not set up, for demonstration purposes
        if "APP_PASSWORD" not in st.secrets:
            st.warning("⚠️ 'APP_PASSWORD' not found in secrets.toml. allowing access for demo.")
            return True
            
        password = st.text_input("Password", type="password")
        if password:
            if password == st.secrets["APP_PASSWORD"]:
                st.session_state["password_correct"] = True
                placeholder.empty()
                st.rerun()
            else:
                st.error("😕 Password incorrect")
    return False

if check_password(): 
    # ───────────────────────────────────────────────────────────────
    # PAGE CONFIG  (must be the very first Streamlit call)
    # ───────────────────────────────────────────────────────────────
    st.set_page_config(
        page_title="Data Tambang Timah Bangka Belitung",
        page_icon="🗺️",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # Remove all Streamlit chrome so the map fills 100 % of viewport
    st.markdown(
        """
        <style>
            #MainMenu, header, footer          { visibility: hidden; }
            .block-container                   { padding: 0 !important; margin: 0 !important; max-width: 100% !important; }
            [data-testid="stAppViewContainer"] { padding: 0 !important; }
            [data-testid="stVerticalBlock"]    { gap: 0 !important; padding: 0 !important; }
            iframe                             { display: block; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ───────────────────────────────────────────────────────────────
    # CONSTANTS
    # ───────────────────────────────────────────────────────────────
    MAX_HEIGHT_PX  = 960
    DATA_PATH      = "data_wiup_timah_babel.csv"
    GEOM_TOLERANCE = 0.002
    COMPACT        = (",", ":")

    # ───────────────────────────────────────────────────────────────
    # HELPER — safe WKT parser
    # ───────────────────────────────────────────────────────────────
    def safe_wkt_load(val):
        if pd.isna(val) or not isinstance(val, str) or not val.strip():
            return None
        try:
            return wkt.loads(val.strip())
        except Exception:
            return None


    # ───────────────────────────────────────────────────────────────
    # HELPER — CSV → GeoDataFrame
    # ───────────────────────────────────────────────────────────────
    def df_to_gdf(df, label="layer"):
        df.columns = [str(c).strip().upper() for c in df.columns]

        # Try WKT geometry columns first
        for candidate in ["GEOMETRY", "GEOM", "WKT", "SHAPE"]:
            if candidate in df.columns:
                df[candidate] = (
                    df[candidate].astype(str).replace({"nan": None, "None": None, "": None})
                )
                df["geometry_parsed"] = df[candidate].apply(safe_wkt_load)
                df = df.dropna(subset=["geometry_parsed"]).copy()
                df = df.drop(columns=[candidate]).rename(columns={"geometry_parsed": "geometry"})
                return gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")

        # Fall back to lat/lon columns
        lat_col = next((c for c in ["LAT", "LATITUDE", "Y", "LAT_DD"]  if c in df.columns), None)
        lon_col = next((c for c in ["LON", "LNG", "LONGITUDE", "X", "LON_DD"] if c in df.columns), None)
        if lat_col and lon_col:
            df   = df.dropna(subset=[lat_col, lon_col]).copy()
            geom = [Point(row[lon_col], row[lat_col]) for _, row in df.iterrows()]
            return gpd.GeoDataFrame(df, geometry=geom, crs="EPSG:4326")

        raise ValueError(f"[{label}] No usable geometry column found.")


    # ───────────────────────────────────────────────────────────────
    # HELPER — clean & simplify polygon geometries
    # ───────────────────────────────────────────────────────────────
    def process_geometries(gdf, tolerance=GEOM_TOLERANCE):
        geom_types = gdf.geometry.geom_type.unique()
        if not any(g in geom_types for g in ["Polygon", "MultiPolygon"]):
            return gdf
        gdf = gdf.copy()
        gdf["geometry"] = gdf.geometry.apply(
            lambda g: g.buffer(0) if g and not g.is_empty else g
        )
        gdf["geometry"] = gdf.geometry.simplify(tolerance, preserve_topology=True)
        gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
        return gdf


    # ───────────────────────────────────────────────────────────────
    # HELPER — detect dominant geometry type
    # ───────────────────────────────────────────────────────────────
    def get_geom_type(gdf):
        types = gdf.geometry.geom_type.unique()
        if any(g in types for g in ["Polygon", "MultiPolygon"]):        return "polygon"
        if any(g in types for g in ["LineString", "MultiLineString"]):  return "polyline"
        return "point"


    # ───────────────────────────────────────────────────────────────
    # HELPER — build a unique-value renderer dict (JSON string)
    # ───────────────────────────────────────────────────────────────
    def build_unique_value_renderer(geom_type, field, class_defs):
        """
        class_defs : list of (value, fill_rgb_tuple, outline_hex, label)
        """
        def make_symbol(fill_rgb, outline_hex):
            fill_color = list(fill_rgb[:3]) + [160]
            if geom_type == "polygon":
                return {
                    "type"   : "simple-fill",
                    "color"  : fill_color,
                    "outline": {"color": outline_hex, "width": 1.5, "style": "solid"},
                }
            if geom_type == "polyline":
                return {"type": "simple-line", "color": outline_hex, "width": 2, "style": "solid"}
            return {
                "type"   : "simple-marker",
                "color"  : fill_color,
                "outline": {"color": outline_hex, "width": 1.5},
                "size"   : 8,
            }

        infos = [
            {"value": val, "label": lbl, "symbol": make_symbol(rgb, hex_)}
            for val, rgb, hex_, lbl in class_defs
        ]

        renderer = {
            "type"            : "unique-value",
            "field"           : field,
            "uniqueValueInfos": infos,
            "defaultSymbol"   : make_symbol([180, 180, 180], "#999999"),
            "defaultLabel"    : "Lainnya",
        }
        return json.dumps(renderer, separators=COMPACT)


    # ───────────────────────────────────────────────────────────────
    # LOAD & PREPARE DATA  (cached)
    # ───────────────────────────────────────────────────────────────
    @st.cache_data(show_spinner="Loading and processing spatial data…")
    def load_and_prepare_data():
        df  = pd.read_csv(DATA_PATH)
        gdf = df_to_gdf(df, label="WIUP")
        gdf = process_geometries(gdf)
        # Serialise datetime columns
        for col in gdf.select_dtypes(include=["datetime64", "datetimetz"]).columns:
            gdf[col] = gdf[col].astype(str)
        return gdf


    gdf_wiup = load_and_prepare_data()

    # ───────────────────────────────────────────────────────────────
    # DERIVED METRICS
    # ───────────────────────────────────────────────────────────────
    n_wiup        = len(gdf_wiup)
    gtype_wiup    = get_geom_type(gdf_wiup)
    status_counts = gdf_wiup["STATUS_IUP"].str.upper().value_counts()
    n_aktif       = int(status_counts.get("AKTIF",      0))
    n_kadaluarsa  = int(status_counts.get("KADALUARSA", 0))

    # ───────────────────────────────────────────────────────────────
    # RENDERER
    # ───────────────────────────────────────────────────────────────
    STATUS_IUP_CLASSES = [
        ("AKTIF",      [33, 150, 243], "#0D47A1", "AKTIF"),
        ("KADALUARSA", [229,  57,  53], "#B71C1C", "KADALUARSA"),
    ]
    renderer_wiup_json = build_unique_value_renderer(gtype_wiup, "STATUS_IUP", STATUS_IUP_CLASSES)

    # ───────────────────────────────────────────────────────────────
    # ENCODE GEOJSON AS BASE-64
    # ───────────────────────────────────────────────────────────────
    b64_geodata = base64.b64encode(
        gdf_wiup.to_json().encode("utf-8")
    ).decode("utf-8")

    # ───────────────────────────────────────────────────────────────
    # UNIQUE NAMA_PERUSAHAAN LIST  (for dropdown filter)
    # ───────────────────────────────────────────────────────────────
    nama_perusahaan_json = json.dumps(
        sorted(gdf_wiup["NAMA_PERUSAHAAN"].dropna().astype(str).unique().tolist()),
        ensure_ascii=False,
        separators=COMPACT,
    )

    # ═══════════════════════════════════════════════════════════════
    # BUILD HTML
    # ═══════════════════════════════════════════════════════════════
    HTML = f"""<!DOCTYPE html>
    <html lang="id">
    <head>
    <meta charset="utf-8">
    <meta name="viewport" content="initial-scale=1,maximum-scale=1,user-scalable=no">
    <title>Data Tambang Timah Bangka Belitung</title>
    <link rel="stylesheet" href="https://js.arcgis.com/4.28/esri/themes/light/main.css">

    <!-- ═══════════ STYLES ═══════════ -->
    <style>
    /* ── Reset & tokens ── */
    :root {{
        --blue    : #1565C0;
        --panel   : #ffffff;
        --shadow  : 0 2px 16px rgba(0,0,0,.22);
        --header-h: 52px;
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{
        width: 100%; height: 100%;
        overflow: hidden;
        font-family: "Segoe UI", Arial, sans-serif;
        background: #0d1b2a;
    }}

    /* ── Header bar ── */
    #mapHeader {{
        position  : absolute;
        top: 0; left: 0; right: 0;
        height    : var(--header-h);
        background: linear-gradient(90deg, #0d1b2a 0%, #1a3a5c 55%, #1565C0 100%);
        display   : flex;
        align-items: center;
        padding   : 0 18px;
        z-index   : 200;
        box-shadow: 0 2px 10px rgba(0,0,0,.4);
        gap       : 10px;
    }}
    .header-title {{
        color         : #e0f7fa;
        font-size     : 15px;
        font-weight   : 700;
        letter-spacing: .04em;
        white-space   : nowrap;
    }}
    .header-sub {{
        color      : #78909c;
        font-size  : 11px;
        white-space: nowrap;
    }}
    .header-divider {{
        width : 1px; height: 22px;
        background: rgba(255,255,255,.18);
        margin: 0 4px;
    }}

    /* ── Header stat chips ── */
    .header-stats {{ margin-left: auto; display: flex; gap: 8px; align-items: center; }}
    .stat-chip {{
        display       : flex;
        flex-direction: column;
        align-items   : center;
        padding       : 3px 14px;
        border-radius : 8px;
        border        : 1px solid rgba(255,255,255,.15);
        min-width     : 74px;
        line-height   : 1.3;
    }}
    .stat-label {{
        font-size     : 9px; font-weight: 600;
        text-transform: uppercase; letter-spacing: .06em;
        color: #cfd8dc; opacity: .8;
    }}
    .stat-value {{ font-size: 15px; font-weight: 800; letter-spacing: .02em; }}
    .stat-total      {{ background: rgba(79,195,247,.12);  border-color: rgba(79,195,247,.35); }}
    .stat-total      .stat-value {{ color: #4fc3f7; }}
    .stat-aktif      {{ background: rgba(33,150,243,.15);  border-color: rgba(13,71,161,.5); }}
    .stat-aktif      .stat-value {{ color: #64b5f6; }}
    .stat-kadaluarsa {{ background: rgba(229,57,53,.15);   border-color: rgba(183,28,28,.5); }}
    .stat-kadaluarsa .stat-value {{ color: #ef9a9a; }}

    /* ── Map container ── */
    #viewDiv {{
        position: absolute;
        top: var(--header-h);
        left: 0; right: 0; bottom: 0;
    }}

    /* ── Loading overlay ── */
    #loadingOverlay {{
        position: absolute;
        top: var(--header-h); left: 0; right: 0; bottom: 0;
        background: #0d1b2a;
        display: flex; flex-direction: column;
        align-items: center; justify-content: center;
        z-index: 9999;
        transition: opacity .5s ease;
    }}
    #loadingOverlay.hidden {{ opacity: 0; pointer-events: none; }}
    .spinner {{
        width: 52px; height: 52px;
        border: 5px solid rgba(255,255,255,.12);
        border-top-color: #4fc3f7;
        border-radius: 50%;
        animation: spin .85s linear infinite;
        margin-bottom: 18px;
    }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    #loadText {{ color: #e0f7fa; font-size: 15px; letter-spacing: .03em; }}
    #loadSub  {{ color: #78909c; font-size: 12px; margin-top: 7px; }}

    /* ── Control panel ── */
    #controlPanel {{
        position    : absolute;
        top         : calc(var(--header-h) + 15px);
        right       : 15px;
        background  : var(--panel);
        padding     : 14px 16px;
        border-radius: 10px;
        box-shadow  : var(--shadow);
        font-size   : 13px;
        z-index     : 99;
        width       : 295px;
        max-height  : calc(100vh - var(--header-h) - 30px);
        overflow-y  : auto;
    }}
    #controlPanel h3 {{
        margin: 0 0 10px; font-size: 14px; font-weight: 700;
        color : #1a237e; border-bottom: 2px solid var(--blue);
        padding-bottom: 6px;
    }}
    .section-label {{
        font-size: 10px; font-weight: 700; color: #888;
        text-transform: uppercase; letter-spacing: .04em;
        margin: 10px 0 5px;
    }}
    hr.divider {{ border: none; border-top: 1px solid #eee; margin: 10px 0; }}

    /* ── Basemap radios ── */
    .basemap-group {{ display: flex; gap: 8px; margin-top: 4px; flex-wrap: wrap; }}
    .basemap-radio {{
        display: flex; align-items: center; gap: 5px;
        cursor: pointer; font-size: 12px;
        padding: 5px 10px; border: 1px solid #ccc;
        border-radius: 20px; user-select: none;
    }}
    .basemap-radio:has(input:checked) {{
        border-color: var(--blue); background: #e3f0fb;
        font-weight: 600; color: var(--blue);
    }}
    .basemap-radio input {{ display: none; }}

    /* ── STATUS_IUP checkbox filter ── */
    .status-group {{ display: flex; gap: 8px; margin-top: 4px; flex-wrap: wrap; }}
    .status-check {{
        display: flex; align-items: center; gap: 6px;
        cursor: pointer; font-size: 12px; font-weight: 600;
        padding: 5px 12px; border: 1.5px solid #ccc;
        border-radius: 20px; user-select: none;
        transition: all .15s ease;
    }}
    .status-check input {{ display: none; }}
    .status-check.aktif:has(input:checked) {{
        border-color: #0D47A1; background: rgba(33,150,243,.12); color: #0D47A1;
    }}
    .status-check.kadaluarsa:has(input:checked) {{
        border-color: #B71C1C; background: rgba(229,57,53,.10); color: #B71C1C;
    }}
    .status-dot {{
        width: 9px; height: 9px;
        border-radius: 50%; flex-shrink: 0;
    }}
    #statusFilterMsg {{ font-size: 11px; color: #666; margin-top: 4px; display: block; }}

    /* ── Nama Perusahaan filter ── */
    #filterSection select {{
        width: 100%; padding: 6px 8px;
        border: 1px solid #c5cae9; border-radius: 6px;
        font-size: 12px; color: #1a237e;
        background: #f5f7ff; cursor: pointer; outline: none;
        margin-top: 3px; appearance: auto;
    }}
    #filterSection select:focus {{
        border-color: var(--blue);
        box-shadow: 0 0 0 2px rgba(21,101,192,.15);
    }}
    #filterCountMsg {{ font-size: 11px; color: #666; margin-top: 4px; display: block; }}
    #clearFilterBtn {{
        margin-top: 5px; width: 100%; padding: 5px 0;
        font-size: 11px; cursor: pointer;
        background: #e3f0fb; border: 1px solid #90caf9;
        border-radius: 5px; color: var(--blue);
        font-weight: 600; display: none;
    }}
    #clearFilterBtn:hover {{ background: #bbdefb; }}

    /* ── Layer toggles ── */
    .layer-row  {{ margin: 6px 0; }}
    .layer-top  {{ display: flex; align-items: center; gap: 7px; }}
    .opacity-row {{
        display: flex; align-items: center; gap: 6px;
        margin-top: 3px; padding-left: 24px;
    }}
    .opacity-row input[type="range"] {{
        flex: 1; height: 3px; cursor: pointer; accent-color: var(--blue);
    }}
    .swatch {{ width: 16px; height: 16px; border-radius: 3px; flex-shrink: 0; }}

    /* ── Status message ── */
    #statusMsg  {{ display: block; font-size: 11px; color: #555; margin-top: 15px; line-height: 1.5; }}
    .ok-text    {{ color: #2e7d32; font-weight: 600; }}
    .error-text {{ color: #c62828; font-weight: 600; }}

    /* ── Hover tooltip ── */
    #hoverTooltip {{
        position: absolute; pointer-events: none; z-index: 500;
        background: rgba(15,25,45,.95); color: #e8f4fd;
        border: 1px solid rgba(79,195,247,.4); border-radius: 8px;
        padding: 10px 14px; font-size: 12px; line-height: 1.6;
        max-width: 300px; box-shadow: 0 4px 20px rgba(0,0,0,.5);
        display: none; backdrop-filter: blur(4px);
    }}
    #hoverTooltip.visible {{ display: block; }}
    #hoverTooltip .tt-title {{
        font-size: 13px; font-weight: 700; color: #4fc3f7;
        margin-bottom: 7px; border-bottom: 1px solid rgba(79,195,247,.25);
        padding-bottom: 5px;
    }}
    #hoverTooltip .tt-row  {{ display: flex; gap: 6px; margin: 2px 0; }}
    #hoverTooltip .tt-key  {{ color: #90caf9; min-width: 80px; flex-shrink: 0; }}
    #hoverTooltip .tt-val  {{ color: #e0f7fa; word-break: break-word; }}
    </style>

    <!-- ═══════════ DATA INJECTION ═══════════ -->
    <script>
    const rWiup          = {renderer_wiup_json};
    const b64GeoData     = "{b64_geodata}";
    const geomType       = "{gtype_wiup}";
    const namaPerusahaan = {nama_perusahaan_json};
    </script>

    <!-- ═══════════ ARCGIS API ═══════════ -->
    <script src="https://js.arcgis.com/4.28/"></script>
    <script>
    require([
    "esri/Map",
    "esri/views/MapView",
    "esri/layers/FeatureLayer",
    "esri/Graphic",
    "esri/widgets/LayerList",
    "esri/widgets/Legend",
    "esri/widgets/Expand",
    "esri/layers/WebTileLayer",
    "esri/Basemap"
    ], function(Map, MapView, FeatureLayer, Graphic,
                LayerList, Legend, Expand, WebTileLayer, Basemap) {{

    // ── DOM refs ──
    const overlay   = document.getElementById("loadingOverlay");
    const loadSubEl = document.getElementById("loadSub");
    const statusEl  = document.getElementById("statusMsg");

    // ── Decode base-64 GeoJSON ──
    loadSubEl.textContent = "Decoding geometry data…";
    let geojson;
    try {{
        geojson = JSON.parse(atob(b64GeoData));
    }} catch(e) {{
        statusEl.innerHTML = "<span class='error-text'>GeoJSON decode failed: " + e.message + "</span>";
        overlay.classList.add("hidden");
        throw e;
    }}

    // ── GeoJSON → ArcGIS Graphics ──
    loadSubEl.textContent = "Building graphics…";

    const GEOJSON_TO_ESRI = {{
        Point           : "point",
        MultiPoint      : "multipoint",
        LineString      : "polyline",
        MultiLineString : "polyline",
        Polygon         : "polygon",
        MultiPolygon    : "polygon",
    }};

    const sampleProps = ((geojson.features[0] || {{}}).properties) || {{}};
    const fields = [
        {{ name: "OBJECTID", alias: "OBJECTID", type: "oid" }},
        ...Object.keys(sampleProps).map(k => ({{
        name : k,
        alias: k.replace(/_/g, " "),
        type : (typeof sampleProps[k] === "number") ? "double" : "string",
        }}))
    ];

    const graphics = [];
    geojson.features.forEach((feat, idx) => {{
        if (!feat.geometry) return;
        const esriType = GEOJSON_TO_ESRI[feat.geometry.type];
        if (!esriType) return;

        let esriGeom = null;
        try {{
        const raw = feat.geometry;
        if (esriType === "point") {{
            esriGeom = {{
            type: "point",
            x: raw.coordinates[0], y: raw.coordinates[1],
            spatialReference: {{ wkid: 4326 }},
            }};
        }} else if (esriType === "polyline") {{
            const paths = raw.type === "LineString" ? [raw.coordinates] : raw.coordinates;
            esriGeom = {{ type: "polyline", paths, spatialReference: {{ wkid: 4326 }} }};
        }} else if (esriType === "polygon") {{
            let rings = [];
            if (raw.type === "Polygon") {{
            rings = raw.coordinates;
            }} else {{
            raw.coordinates.forEach(poly => poly.forEach(ring => rings.push(ring)));
            }}
            esriGeom = {{ type: "polygon", rings, spatialReference: {{ wkid: 4326 }} }};
        }}
        }} catch(e) {{ return; }}

        if (!esriGeom) return;
        graphics.push(new Graphic({{
        geometry  : esriGeom,
        attributes: Object.assign({{ OBJECTID: idx + 1 }}, feat.properties || {{}}),
        }}));
    }});

    console.log("Graphics built:", graphics.length);

    // ── Client-side FeatureLayer ──
    const layerWiup = new FeatureLayer({{
        source       : graphics,
        fields,
        objectIdField: "OBJECTID",
        geometryType : geomType === "polygon"  ? "polygon"
                    : geomType === "polyline" ? "polyline"
                    : "point",
        spatialReference: {{ wkid: 4326 }},
        title        : "WIUP",
        renderer     : rWiup,
        outFields    : ["*"],
        popupEnabled : false,
    }});

    // ── CartoDB Positron basemap ──
    const cartoPositron = new Basemap({{
        baseLayers: [new WebTileLayer({{
        urlTemplate: "https://{{subDomain}}.basemaps.cartocdn.com/light_all/{{level}}/{{col}}/{{row}}.png",
        subDomains : ["a","b","c","d"],
        copyright  : "Map tiles by CartoDB, CC BY 3.0 — Data © OpenStreetMap contributors, ODbL.",
        }})],
        id: "carto-positron",
    }});

    // ── Map & View ──
    const map  = new Map({{ basemap: "satellite" }});
    const view = new MapView({{
        container: "viewDiv",
        map,
        center: [106.0, -2.5],
        zoom  : 7,
        ui    : {{ components: ["zoom", "compass"] }},
    }});

    map.add(layerWiup);

    // ── Wait for view & layer to be ready ──
    loadSubEl.textContent = "Rendering map…";

    view.when(() => {{
        const safetyTimer = setTimeout(() => {{
        overlay.classList.add("hidden");
        statusEl.innerHTML = "<span class='error-text'>Warning: render is taking longer than expected.</span>";
        }}, 12000);

        layerWiup.when(() => {{
        clearTimeout(safetyTimer);
        overlay.classList.add("hidden");
        setTimeout(() => overlay.remove(), 600);
        statusEl.innerHTML =
            "<span class='ok-text'>Map ready.</span> {n_wiup} WIUP features loaded.";
        }}).catch(err => {{
        clearTimeout(safetyTimer);
        overlay.classList.add("hidden");
        statusEl.innerHTML = "<span class='error-text'>Layer error: " + err.message + "</span>";
        console.error("FeatureLayer error:", err);
        }});

        // ── Built-in widgets ──
        view.ui.add(new Expand({{ view, content: new LayerList({{ view }}), expanded: false }}), "bottom-right");
        view.ui.add(new Expand({{ view, content: new Legend({{ view }}),    expanded: false }}), "bottom-left");

        // ── Layer visibility & opacity ──
        const chkWiup  = document.getElementById("chkWiup");
        const opSlider = document.getElementById("opWiup");
        const opValEl  = document.getElementById("opWiupVal");

        function applyOpacity(pct) {{
        layerWiup.opacity = pct / 100;
        opValEl.textContent = pct + "%";
        }}
        applyOpacity(parseInt(opSlider.value, 10));
        chkWiup.addEventListener("change",  () => layerWiup.visible = chkWiup.checked);
        opSlider.addEventListener("input",  function() {{ applyOpacity(parseInt(this.value, 10)); }});

        // ── Basemap toggle ──
        document.querySelectorAll('input[name="basemap"]').forEach(radio => {{
        radio.addEventListener("change", function() {{
            map.basemap = this.value === "carto-positron" ? cartoPositron : this.value;
        }});
        }});

        // ════════════════════════════════════════════
        //  COMBINED FILTER STATE
        //  Both STATUS_IUP and NAMA_PERUSAHAAN filters
        //  are merged via AND into definitionExpression
        // ════════════════════════════════════════════
        let activeStatusExpr = "";
        let activeNamaExpr   = "";

        function buildCombinedExpr() {{
        if (activeStatusExpr && activeNamaExpr) return activeStatusExpr + " AND " + activeNamaExpr;
        return activeStatusExpr || activeNamaExpr || "";
        }}

        function refreshLayer() {{
        layerWiup.definitionExpression = buildCombinedExpr();
        }}

        // ── STATUS_IUP checkbox filter ──
        const statusFilterMsg  = document.getElementById("statusFilterMsg");
        const chkAktif         = document.getElementById("chkStatusAktif");
        const chkKadaluarsa    = document.getElementById("chkStatusKadaluarsa");

        function applyStatusFilter() {{
        const bothChecked = chkAktif.checked && chkKadaluarsa.checked;
        const noneChecked = !chkAktif.checked && !chkKadaluarsa.checked;

        if (bothChecked || noneChecked) {{
            activeStatusExpr = "";
            statusFilterMsg.textContent = noneChecked
            ? "No status selected — showing all."
            : "";
        }} else {{
            const vals = [];
            if (chkAktif.checked)      vals.push("'AKTIF'");
            if (chkKadaluarsa.checked) vals.push("'KADALUARSA'");
            activeStatusExpr = "STATUS_IUP IN (" + vals.join(",") + ")";

            layerWiup.queryFeatureCount({{ where: buildCombinedExpr() }})
            .then(count => {{ statusFilterMsg.textContent = count + " fitur ditampilkan"; }})
            .catch(()   => {{ statusFilterMsg.textContent = ""; }});
        }}
        refreshLayer();
        }}

        chkAktif.addEventListener("change",      applyStatusFilter);
        chkKadaluarsa.addEventListener("change",  applyStatusFilter);

        // ── Nama Perusahaan filter ──
        const filterSelect   = document.getElementById("filterNama");
        const filterCountMsg = document.getElementById("filterCountMsg");
        const clearFilterBtn = document.getElementById("clearFilterBtn");

        // Populate dropdown
        namaPerusahaan.forEach(nama => {{
        const opt = document.createElement("option");
        opt.value = nama; opt.textContent = nama;
        filterSelect.appendChild(opt);
        }});

        function applyNamaFilter() {{
        const val = filterSelect.value;
        if (!val) {{
            activeNamaExpr               = "";
            filterCountMsg.textContent   = "";
            clearFilterBtn.style.display = "none";
        }} else {{
            const safe   = val.replace(/'/g, "''");
            activeNamaExpr               = "NAMA_PERUSAHAAN = '" + safe + "'";
            clearFilterBtn.style.display = "block";

            layerWiup.queryFeatureCount({{ where: buildCombinedExpr() }})
            .then(count => {{ filterCountMsg.textContent = count + " fitur ditemukan"; }})
            .catch(()   => {{ filterCountMsg.textContent = ""; }});
        }}
        refreshLayer();
        }}

        filterSelect.addEventListener("change",  applyNamaFilter);
        clearFilterBtn.addEventListener("click", () => {{
        filterSelect.value = "";
        applyNamaFilter();
        }});

        // ── Hover tooltip ──
        const tooltip  = document.getElementById("hoverTooltip");
        let hitPending = false, lastX = 0, lastY = 0;

        view.on("pointer-move", event => {{
        lastX = event.x; lastY = event.y;
        if (hitPending) return;
        hitPending = true;

        view.hitTest(event, {{ include: [layerWiup] }}).then(response => {{
            hitPending = false;
            if (!response.results.length) {{
            tooltip.classList.remove("visible");
            return;
            }}

            const attrs     = response.results[0].graphic.attributes;
            const layerName = (response.results[0].graphic.layer || {{}}).title || "";

            const rows = Object.keys(attrs)
            .filter(k => k !== "OBJECTID" && k !== "layer")
            .map(k => {{
                let val = attrs[k];
                if (val == null || val === "") val = "-";
                if (typeof val === "number")
                val = val.toLocaleString("id-ID", {{ maximumFractionDigits: 2 }});
                return `<div class="tt-row">
                        <span class="tt-key">${{k.replace(/_/g," ")}}</span>
                        <span class="tt-val">${{val}}</span>
                        </div>`;
            }}).join("");

            tooltip.innerHTML = `<div class="tt-title">${{layerName}}</div>${{rows}}`;

            let tx = lastX + 16, ty = lastY - 10;
            if (tx + 310 > window.innerWidth)               tx = lastX - 310 - 10;
            if (ty + tooltip.offsetHeight > window.innerHeight) ty = lastY - tooltip.offsetHeight - 10;
            if (ty < 0) ty = 4;

            tooltip.style.left = tx + "px";
            tooltip.style.top  = ty + "px";
            tooltip.classList.add("visible");
        }}).catch(() => {{ hitPending = false; }});
        }});

        view.on("pointer-leave", () => tooltip.classList.remove("visible"));
        view.on("click",         () => tooltip.classList.remove("visible"));

    }}); // end view.when
    }}); // end require
    </script>
    </head>
    <body>

    <!-- ══════════ HEADER BAR ══════════ -->
    <div id="mapHeader">
    <span class="header-title">Data Tambang Timah Bangka Belitung</span>
    <span class="header-divider"></span>
    <span class="header-sub">Wilayah Izin Usaha Pertambangan (WIUP)</span>
    <div class="header-stats">
        <div class="stat-chip stat-total">
        <span class="stat-label">Total</span>
        <span class="stat-value">{n_wiup}</span>
        </div>
        <div class="stat-chip stat-aktif">
        <span class="stat-label">Aktif</span>
        <span class="stat-value">{n_aktif}</span>
        </div>
        <div class="stat-chip stat-kadaluarsa">
        <span class="stat-label">Kadaluarsa</span>
        <span class="stat-value">{n_kadaluarsa}</span>
        </div>
    </div>
    </div>

    <!-- ══════════ LOADING OVERLAY ══════════ -->
    <div id="loadingOverlay">
    <div class="spinner"></div>
    <div id="loadText">Initializing map…</div>
    <div id="loadSub">Loading ArcGIS API</div>
    </div>

    <!-- ══════════ MAP ══════════ -->
    <div id="viewDiv"></div>

    <!-- ══════════ HOVER TOOLTIP ══════════ -->
    <div id="hoverTooltip"></div>

    <!-- ══════════ CONTROL PANEL ══════════ -->
    <div id="controlPanel">
    <h3>Layer Controls</h3>

    <!-- Basemap -->
    <div class="section-label">Basemap</div>
    <div class="basemap-group">
        <label class="basemap-radio">
        <input type="radio" name="basemap" value="satellite" checked> ESRI Satellite
        </label>
        <label class="basemap-radio">
        <input type="radio" name="basemap" value="carto-positron"> CartoDB Positron
        </label>
    </div>

    <hr class="divider">

    <!-- STATUS_IUP checkbox filter -->
    <div class="section-label">Filter Status IUP</div>
    <div class="status-group">
        <label class="status-check aktif">
        <input type="checkbox" id="chkStatusAktif" checked>
        <span class="status-dot" style="background:#2196F3;border:2px solid #0D47A1;"></span>
        Aktif
        </label>
        <label class="status-check kadaluarsa">
        <input type="checkbox" id="chkStatusKadaluarsa" checked>
        <span class="status-dot" style="background:#E53935;border:2px solid #B71C1C;"></span>
        Kadaluarsa
        </label>
    </div>
    <span id="statusFilterMsg"></span>

    <hr class="divider">

    <!-- Nama Perusahaan filter -->
    <div class="section-label">Filter Nama Perusahaan</div>
    <div id="filterSection">
        <select id="filterNama">
        <option value="">-- Semua Perusahaan --</option>
        </select>
        <span id="filterCountMsg"></span>
        <button id="clearFilterBtn">Reset Filter</button>
    </div>

    <hr class="divider">

    <!-- Layer toggle & opacity -->
    <div class="section-label">Layers</div>
    <div class="layer-row">
        <div class="layer-top">
        <input type="checkbox" id="chkWiup" checked>
        <div class="swatch"
            style="background:rgba(33,150,243,0.5);border:2px solid #0D47A1;"></div>
        <span>WIUP</span>
        </div>
        <div class="opacity-row">
        <input type="range" id="opWiup" min="0" max="100" value="50">
        <span class="opacity-val" id="opWiupVal">50%</span>
        </div>
    </div>

    <span id="statusMsg">Loading layers…</span>
    </div>

    <!-- ══════════ VIEWPORT RESIZE SCRIPT ══════════ -->
    <script>
    (function() {{
    var MAX_H = {MAX_HEIGHT_PX};
    function setHeight() {{
        var h      = window.innerHeight || document.documentElement.clientHeight || 800;
        var finalH = Math.min(h, MAX_H);
        try {{
        window.parent.document.querySelectorAll("iframe").forEach(function(f) {{
            if (f.contentWindow === window) {{
            f.style.height = finalH + "px";
            f.style.width  = "100%";
            }}
        }});
        }} catch(e) {{}}
    }}
    setHeight();
    window.addEventListener("resize", setHeight);
    }})();
    </script>

    </body>
    </html>"""

    # ───────────────────────────────────────────────────────────────
    # RENDER
    # ───────────────────────────────────────────────────────────────
    components.html(HTML, height=MAX_HEIGHT_PX, scrolling=False)

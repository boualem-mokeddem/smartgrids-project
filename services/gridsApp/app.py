import os
from datetime import date
from dotenv import load_dotenv
import requests

import folium
from streamlit_folium import st_folium

import pandas as pd
import pydeck as pdk


import pandas as pd
import streamlit as st

from neo4j_client import Neo4jClient, Neo4jConfig
import queries as Q

import itertools
import hashlib

import math



def grid_signature(ids):
    # stable dedup key
    s = ",".join(sorted(ids))
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def score_eval(r):
    # Higher is better
    # prioritize valid + higher coverage + higher monetary gain
    return (1 if r["isValid"] else 0, float(r["coverageRatio"]), float(r["monetaryGain"]))


if "saved_grids" not in st.session_state:
    st.session_state.saved_grids = []

if "last_evaluation" not in st.session_state:
    st.session_state.last_evaluation = None

# -------------------------------------------------
# INTERNAL CONFIG (not exposed to UI)
# -------------------------------------------------
SHOW_OUT_OF_RADIUS_BUILDINGS = True

#------------------------------------------
# Julia Helpers
#------------------------------------------





FASTAPI_URL = os.getenv("FASTAPI_URL", "http://127.0.0.1:8000/ask")
JULIA_OPT_URL = os.getenv("JULIA_OPT_URL", "http://127.0.0.1:8081")

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def build_julia_payload(db, building_ids, start_d, end_d, N, radius_m, T, max_grids=10):
    rows = db.query(
        Q.BUILDINGS_ENERGY_SUMMARY,
        {
            "buildingIds": building_ids,
            "startDate": start_d.isoformat(),
            "endDate": end_d.isoformat()
        }
    )
    if not rows:
        return None, "No rows returned for selected buildings."

    df = pd.DataFrame(rows)

    # Defensive checks
    needed = {"building_id","cons","prod","labels","lat","lon"}
    if not needed.issubset(set(df.columns)):
        return None, f"Missing columns for Julia payload. Got: {list(df.columns)}"

    # Keep the same order as user selection
    df["order"] = df["building_id"].apply(lambda x: building_ids.index(x) if x in building_ids else 10**9)
    df = df.sort_values("order").reset_index(drop=True)

    buildings = []
    for _, r in df.iterrows():
        labels = r["labels"]
        is_prosumer = ("Prosumer" in labels) if isinstance(labels, list) else ("Prosumer" in str(labels))

        buildings.append({
            "id": r["building_id"],
            "cons": float(r["cons"] or 0.0),
            "prod": float(r["prod"] or 0.0),
            "isProsumer": bool(is_prosumer),
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
        })

    # Build NxN distance matrix (meters)
    n = len(buildings)
    dist = [[0.0]*n for _ in range(n)]
    for i in range(n):
        for j in range(i+1, n):
            d = haversine_m(buildings[i]["lat"], buildings[i]["lon"], buildings[j]["lat"], buildings[j]["lon"])
            dist[i][j] = d
            dist[j][i] = d

    # Remove lat/lon from payload (Julia doesn’t need them)
    buildings_payload = [
        {"id": b["id"], "cons": b["cons"], "prod": b["prod"], "isProsumer": b["isProsumer"]}
        for b in buildings
    ]

    payload = {
        "min_members": int(N),
        "max_radius": float(radius_m),
        "coverage_threshold": float(T),
        "max_grids": int(max_grids),
        "buildings": buildings_payload,
        "distances": dist
    }
    return payload, None

def call_julia(payload):
    return requests.post(JULIA_OPT_URL, json=payload, timeout=120).json()





#-------------------------------------------------

def auto_generate_grids(
    db,
    center_building_id,
    radius_candidates,
    min_n,
    max_n,
    params_common,
    beam_width=20,
    max_grids=30,
):
    # radius_candidates contains center too, but we want selectable pool excluding center
    pool_ids = [r["building_id"] for r in radius_candidates if r["building_id"] != center_building_id]

    # state: list of partial grids (each is list of building_ids)
    beams = [[center_building_id]]
    found = []
    found_keys = set()

    # grow size from 2 to max_n
    for target_size in range(2, max_n + 1):
        candidates_next = []

        for grid_ids in beams:
            used = set(grid_ids)

            # heuristic: only try a limited subset of pool to avoid explosion
            # (optional) you can rank pool by distance or by being Prosumer
            for bid in pool_ids:
                if bid in used:
                    continue
                new_grid = grid_ids + [bid]

                eval_params = dict(params_common)
                eval_params["buildingIds"] = new_grid
                eval_params["N"] = int(min_n)  # overwrite N
                # Note: max_n is handled here, N is "min participants rule"

                res = db.query(Q.EVALUATE_GRID, eval_params)
                if not res:
                    continue
                r = res[0]

                candidates_next.append((new_grid, r))

        # sort candidates by score, keep best beam_width
        candidates_next.sort(key=lambda x: score_eval(x[1]), reverse=True)
        beams = [g for g, _ in candidates_next[:beam_width]]

        # collect valid grids if we’re >= min_n
        if target_size >= min_n:
            for g, r in candidates_next:
                if r["isValid"]:
                    key = grid_signature(g)
                    if key not in found_keys:
                        found_keys.add(key)
                        found.append({"building_ids": g, "eval": r})
                        if len(found) >= max_grids:
                            return found

    return found


# ---------- Streamlit setup ----------
st.set_page_config(page_title="SmartGrids", layout="wide")
st.title("⚡ SmartGrids")

# ---------- Load env ----------
load_dotenv()

def env_required(key: str) -> str:
    v = os.getenv(key)
    if not v:
        raise RuntimeError(f"Missing env var: {key}. Create a .env file (see .env.example).")
    return v

cfg = Neo4jConfig(
    uri=env_required("NEO4J_URI"),
    user=env_required("NEO4J_USER"),
    password=env_required("NEO4J_PASSWORD"),
    database=env_required("NEO4J_DATABASE"),
)

DEFAULT_N = int(os.getenv("DEFAULT_MIN_PARTICIPANTS", "5"))
DEFAULT_RADIUS = int(os.getenv("DEFAULT_RADIUS_METERS", "2000"))
DEFAULT_T = float(os.getenv("DEFAULT_THRESHOLD_T", "0.6"))

@st.cache_resource
def get_client():
    return Neo4jClient(cfg)

db = get_client()

# ---------- Helpers ----------
def df_from(records):
    return pd.DataFrame(records) if records else pd.DataFrame()

def label_str(labels):
    if isinstance(labels, list):
        return ",".join(labels)
    return str(labels)

# ---------- Sidebar: admin actions ----------
st.sidebar.header("⚙️ Data Loader")

if st.sidebar.button("Create Database", use_container_width=True):
    try:
        for stmt in Q.CREATE_GRAPH_DB_STEPS:
            db.query(stmt)
        st.sidebar.success("Graph Database Created.")
    except Exception as e:
        st.sidebar.error(str(e))


# ---------- Main layout ----------
st.markdown("""
<style>

/* Tab container spacing */
div[data-baseweb="tab-list"] {
    gap: 10px;
    margin-bottom: 25px;
}

/* Default tab style */
button[data-baseweb="tab"] {
    font-size: 20px !important;
    font-weight: 600 !important;
    padding: 14px 28px !important;
    border-radius: 12px !important;
    background-color: #f5f5f5 !important;
    color: #333 !important;
    transition: all 0.3s ease !important;
    border: none !important;
}

/* Hover effect */
button[data-baseweb="tab"]:hover {
    background-color: #ffe5e5 !important;
    color: #b30000 !important;
}

/* Active tab (RED STYLE) */
button[data-baseweb="tab"][aria-selected="true"] {
    background-color: #e60000 !important;
    color: white !important;
    box-shadow: 0px 4px 12px rgba(230, 0, 0, 0.4) !important;
    transform: translateY(-2px);
}

</style>
""", unsafe_allow_html=True)


tab1, tab3, tab4 = st.tabs(
    [
        "Build & Evaluate Grid",
        #"🏷️ Providers",
        "Candidate Grids Performance",
        "SmartGrids Assistant"
    ]
)





with tab1:
    st.subheader("Grid parameters")

    colA, colB, colC, colD = st.columns(4)
    with colA:
        N = st.number_input("Min participants", min_value=1, value=DEFAULT_N, step=1)
    with colB:
        radius_m = st.number_input("Pairwise Distance", min_value=100, value=DEFAULT_RADIUS, step=100)
    with colC:
        T = st.number_input("Coverage ratio of consumption", min_value=0.0, max_value=1.0, value=DEFAULT_T, step=0.05)
    with colD:
        provider_mode = st.selectbox("Provider choice", ["Cheapest (auto)", "Select provider"])
        provider_id = None  # default → auto cheapest


    colE, colF = st.columns(2)
    with colE:
        start_d = st.date_input("Start date", value=date(2025, 1, 1))
    with colF:
        end_d = st.date_input("End date", value=date(2025, 12, 31))

    if end_d < start_d:
        st.error("End date must be >= start date.")
        st.stop()

    st.subheader("Geography filters")
    col1, col2 = st.columns(2)

    # Commune dropdown
    communes = db.query(Q.COMMUNES)
    commune_options = {f"{r['name']} ({r['code']})": r["code"] for r in communes}
    with col1:
        commune_label = st.selectbox("Select Commune", options=list(commune_options.keys()))
        commune_code = commune_options[commune_label]

    # IRIS dropdown (filtered by commune)
    iris_rows = db.query(Q.IRIS_BY_COMMUNE, {"communeCode": commune_code})
    iris_options = {f"{r['name']} ({r['code']})": r["code"] for r in iris_rows}
    with col2:
        if not iris_options:
            st.warning("No IRIS found for this commune.")
            st.stop()
        iris_label = st.selectbox("Select IRIS", options=list(iris_options.keys()))
        iris_code = iris_options[iris_label]

    show_map = st.checkbox("Show buildings on map")

    if show_map:

        radius_candidates = db.query(Q.BUILDINGS_IN_IRIS, {"irisCode": iris_code})
        map_df = pd.DataFrame(radius_candidates)

        if map_df.empty:
            st.warning("No buildings found for this IRIS.")
            st.stop()

        if "lat" not in map_df.columns or "lon" not in map_df.columns:
            st.error("Latitude/Longitude not returned from Neo4j query.")
            st.stop()

        # Initialize session state
        if "map_selected" not in st.session_state:
            st.session_state.map_selected = []

        # Multiselect synced with map
        selected_from_map = st.multiselect(
            "Select buildings",
            options=map_df["building_id"].tolist(),
            default=st.session_state.map_selected
        )

        st.session_state.map_selected = selected_from_map

        # 🎨 Better color logic
        def get_color(row):
            if row["building_id"] in selected_from_map:
                return [255, 0, 0]          # Selected = red
            elif "Prosumer" in row["labels"]:
                return [0, 200, 0]         # Prosumer = green
            else:
                return [0, 102, 204]       # Consumer = blue (nicer shade)

        map_df["color"] = map_df.apply(get_color, axis=1)

        # Optional: slightly larger selected buildings
        map_df["radius"] = map_df["building_id"].apply(
            lambda x: 80 if x in selected_from_map else 40
        )

        # 📍 Improved Scatter Layer
        layer = pdk.Layer(
            "ScatterplotLayer",
            data=map_df,
            get_position='[lon, lat]',
            get_fill_color='color',
            get_radius=10,              # very small in meters
            radius_min_pixels=1,        # extremely small
            radius_max_pixels=4,
            pickable=True,
            opacity=0.9
        )

        view_state = pdk.ViewState(
            latitude=map_df["lat"].mean(),
            longitude=map_df["lon"].mean(),
            zoom=16,        # closer view
            pitch=0,        # 🔥 perfectly perpendicular
            bearing=0
        )

        deck = pdk.Deck(
            layers=[layer],
            initial_view_state=view_state,
            map_style="mapbox://styles/mapbox/light-v11",  # cleaner than v9
            tooltip={
                "html": "<b>Building:</b> {building_id}",
                "style": {"backgroundColor": "white", "color": "black"}
            }
        )

        st.pydeck_chart(deck)



    st.subheader("Buildings selection")

    # Get ALL buildings from IRIS (no radius filtering)
    buildings_in_iris = db.query(Q.BUILDINGS_IN_IRIS, {"irisCode": iris_code})

    if not buildings_in_iris:
        st.warning("No buildings found in this IRIS.")
        st.stop()

    # Build selection list
    building_options = []
    building_meta = {}

    for r in buildings_in_iris:
        bid = r["building_id"]
        label = label_str(r["labels"])

        display = f"{bid} | {label}"
        if label == "Building,Prosumer":
            display += f" | kwp={r['pv_kwp']}"

        building_options.append(display)
        building_meta[display] = bid

    # Single multiselect
    selected_labels = st.multiselect(
        "Select grid participants",
        options=building_options
    )

    if not selected_labels:
        st.info("Select at least one building.")
        st.stop()

    selected_building_ids = [building_meta[x] for x in selected_labels]

    st.info(f"Selected buildings: {len(selected_building_ids)}")

    gen_mode = st.radio(
        "Choose Evauation method",
        ["Evaulate the Selected Grid", "All possible valid grids combinations"],
        horizontal=True
    )

    if gen_mode == "All possible valid grids combinations":

        max_grids = st.number_input("Max grids to return", min_value=1, max_value=50, value=10, step=1)

        if st.button("⚙️ Run with Julia optimisation", use_container_width=True):
            payload, err = build_julia_payload(
                db=db,
                building_ids=selected_building_ids,
                start_d=start_d,
                end_d=end_d,
                N=N,
                radius_m=radius_m,
                T=T,
                max_grids=int(max_grids),
            )
            payload["max_return"] = int(max_grids)

            if err:
                st.error(err)
                st.stop()

            try:
                with st.spinner("Loading..."):
                    
                    out = call_julia(payload)

                if out.get("status") != "ok":
                    st.error(out)
                    st.stop()

                grids = out.get("grids", [])
                if not grids:
                    st.warning("No feasible grids found by Julia for these constraints.")
                    st.stop()

                # Store results
                st.session_state.julia_grids = grids

                # Display
                rows = []
                for i, g in enumerate(grids, start=1):
                    rows.append({
                    "rank": i,
                    "size": g["size"],
                    "coverage": round(g["coverage_ratio"], 3),
                    "grid_ids": ", ".join(g["building_ids"]),
                    })

                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            except requests.exceptions.ConnectionError:
                st.error("Cannot reach Julia optimiser on http://127.0.0.1:8081. Is the Docker container running?")

    # Provider selection
    provider_id = None
    if provider_mode == "Select provider":
        prov = db.query(Q.PROVIDERS_LIST)
        if not prov:
            st.warning("No providers found. Load providers first from sidebar.")
            st.stop()
        prov_map = {f"{p['name']} ({p['id']}) - {p['price']:.4f} €/kWh": p["id"] for p in prov}
        provider_label = st.selectbox("Choose provider", options=list(prov_map.keys()))
        provider_id = prov_map[provider_label]

    st.subheader("Evaluate grid")

    buyer_provider_id = None  # internal default
    # (optional) later you can set it from code, not UI

    if st.button("Evaluate", type="primary", use_container_width=True):
        params = {
            "buildingIds": selected_building_ids,
            "radiusMeters": int(radius_m),
            "N": int(N),
            "T": float(T),
            "startDate": start_d.isoformat(),
            "endDate": end_d.isoformat(),
            "providerId": provider_id,
            "buyerProviderId": buyer_provider_id,
        }

        try:
            res = db.query(Q.EVALUATE_GRID_PAIRWISE, params)
            if not res:
                st.error("No result returned from evaluation query.")
                st.stop()

            r = res[0]

            st.session_state.last_evaluation = {
                "result": r,
                "selected_building_ids": selected_building_ids,
                "radius_m": radius_m,
                "N": N,
                "T": T,
                "start_d": start_d,
                "end_d": end_d,
                "provider_id": provider_id,
                "buyer_provider_id": buyer_provider_id,
            }

            # Only VALID/INVALID banner + Constraints
            if r["isValid"]:
                st.success("VALID GRID")
            else:
                st.error("INVALID GRID")

            st.markdown("### Constraints check")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Count", f"{r['selectedCount']}/{r['N']}")
            c2.metric("Has Prosumer", "Yes" if r["hasProsumer"] else "No")
            c3.metric("Within Radius", "Yes" if r["withinRadius"] else "No")
            c4.metric("Coverage OK", "Yes" if r["coverageOk"] else "No")

            if r.get("outOfRadiusPairs"):
                st.error("Pairwise distance violations (must be <= radius)")
                st.dataframe(pd.DataFrame(r["outOfRadiusPairs"]), use_container_width=True, hide_index=True)

        except Exception as e:
            st.error(str(e))


    # ---------------------------------
    # Save last evaluated grid
    # ---------------------------------
    if st.session_state.last_evaluation is not None:
        r = st.session_state.last_evaluation["result"]
        if r["isValid"]:
            st.markdown("### Save evaluated grid")
            if st.button("Save this grid as candidate", key="save_grid"):
                st.session_state.saved_grids.append({
                    "grid_id": f"grid_{len(st.session_state.saved_grids)+1}",
                    "timestamp": pd.Timestamp.now(),
                    "building_ids": st.session_state.last_evaluation["selected_building_ids"],
                    "radius_m": st.session_state.last_evaluation["radius_m"],
                    "N": st.session_state.last_evaluation["N"],
                    "T": st.session_state.last_evaluation["T"],
                    "start_date": st.session_state.last_evaluation["start_d"],
                    "end_date": st.session_state.last_evaluation["end_d"],
                    "provider_id": st.session_state.last_evaluation.get("provider_id", None),
                    "buyer_provider_id": None,
                })
                st.success("Grid saved successfully.")
            


with tab4:
    st.subheader("SmartGrids GraphDB Assistant")

    st.markdown("""
    How can I help you..
    """)

    question = st.text_area(
        "Your question",
        placeholder="Example: Show me the top 10 buildings by total consumption in June",
        height=120
    )

    col1, col2 = st.columns([1, 1])
    with col1:
        mode = st.selectbox("Response mode", ["answer", "raw"])
    with col2:
        limit = st.number_input("Max rows", min_value=1, max_value=500, value=50)

    if st.button("Send", use_container_width=True):
        if not question.strip():
            st.warning("Please enter a question.")
            st.stop()

        payload = {
            "question": question,
            "mode": mode,
            "limit": int(limit)
        }

        try:
            with st.spinner("Loading.."):
                resp = requests.post(FASTAPI_URL, json=payload, timeout=120)

            if resp.status_code != 200:
                st.error(f"API error {resp.status_code}: {resp.text}")
                st.stop()

            data = resp.json()

            # -----------------------
            # Display Cypher
            # -----------------------
            st.markdown("### Generated Cypher Query")
            st.code(data["cypher"], language="cypher")

            # -----------------------
            # Display rows
            # -----------------------
            st.markdown("### Query results")
            if data["rows"]:
                st.dataframe(pd.DataFrame(data["rows"]), use_container_width=True)
            else:
                st.info("No rows returned.")

            # -----------------------
            # Display answer
            # -----------------------
            if mode == "answer" and data.get("answer"):
                st.markdown("### Analysis")
                st.write(data["answer"])

        except requests.exceptions.ConnectionError:
            st.error(
                "❌ Cannot reach FastAPI server.\n\n"
                "Make sure FastAPI is running:\n"
                "`uvicorn app:app --reload --port 8000`"
            )



def fmt_num(x, digits=2):
    if x is None:
        return "N/A"
    try:
        return f"{float(x):.{digits}f}"
    except Exception:
        return "N/A"

def fmt_id(x):
    return x if x is not None else "N/A"



with tab3:
    st.subheader("Candidate grids")

    if not st.session_state.saved_grids:
        st.info("No saved grids yet. Evaluate and save a grid first.")
        st.stop()

    grid_map = {g["grid_id"]: g for g in st.session_state.saved_grids}
    grid_ids = list(grid_map.keys())

    selected_grid_id = st.selectbox("Select a grid", options=grid_ids, key="selected_grid_id")
    grid = grid_map[selected_grid_id]

    # -----------------------------
    # Providers list (once)
    # -----------------------------
    providers = db.query(Q.PROVIDERS_LIST)
    params = {
        "buildingIds": grid["building_ids"],
        "radiusMeters": int(grid["radius_m"]),
        "N": int(grid["N"]),
        "T": float(grid["T"]),
        "startDate": grid["start_date"].isoformat() if hasattr(grid["start_date"], "isoformat") else str(grid["start_date"]),
        "endDate": grid["end_date"].isoformat() if hasattr(grid["end_date"], "isoformat") else str(grid["end_date"]),
        "providerId": grid.get("provider_id", None),
        "buyerProviderId": None,  # None => auto best buyer in Cypher
    }

    res = db.query(Q.EVALUATE_GRID_PAIRWISE, params)
    if not res:
        st.error("No result returned.")
        st.stop()
    r = res[0]

    if not providers:
        st.error("No providers found in DB.")
        st.stop()

    # Normalize buy price key (handles mismatched keys / string values)
    def get_buy_price(p):
        for k in ("buy_eur_per_kwh", "buy_price", "buyPrice", "buy_price_eur_per_kwh", "buy"):
            if k in p and p[k] is not None:
                try:
                    return float(p[k])
                except Exception:
                    return None
        return None
    if r.get("surplusKwh") >0:
        # Build buyer candidates = providers with a valid buy price
        buyer_candidates = []
        for p in providers:
            bp = get_buy_price(p)
            if bp is not None:
                buyer_candidates.append({**p, "buy_eur_per_kwh": bp})

        # Always offer a dropdown if we have at least 1 buyer provider
        buyer_provider_id = None

        if not buyer_candidates:
            st.warning(
                "No buyer providers have a buy price available (buy_eur_per_kwh). "
                "Surplus selling cannot be evaluated."
            )
        else:
            # Option 1: Auto
            buyer_labels = ["Auto (best buyer in DB)"]
            buyer_label_to_id = {"Auto (best buyer in DB)": None}

            # Option 2: explicit provider choices
            for p in sorted(buyer_candidates, key=lambda x: x["buy_eur_per_kwh"], reverse=True):
                name = p.get("name", "Unknown")
                pid = p.get("id") or p.get("provider_id")  # support either key
                if pid is None:
                    continue
                label = f"{name} ({pid}) | buy={p['buy_eur_per_kwh']:.4f} €/kWh"
                buyer_labels.append(label)
                buyer_label_to_id[label] = pid

            # Persist selection per grid_id
            if "buyer_choice_by_grid" not in st.session_state:
                st.session_state.buyer_choice_by_grid = {}

            saved_choice = st.session_state.buyer_choice_by_grid.get(selected_grid_id, None)
            if saved_choice is None:
                saved_choice = grid.get("buyer_provider_id", None)

            # Determine default index
            default_index = 0  # Auto by default
            if saved_choice is not None:
                for idx, lbl in enumerate(buyer_labels):
                    if buyer_label_to_id[lbl] == saved_choice:
                        default_index = idx
                        break

            # ---- on_change callback ensures state is persisted and triggers rerun cleanly
            buyer_key = f"buyer_provider_select_{selected_grid_id}"

            def on_buyer_change():
                lbl = st.session_state.get(buyer_key)
                st.session_state.buyer_choice_by_grid[selected_grid_id] = buyer_label_to_id.get(lbl)

            chosen_buyer_label = st.selectbox(
                "Buyer provider (grid sells surplus to)",
                options=buyer_labels,
                index=default_index,
                key=buyer_key,
                on_change=on_buyer_change,
            )

            # Read buyer id from persisted state (source of truth)
            if selected_grid_id not in st.session_state.buyer_choice_by_grid:
                st.session_state.buyer_choice_by_grid[selected_grid_id] = buyer_label_to_id.get(chosen_buyer_label)

            buyer_provider_id = st.session_state.buyer_choice_by_grid.get(selected_grid_id, None)

            # optional, keeps it with the saved grid object
            grid["buyer_provider_id"] = buyer_provider_id

        # -----------------------------
        # Recompute performance
        # -----------------------------
        params = {
            "buildingIds": grid["building_ids"],
            "radiusMeters": int(grid["radius_m"]),
            "N": int(grid["N"]),
            "T": float(grid["T"]),
            "startDate": grid["start_date"].isoformat() if hasattr(grid["start_date"], "isoformat") else str(grid["start_date"]),
            "endDate": grid["end_date"].isoformat() if hasattr(grid["end_date"], "isoformat") else str(grid["end_date"]),
            "providerId": grid.get("provider_id", None),
            "buyerProviderId": buyer_provider_id,  # None => auto best buyer in Cypher
        }

    try:
        res = db.query(Q.EVALUATE_GRID_PAIRWISE, params)
        if not res:
            st.error("No result returned.")
            st.stop()
        r = res[0]

                # -----------------------------
        # Rich display (adds sections, no variable renames)
        # -----------------------------



        st.markdown("### Constraints check")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Count", f"{r.get('selectedCount','?')}/{r.get('N','?')}")
        c2.metric("Has Prosumer", "Yes" if r.get("hasProsumer") else "No")
        c3.metric("Within Radius", "Yes" if r.get("withinRadius") else "No")
        c4.metric("Coverage OK", "Yes" if r.get("coverageOk") else "No")
        c5.metric("Entity Buying Better", "Yes" if r.get("buyingAsEntityIsBetter") else "No")

        if r.get("outOfRadiusPairs"):
            st.markdown("### Pairwise distance violations")
            st.dataframe(pd.DataFrame(r["outOfRadiusPairs"]), use_container_width=True, hide_index=True)

        # B) Energy metrics
        st.markdown("### Energy & cost metrics")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Consumption (kWh)", fmt_num(r.get("totalCons"), 2))
        m2.metric("Total Production (kWh)", fmt_num(r.get("totalProd"), 2))
        m3.metric("Coverage ratio", fmt_num(r.get("coverageRatio"), 3))
        # Energy balance: show surplus if present, otherwise show deficit as negative
        energy_balance = None
        if r.get("surplusKwh") >0:
            energy_balance = float(r["surplusKwh"])
            st.markdown("### Gain from selling")
            k1, k2, k3= st.columns(3)
            k1.metric("Chosen buyer", fmt_id(r.get("chosenBuyerProviderId")))
            k2.metric("Sell price (€/kWh)", fmt_num(r.get("sellPrice"), 4))
            k3.metric("Grid sell revenue (€)", fmt_num(r.get("gridSellRevenue"), 2))
        elif r.get("surplusKwh") <0:
            energy_balance = float(r["surplusKwh"])
            st.markdown("### Gain from buying as a grid ")
            g1, g2, g3, g4,g5 = st.columns(5)
            g1.metric("Chosen provider", fmt_id(r.get("chosenProviderId")))
            g2.metric("Buy price (€/kWh)", fmt_num(r.get("buyPrice"), 4))
            g3.metric("Sum individual cost (€)", fmt_num(r.get("sumIndividualCost"), 2))
            g4.metric("Grid buy cost (€)", fmt_num(r.get("gridBuyCost"), 2))
            g5.metric("Monetary gain (€)", fmt_num(r.get("gainFromPooling"), 2))

        m4.metric("Energy Balance (kWh)", fmt_num(energy_balance, 2))


        # D) Per-building breakdown (cons/prod + net deficit/surplus)
        st.markdown("### Per-building breakdown")
        pb = pd.DataFrame(r.get("perBuilding", []))
        if pb.empty:
            st.info("No per-building rows returned.")
        else:
            # Ensure numeric cols exist
            for col in ["cons", "prod"]:
                if col in pb.columns:
                    pb[col] = pd.to_numeric(pb[col], errors="coerce").fillna(0.0)
                else:
                    pb[col] = 0.0

            if "isProsumer" not in pb.columns:
                pb["isProsumer"] = False

            pb["net_deficit_kwh"] = (pb["cons"] - pb["prod"]).clip(lower=0.0)
            pb["net_surplus_kwh"] = (pb["prod"] - pb["cons"]).clip(lower=0.0)

            # nicer ordering: prosumers first, then biggest deficits
            show_cols = ["id", "isProsumer", "cons", "prod"]
            show_cols = [c for c in show_cols if c in pb.columns]

            st.dataframe(
                pb[show_cols].sort_values(["isProsumer", "prod"], ascending=[False, False]),
                use_container_width=True,
                hide_index=True
            )
        if r.get("surplusKwh") <0:
            # E) Deficit cost allocation (consumers only) + percent shares
            st.markdown("### Deficit cost allocation (consumers only)")
            deficit = r.get("deficitKwh")
            buy_price = r.get("buyPrice")

            if pb.empty:
                st.info("No data to allocate.")
            elif deficit is None or float(deficit) <= 0:
                st.info("No deficit to allocate.")
            elif buy_price is None:
                st.warning("Deficit exists but buy price is missing; cannot allocate cost.")
            else:
                consumers = pb[pb["isProsumer"] == False].copy()
                if consumers.empty:
                    st.info("No consumers in this grid.")
                else:
                    total_cons_consumers = consumers["cons"].sum()
                    if total_cons_consumers <= 0:
                        st.info("Consumers have zero total consumption in period.")
                    else:
                        consumers["cons_share"] = consumers["cons"] / total_cons_consumers
                        consumers["allocated_deficit_kwh"] = consumers["cons_share"] * float(deficit)
                        consumers["allocated_cost_eur"] = consumers["allocated_deficit_kwh"] * float(buy_price)

                        # present % share
                        consumers["cons_share_pct"] = (consumers["cons_share"] * 100.0)

                        alloc_cols = ["id", "cons", "cons_share_pct", "allocated_deficit_kwh", "allocated_cost_eur"]
                        alloc_cols = [c for c in alloc_cols if c in consumers.columns]

                        st.dataframe(
                            consumers[alloc_cols].sort_values("allocated_cost_eur", ascending=False),
                            use_container_width=True,
                            hide_index=True
                        )



        # ... keep your metrics display code as-is ...
    except Exception as e:
        st.error(str(e))

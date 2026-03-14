# ⚡ SmartGrids

A decision-support platform for designing, evaluating, and optimising **local energy communities** (LEC). SmartGrids combines a Neo4j graph database, a Streamlit interface, a Julia optimisation engine, and a natural-language graph assistant into a fully containerised multi-service application.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
  - [Environment Variables](#environment-variables)
  - [Docker Deployment (Full Stack)](#docker-deployment-full-stack)
  - [Building the Graph Database](#building-the-graph-database)
- [Application Tabs](#application-tabs)
  - [Tab 1 – Build & Evaluate Grid](#tab-1--build--evaluate-grid)
  - [Tab 2 – Candidate Grids & Comparison](#tab-2--candidate-grids--comparison)
  - [Tab 3 – SmartGrids Assistant](#tab-3--smartgrids-assistant)
- [Grid Validity Model](#grid-validity-model)
- [Julia Optimisation Service](#julia-optimisation-service)
- [GraphRAG Assistant](#graphrag-assistant)
- [Data Model](#data-model)
- [CSV Data Files](#csv-data-files)
- [API Reference](#api-reference)
- [Development](#development)
- [Known Limitations](#known-limitations)

---

## Overview

SmartGrids enables users to:

- Filter buildings geographically by **Commune** and **IRIS** zone
- Visualise buildings on an interactive map
- Select candidate participants and **evaluate** whether they form a valid energy grid
- **Enumerate all valid grid configurations** using a combinatorial optimisation model (Julia + JuMP + HiGHS)
- Save valid grids and **analyse their economic performance** — including deficit pooling and surplus selling
- Query the energy graph in **natural language** (powered by Gemini + Neo4j)

---

## Architecture

```
┌─────────────────────┐        ┌──────────────────────┐
│   Streamlit UI      │──────▶ │   GraphRAG FastAPI   │
│   (Port 8501)       │        │   (Port 8000)        │
└────────┬────────────┘        └──────────┬───────────┘
         │                                │
         │                        ┌───────▼──────────┐
         │                        │   Neo4j Graph DB  │
         │                        │   (Port 7687)     │
         │                        └──────────────────┘
         │
         ▼
┌─────────────────────┐
│  Julia Optimizer    │
│  (Port 8081)        │
│  JuMP + HiGHS       │
└─────────────────────┘
```

**Key design principle:** The Julia optimisation service and the GraphRAG assistant never connect directly to Neo4j. The Streamlit layer acts as the orchestrator — extracting data, building payloads, calling services, and displaying results.

---

## Features

| Feature | Description |
|---|---|
| **Geographic filtering** | Commune → IRIS → Building hierarchy |
| **Interactive map** | PyDeck perpendicular view with colour-coded building types |
| **Grid evaluation** | Pairwise spatial constraint, coverage ratio, economic gain |
| **Valid grid enumeration** | Exhaustive subset enumeration via Julia (JuMP + HiGHS) |
| **Economic analysis** | Deficit pooling gain + surplus selling revenue |
| **Fair cost allocation** | Per-consumer share of external deficit proportional to consumption |
| **Buyer provider selection** | Per-grid dropdown to choose the surplus buyer |
| **NL graph assistant** | Text-to-Cypher with Gemini, read-only guardrails |
| **Admin sidebar** | One-click index creation, CSV loading, graph bootstrapping |

---

## Tech Stack

| Component | Technology |
|---|---|
| Interface | Python · Streamlit · PyDeck |
| Graph DB | Neo4j 5.x |
| Optimisation | Julia 1.10 · JuMP · HiGHS |
| Graph assistant | FastAPI · Gemini (google-genai) |
| Containerisation | Docker · Docker Compose |
| Data manipulation | Pandas |
| Geospatial | Neo4j Point · Haversine (Python) |

---

## Project Structure

```
smartgrids-stack/
├── docker-compose.yml
├── .env
│
├── neo4j/
│   └── import/
│       ├── buildings_enriched_with_iris.csv
│       ├── reseau-souterrain-bt-neo4j.csv
│       ├── consumption_daily.csv
│       ├── production_daily.csv
│       ├── providers_paris.csv
│       └── building_producers_prices.csv
│
├── streamlit/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py
│   ├── queries.py
│   └── neo4j_client.py
│
├── graphrag/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py
│
└── julia/
    ├── Dockerfile
    └── optimizer.jl
```

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (with WSL 2 backend on Windows)
- A Google Gemini API key ([get one here](https://aistudio.google.com/app/apikey))

---

## Getting Started

### Environment Variables

Create a `.env` file at the project root:

```env
# Neo4j
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=smartgrids123
NEO4J_DATABASE=neo4j
NEO4J_AUTH=neo4j/smartgrids123

# App defaults
DEFAULT_MIN_PARTICIPANTS=5
DEFAULT_RADIUS_METERS=2000
DEFAULT_THRESHOLD_T=0.6

# Service URLs (used inside containers)
FASTAPI_URL=http://graphrag-api:8000/ask
JULIA_OPT_URL=http://julia-optimizer:8081

# GraphRAG assistant
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
```

> **Note:** `NEO4J_AUTH` is used by the Neo4j container to initialise credentials on first start. The other `NEO4J_*` variables are used by the Python services.

---

### Docker Deployment (Full Stack)

```bash
# Build all images and start all services
docker compose up --build

# Or run in detached mode
docker compose up --build -d
```

Once running, access:

| Service | URL |
|---|---|
| Streamlit UI | http://localhost:8501 |
| FastAPI docs | http://localhost:8000/docs |
| Neo4j Browser | http://localhost:7474 |
| Julia optimizer | http://localhost:8081 |

To stop and remove everything (including volumes):

```bash
docker compose down -v
```

---

### Building the Graph Database

On first launch, the Neo4j container starts with an empty database. Use the **Admin / Setup** sidebar in the Streamlit UI to bootstrap the graph:

1. Click **"Create indexes (recommended)"** — creates all constraints and spatial indexes.
2. Click **"Load providers_paris.csv into Neo4j"** — loads electricity providers.
3. Click **"Load building producer prices"** — enriches prosumer nodes with reliability scores and selling prices.

> For the full graph (buildings, IRIS, BT lines, consumption, production), run the `CREATE_GRAPH_DB_STEPS` list from `queries.py` via the Neo4j Browser or a Python migration script. Place all required CSV files in `neo4j/import/` before running.

---

## Application Tabs

### Tab 1 – Build & Evaluate Grid

**Step 1 — Grid parameters**

| Parameter | Description |
|---|---|
| `N` | Minimum number of participants |
| `Radius (m)` | Maximum pairwise distance between any two buildings |
| `T` | Minimum coverage ratio (production / consumption) |
| `Provider` | Cheapest auto-selected or manually chosen |
| `Start / End date` | Energy aggregation period |

**Step 2 — Geography filters**

Select a Commune, then an IRIS zone. Optionally enable the **"Show buildings on map"** toggle to display buildings as colour-coded dots:

- 🔴 Red — selected
- 🟢 Green — Prosumer
- 🔵 Blue — Consumer

**Step 3 — Building selection**

A multiselect dropdown lists all buildings in the IRIS. Select participants — no pre-filtering by radius occurs at this stage; any combination is allowed.

**Generation mode:**

| Mode | Description |
|---|---|
| Manual evaluation (Cypher) | Evaluate the selected set directly |
| Optimise grids (Julia) | Enumerate all valid subsets from the selected pool |

**Step 4 — Evaluate**

Pressing **✅ Evaluate** runs the pairwise validation query and displays:

- Valid / Invalid banner
- Constraints check (Count, Prosumer, Within Radius, Coverage OK, Entity Buying Better)
- Pairwise distance violations table (if any)
- Save Grid button (only visible for valid grids)

---

### Tab 2 – Candidate Grids & Comparison

Select any saved grid from the dropdown. The tab re-evaluates performance on demand and displays:

- **Energy & cost metrics** — total consumption, production, coverage ratio, energy balance
- **Provider & gain** — buy provider, sell provider, individual cost sum, grid buy cost, grid sell revenue, monetary gain
- **Gain decomposition** — gain from pooling vs. gain from selling surplus
- **Per-building breakdown** — individual consumption, production, net deficit, net surplus
- **Deficit cost allocation** — consumers share the external deficit proportionally to their consumption share

A **buyer provider dropdown** lets you choose which provider purchases the grid's surplus energy, with the choice persisted per saved grid.

---

### Tab 3 – SmartGrids Assistant

Ask any question in natural language. The assistant:

1. Extracts the Neo4j graph schema
2. Generates a read-only Cypher query via Gemini
3. Executes it against Neo4j
4. Returns the results and (in `answer` mode) a natural-language explanation

**Example questions:**

```
Show me the top 10 buildings by total consumption in June 2025
Which IRIS has the best energy coverage ratio in 2025?
How many prosumers are there in each IRIS?
What is the total production of building 75101_2318_00004 in 2025?
Which providers are closest to building X?
```

> Only read operations are permitted. Write clauses (`CREATE`, `MERGE`, `SET`, `DELETE`, etc.) are blocked by a regex guardrail.

---

## Grid Validity Model

A grid **G** is a subset of selected buildings. It is **valid** if all of the following hold:

| Constraint | Rule |
|---|---|
| Minimum size | \|G\| ≥ N |
| Prosumer presence | At least one building in G has `has_pv = true` |
| Pairwise radius | For all pairs (i, j) in G: `distance(i, j) ≤ R` |
| Coverage (deficit grids) | `totalProd / totalCons ≥ T` |
| Economic advantage (deficit grids) | Grid buy cost < sum of individual costs |

**Special case — surplus grids:** If `totalProd ≥ totalCons`, the grid is automatically valid regardless of the coverage threshold, provided structural constraints are met.

**Monetary gain** is computed as:

```
Gain = (sum of individual costs − grid buy cost) + (surplus kWh × buyer sell price)
```

---

## Julia Optimisation Service

The Julia service (`optimizer.jl`) receives a JSON payload and returns all valid grid subsets, ranked by business objective.

### Payload format

```json
{
  "min_members": 3,
  "max_radius": 2000,
  "coverage_threshold": 0.6,
  "max_return": 50,
  "buildings": [
    {"id": "B1", "cons": 5122.0, "prod": 2119.0, "isProsumer": true},
    {"id": "B2", "cons": 2730.0, "prod": 0.0,    "isProsumer": false}
  ],
  "distances": [
    [0.0, 23.5],
    [23.5, 0.0]
  ]
}
```

### Response format

```json
{
  "status": "ok",
  "count": 1,
  "grids": [
    {
      "building_ids": ["B1", "B2"],
      "size": 2,
      "total_cons": 7852.0,
      "total_prod": 2119.0,
      "coverage_ratio": 0.27,
      "waste_kwh": 0.0,
      "deficit_kwh": 5733.0,
      "mismatch_kwh": 5733.0
    }
  ]
}
```

### Ranking objective

Grids are ranked by:
1. **Minimal energy mismatch** `|P − C|` (balanced grids first)
2. **Coverage ratio closest to 1** (locally consumed production preferred)
3. **Larger grid size** as a tiebreaker

### Rebuilding the Julia container

```bash
# After editing optimizer.jl
docker build --no-cache -t smartgrids-julia ./julia
docker run --rm -p 8081:8081 smartgrids-julia
```

---

## GraphRAG Assistant

The FastAPI service (`graphrag/app.py`) wraps Gemini to generate read-only Cypher queries.

### Health check

```bash
curl http://localhost:8000/health
```

### Manual test

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Top 5 prosumers by annual production", "mode": "answer", "limit": 5}'
```

---

## Data Model

```
(:Region)<-[:SITUATED_IN]-(:Departement)
(:Departement)<-[:SITUATED_IN]-(:EPCI)
(:EPCI)<-[:SITUATED_IN]-(:Commune)
(:Commune)<-[:SITUATED_IN]-(:IRIS)
(:IRIS)<-[:IN_IRIS]-(:Building/:Prosumer/:Consumer)
(:BT_Line)-[:SERVES_IRIS]->(:IRIS)
(:Building)-[:CONSUMED_ON {consumption_kwh}]->(:Day)
(:Prosumer)-[:PRODUCED_ON {production_kwh}]->(:Day)
```

### Key node properties

| Node | Key properties |
|---|---|
| `Building` / `Prosumer` / `Consumer` | `building_id`, `location` (Point), `has_pv`, `pv_capacity_kwp`, `iris_code` |
| `Prosumer` (enriched) | `annual_kwh`, `reliability_score`, `variability_cv`, `sell_price_eur_per_kwh`, `inferred_kwp` |
| `Day` | `date` |
| `IRIS` | `code`, `nom` |
| `Commune` | `code`, `nom` |
| `Provider` | `provider_id`, `name`, `price_eur_per_kwh`, `buy_eur_per_kwh`, `location` (Point) |

---

## CSV Data Files

Place all files in `neo4j/import/` before loading:

| File | Contents |
|---|---|
| `buildings_enriched_with_iris.csv` | Building nodes with location, IRIS code, PV info |
| `reseau-souterrain-bt-neo4j.csv` | BT lines with geographic hierarchy |
| `consumption_daily.csv` | Daily consumption per building |
| `production_daily.csv` | Daily production per prosumer |
| `providers_paris.csv` | Provider nodes with `price_eur_per_kwh` and `buy_eur_per_kwh` |
| `building_producers_prices.csv` | Prosumer enrichment (reliability, variability, sell price) |

---

## API Reference

### Julia Optimizer — `POST /`

| Field | Type | Description |
|---|---|---|
| `min_members` | int | Minimum grid size |
| `max_radius` | float | Max pairwise distance (metres) |
| `coverage_threshold` | float | Min production/consumption ratio |
| `max_return` | int | Max grids to return (default 200) |
| `buildings` | array | `{id, cons, prod, isProsumer}` |
| `distances` | array[array] | N×N symmetric distance matrix (metres) |

### GraphRAG Assistant — `POST /ask`

| Field | Type | Description |
|---|---|---|
| `question` | string | Natural language question |
| `mode` | string | `"answer"` (with explanation) or `"raw"` (rows only) |
| `limit` | int | Max result rows (1–500, default 50) |

---

## Development

### Running services individually (without Docker)

**Streamlit:**
```bash
cd streamlit
pip install -r requirements.txt
streamlit run app.py
```

**GraphRAG API:**
```bash
cd graphrag
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

**Julia optimizer:**
```bash
# In WSL / Linux
cd julia
julia optimizer.jl
```

### Viewing container logs

```bash
docker compose logs streamlit-ui
docker compose logs graphrag-api
docker compose logs julia-optimizer
docker compose logs neo4j
```

---

## Known Limitations

| Limitation | Notes |
|---|---|
| Exponential enumeration | Subset enumeration is O(2ⁿ). Practical for ≤ 20 buildings per query. |
| Static time window | Energy data is aggregated over a fixed date range; hourly resolution is not modelled. |
| Deterministic production/consumption | No stochastic or forecast modelling. |
| No battery storage | Grid balance assumes instantaneous net consumption/production. |
| Session-only saved grids | Saved grids live in `st.session_state` and are lost on page refresh. |

---

## Licence

This project is developed as part of an academic research module on local energy communities and smart grid optimisation.

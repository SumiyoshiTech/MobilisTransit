# MobilisTransit — System Architecture

## 1. Context & Scope
MobilisTransit is an integrated four-course-unit Master's prototype (TEI, AAP, DDM, RVA) for **Mobilis Line 9** (Leiria–Campus 2 ULO) that simulates urban bus trips in Eclipse SUMO, predicts downstream arrival times via a centrally hosted versioned regression model, runs paired delay-propagation experiments, and presents synchronized 2D, 3D AR, and web administration interfaces.

- **Source of Truth**: [docs/PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) and [docs/REQUIREMENTS.md](REQUIREMENTS.md) (`43 ready`: `REQ-001`–`REQ-016`, `BR-001`–`BR-015`, `CON-001`–`CON-012`).
- **Explicitly Out of Scope**: Kalman filter (`REQ-017`), live Mobilis GPS/ticketing ingestion (`REQ-018`), mobile notifications/export/geolocation/multi-route (`REQ-019`), advanced agentic workflows beyond 3 tools (`REQ-020`), Virtual Reality (`REQ-021`), and TEI business model (`REQ-022`).
- **Confirmed User Decisions**:
  - `DEC-001`: Core AR includes **both** Stop-Anchored arrival panel + compact 3D mini-route (`REQ-012`) **and** Tabletop 3D full-route mode (`REQ-013`).
  - `DEC-002`: Experiments support **both** stop hold (`0–900 s` integer) and optional bounded background traffic multiplier (default `1.0x`, provisional safe range `[0.5, 2.5]`).
  - `DEC-003`: Standard REST polling starts at `5 s` (`1 s / 5 s / 30 s` configurable); live demonstrations meet the provisional `< 2 s` server-update-to-display latency target via Server-Sent Events (SSE) push (with fast `1 s` polling fallback), while telemetry reports both request-to-display latency and full state freshness transparently.
  - `DEC-004`: Team's existing Mobilis Line 9 SUMO network is already committed in `sumo/` (`linha9.sumocfg`, `leiria.net.xml.gz`, `pt_vtypes.xml`, `gtfs_pt_stops.add.xml`, `gtfs_pt_vehicles_L9.add.xml` — 22-stop loop, 12 scheduled trips) and serves as the canonical route/stop ground truth alongside `sumo/manifest.json`; a fast 4-stop synthetic SUMO fixture (`sumo/fixtures/sample_line9/`) supports CI and fast headless unit tests.
  - `DEC-005`: Undated `M1`–`M4` technical milestones; retains DDM §6.1–§6.2 checks, setup runbook, and demo video checklist.
  - **Architectural choices confirmed during planning**:
    1. **AR 3D Stack**: 100% Native Android (Kotlin + Jetpack Compose + ARCore `AR Optional` + `io.github.sceneview:arsceneview` / Filament) — zero Unity bridge.
    2. **Workspace Layout**: Git repository rooted at `MobilisTransit/` (`C:\Users\loupa\Desktop\PROJETO_4X\MobilisTransit`) with ASCII top-level directories (`sumo/`, `backend/`, `ml/`, `android/`, `evaluation/`) to avoid Windows Gradle/NDK/ARCore/SUMO path-encoding bugs; academic proposal docs stay in the parent folder `../`.
    3. **Backend & Admin Web Stack**: FastAPI + SQLAlchemy 2.0 (SQLite WAL default, PostgreSQL-ready via `DATABASE_URL`) + FastAPI-served Admin Web UI (Jinja2 + vanilla JS + Chart.js; zero Node/npm build step).
    4. **SUMO & Ollama Runtime**: System Eclipse SUMO `1.28.0` installed locally (`SUMO_HOME=C:\Program Files (x86)\SUMO\`) plus local Python 3.13 `.venv` with PyPI `eclipse-sumo` fallback (`sumo.exe` bundled) + local Ollama (3B–4B tool-calling model such as `qwen2.5:3b` fitting 4 GB VRAM / 16 GB RAM) + OpenAI-compatible external API fallback via environment variables.
    5. **Offline ML Training in Google Colab**: SUMO multi-scenario datasets are generated locally and exported as a self-contained Colab bundle (`ml/exports/`). Training, model comparison, and held-out evaluation run in **Google Colab** via `ml/notebooks/MobilisTransit_AAP_Training_Colab.ipynb` calling the same pure-Python `ml/` modules (also runnable locally for CI tests). Exported artifacts use **portable cross-Python/cross-OS JSON serialization** (XGBoost native JSON + JSON preprocessor/linear/baseline weights — **no `pickle`/`joblib`**) so models trained on Colab Linux (Python 3.11/3.12) load identically on Windows (Python 3.13) with zero unpickling or security issues.

## 2. Repository Structure & Four-UC Boundaries (`CON-012`)
```text
MobilisTransit/                      # Main Git repo (parent ../ holds academic docs)
├── AGENTS.md                        # Agent workflow & repo pointers
├── README.md                        # Repo overview & SUMO replication instructions
├── pyproject.toml                   # Unified Python 3.13 workspace dependencies & pytest config
├── docs/                            # Discovery, architecture, plan, and task specs
├── sumo/                            # Canonical Line 9 SUMO assets + fixture (NEVER add sumo/__init__.py!)
│   ├── linha9.sumocfg               # Team's canonical Mobilis Line 9 SUMO config
│   ├── leiria.net.xml.gz            # OSM Leiria road network
│   ├── pt_vtypes.xml                # Bus vType definitions
│   ├── gtfs_pt_stops.add.xml        # GTFS bus stops mapped onto Leiria SUMO lanes
│   ├── gtfs_pt_vehicles_L9.add.xml  # Filtered Line 9 (route 1010): 22-stop loop, 12 daily trips
│   ├── manifest.json                # Canonical Line 9 topology, geometry & timetable manifest
│   ├── manifest.schema.json         # JSON Schema for manifest.json validation
│   ├── fixtures/sample_line9/       # Fast runnable 4-stop synthetic SUMO network for CI & tests
│   └── deprecated/                  # Archived unused OSM/GTFS exports & prototype scripts
├── backend/                         # TEI: Simulation control, API, DB, prediction hosting, admin UI, agent
│   ├── app/
│   │   ├── main.py                  # FastAPI app factory, lifespan, router & web mounts
│   │   ├── settings.py              # Pydantic Settings (env-driven)
│   │   ├── schemas/                 # Canonical Pydantic v2 DTOs & validation rules
│   │   ├── db/                      # SQLAlchemy 2.0 models, session factory, repositories
│   │   ├── simulation/              # SUMO binary locator, TraCI worker, replay engine
│   │   ├── adapter/                 # Unified SourceAdapter (live SUMO, replay, future GPS protocol)
│   │   ├── prediction/              # Versioned AAP model artifact loader & inference service
│   │   ├── jobs/                    # Async paired experiment queue, deduplication & runner
│   │   ├── api/                     # REST/JSON endpoints + SSE live stream + telemetry endpoints
│   │   ├── web/                     # Admin HTML/JS/CSS templates & static assets (mounted at /admin)
│   │   └── agent/                   # LangGraph state graph, 3 typed tools, LLM provider factory
│   └── tests/                       # pytest suite for simulation, DB, API, SSE, jobs, web, agent
├── ml/                              # AAP: Dataset export, Colab notebook, causal features, splits, models, eval
│   ├── dataset_gen.py               # Multi-scenario SUMO batch runner -> DB + Colab-ready zip/CSV/Parquet bundle
│   ├── features.py                  # Strictly causal pure-Python feature builder (shared by Colab & backend!)
│   ├── splits.py                    # Family-grouped leak-free splitter + split_manifest.json + auditor
│   ├── baselines.py                 # Historical segment+stop average & static timetable comparator
│   ├── serialization.py             # Portable JSON model/preprocessor serializer (zero pickle/joblib!)
│   ├── train.py                     # Ridge + XGBoost training, val selection & portable versioned export
│   ├── evaluate.py                  # Held-out MAE/RMSE/P90 (per-journey & pooled, 1s/5s/30s)
│   ├── notebooks/
│   │   └── MobilisTransit_AAP_Training_Colab.ipynb  # Primary Google Colab training & evaluation notebook
│   ├── exports/                     # Gitignored Colab dataset export bundles (CSV/Parquet + manifest)
│   ├── artifacts/                   # Versioned portable JSON model bundles + active_model.json pointer
│   └── tests/                       # Causality, leakage-audit, portable serialization & train/serve parity tests
├── android/                         # DDM + RVA: Native Kotlin Android app (Compose + Room + ARCore/SceneView)
│   ├── gradle/libs.versions.toml    # Pinned dependency catalog (JDK 17, compileSdk 36, minSdk 26)
│   └── app/src/main/
│       ├── AndroidManifest.xml      # AR Optional, INTERNET, CAMERA (lazy); NO location permissions
│       ├── assets/                  # demo_trip.json, ar_markers/ (printable reference images + manifest)
│       └── java/pt/ulo/estg/mobilistransit/
│           ├── domain/              # Pure Kotlin models, SequenceOrderingGuard, ArrivalCalculator
│           ├── data/                # Room (8 entities), Retrofit/OkHttp/SSE client, TransitRepository
│           ├── telemetry/           # Clock-offset sync (/time) + latency event reporter
│           └── ui/                  # Navigation, 2D Canvas route (E1), E2, E3/E4, E6, E5 AR + Tabletop AR
└── evaluation/                      # DEL-005 & DEL-006: Benchmarks, usability study kit & demo runbook
    ├── latency_analysis.py          # Computes median, P95, failure rates (server-to-display & freshness)
    ├── agent_eval.py                # 5-category × 5-repeat agent tool-use & DB fidelity audit
    ├── usability_protocol/          # Within-subjects 10–15 student study protocol, tasks, printable markers
    └── demo/RUNBOOK.md              # End-to-end setup, Line 9 drop-in, calibration & demo video script
```

> **Critical Windows/Python Safeguard**: Because `pip install eclipse-sumo` exposes a site-packages module named `sumo`, NEVER place an `__init__.py` file or top-level Python code inside `sumo/`. Production server/ML modules live strictly in `backend/` and `ml/`. `backend/app/simulation/sumo_env.py` locates `sumo`/`sumo.exe` by checking: (1) `os.environ["SUMO_HOME"]`, (2) `shutil.which("sumo")`, (3) `importlib.metadata.distribution("eclipse-sumo")` binary files — immune to working-directory shadowing.

## 3. Logical Component Architecture & Data Flows
```text
                         +---------------------------------------------------+
                         |              SUMO / Recorded Replay               |
                         |  sumo/ (Line 9) or sample_line9 + TraCI / Replay  |
                         +-------------------------+-------------------------+
                                                   | raw vehicle states & stop arrivals
                                                   v
+-----------------------------+      +---------------------------------------------------+
|   AAP Offline ML Pipeline   |      |         Central FastAPI Server (backend/)         |
|  ml/dataset_gen.py          |      |  +---------------------------------------------+  |
|  ml/features.py (shared)    | ---> |  | Unified SourceAdapter (REQ-002)             |  |
|  ml/splits.py (leak-free)   |      |  +----------------------+----------------------+  |
|  ml/train.py -> artifacts/  |      |                         | canonical state         |
+--------------+--------------+      |  +----------------------v----------------------+  |
               | versioned artifact  |  | Live Snapshot Store + PredictionService     |  |
               +-------------------> |  | (ml/features.py + versioned model bundle)   |  |
                                     |  +--------+-------------------------+----------+  |
+-----------------------------+      |           |                         |             |
| Historical Relational DB    | <----+-----------+ writes journeys,        | REST/JSON   |
| SQLite WAL (Postgres-ready) |      |             snapshots, experiments  | + SSE push  |
+--------------+--------------+      |  +----------------------------------v----------+  |
               ^                     |  | REST/SSE API + Experiment Job Manager       |  |
               |                     |  +--------+-------------------------+----------+  |
               |                     +-----------|-------------------------|-------------+
               |                                 |                         |
               |                    same REST/service layer                | REST + SSE (/journeys/{id}/stream)
               |                                 v                         v
               |                     +-----------------------+  +-------------------------+
               |                     | Admin Web UI (/admin) |  | Native Android App      |
               |                     | - Runs & Comparisons  |  | - TransitRepository     |
               +---------------------| - Experiment Forms    |  |   (Room + Poll/SSE +    |
                                     | - LangGraph + Ollama  |  |    Bundled Offline Demo)|
                                     |   3-Tool Assistant    |  | - 2D Compose (E1-E4,E6) |
                                     +-----------------------+  | - ARCore/SceneView      |
                                                                |   E5 Mini-Route + Table |
                                                                +-------------------------+
```

### Core Data Flows
1. **Live Simulation / Recorded Replay Snapshot Flow (`REQ-001`, `REQ-002`, `REQ-005`, `REQ-006`)**:
   - `TraCIRunner` or `ReplayRunner` emits raw `RawVehicleState` events tagged with `worker_generated_at_ms` (wall clock) and `simulation_clock_s` (simulation clock).
   - `SourceAdapter` normalizes both sources into `CanonicalVehicleState` with identical schema (`REQ-002`).
   - `PredictionService` builds causal features using `ml/features.py`, runs the active versioned model for every unvisited downstream stop, stamps `prediction_completed_at_ms` and `model_version`, computes `predicted_delay_s` ONLY when `reference_timetable_s` is present (`BR-001`), and sets `prediction_available=false` with `null` times on missing predictions (`BR-002`).
   - `SnapshotBus` persists completed records to the relational DB, publishes the new `JourneySnapshotDto` (`generation_time_ms`), and notifies all connected SSE subscribers immediately (`< 2 s` live demo target, `DEC-003`).
2. **Paired Delay Experiment Flow (`REQ-006`, `REQ-009`, `REQ-014`, `REQ-015`, `BR-005`–`BR-008`)**:
   - Mobile E3, Admin Web Form, or LangGraph `run_delay_experiment` submits `POST /experiments`.
   - Server validates route, stop ID, `stop_hold_s` ∈ `[0, 900]` (integer), and `traffic_multiplier` ∈ `[0.5, 2.5]` (`BR-007`).
   - `ExperimentJobManager` checks active/identical jobs by canonical parameter fingerprint (or explicit `idempotency_key`) to block duplicate jobs (`BR-008`).
   - Background worker executes **two** SUMO runs sequentially with identical `(network_version, demand_profile, departure_time_s, random_seed)` (`BR-005`): `EXP-xxx` (baseline: `stop_hold_s=0, traffic_multiplier=1.0`) and `EXP-yyy` (perturbed).
   - Actual stop arrival timestamps are stored in `observed_stop_arrivals`; comparison endpoints compute stop-by-stop **observed** differences (`BR-006`), strictly distinct from ML predicted delays.
3. **Android Synchronization & Shared 2D/AR State Flow (`REQ-007`–`REQ-013`, `BR-003`, `BR-009`, `BR-010`, `BR-015`)**:
   - `TransitRepository` is the single source of truth (`StateFlow<ActiveJourneyUiState>`) consumed by E1, E2, E5 (Stop-Anchored AR), and Tabletop 3D AR (`BR-010`).
   - Incoming snapshots pass through `SequenceOrderingGuard` scoped to `(journey_id, scenario_id, execution_id)`: older sequences are discarded; new `execution_id` resets sequence tracking; mismatched journeys/scenarios are ignored (`BR-009`).
   - Every valid snapshot updates Room cache (`VehicleSnapshotEntity`, `ArrivalPredictionEntity`). On network error/timeout, UI keeps the last valid Room state, shows the explicit Offline/Stale banner with `last_received_at`, and exposes Retry (`REQ-011`).
   - Remaining time is computed strictly from `predicted_arrival_s - simulation_clock_s`, freezing when `playback_state == PAUSED` or cache is stale (`BR-003`).
   - In offline demonstration mode, `DemoTripPlayer` replays `assets/demo_trip.json` with `source = "DEMONSTRATION"` and blocks new server experiments (`BR-004`, `BR-015`).

## 4. Public Contracts

### 4.1 SUMO Line 9 Canonical Manifest (`sumo/manifest.json`)
```json
{
  "route_id": "LINE-9",
  "route_code": "L9",
  "route_name": "Mobilis Line 9 (Leiria - Campus 2 ULO)",
  "network_version": "line9-v1.0",
  "sumocfg": "linha9.sumocfg",
  "net_file": "leiria.net.xml.gz",
  "route_file": null,
  "additional_files": ["pt_vtypes.xml", "gtfs_pt_stops.add.xml", "gtfs_pt_vehicles_L9.add.xml"],
  "bus_vehicle_id": "1010-1-001-XJA1-U-07h10-AvBernardoPimen.0",
  "bus_vehicle_prefix": "1010-",
  "bus_route_id": "1010-1-001-XJA1-U-07h10-AvBernardoPimen",
  "calibration_notes": "GTFS weekday baseline (2026-11-23); pending field-timed calibration",
  "stops": [
    {
      "stop_id": "P01",
      "sumo_bus_stop_id": "gtfs_lra_0096",
      "name": "Av. Bernardo Pimenta (Estádio)",
      "sequence_index": 0,
      "distance_from_start_m": 0.0,
      "x_norm": 0.42,
      "y_norm": 0.31,
      "reference_offset_s": 0,
      "ar_marker_id": "marker_p01",
      "ar_marker_width_m": 0.18
    }
  ]
}
```
- `net_file` supports both compressed `.net.xml.gz` (canonical Leiria network) and uncompressed `.net.xml` (synthetic fixture).
- `route_file` is optional (`null` when `<route>` and `<vehicle>` elements live inside `additional_files`, as in `gtfs_pt_vehicles_L9.add.xml`).
- **Circular/loop route support**: Real Line 9 starts (`P01`, `sequence_index: 0`) and ends (`P22`, `sequence_index: 21`) at `gtfs_lra_0096`. Each stop entry has a unique `stop_id` and strictly increasing `sequence_index`, while `sumo_bus_stop_id` may repeat for loop terminals (tracked by stop sequence index during simulation!).
- `reference_offset_s` is nullable (`null` tests `BR-001`); on canonical Line 9 it is populated from GTFS `until` offsets (`0`–`2400` s).
- `x_norm`, `y_norm` ∈ `[0.0, 1.0]` drive both the 2D Compose `Canvas` route (`CON-005`) and 3D AR layouts (`REQ-012`, `REQ-013`).

### 4.2 Central REST/JSON + SSE Endpoints (`backend/app/api/`)
| Method & Path | Purpose | Covered IDs |
| :--- | :--- | :--- |
| `GET /health` | Liveness, DB/SUMO availability, active `model_version`, `llm_provider`, `server_wall_time_ms` | `CON-003`, `REQ-005` |
| `GET /time` | Lightweight server wall-clock epoch ms for client clock-offset compensation | `REQ-016`, `EVAL-004` |
| `GET /routes` | List configured routes (`LINE-9`) | `REQ-006`, `CON-012` |
| `GET /routes/{route_id}/stops` | Ordered stops with geometry, optional reference offsets, and AR marker metadata | `REQ-006`, `REQ-012` |
| `GET /journeys` | List active & recorded journeys (filterable by `route_id`, `source`, `status`) | `REQ-002`, `REQ-006` |
| `GET /journeys/{journey_id}/snapshot` | Canonical latest snapshot (vehicle + predictions + provenance + timing metadata) | `REQ-002`, `REQ-005`, `REQ-006`, `BR-001`–`BR-004`, `BR-009` |
| `GET /journeys/{journey_id}/stream` | SSE stream pushing identical `JourneySnapshotDto` events on every update (`< 2 s` demo) | `REQ-006`, `REQ-011`, `CON-006`, `DEC-003` |
| `POST /journeys/{journey_id}/playback` | Control recorded/demo journey (`play`, `pause`, `step`, `restart`) | `REQ-002`, `BR-003`, `BR-009` |
| `POST /experiments` | Validate & enqueue paired baseline-vs-perturbed SUMO job (deduplicated) | `REQ-006`, `REQ-009`, `REQ-014`, `BR-005`, `BR-007`, `BR-008`, `DEC-002` |
| `GET /experiments` | List stored experiments/jobs filtered by period, stop hold, traffic multiplier, status | `REQ-003`, `REQ-014`, `REQ-015` |
| `GET /experiments/{id}` | Fetch job lifecycle status, errors, invariants, and stop-by-stop observed comparison | `REQ-006`, `REQ-009`, `BR-005`, `BR-006` |
| `GET /experiments/compare` | Compare two completed experiment IDs (`baseline_id`, `perturbed_id`) stop-by-stop | `REQ-006`, `REQ-015`, `BR-006` |
| `POST /telemetry/events` | Ingest client-side latency & error telemetry records | `REQ-016`, `EVAL-004` |
| `GET /telemetry/summary` | Aggregate median, P95, and failure rate by transport/interval | `REQ-016`, `EVAL-004` |
| `POST /agent/chat` | LangGraph 3-tool conversational assistant endpoint returning markdown + verbatim table | `REQ-015`, `BR-013`, `CON-007` |
| `GET /admin` | Server-rendered Administration Web UI (runs, experiment forms, charts, chat panel) | `REQ-014`, `REQ-015` |

### 4.3 Canonical Error Envelope
All `4xx`/`5xx` API responses return structured JSON:
```json
{
  "error_code": "VALIDATION_ERROR",
  "message": "stop_hold_s must be an integer between 0 and 900",
  "details": { "field": "stop_hold_s", "received": 1200, "allowed_range": [0, 900] }
}
```

### 4.4 Relational Database Schema (`backend/app/db/models.py`)
SQLAlchemy 2.0 declarative models compatible with both **SQLite (WAL mode, default `sqlite:///./data/mobilistransit.db`)** and **PostgreSQL** (`DATABASE_URL=postgresql+psycopg://...`):
- `routes`, `stops` (unique `(route_id, sequence_index)`), `journeys` (stores `journey_id`, `route_id`, `scenario_id`, `execution_id`, `experiment_family_id`, `source`, `network_version`, `demand_profile`, `traffic_multiplier`, `stop_hold_s`, `intervention_stop_id`, `departure_time_s`, `random_seed`, `status`, `playback_state`)
- `vehicle_snapshots` (FK `journey_id`, `sequence_number`, `simulation_clock_s`, `edge_id`, `lane_position_m`, `distance_travelled_m`, `speed_mps`, `current_segment_index`, `last_visited_stop_id`, `ongoing_stop_elapsed_s`, `last_completed_stop_dwell_s`, `worker_generated_at_ms`)
- `observed_stop_arrivals` (FK `journey_id`, `stop_id`, `observed_arrival_s`, `dwell_duration_s` — ground truth for ML target $a_{r,s}$ and `BR-006` comparisons)
- `experiment_jobs` (`job_id`, `name`, `idempotency_hash`, `status` ∈ `{QUEUED, RUNNING, COMPLETED, FAILED}`, `baseline_experiment_id`, `perturbed_experiment_id`, `error_message`, `created_at`, `started_at`, `completed_at`)
- `experiment_runs` (`experiment_id` formatted `EXP-001`, `EXP-002`, …, FK `job_id`, FK `journey_id`, `role` ∈ `{BASELINE, PERTURBED}`, full invariant parameters)
- `telemetry_events` (wall-clock timestamps, `clock_offset_ms`, `transport_mode`, `poll_interval_s`, `status`, `error_code`)

### 4.5 Android Room Database Entities (`android/.../data/local/`)
Implements all 8 entities specified in `../MobilisTransit_DDM_Draft/main.tex` §3.1:
1. `RouteEntity`
2. `StopEntity`
3. `RouteStopEntity`
4. `VehicleSnapshotEntity` (carries `journeyId`, `scenarioId`, `executionId`, `sequenceNumber`, `source`, `simulationClockS`, `playbackState`, `receivedAtMs`, `modelVersion`)
5. `ArrivalPredictionEntity` (carries `journeyId`, `scenarioId`, `executionId`, `sequenceNumber`, `stopId`, `predictionAvailable`, `remainingTimeS`, `predictedArrivalS`, `referenceTimetableS`, `predictedDelayS`, `modelVersion`)
6. `FavouriteStopEntity` (`stopId`, `routeId`, `displayOrder`, `createdAtMs`)
7. `ScenarioConfigEntity` (`localId`, `name`, `routeId`, `baseJourneyId`, `interventionStopId`, `stopHoldS`, `trafficMultiplier`, `demandProfile`, `departureTimeS`, `randomSeed`, `lastJobId`, `lastStatus`, `updatedAtMs`)
8. `ScenarioResultEntity` (`jobId`, `scenarioLocalId`, `baselineExperimentId`, `perturbedExperimentId`, `status`, `summaryJson`, `comparisonRowsJson`, `errorMessage`, `fetchedAtMs`)

### 4.6 Shared Causal ML Feature Contract (`ml/features.py`)
Single pure-Python module imported by BOTH `ml/train.py` (offline) and `backend/app/prediction/service.py` (online):
- Target: $y_{r,s,t} = a_{r,s} - t$ for $t < a_{r,s}$ (seconds).
- Strictly causal feature columns (verified by automated timestamp audit):
  `distance_travelled_m`, `remaining_distance_m`, `current_segment_index`, `target_stop_index`, `remaining_stops_count`, `current_speed_mps`, `recent_avg_speed_30s_mps`, `recent_avg_speed_60s_mps`, `last_completed_stop_dwell_s`, `ongoing_stop_elapsed_s`, `time_of_day_sin`, `time_of_day_cos`, `demand_profile`.
- **Forbidden features**: `observed_arrival_s`, future speeds, total trip duration, or oracle perturbation parameters (`stop_hold_s`, `traffic_multiplier`).

### 4.7 LangGraph Conversational Assistant Contract (`backend/app/agent/`)
- **Allowed tools (strict 3-tool allowlist, `BR-013`)**:
  1. `list_scenarios(route_id, demand_profile, min_hold_s, max_hold_s, status)`
  2. `run_delay_experiment(name, route_id, intervention_stop_id, stop_hold_s, traffic_multiplier, demand_profile, departure_time_s, random_seed)`
  3. `compare_experiments(baseline_experiment_id, perturbed_experiment_id)`
- **Graph nodes**: `reason_or_clarify` → `validate_tool_calls` → (`clarify_response` | `execute_tools`) → `format_response`.
- **Deterministic grounding**: Numeric comparison/list tables are returned in `structured_table` directly from executed tool outputs alongside `cited_experiment_ids`, preventing LLM numeric fabrication (`BR-002`, `BR-013`).

## 5. Key Decisions, Verified Versions & Trade-offs
| Decision | Selected Approach | Rejected Alternative | Rationale |
| :--- | :--- | :--- | :--- |
| **AR 3D Engine** | Native Kotlin + Compose + ARCore `1.57.0` + `io.github.sceneview:arsceneview:4.70.0` (Filament) | Unity as a Library (UAAL / AR Foundation) | Eliminates dual C#/Kotlin build, keeps single Gradle pipeline, shares `TransitRepository` `StateFlow` in-process (`BR-010`), supports both Augmented Images (`REQ-012`) and Horizontal Plane Tabletop (`REQ-013`). |
| **Android SDK & Toolchain** | `compileSdk=36`, `targetSdk=36`, `minSdk=26`, AGP `8.9.1`, Gradle `8.11.1`, Kotlin `2.1.20`, KSP `2.1.20-1.0.31`, Room `2.8.5`, JDK 17 | AGP 9 / JDK 21 or `minSdk=24` | Matches installed Android SDK (`android-34`/`36`) and host OpenJDK `17.0.14`; `minSdk=26` satisfies ARCore (`>=24`) and native `java.time`. |
| **Python Runtime & SUMO** | Python 3.13 venv, FastAPI `>=0.162.0`, SQLAlchemy `>=2.1.9`, XGBoost `>=3.4.1`, scikit-learn `>=1.9.1`, `eclipse-sumo` + `traci` + `sumolib`, `langgraph` + `langchain-ollama` + `langchain-openai` | Docker-only runtime | PyPI `eclipse-sumo` ships precompiled Windows `sumo.exe`; zero Docker prerequisite for tests/demos while still honoring external `SUMO_HOME`. |
| **Colab Model Portability** | Train in Google Colab (`MobilisTransit_AAP_Training_Colab.ipynb`) and export portable JSON artifacts (XGBoost native JSON + JSON preprocessor/Ridge/baseline params) | `pickle` / `joblib` binary dumps | Eliminates Linux-Colab (Python 3.11/3.12) → Windows-Server (Python 3.13) `sklearn`/`pickle` version crashes and prevents unsafe arbitrary-code deserialization. |
| **Database Engine** | SQLite (WAL mode + `foreign_keys=ON`) default via SQLAlchemy 2.0; Postgres via `DATABASE_URL` | PostgreSQL-only | Instant Windows execution and fast isolated `pytest` fixtures while preserving full relational SQL and seamless Postgres upgrade. |
| **Admin Web UI** | FastAPI Jinja2 + vanilla JS + Chart.js (`/admin`) | React/Vite SPA | Avoids Node/npm build step (Windows PowerShell blocks `npm.ps1`), same-origin API/agent access, low maintenance for a 2-student team. |
| **Live Demo Latency (`< 2 s`)** | Dual transport: configurable REST polling (`5 s` default; `1 s / 30 s` eval options) + SSE push (`/journeys/{id}/stream`) in Live Demo mode | Polling-only or full WebSocket | SSE is unidirectional HTTP (zero proxy/framing overhead, native `okhttp-sse`), delivers server updates in `< 200 ms` on LAN (`DEC-003`), while preserving REST polling benchmarks (`EVAL-004`). |
| **Traffic Multiplier Bounds** | Provisional `[0.5, 2.5]`, default `1.0` (configurable via env settings) | Unbounded float | Prevents SUMO gridlock / runaway simulation times on a 16 GB laptop (`BR-007`, `CON-010`). |
| **Local LLM Sizing (`CON-007`)** | Default Ollama `qwen2.5:3b` (fallback `llama3.2:3b`), `temperature=0`, abstracted behind `LLM_PROVIDER=ollama|openai_compatible` | 8B–70B local models | Fits 4 GB VRAM (RTX 2050) alongside SUMO + backend on 16 GB RAM; allows seamless external API swap during stress tests. |

## 6. Error Handling, Security, Privacy & Testing Strategy
- **Validation & Security**:
  - Strict Pydantic v2 schemas enforce types, ranges (`stop_hold_s` int `[0, 900]`, `traffic_multiplier` `[0.5, 2.5]`), and route/stop existence before spawning any subprocess (`BR-007`).
  - SUMO subprocesses are launched exclusively with `subprocess` argument lists (`shell=False`) and bounded per-run timeouts (`SUMO_RUN_TIMEOUT_S = 180`), preventing command injection and hung workers.
  - Experiment concurrency is serialized/bounded (`MAX_CONCURRENT_SUMO_JOBS = 1` default on 16 GB host) with idempotent deduplication (`BR-008`, `CON-010`).
  - LangGraph tool execution enforces a hard 3-tool allowlist, pre-execution validation node, and `max_steps = 6` recursion cap (`BR-013`); API keys/secrets (if external LLM fallback is used) load exclusively from environment variables / untracked `.env`.
- **Privacy (`CON-008`)**:
  - Android declares **no** location permissions; server schemas store **no** user location history or device tracking IDs.
  - AR camera frames stay transiently inside ARCore/Filament memory and are never persisted to disk or transmitted over the network (`REQ-012`).
- **Accessibility (`CON-009`)**:
  - Every Compose control provides explicit `contentDescription` / visible text labels, logical focus/reading order, `sp` font scaling, and text+icon status badges (`Online`, `Offline`, `SIMULATION`, `DEMONSTRATION`, delay badges) that never rely on color alone.
- **Testing & Verification Strategy**:
  - **Python (`pytest`)**: Unit tests for SUMO runner determinism (`BR-005`), source adapter parity (`REQ-002`), DB persistence (`REQ-003`), REST/SSE contract & deduplication (`BR-007`, `BR-008`), causal feature/leakage audit (`BR-012`), model export/inference parity (`REQ-004`, `REQ-005`), admin web routes (`REQ-014`), and LangGraph 5-category tool-use tests using deterministic mock chat model (`REQ-015`, `BR-013`).
  - **Android (`./gradlew.bat testDebugUnitTest assembleDebug`)**: JVM unit tests for `SequenceOrderingGuard` (`BR-009`), `ArrivalCalculator` (`BR-001`–`BR-003`), Room DAO persistence (`REQ-010`, `REQ-011`), `TransitRepository` online/SSE/offline/demo transitions (`BR-010`, `BR-015`), and ViewModel state machines (`E1`–`E6`, `E5 AR` fallback states `BR-011`).
  - **Integration & Evaluation (`T015`, `T016`)**: End-to-end automated integration suite across SUMO → Backend → ML → Agent + physical device verification checklist for ARCore tracking, live Ollama 5×5 audit, and latency telemetry analysis (`EVAL-001`–`EVAL-005`).

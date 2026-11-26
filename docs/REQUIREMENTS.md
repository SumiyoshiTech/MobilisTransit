# Project Requirements

- **Summary**: `43 ready` (`16 REQ`, `15 BR`, `12 CON`), `0 pending-decision`, `6 out-of-scope` (`6 REQ`)
- **Workspace Location Note**: This document lives inside the active Git repository `MobilisTransit/docs/`. Academic source citations below (`tema.MD`, `MobilisTransit_DDM_Draft/...`, `REPORT_EIT_VAR_IEEE/...`, `Apresentação/...`) refer to the reference documents located in the parent workspace folder `../` (`C:\Users\loupa\Desktop\PROJETO_4X`).

## 1. Functional Requirements

### REQ-001: Mobilis Line 9 Microscopic Traffic Simulation & Scenario Generation
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§1, §3.1, §4.3); `Apresentação/apresentação.md` (Slide 2, Slide 7, Part 3A); `tema.MD` (§4, §7); user confirmation (`DEC-004`)
- **Description**: Use the team's existing Eclipse SUMO model of **Mobilis Line 9** (imported from OpenStreetMap and matched to published Mobilis Line 9 stops/timetables) driven by a Python/TraCI worker that generates repeatable bus trips under varied background traffic volumes (including configurable traffic multipliers), intersection signal plans, stop dwell times, random seeds, departure offsets, and injected stop-hold disruptions (`setStop`). Record periodic vehicle snapshots (`road/edge`, `lane position`, `speed`), actual stop arrival timestamps, and full run metadata (network version, demand profile, random seed, departure time, disturbances). Calibrate baseline travel durations and dwell times using a small sample of field-timed Line 9 trips.
- **Acceptance Criteria**:
  - [ ] Existing team SUMO Line 9 network and ordered stop definitions serve as the canonical simulated route topology and reference timetable source.
  - [ ] Python/TraCI worker reproduces identical trajectories when run with the same network version, demand profile/traffic multiplier, departure time, stop-hold disturbance, and random seed.
  - [ ] Worker logs periodic vehicle state snapshots, actual arrival timestamps per stop, and full run configuration metadata for every journey.
  - [ ] Simulation supports configurable demand profiles (e.g., off-peak, campus rush, heavier congestion), traffic multipliers, random seeds, departure offsets, and stop dwell holds.
  - [ ] Baseline segment and dwell durations are calibrated against field-timed Line 9 observations with documented calibration notes.
- **Decision / Planning Notes**: Resolved via `DEC-004` (Line 9 already exists in SUMO; offline simulation + replay accepted as prototype digital representation per `main_changes7.tex` §2.1/§6). Dataset scale and scenario matrix remain open for pilot sizing in `ai-plan`.

### REQ-002: Unified Data Source Adapter & Recorded Journey Replay
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `tema.MD` (§5, §8); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§1, §4.2, Fig. 1); `Apresentação/apresentação.md` (Slide 5, Part 3B); `MobilisTransit_DDM_Draft/main.tex` (§1, §4)
- **Description**: Provide a unified source adapter that transforms vehicle state streams from live SUMO simulations and recorded journey replays (and is structured for future GPS telemetry) into a single canonical JSON format so the backend API, prediction service, and clients operate identically across data sources.
- **Acceptance Criteria**:
  - [ ] Live SUMO runs and recorded journey replays produce payloads conforming to the same canonical JSON state schema.
  - [ ] Prediction service, API endpoints, and mobile/admin clients consume live simulation and recorded replay streams without source-specific logic branches.
  - [ ] Recorded replay exposes play/pause status and advances according to its own source/simulation clock.
- **Decision / Planning Notes**: Concrete serialization format and adapter module boundaries are open for `ai-plan`.

### REQ-003: Central Historical Store for Journeys and Experiment Records
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `tema.MD` (§7); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§3.1, §4.2, Fig. 1); `Apresentação/apresentação.md` (Slide 5)
- **Description**: Persist completed journey records, periodic vehicle snapshots, observed stop arrival timestamps, scenario parameters, and paired experiment metadata/results (with unique identifiers such as `EXP-001`, `EXP-002`) in a central relational database to support offline ML dataset preparation and online queries from the API, web forms, and conversational agent.
- **Acceptance Criteria**:
  - [ ] All completed journeys and experiment runs persist across server restarts with unique identifiers and full configuration metadata (network version, demand profile, seed, departure time, disturbance parameters).
  - [ ] Stored experiments can be filtered and queried by time period, delay setting, or experiment ID.
  - [ ] Historical journey snapshots and stop arrivals can be exported cleanly to build reproducible offline ML datasets.
- **Decision / Planning Notes**: A relational database is specified (`main_changes7.tex` §3.1, §4.2); the concrete RDBMS engine (e.g., PostgreSQL vs. SQLite) remains an open architectural choice for `ai-plan`.

### REQ-004: Offline Arrival-Time Dataset Preparation, Model Training & Comparison
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `tema.MD` (§6, §10); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§3.1, §4.3, §5.1); `Apresentação/apresentação.md` (Slide 4, Slide 7, Slide 11, Part 3A)
- **Description**: Build an offline ML pipeline in Python that extracts supervised regression observations predicting remaining travel time ($y_{r,s,t} = a_{r,s} - t$ in seconds) to each downstream stop $s$ from strictly causal features available at time $t$ (travelled distance, remaining distance, current road segment index, remaining stops, recent average speed over $[t-\Delta t, t]$, last completed stop duration, ongoing stop elapsed time, and well-defined time-of-day/demand labels). Train and compare a historical segment-and-stop average baseline, regularized linear regression, and XGBoost (plus static published timetable comparison where applicable), evaluating sensitivity to update intervals (e.g., 1 s, 5 s, 30 s) and normal vs. disrupted journeys.
- **Acceptance Criteria**:
  - [ ] Feature extraction excludes post-trip speeds and future arrival timestamps and splits complete journeys + paired experiment families into train/validation/test partitions before generating observation rows.
  - [ ] Test split uses independent random seeds and includes unseen demand levels and delay durations; preprocessing, historical averages, and hyperparameter tuning fit strictly on train/validation partitions.
  - [ ] Evaluation reports MAE (primary), RMSE, and P90 absolute error both per journey and pooled across stops, prediction horizons, update intervals (1 s, 5 s, 30 s), and normal vs. disrupted scenarios.
  - [ ] Selected regression model outperforms the historical baseline in held-out test MAE across normal and delayed trips and is exported as a versioned model artifact bundled with its feature pipeline.
- **Decision / Planning Notes**: Training may run in Google Colab or local Python (`main_changes7.tex` §3.1); exact dataset volume and scenario matrix are finalized after pilot generation (`apresentação.md` Part 3A).

### REQ-005: Centralized Versioned Arrival Prediction Service
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§3.1, §4.2, Fig. 1, Table II); `MobilisTransit_DDM_Draft/main.tex` (§1, §3.1); `Apresentação/apresentação.md` (Slide 5)
- **Description**: Host the selected versioned AAP regression model and feature pipeline on the central Python server to compute arrival-time predictions for downstream stops from active journey state snapshots, ensuring the Android app and administration interface read predictions from the same model version.
- **Acceptance Criteria**:
  - [ ] Prediction service loads a versioned model artifact and computes remaining travel time / predicted arrival times server-side for all upcoming stops of a journey snapshot.
  - [ ] Every prediction payload includes the active `model_version` identifier alongside journey and sequence identifiers.
  - [ ] When a prediction cannot be computed for a stop, the service explicitly marks it unavailable rather than returning a fabricated estimate.

### REQ-006: Central REST/JSON Backend API for Routes, Snapshots & Paired Experiments
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `MobilisTransit_DDM_Draft/main.tex` (§3.1, §4); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§3.3, §4.2, §4.4, §5.3, Table II); `Apresentação/apresentação.md` (Slide 5, Slide 10, Part 3B, Part 3D); user confirmation (`DEC-002`, `DEC-003`)
- **Description**: Provide a central REST/JSON API serving route topology and ordered stops (`GET /routes`, `GET /routes/{id}/stops`), consistent journey state + prediction snapshots (`GET /journeys/{id}/snapshot`), and paired delay-comparison experiment submission and retrieval (`POST /experiments`, `GET /experiments/{id}` — supporting stop hold and/or background traffic multiplier per `main_changes7.tex` §3.3), shared across the Android client, administration web forms, and LangGraph assistant.
- **Acceptance Criteria**:
  - [ ] API exposes endpoints to retrieve routes, ordered stops, consistent journey snapshots (vehicles + predictions), submit paired experiments (accepting stop hold in `[0, 900] s` and/or bounded background traffic multiplier) returning a job ID, and query job status, errors, and stop-by-stop comparison results.
  - [ ] Every dynamic snapshot/prediction response carries `source`, `journey`, `scenario`, `sequence_number`, `simulation_clock`, `generation_time`, `model_version`, and explicit playback/pause state.
  - [ ] Server validates stop IDs, journey/experiment IDs, record existence, and intervention bounds (provisional `0–900 s` stop hold; bounded positive traffic multiplier) before launching SUMO jobs, returning structured validation errors on failure.
  - [ ] Experiment jobs track lifecycle states (`Queued`, `Running`, `Completed`, `Failed`), enforce paired baseline/perturbed execution with identical network, base demand, departure time, and seed, and prevent duplicate job creation for identical concurrent submissions.
- **Decision / Planning Notes**: Resolved via `DEC-002` (both stop hold and traffic multiplier supported per `main_changes7.tex`) and `DEC-003` (`ai-plan` decides whether demo `< 2 s` server-to-display latency is achieved via fast demo polling interval or optional push/SSE/WebSocket alongside REST snapshots). Concrete Python web framework and async job runner remain open for `ai-plan`.

### REQ-007: Android 2D Route Overview & Vehicle Tracking Screen (E1)
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `MobilisTransit_DDM_Draft/main.tex` (§2, §2.1 #1, §4, §5.2 E1); `Apresentação/apresentação.md` (Slide 8, Part 3C)
- **Description**: Provide the main 2D route overview screen (E1) displaying a self-drawn 2D route diagram (without external map services) with ordered stops, travel direction, visited vs. next stops, current vehicle position, upcoming arrivals list, manual refresh control, and explicit source, trip, simulation clock, reception age, and online/offline indicators.
- **Acceptance Criteria**:
  - [ ] Screen renders ordered stops, direction of travel, visited/next status, and vehicle position on a self-drawn 2D diagram without external mapping SDK dependencies.
  - [ ] Users can select a stop either directly on the 2D diagram or from the stop/arrivals list to open Stop Details (E2), and save a favourite stop.
  - [ ] Header clearly displays selected route, data source (`SIMULATION` / `DEMONSTRATION`), journey ID, simulation clock, last received age, and online/offline status distinguishable without color.

### REQ-008: Android Stop Arrival Details & Delay Display Screen (E2)
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `MobilisTransit_DDM_Draft/main.tex` (§2.1 #2, §5.2 E2); `Apresentação/apresentação.md` (Slide 8, Part 3C)
- **Description**: Display detailed arrival information for a selected stop (E2): predicted arrival time, remaining time calculated against the source/simulation clock, reference timetable when available, predicted delay (`prediction - reference`), vehicle ID and operational status, data provenance metadata, and navigation actions to route view (E1), AR view (E5), and favourites toggle.
- **Acceptance Criteria**:
  - [ ] Screen visually distinguishes predicted arrival, remaining time, reference timetable, and predicted delay (`prediction - reference`).
  - [ ] When no reference timetable exists, no delay label/value is shown; when a prediction is missing, the screen indicates unavailability without inventing a time.
  - [ ] Remaining time is computed against the source/simulation clock and freezes when playback is paused or data is stale.
  - [ ] Screen provides working actions to return to route view, open the stop in AR (E5), and add/remove the stop from favourites.

### REQ-009: Android Scenario Configuration & Experiment Comparison (E3 & E4)
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `MobilisTransit_DDM_Draft/main.tex` (§2.1 #4, §5.3 E3 & E4); `MobilisTransit_DDM_Draft/MobilisTransit_DDM_Draft.pdf` (§6.1); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§3.3, §4.1); `Apresentação/apresentação.md` (Slide 3, Part 3C); user confirmation (`DEC-002`)
- **Description**: Allow mobile users to create, edit, save locally, and delete (with confirmation) delay scenario configurations (name, route, trip/journey, intervention stop, additional stop-hold delay in seconds, optional background traffic multiplier defaulting to `1.0x`, and simulation seed), submit paired baseline-vs-delayed runs to the server, monitor execution states (`Queued`, `Running`, `Completed`, `Failed`), block duplicate submissions, preserve configurations upon failure, and inspect stop-by-stop observed arrival comparisons (E4) displaying baseline and scenario experiment IDs (e.g., `EXP-001`, `EXP-002`).
- **Acceptance Criteria**:
  - [ ] E3 form validates scenario name, route, journey, stop, integer stop-hold delay within allowed bounds (provisional `0–900 s`), and optional bounded traffic multiplier (default `1.0x`) before saving or running.
  - [ ] Saved scenario configurations and retrieved experiment results persist locally across app restarts; deleting a scenario requires confirmation.
  - [ ] Duplicate submission requests for the same run do not create duplicate server jobs; failed runs preserve user inputs and allow retrying.
  - [ ] E4 displays baseline and scenario experiment IDs, shared configuration summary, and a stop-by-stop table of observed baseline arrivals, delayed arrivals, and time differences, plus navigation to route view or back to scenario.
- **Decision / Planning Notes**: Resolved via `DEC-002` (supports both stop hold and optional traffic multiplier per `main_changes7.tex`).

### REQ-010: Android Persistent Favourite Stops Management (E6)
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `MobilisTransit_DDM_Draft/main.tex` (§2.1 #3, §3.1, §5.4 E6); `MobilisTransit_DDM_Draft/MobilisTransit_DDM_Draft.pdf` (§6.1); `Apresentação/apresentação.md` (Slide 8, Part 3C)
- **Description**: Enable users to add, view, reorder (move up/down), open, and remove favourite stops, persisting the user's ordered selection locally across app restarts and making saved favourites accessible offline.
- **Acceptance Criteria**:
  - [ ] Users can add favourite stops from E1/E2 and view, reorder (up/down), open in E2, or remove stops in E6.
  - [ ] Saved favourite stops and their order persist across application restarts and remain visible without network connectivity.

### REQ-011: Android Local Caching, Synchronized Updates, Stale Handling & Offline Demonstration
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `MobilisTransit_DDM_Draft/main.tex` (§2.1 #6, §3.1, §4, §5.1–§5.4 E6); `MobilisTransit_DDM_Draft/MobilisTransit_DDM_Draft.pdf` (§6.1–§6.2); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§5.3); `Apresentação/apresentação.md` (Slide 8, Slide 11, Part 3B); user confirmation (`DEC-003`)
- **Description**: Persist routes, stops, favourites, scenario configs/results, and cached journey snapshots locally (`Route`, `Stop`, `RouteStop`, `VehicleSnapshot`, `ArrivalPrediction`, `FavouriteStop`, `ScenarioConfig`, `ScenarioResult`). While visible in online mode, synchronize state updates from the server (configurable polling starting at 5 s, with live-demo fast polling or push/SSE/WebSocket as designed in `ai-plan` to meet the provisional `< 2 s` server-update-to-display demonstration target), discard older out-of-order responses scoped to the active journey/execution, and reject cross-journey/scenario overwrites. On connectivity loss or request failure, retain last valid cached state, show an offline banner with last reception timestamp and Retry action across info screens, and provide a bundled offline recorded journey demonstration with Play/Pause playback controls clearly distinguished from online data (disabling new server runs/predictions offline). Provide 4-destination bottom navigation (`Route`, `Favourites`, `Scenarios`, `AR`) with context-preserving back navigation, loading/empty/error states, and accessibility support.
- **Acceptance Criteria**:
  - [ ] Local persistence preserves cached state, favourites, and scenarios across restarts without overriding server reference truth.
  - [ ] Online synchronization runs only when a screen is visible, supports configurable polling intervals starting at 5 s plus a `< 2 s` server-to-display demonstration mode, discards older sequence numbers within the active journey/execution, accepts reset sequences on simulation restart, and ignores responses for non-selected journeys/scenarios.
  - [ ] On network failure, HTTP error, or timeout, the UI keeps the last valid cached data, displays an explicit offline/stale indicator with last reception time, and offers a functional Retry button.
  - [ ] Built-in offline demonstration replays precomputed snapshots of a bundled recorded trip with Play/Pause controls and clear `DEMONSTRATION` / playback clock labels, while blocking new experiment runs with an informative message.
  - [ ] App implements 4 main destinations (`Route`, `Favourites`, `Scenarios`, `AR`) plus secondary screens (`Stop Details`, `Experiment Comparison`), progress indicators, empty states, explicit text labels, accessible reading order, and system font scaling.
- **Decision / Planning Notes**: Resolved via `DEC-003` (prioritizing `main_changes7.tex` §5.3 `< 2 s` demonstration display latency target; `ai-plan` selects fast demo polling vs. SSE/WebSocket transport alongside REST polling). Specific Android persistence/UI library versions (Room, Compose, HTTP client) remain open for `ai-plan`.

### REQ-012: Stop-Anchored Augmented Reality View (E5) with 3D Mini-Route, Shared State & 2D Fallback
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `MobilisTransit_DDM_Draft/main.tex` (§2, §2.1 #5, §3.1, §5.4 E5); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§1.1, §3.2, §4.4, Table II); `Apresentação/apresentação.md` (Slide 6, Slide 9, Part 3C); `Apresentação/ApresentaçãoV1.pptx` (Slide 9); user confirmation (`DEC-001`)
- **Description**: Detect a printed stop reference image via camera (using `AR Optional` and ARCore `Augmented Images`, without assuming recognition of arbitrary real bus-stop signs) and anchor an arrival info card (stop name, line number, predicted arrival, delay relative to schedule, data source label, update timestamp) **paired directly above a compact 3D mini-route** showing neighbouring stops, the approaching 3D bus, and colour + text coding for downstream delays (`main_changes7.tex` §3.2, §4.4). Consume the exact same shared state/repository layer as the 2D view, provide single-tap switching to Tabletop mode (`REQ-013`) or 2D view, handle permission refusal, unsupported devices, and tracking loss gracefully, and never save camera frames.
- **Acceptance Criteria**:
  - [ ] App checks ARCore compatibility and requests camera permission lazily upon opening E5 (`AR Optional`).
  - [ ] After valid reference-image confirmation, the AR view anchors both the arrival card and the compact 3D mini-route (neighbouring stops, approaching 3D bus, delay colour + text coding) showing identical stop, predicted arrival, delay, source, and simulation clock values as E2.
  - [ ] Refused camera permission, unsupported device, or lost image tracking presents a clear explanation and direct controls for manual stop selection / 2D view.
  - [ ] No camera frames are saved locally or uploaded to the server.
- **Decision / Planning Notes**: Resolved via `DEC-001` (prioritizing `main_changes7.tex` §3.2/§4.4: arrival card + compact 3D mini-route in core scope). Concrete 3D/panel rendering engine (native ARCore/SceneView vs. embedded Unity AR) and `minSdk` remain open for `ai-plan` after hardware tests.

### REQ-013: Tabletop 3D Full-Route Augmented Reality Mode
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§1.1, §3.2, §4.4); `tema.MD` (§4); user confirmation (`DEC-001`)
- **Description**: Detect a horizontal desk/tabletop plane and render the complete Line 9 route in 3D so students and analysts can inspect vehicle movement and watch an injected delay propagate from stop to stop during full-route analysis or classroom demonstrations, synchronized with the shared journey repository and offering single-tap switching to stop-anchored AR or the 2D screen.
- **Acceptance Criteria**:
  - [ ] Horizontal plane detection anchors the complete Line 9 3D route onto a desk surface in indoor environments without outdoor GPS alignment.
  - [ ] Rendered 3D vehicle position, stops, and downstream delay indicators read from the same shared repository layer as the 2D view and stop-anchored AR mode.
  - [ ] Users can switch between Tabletop 3D mode, stop-anchored AR mode (E5), and the 2D screen (E1/E2) with a single tap while retaining the active journey/scenario state.
- **Decision / Planning Notes**: Resolved via `DEC-001` (confirmed in current scope per `main_changes7.tex` §1.1, §3.2, §4.4). Concrete 3D rendering framework (native ARCore vs. embedded Unity AR module) remains open for `ai-plan`.

### REQ-014: Administration Web Forms & Monitoring Interface
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `tema.MD` (§6, §7); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§3.3, §4.1, §4.2, §4.5, §5.3); `Apresentação/apresentação.md` (Slide 5, Slide 10, Part 3C, Part 3D); user confirmation (`DEC-002`)
- **Description**: Provide a web administration interface allowing analysts to browse stored simulation runs, launch paired SUMO delay experiments (with stop hold and/or background traffic multiplier per `main_changes7.tex` §3.3) via structured web forms, monitor experiment status, and inspect stop-by-stop arrival comparison tables/curves against baseline runs — sharing the same backend API as the conversational assistant to enable task-time and step-count benchmarking.
- **Acceptance Criteria**:
  - [ ] Web UI lists stored simulation runs/experiments with filtering by time period or delay parameters.
  - [ ] Web forms allow configuring and submitting paired baseline-vs-delayed SUMO runs (specifying stop hold in `[0, 900] s` and/or bounded traffic multiplier), monitoring job progress/errors, and displaying stop-by-stop arrival differences/curves with experiment IDs.
  - [ ] Web forms invoke the same central REST/JSON backend endpoints used by the agentic assistant.
- **Decision / Planning Notes**: Resolved via `DEC-002` (both stop hold and traffic multiplier supported per `main_changes7.tex`). Web UI frontend stack remains open for `ai-plan`.

### REQ-015: Tool-Restricted Conversational Scenario Analysis Assistant
- **Origin**: `explicit`
- **Status**: `ready`
- **Source(s)**: `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§1.1, §2.4, §3.3, §4.2, §4.4, §5.3, Table II); `Apresentação/apresentação.md` (Slide 5, Slide 10, Slide 11, Part 3D); user confirmation (`DEC-002`)
- **Description**: Embed a conversational analysis assistant in the administration interface orchestrated via a LangGraph state graph over a local tool-calling open-weight LLM in Ollama (with external API or lighter/separate model execution as hardware contingency). Restrict execution strictly to three typed backend tools — `list_scenarios`, `run_delay_experiment` (supporting stop hold and/or traffic multiplier per `main_changes7.tex` §3.3), and `compare_experiments` — validating parameters server-side, requesting clarification on missing/ambiguous/out-of-range inputs before calling tools, presenting numeric comparison tables directly from tool outputs citing stored experiment IDs, and reporting unavailable records honestly.
- **Acceptance Criteria**:
  - [ ] LangGraph state graph parses analyst requests into typed tool arguments and invokes only `list_scenarios`, `run_delay_experiment` (stop hold and/or traffic multiplier), and `compare_experiments`.
  - [ ] Ambiguous, incomplete, or out-of-range requests (e.g., invalid stop, delay outside `[0, 900] s`, or invalid traffic multiplier) trigger a clarification prompt without executing any backend tool.
  - [ ] Comparison outputs display exact numeric values from backend tools alongside stored experiment IDs (e.g., `EXP-001`, `EXP-002`); non-existent records are explicitly reported as unavailable.
  - [ ] All arrival predictions originate from the AAP model / stored SUMO records; the LLM never computes or invents arrival estimates.
- **Decision / Planning Notes**: Resolved via `DEC-002` (`run_delay_experiment` supports stop hold and traffic multiplier per `main_changes7.tex` §3.3). Specific Ollama open-weight model selection remains open for `ai-plan`.

### REQ-016: End-to-End Telemetry Logging & Latency Instrumentation
- **Origin**: `inferred`
- **Status**: `ready`
- **Source(s)**: `tema.MD` (§10); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§5.3); `MobilisTransit_DDM_Draft/MobilisTransit_DDM_Draft.pdf` (§6.2); `Apresentação/apresentação.md` (Slide 11, Part 3B); user confirmation (`DEC-003`)
- **Description**: Provide wall-clock timestamp instrumentation (aligned or offset-compensated across host and phone, strictly separate from the simulation clock) from SUMO state generation through prediction output, server update, API request/response, and Android screen rendering, enabling evaluation of the provisional `< 2 s` demonstration display latency target (`main_changes7.tex` §5.3) as well as reporting median and P95 for both API request-to-display latency and end-to-end state freshness across polling intervals starting at 5 s, plus failure rates. Concrete telemetry/logging format and clock-sync mechanism remain open for `ai-plan`.
- **Acceptance Criteria**:
  - [ ] Instrumentation records correlated wall-clock timestamps (keyed by journey, scenario, and sequence ID) for worker state generation, prediction completion, server update publication, API request start, client receipt, and UI rendering.
  - [ ] Analysis output computes median, P95, and failure rates across tested update/polling intervals, reporting server-update-to-display latency (against the provisional `< 2 s` live-demo target) alongside API request-to-display latency and full state freshness.
- **Decision / Planning Notes**: Resolved via `DEC-003` (prioritizing `main_changes7.tex` §5.3 while preserving decomposed reporting). Concrete instrumentation implementation is open for `ai-plan`.

### REQ-017: Kalman Filter Online Arrival-Time State Estimator
- **Origin**: `suggestion`
- **Status**: `out-of-scope`
- **Source(s)**: `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§3.1); `Apresentação/apresentação.md` (Slide 12, Part 3A, Part 3C)
- **Description**: Implement and benchmark a Kalman filter variant (linear/extended/unscented) as an online state estimator alongside the primary supervised regressors if time permits within the AAP schedule.
- **Acceptance Criteria**:
  - [ ] Out of current scope (optional extension after completing historical baseline, regularized linear regression, and XGBoost).

### REQ-018: Live Real-World Mobilis GPS Telemetry Ingestion & Operational Digital Twin
- **Origin**: `future`
- **Status**: `out-of-scope`
- **Source(s)**: `tema.MD` (§5, §8); `MobilisTransit_DDM_Draft/main.tex` (§1); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§1, §2.1, §4.2); `Apresentação/apresentação.md` (Slide 5, Slide 12, Part 3C)
- **Description**: Ingest real-time GPS vehicle feeds (and/or ticketing data) from Mobilis buses, perform road-network map-matching, and re-train/re-validate arrival models for live operational digital twin deployment.
- **Acceptance Criteria**:
  - [ ] Out of current scope (future work; only the unified source adapter contract in `REQ-002` is in current scope).

### REQ-019: Mobile Feature Extensions (Notifications, Export, User Location, Multi-Route)
- **Origin**: `suggestion`
- **Status**: `out-of-scope`
- **Source(s)**: `MobilisTransit_DDM_Draft/main.tex` (§2 Priority Table); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§4.1)
- **Description**: Add approaching-bus notifications, comparison result export, real-time user geolocation, additional Mobilis routes, inter-line transfers, city-wide schedule optimization, and physical bus/street-stop deployment.
- **Acceptance Criteria**:
  - [ ] Out of current scope (explicitly listed under Extensions / outside semester scope).

### REQ-020: Advanced Analytical Agent Queries & Additional Synchronization Optimizations
- **Origin**: `suggestion`
- **Status**: `out-of-scope`
- **Source(s)**: `Apresentação/apresentação.md` (Part 3C)
- **Description**: Extend the TEI agent to handle complex analytical workflows beyond its three core tools, and add advanced DDM synchronization optimizations beyond the core polling/caching mechanism.
- **Acceptance Criteria**:
  - [ ] Out of current scope (listed under *Extensões possíveis* in `apresentação.md` Part 3C).

### REQ-021: Virtual Reality (VR) Component
- **Origin**: `suggestion` *(conditional item in early canvas `tema.MD`, omitted from `main_changes7.tex` and all later specifications)*
- **Status**: `out-of-scope`
- **Source(s)**: `tema.MD` (§6 RVA, §8); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§1.1, Table II); `Apresentação/apresentação.md` (Slide 6, Part 3C); user confirmation (`DEC-001`)
- **Description**: Deliver a Virtual Reality (VR) visualization/interaction component for RVA if required by the agreed course scope and available VR equipment.
- **Acceptance Criteria**:
  - [ ] Out of current scope (confirmed dropped per `DEC-001`, privileging `main_changes7.tex`).

### REQ-022: TEI Business Model Definition
- **Origin**: `suggestion` *(listed in early canvas `tema.MD`, omitted from `main_changes7.tex` and all later specifications)*
- **Status**: `out-of-scope`
- **Source(s)**: `tema.MD` (§6 TEI); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§1.1, Table II); `Apresentação/apresentação.md` (Slide 6, Part 3C); user confirmation (`DEC-001`)
- **Description**: Produce a business model definition for MobilisTransit as part of the TEI course unit contribution.
- **Acceptance Criteria**:
  - [ ] Out of current scope (confirmed dropped per `DEC-001`, privileging `main_changes7.tex`).

## 2. Business Rules
| ID | Rule | Origin | Status | Source(s) | Validation / Acceptance Criterion |
| :--- | :--- | :--- | :--- | :--- | :--- |
| BR-001 | **Delay definition & missing reference timetable**: Predicted delay equals `predicted_arrival - reference_timetable`; if no reference timetable exists for a stop, the UI must not compute or label any value as a delay. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§2.1 #2, §5.2); `Apresentação/apresentação.md` (Part 3C) | Given a snapshot without a reference timetable, stop detail and AR views omit any delay label/value; with a reference time, displayed delay equals `prediction - reference`. |
| BR-002 | **No fabricated arrival predictions**: Missing predictions must be explicitly displayed as unavailable; neither the mobile client nor the LLM assistant may invent arrival estimates. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§5.2); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§2.4); `Apresentação/apresentação.md` (Slide 10) | When a stop lacks a model prediction in the API response, UI shows an unavailable state rather than a placeholder/interpolated time. |
| BR-003 | **Source-clock remaining time**: Remaining arrival time is calculated strictly against the source/simulation clock (not the phone's wall clock), so paused playback or stale cache never continues counting down. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§4, §5.2); `Apresentação/apresentação.md` (Slide 8, Part 3B) | Pausing playback or disconnecting network freezes the displayed remaining minutes/seconds until a newer source clock arrives. |
| BR-004 | **Explicit provenance & staleness disclosure**: Every informational screen must identify data source (`SIMULATION` / `DEMONSTRATION` / cached), simulation/playback clock, and last reception time, marking cached/offline data as stale. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§1, §2.1 #6, §4); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§4.1); `Apresentação/apresentação.md` (Slide 2, Slide 8, Part 3B) | E1, E2, E5, and E6 display data source and clock metadata; offline mode displays a stale banner with last reception timestamp. |
| BR-005 | **Paired experiment invariance**: Baseline and delayed simulation runs in a comparison experiment must share identical road network, demand profile, departure time, and random seed, changing only the configured intervention. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§5.3); `Apresentação/apresentação.md` (Slide 3 Notes) | Inspecting paired experiment logs (`EXP-001`, `EXP-002`) confirms identical network version, demand, departure time, and seed, differing only in the injected disturbance. |
| BR-006 | **Predicted delay vs. observed experiment difference**: Stop arrival views (E2/E5) show predicted delay against the reference timetable, whereas experiment comparisons (E4 and `compare_experiments`) report differences between observed simulated arrival times of paired runs. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§5.3 E4); `Apresentação/apresentação.md` (Part 3C) | E4 and `compare_experiments` outputs are labeled as observed simulated arrivals/differences and match actual SUMO arrival logs rather than model predictions. |
| BR-007 | **Intervention parameter bounds & server-side validation**: Stop-hold additional delay must be an integer within the provisional range `[0, 900]` seconds, and optional background traffic multiplier must be within positive bounded limits (default `1.0x`, upper cap finalized in `ai-plan`); stop IDs, journey/experiment IDs, and bounds must be validated in both client forms and server-side before running SUMO. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§5.3); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§3.3, §4.4); `Apresentação/apresentação.md` (Part 3D); user confirmation (`DEC-002`) | Requests with negative, non-integer, or `> 900 s` stop holds, non-positive/out-of-range traffic multipliers, or non-existent stop/experiment IDs are rejected with a validation error and never spawn SUMO runs. |
| BR-008 | **Duplicate-job prevention, error preservation & delete confirmation**: Repeated submissions for an active/identical run must not create duplicate jobs; execution failures must preserve scenario configuration for retry; deleting a saved scenario requires user confirmation. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§5.3); `MobilisTransit_DDM_Draft/MobilisTransit_DDM_Draft.pdf` (§6.1) | Rapid double-tap on Run creates at most one job; simulated API failure retains E3 fields; tapping Delete displays a confirmation dialog before removal. |
| BR-009 | **Scoped monotonic sequence ordering**: Clients discard out-of-order responses with older sequence numbers, checked strictly within the active `(journey, scenario, execution)` scope so simulation restarts are not dropped and unrelated journeys/scenarios never overwrite active state. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§4); `MobilisTransit_DDM_Draft/MobilisTransit_DDM_Draft.pdf` (§6.1); `Apresentação/apresentação.md` (Part 3B) | Injecting an older sequence packet is ignored; restarting a run with a new execution ID accepts sequence `1`; receiving a packet for another journey/scenario does not alter displayed state. |
| BR-010 | **2D and AR single source of truth**: The 2D views and AR view(s) must consume the exact same shared state/repository layer so they never present conflicting predictions for the same journey and stop. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§3.1); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§3.2, Table II); `Apresentação/apresentação.md` (Slide 6, Slide 9) | Switching between E2 and E5 for the same stop and sequence displays identical predicted arrival, delay, source, and simulation clock values. |
| BR-011 | **Valid AR marker confirmation & mandatory 2D fallback**: The AR stop panel may only be associated with and anchored to a stop after valid reference-image identification; permission refusal, unsupported hardware, or tracking loss must expose clear status and 2D access. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§5.4); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§3.2) | No stop panel anchors before marker detection; revoking camera permission or losing tracking shows an explanatory message and working 2D view button. |
| BR-012 | **Causal ML features & leakage-free family-grouped splits**: Regression target is $y_{r,s,t} = a_{r,s} - t$ ($t < a_{r,s}$); future arrival timestamps and post-trip speeds are excluded; complete journeys and paired experiment families stay together in one partition with independent test seeds and unseen demand/delay profiles; preprocessing, historical averages, and hyperparameter tuning fit on train/validation only. | `explicit` | `ready` | `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§4.3, §6); `Apresentação/apresentação.md` (Slide 7, Part 3A) | Automated split audit verifies zero journey-ID, experiment-family, or random-seed overlap across train/validation/test and confirms no feature accesses timestamps `> t`. |
| BR-013 | **Grounded tool-only agent execution & clarification gate**: The assistant only calls predefined typed backend tools (`list_scenarios`, `run_delay_experiment`, `compare_experiments`), renders numeric tables directly from tool outputs citing stored experiment IDs, and asks for clarification on ambiguous/missing/out-of-range inputs before calling any tool. | `explicit` | `ready` | `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§2.4, §3.3, §4.4); `Apresentação/apresentação.md` (Slide 10, Part 3D) | Agent audit confirms 0 unauthorized tool calls, clarification returned for ambiguous/out-of-range prompts, and 100% numeric/ID match between displayed tables and DB records. |
| BR-014 | **Simulated-domain validity boundary**: Prediction accuracy and evaluation results obtained on SUMO simulated trips (even when field-calibrated) must not be presented as proven real-world operational accuracy. | `explicit` | `ready` | `tema.MD` (§9); `MobilisTransit_DDM_Draft/MobilisTransit_DDM_Draft.pdf` (§6.2); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§6); `Apresentação/apresentação.md` (Slide 12, Part 3C–3D) | Evaluation reports and UI labels explicitly qualify all results as simulation-based and document simulator-to-reality limitations. |
| BR-015 | **Offline operational boundary**: Offline mode permits viewing favourites, cached snapshots, saved results, and replaying the bundled demonstration trip, but forbids executing new simulations or computing new live predictions without server connectivity. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§4, §5.4); `MobilisTransit_DDM_Draft/README.md`; `Apresentação/apresentação.md` (Slide 8 Notes) | In airplane mode, favourites, saved results, and bundled demo playback function normally while submitting a new experiment is disabled with a connectivity notice. |

## 3. Technical & Operational Constraints
| ID | Constraint | Origin | Status | Source(s) | Impact on Planning |
| :--- | :--- | :--- | :--- | :--- | :--- |
| CON-001 | **Native Android with Kotlin**: The mobile application must be built natively for Android in Kotlin (with Jetpack Compose, MVVM/StateFlow, Coroutines, and Room over SQLite proposed and adopted across reports). | `explicit` | `ready` | `MobilisTransit_DDM_Draft/README.md`; `MobilisTransit_DDM_Draft/main.tex` (§3); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§3.2); `Apresentação/apresentação.md` (Slide 8) | `ai-plan` must architect a native Android Kotlin client and confirm `minSdk` and library versions after testing available devices. |
| CON-002 | **Eclipse SUMO + Python/TraCI Simulator**: Traffic simulation must use open-source Eclipse SUMO driven from Python via TraCI (`getRoadID`, `getLanePosition`, `getSpeed`, `setStop`). | `explicit` | `ready` | `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§2.1, §3.1, §4.2); `Apresentação/apresentação.md` (Slide 5, Slide 7, Part 3A) | `ai-plan` must design the Python simulation worker around SUMO/TraCI process orchestration and deterministic seeding. |
| CON-003 | **Centralized Python Server, Relational Store & REST/JSON Contract**: Simulation execution, ML inference, historical persistence (relational DB), and agent orchestration run on a central Python server accessed via REST/JSON carrying journey, scenario, sequence, simulation clock, generation time, and model version metadata. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§1, §3.1, §4); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§3.1, §4.2); `Apresentação/apresentação.md` (Slide 5, Part 3B) | `ai-plan` must keep heavy compute off Android, select a Python web framework and RDBMS engine, and freeze the shared REST/JSON schema early (M1). |
| CON-004 | **ARCore `AR Optional`, Printed Stop Markers & Horizontal Plane Detection**: AR integration uses ARCore in `AR Optional` mode with `Augmented Images` on printed stop reference images and horizontal plane detection for Tabletop 3D mode in controlled indoor environments (no generic street-sign recognition). | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§3.1); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§3.2, §4.4, §6); user confirmation (`DEC-001`) | `ai-plan` must ensure the app installs and runs on non-AR devices, design the printed reference-image database and plane-tracking Tabletop view, and select native ARCore vs. Unity AR rendering. |
| CON-005 | **No External Mapping SDK Dependency**: The initial Android release must render its own 2D schematic route diagram without depending on external mapping services. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§4) | `ai-plan` must plan a custom 2D Compose/Canvas route diagram rather than Google Maps/Mapbox integration. |
| CON-006 | **Synchronization Latency & Update Interval Constraint**: Standard REST polling starts at `5 s` while visible and online; live demonstrations target provisional **`< 2 s` display latency from server update to client rendering** (`main_changes7.tex` §5.3, `DEC-003`), while evaluations also report API request-to-display latency and full state freshness separately (`apresentação.md` Part 3B). | `explicit` | `ready` | `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§5.3); `Apresentação/apresentação.md` (Slide 11, Part 3B); `MobilisTransit_DDM_Draft/MobilisTransit_DDM_Draft.pdf` (§6.2); user confirmation (`DEC-003`) | `ai-plan` must support configurable polling intervals (default `5 s`) plus a live-demo mechanism (fast demo polling `≤ 1 s` or SSE/WebSocket push alongside REST snapshots) to meet `< 2 s` server-to-display latency. |
| CON-007 | **Local-First LLM Orchestration (LangGraph + Ollama) with Resource Contingency**: Conversational assistant uses LangGraph and a local tool-calling model in Ollama; external API, lighter model, or separate execution host is permitted if co-hosting Ollama + SUMO + backend exceeds test machine CPU/RAM. | `explicit` | `ready` | `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§2.4, §3.3, §6); `Apresentação/apresentação.md` (Slide 10, Slide 12) | `ai-plan` must abstract the LLM provider behind LangGraph so local Ollama is default while allowing seamless swap to a lighter model or external API under hardware pressure. |
| CON-008 | **Privacy & Non-Storage of Camera Frames / Personal Location**: Camera frames must never be stored locally or remotely, and no personal user location history may be stored on the server. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§5.4); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§6); `Apresentação/apresentação.md` (Part 3D) | `ai-plan` must process AR camera frames transiently in memory only and exclude GPS location collection/logging from backend schemas. |
| CON-009 | **Accessibility & Color-Independent UI States**: Mobile controls require explicit text labels, accessible reading order, system font scaling, and status indicators (`Online`, `Offline`, `Simulation`, `Demonstration`) distinguishable without color. | `explicit` | `ready` | `MobilisTransit_DDM_Draft/main.tex` (§5.2) | `ai-plan` must incorporate accessible semantics, dynamic typography, and text/symbol status badges into UI component design. |
| CON-010 | **Target Hardware Constraints**: Prototype execution requires an ARCore-compatible Android device (emulator usable for non-AR UI tests) and a laptop/desktop host able to run SUMO, Python backend/DB, and local Ollama. | `explicit` | `ready` | `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§4.5); `MobilisTransit_DDM_Draft/MobilisTransit_DDM_Draft.pdf` (§6.1) | `ai-plan` must budget host CPU/RAM for concurrent SUMO + backend + Ollama execution and plan early AR device compatibility verification. |
| CON-011 | **No Live Operator Feeds Assumed**: Development, testing, and demonstration must function completely without live Mobilis GPS telemetry or operator ticketing APIs. | `explicit` | `ready` | `tema.MD` (§5); `MobilisTransit_DDM_Draft/main.tex` (§1); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§1); `Apresentação/apresentação.md` (Slide 2) | `ai-plan` must support mock/recorded JSON fixtures for decoupled client work and full offline/local SUMO simulation execution. |
| CON-012 | **Single-Route Semester Boundary & Four-UC Modularity**: Implementation is bounded to one route (Mobilis Line 9) with clean modular separation across TEI, AAP, DDM, and RVA sharing identical state, prediction, and experiment identifiers. | `explicit` | `ready` | `tema.MD` (§6); `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§1.1, §4.1, Table II); `Apresentação/apresentação.md` (Slide 6, Slide 12) | `ai-plan` must organize repository modules and integration contracts so each UC's contribution is independently testable and demonstrably integrated. |

## 4. Contradictions & Conflicts
| ID | Topic | Reference A | Reference B | Impact | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| CONF-001 | **Tabletop 3D AR route mode: Core vs. Extension** | `tema.MD` (§4: *"explorar a linha em realidade aumentada sobre uma mesa"*) and `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§1.1, §3.2, §4.4: defines tabletop 3D full-route placement via horizontal plane detection as one of two viewing modes in the prototype) | `MobilisTransit_DDM_Draft/main.tex` (§2 Priority Table & `README.md`: classifies tabletop route representation as an *Extension* after core features) and `Apresentação/apresentação.md` (Slide 9, Slide 12, Part 3C: explicitly marks *"rota completa em modo Tabletop"* as an extension outside essential core delivery) | Resolved via `DEC-001`: Tabletop 3D mode (`REQ-013`) kept in core scope per `main_changes7.tex`. | `resolved` |
| CONF-002 | **Core AR stop experience: Info panel only vs. Info panel + 3D mini-route** | `MobilisTransit_DDM_Draft/main.tex` (§2 Priority Table, §2.1 #5, §5.4 E5: specifies an arrival info panel anchored to a stop reference image, with no 3D mini-route) | `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§1.1, §3.2, §4.4), `Apresentação/apresentação.md` (Slide 6, Slide 9, Part 3C), and `Apresentação/ApresentaçãoV1.pptx` (Slide 9 visual mockup): defines core AR as an anchored arrival panel **paired with a compact 3D mini-route** showing neighbouring stops and the approaching 3D bus | Resolved via `DEC-001`: Core E5 (`REQ-012`) includes both anchored arrival panel and compact 3D mini-route per `main_changes7.tex`. | `resolved` |
| CONF-003 | **2-second latency target definition vs. 5-second polling** | `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§5.3: *"provisional display latency target of 2 s from server update to client rendering during demonstrations"* while polling starts at 5 s) and `tema.MD` (§10: latency from server update reception to app display) | `Apresentação/apresentação.md` (Slide 11 & Part 3B: explicitly defines provisional **P95 < 2 s** for *API request start to screen render* only, measures *server generation to display including ~5 s polling wait* separately, and warns not to claim full updates `< 2 s`) and `MobilisTransit_DDM_Draft/MobilisTransit_DDM_Draft.pdf` (§6.2: distinguishes reception-to-display vs. total latency with poll wait) | Resolved via `DEC-003`: Primary target follows `main_changes7.tex` §5.3 (`< 2 s` server-update-to-display during live demos via configurable fast polling or push chosen in `ai-plan`), while logging and reporting both sub-metrics transparently. | `resolved` |
| CONF-004 | **User-triggered experiment interventions: Stop hold only vs. Stop hold + Traffic multiplier** | `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§3.3, §4.1: defines `run_delay_experiment` and analyst scenario runs as supporting *"a specified stop hold or traffic multiplier"* / *"increasing peak traffic"*) | `MobilisTransit_DDM_Draft/main.tex` (§2.1 #4, §5.3 E3) and `Apresentação/apresentação.md` (Slide 10, Part 3C–3D: specifies only stop hold/retention `[0, 900] s` at a stop for E3 and `run_delay_experiment`, varying traffic volume only during offline dataset generation) | Resolved via `DEC-002`: Support both stop hold (`0–900 s`) and optional bounded traffic multiplier (`1.0x` default) across `REQ-006`, `REQ-009`, `REQ-014`, `REQ-015`, and `BR-007` per `main_changes7.tex`. | `resolved` |
| CONF-005 | **Mobilis Line 9 route landmarks (Railway station included vs. explicitly excluded)** | `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§1, §4.1: describes Line 9 as *"linking the city centre, the railway station and the university campus"*) | `Apresentação/apresentação.md` (Part 1 Rule 8 & Slide 2 Notes: identifies Line 9 as *"Leiria–Campus 2 ULO"* per official 1 Sep 2026 schedule and instructs *"Não acrescentes paragens reais ou a estação ferroviária"*), while `MobilisTransit_DDM_Draft/main.tex` (§2) leaves the exact route unconfirmed | Resolved via `DEC-004`: Team's existing SUMO Line 9 network is the canonical stop/route ground truth for `REQ-001`. | `resolved` |
| CONF-006 | **VR component and TEI business model present in early canvas vs. omitted in all later specs** | `tema.MD` (§6 TEI & RVA, §8: lists *"modelo de negócio"* under TEI and *"componente de realidade virtual conforme o âmbito acordado"* / VR equipment decision under RVA) | `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§1.1, Table II), `MobilisTransit_DDM_Draft/main.tex`, and `Apresentação/apresentação.md` (Slide 6, Slide 12, Part 3C): omit both VR and any business model completely | Resolved via `DEC-001`: Both VR (`REQ-021`) and TEI business model (`REQ-022`) confirmed `out-of-scope` per `main_changes7.tex`. | `resolved` |
| CONF-007 | **Schedule, milestone sequencing & missing DDM Section 6** | `MobilisTransit_DDM_Draft/MobilisTransit_DDM_Draft.pdf` (§6, 21 Oct 2026: dated 5-phase schedule 21 Oct–10 Dec 2026 + §6.1 checks + §6.2 evaluation) vs. `MobilisTransit_DDM_Draft/main.tex` (22 Oct 2026: omits Section 6 entirely) | `REPORT_EIT_VAR_IEEE/main_changes7.tex` (§4.5: 4 sequential milestones where M1 is SUMO/TraCI only) vs. `Apresentação/apresentação.md` (Slide 13, Part 3E: relative 8-week schedule T0–T0+8w requiring early SUMO→API→App vertical slice in M1) | Resolved via `DEC-005`: Fixed calendar dates dropped; follow undated `M1`–`M4` progression from `main_changes7.tex` §4.5 while retaining DDM §6.1–§6.2 checks and demo video (`DEL-005`, `DEL-006`, `EVAL-005`). | `resolved` |

## 5. Pending Decisions (High-Impact Only)
| ID | Question / Decision Needed | Affected IDs | Resolution / Confirmed Decision | Status |
| :--- | :--- | :--- | :--- | :--- |
| DEC-001 | **RVA/AR/VR and TEI scope**: Core AR composition, Tabletop 3D mode inclusion, and VR/business model retention. | `REQ-012`, `REQ-013`, `REQ-021`, `REQ-022`, `CONF-001`, `CONF-002`, `CONF-006` | **Confirmed privileging `main_changes7.tex`**: Core AR includes **both** the stop-anchored arrival panel + compact 3D mini-route (`REQ-012`) **and** Tabletop 3D full-route mode (`REQ-013`). VR (`REQ-021`) and TEI business model (`REQ-022`) are dropped (`out-of-scope`). | `resolved` |
| DEC-002 | **User-triggered delay experiment parameters**: Stop hold only vs. stop hold + background traffic multiplier. | `REQ-006`, `REQ-009`, `REQ-014`, `REQ-015`, `BR-007`, `CONF-004` | **Confirmed privileging `main_changes7.tex`**: Support **both** stop hold (`0–900 s` integer) and optional bounded background traffic multiplier (default `1.0x`) across API (`POST /experiments`), mobile E3, admin web forms, and `run_delay_experiment`. | `resolved` |
| DEC-003 | **Synchronization latency target & transport expectation**: `< 2 s` server-update-to-display vs. `< 2 s` API request-to-display with 5 s polling. | `REQ-006`, `REQ-011`, `REQ-016`, `CON-006`, `EVAL-004`, `CONF-003` | **Confirmed privileging `main_changes7.tex` §5.3**: Keep provisional **`< 2 s` display latency from server update to client rendering during live demonstrations** (satisfied in `ai-plan` via configurable fast demo polling `≤ 1 s` or optional SSE/WebSocket push alongside standard `5 s` REST polling), while logging and reporting both request-to-display latency and full state freshness transparently. | `resolved` |
| DEC-004 | **Line 9 route ground truth & digital-twin expectation**: Official Line 9 stop list and offline simulation positioning. | `REQ-001`, `CONF-005` | **Confirmed by user**: Team already has the **Mobilis Line 9 SUMO network** ready (canonical route/stop ground truth for `REQ-001`); offline SUMO simulation + replay (with field timing calibration and simulation-domain caveats per `main_changes7.tex` §2.1/§6) is confirmed as the prototype digital representation. | `resolved` |
| DEC-005 | **Milestone plan & DDM Section 6 retention**: Calendar dates vs. undated milestones and DDM checks/demo video. | `DEL-001`–`DEL-006`, `EVAL-005`, `CONF-007` | **Confirmed by user**: Ignore all fixed calendar dates; follow the undated `M1`–`M4` technical milestones from `main_changes7.tex` §4.5 while retaining DDM §6.1–§6.2 functional/integration checklists, setup documentation, and recorded demonstration video. | `resolved` |

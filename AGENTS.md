# AGENTS.md — MobilisTransit

This repository implements **MobilisTransit**, an integrated four-course-unit Master's prototype (TEI, AAP, DDM, RVA) for **Mobilis Line 9** combining Eclipse SUMO traffic simulation, arrival-time machine learning, a central FastAPI backend + LangGraph/Ollama assistant, and a native Android (Kotlin/Compose/ARCore/SceneView) mobile app.

## Authoritative Planning & Discovery Docs
Read these before modifying code:
1. [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) — Project objectives, deliverables (`DEL-001`–`DEL-007`), evaluation criteria (`EVAL-001`–`EVAL-005`), and scope boundaries.
2. [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) — Confirmed functional requirements (`REQ-001`–`REQ-016`), business rules (`BR-001`–`BR-015`), constraints (`CON-001`–`CON-012`), and resolved decisions (`DEC-001`–`DEC-005`).
3. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — System boundaries, repository layout, REST/SSE/DB/ML/AR/Agent contracts, and security/privacy rules.
4. [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) — Ordered task table (`T001`–`T016`) and current task states.
5. `docs/tasks/Txxx.md` — Self-contained specifications for each task.

## Workflow (`ai-discover` → `ai-plan` → `ai-execute` → `ai-review`)
1. **`ai-discover`** *(complete)*: Extracted requirements and resolved ambiguities into `docs/PROJECT_CONTEXT.md` and `docs/REQUIREMENTS.md`.
2. **`ai-plan`** *(complete)*: Designed architecture (`docs/ARCHITECTURE.md`) and decomposed work into `docs/IMPLEMENTATION_PLAN.md` + `docs/tasks/T001.md`–`T016.md`.
3. **`ai-execute`**: Implement **one `todo` (or `needs-fixes`) task at a time** strictly within its `Allowed files`, following `## Contracts` and `## Implementation steps`, running `## Tests`, updating task state to `ready-for-review`, and filling `## Execution Report`.
4. **`ai-review`**: Audit completed tasks against `docs/ARCHITECTURE.md`, diffs, tests, and acceptance criteria; mark `approved` or `needs-fixes` in `## Review Findings` and `docs/IMPLEMENTATION_PLAN.md`.

## Repository Layout & Key Guardrails
- **Repository Root**: `MobilisTransit/` (`C:\Users\loupa\Desktop\PROJETO_4X\MobilisTransit`, Git repo). Academic proposal/reference sources (`tema.MD`, `MobilisTransit_DDM_Draft/`, `REPORT_EIT_VAR_IEEE/`, `Apresentação/`) reside in the parent folder `../`.
- `sumo/` — Contains the team's canonical **Mobilis Line 9** SUMO network & GTFS assets (`linha9.sumocfg`, `leiria.net.xml.gz`, `pt_vtypes.xml`, `gtfs_pt_stops.add.xml`, `gtfs_pt_vehicles_L9.add.xml`, `manifest.json`, `manifest.schema.json`), the fast 4-stop CI fixture (`sumo/fixtures/sample_line9/`), helper GUI launcher (`sumo/run_gui_with_traffic.bat`), and archived legacy/unused files in `sumo/deprecated/`. **CRITICAL (Windows/Python safeguard)**: NEVER create `sumo/__init__.py` or top-level `.py` modules inside `sumo/` (prevents shadowing PyPI `eclipse-sumo`'s `sumo` package). Place all production server/ML code in `backend/` and `ml/`.
- `.gitignore` & `README.md` — Already tracked by Git from the colleague's initial commits; **extend** `.gitignore` when bootstrapping `T001` rather than overwriting existing rules.
- `backend/` — Python 3.13 FastAPI server, SQLAlchemy 2.0 historical DB (SQLite WAL default, Postgres-ready), SUMO/TraCI worker, SourceAdapter, Admin Web UI (`/admin`), and 3-tool LangGraph + Ollama agent.
- `ml/` — Multi-scenario dataset generator + Colab export bundle, strictly causal pure-Python `ml/features.py`, leak-free family splitter (`BR-012`), Google Colab training notebook (`ml/notebooks/MobilisTransit_AAP_Training_Colab.ipynb`) + local CLI parity (Baseline, Ridge, XGBoost), evaluation, and portable cross-OS JSON `ml/artifacts/` (zero `pickle`/`joblib`).
- `android/` — Native Kotlin/Compose Android app (`compileSdk=36`, `minSdk=26`, JDK 17), Room (8 entities), REST/SSE sync, offline demo player, E1–E6 2D views, E5 Stop-Anchored AR + 3D mini-route, and Tabletop 3D AR via ARCore (`AR Optional`) + SceneView/Filament.
- `evaluation/` — Latency/freshness analysis, 5×5 agent audit harness, 2D vs. AR usability study kit, E2E integration tests, and demo runbook.

### Non-Negotiable Rules
- Keep out-of-scope items (`REQ-017`–`REQ-022`: Kalman filter, live Mobilis GPS ingestion, mobile notifications/export/geolocation/multi-route, >3 agent tools, VR, business model) out of code.
- **Privacy (`CON-008`)**: No Android location permissions, no server user-location storage, no camera frame storage/upload.
- **Ground Truth (`BR-001`, `BR-002`, `BR-006`, `BR-013`)**: Never fabricate missing predictions; never show delay without a reference timetable; distinguish predicted delay from observed simulated experiment differences; restrict the agent strictly to its 3 validated tools.
- **Windows commands**: Run Python via `.\.venv\Scripts\python.exe -m pytest ...` (created in `T001`) and Android via `cd android; .\gradlew.bat testDebugUnitTest assembleDebug` (created in `T008`).

# Progrez

A working learning platform with **Data Analysis Foundations** and **Introduction to Python**, with independent progress and support for every course.

**Choose a course → Onboard → Diagnose → Learn → Detect friction → Confirm → Adapt → Recover → Prove progress**

The application uses Django 5.2, Django REST Framework, Django templates, a small browser JavaScript client, and SQLite locally. PostgreSQL is configurable. All learner progress is stored on the server. No npm install or frontend build is needed.

## Run locally

Python 3.12 is recommended. This implementation also supports Python 3.10 and was verified with the Python installation available in this workspace.

```powershell
cd progrez
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py backfill_circle
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Open **http://127.0.0.1:8000/**. Sign in, choose a demo learner, or create a local account; then select a course from the library.

If dependencies are already installed, run `start.ps1` or use the same commands with `python` directly. The seed command adds missing demo accounts and updates curated content; it does **not** reset existing learner progress.

On macOS/Linux, use `.venv/bin/python` instead of `.venv\Scripts\python.exe`.

## What is included

- Registration, session login/logout, password validation, CSRF protection, and user-scoped API operations.
- Goal contract with prior experience, constraints, success criteria, optional date, time budget, confidence, and connectivity.
- A course library and administrator course authoring, with independent journeys for each course.
- A course-specific six-question diagnostic with knowledge and assimilation scores and a recommended session length.
- Six sequential milestones, twelve curated text lessons, twelve scored checkpoints, and six competencies **per bundled course** (24 lessons total).
- Server-side answer keys, repeatable assessments, ordered unlocking, hints, pause/resume, and competency evidence.
- Standardized learning signals, weighted momentum, immutable snapshots, and explainable friction candidates.
- Learner confirmation or correction before intervention creation.
- Twenty seeded intervention choices across ten friction categories.
- Three-step recovery plans, captured baselines, real reassessment, before/after comparison, and recovery scoring.
- Failed-intervention history: a repeat check-in selects an untried strategy for the confirmed barrier.
- Four demo personas, explicit simulation controls, demo-only resets, and cohort analytics.
- Responsive layout, keyboard-friendly forms, visible focus states, semantic labels, and text-only content.
- Downloadable recovery plans for offline reading. Submission still requires a connection.
- Optional schema-validated AI gateway with an eight-second timeout and curated fallback.
- Django administration at `/admin/`.

## Courses

Choose **Data Analysis Foundations** to continue the original course, or **Introduction to Python** to start a separate journey. Both have goals, diagnostics, momentum, Comeback, proof of progress, and Circle. Use **Switch course** at any time. Administrators can add and publish more courses without frontend changes. See [COURSES.md](COURSES.md) for the curriculum, authoring workflow, upgrade behavior, and API context.

## Progrez Circle

Within the selected course, open **My Circle** to choose Private, My Circle, or Public sharing; select profile fields; copy invitations; and read encouragement. Supporters can follow milestone updates without a learner account. Profiles are private by default, and sensitive learning details are excluded. See [CIRCLE.md](CIRCLE.md) for setup, privacy behavior, optional email delivery, and limits.

## Demo

See [DEMO.md](DEMO.md) for a short presentation script.

| Learner | Intended barrier | Evidence |
| --- | --- | --- |
| Tunde | Prerequisite gap | Repeated errors, weak diagnostic foundation, longer sessions |
| Amina | Limited time | Short sessions and strong assessment performance |
| Mariam | Connectivity | Interrupted sessions and connection failures |
| David | Relevance | Strong performance and explicit relevance feedback |

Demo accounts have unusable passwords. The dedicated demo sign-in works only when `DEBUG=true` and `DEMO_MODE=true`. They are shared sample records on this local installation, not private accounts. Real accounts cannot use simulation/reset controls or inspect demo analytics.

`python manage.py seed_demo --reset` restores the **Data Analysis journeys of all four demo accounts**. It does not affect real accounts or other courses. The in-app reset affects only the selected demo learner and course.

## Verification

```powershell
python manage.py check
python manage.py test tests
python manage.py makemigrations --check --dry-run
```

61 tests cover course selection, cross-course isolation, full Python completion, course authoring, Circle privacy, invitations, notifications, encouragement limits, consent, email eligibility, and the core score boundaries, friction distinctions, inactivity, new-learner protection, authentication and CSRF, data ownership, registration and diagnostics, lesson unlocking, duplicate completion, learner correction, recovery success/failure, strategy rotation, text downloads, and AI fallback/validation. Tests use an isolated test database and do not modify local learner records.

## Design and implementation choices

### Momentum

All components are clamped to 0–100:

```text
0.25 × consistency
+ 0.25 × completion
+ 0.20 × assessment
+ 0.15 × engagement
+ 0.15 × confidence
```

- Consistency: active days out of five, reduced by absence of up to seven days.
- Completion: missions and recorded recovery practice steps out of five weekly learning actions.
- Assessment: recent score average plus half the change from the preceding window.
- Engagement: session length relative to the learner’s plan, reduced by abandonment.
- Confidence: the learner’s own latest confidence report.

States follow the blueprint: 80+ progressing, 60+ stable, 40+ slowing, 20+ at risk, below 20 disengaged. An active recovery plan overrides the displayed state to recovering. A learner with no learning history starts at a neutral 50; a long-absent learner does not reset to that baseline.

Returning to the app refreshes a score older than thirty minutes. To refresh while learners are absent, run `python manage.py refresh_momentum` from your deployment scheduler, for example hourly. No external scheduler is installed automatically.

### Friction

Weighted rules produce evidence-bearing candidates. Scores are **heuristic rule scores, not statistical probabilities**. A specific prerequisite gap wins a tie with generic difficulty when supported by repeated concept errors and the diagnostic. The learner’s confirmed/corrected barrier determines the intervention.

New learners are not flagged just because they lack a full week of activity. Detection requires enough observation, repeated failed attempts, connection failures, explicit feedback, or an explicit demo simulation.

Demo simulations append a `SIMULATION_BOUNDARY` and synthetic signals. The aggregator isolates the latest simulated scenario for demo accounts while retaining older signal and snapshot records. Simulations never change real learners.

### Recovery

A baseline is captured once when the learner starts the plan. Reading and practice steps are idempotent. The final step requires the target mission’s actual assessment answers.

```text
0.30 × resumption
+ 0.30 × normalized competency improvement
+ 0.20 × plan completion
+ 0.10 × normalized confidence improvement
+ 0.10 × normalized momentum restoration
```

Normalization is defined in `apps/engines/recovery.py`. If the assessment fails, recovery is capped below 40, even if all steps were attempted. Outcomes are strong recovery (80+), recovering (60+), partial recovery (40+), and intervention ineffective (below 40). Both partial and ineffective outcomes are excluded from future selection of the same strategy for that learner.

The exact before/after numbers depend on recorded activity and answers; the app does not force the blueprint’s illustrative 41→78 or 34→67 numbers. Checkpoints currently contain two questions, so scores are 0, 50, or 100. A milestone competency is demonstrated only after both of its mission checkpoints pass.

### Structure

```text
config/                        Settings, URL routing, WSGI
apps/core/models.py            Relational domain models
apps/core/api.py               Authenticated API and learning operations
apps/core/content.py           Curated lesson material and answer keys
apps/core/seed.py               Content, demo personas, and simulations
apps/core/migrations/          Committed database migrations
apps/core/management/commands/ Seed and momentum-refresh commands
apps/engines/                  Momentum, friction, intervention, recovery logic
apps/ai/services.py            Gateway client and strict output schemas
templates/index.html          Django page shell and CSRF initialization
static/                       Responsive CSS, browser client, favicon
tests/                        Engine and API regression tests
```

For the MVP, relational models share one Django app rather than ten nearly empty apps. Intelligence engines remain separate from HTTP views. Curriculum milestones/missions are shared immutable definitions; progress and competencies are learner-specific. This avoids duplicating the curriculum for each account.

## API

Base: `/api/v1/`. Browser writes use same-origin session cookies and an `X-CSRFToken` header from the initial page’s CSRF cookie. All learner operations scope records to the authenticated user.

| Area | Endpoints |
| --- | --- |
| Authentication | `POST auth/register`, `POST auth/login`, `POST auth/logout`, `GET auth/me` |
| Workspace | `GET state`, `POST profile` |
| Courses | `GET courses`, `POST courses/select` |
| Goals | `POST goals`, `GET goals/current`, `PATCH goals/{id}` |
| Diagnostic | `POST diagnostics/start`, `POST diagnostics/{id}/answer`, `POST diagnostics/{id}/complete`, `GET diagnostics/latest` |
| Path | `GET paths/current`, `GET paths/{id}/milestones` |
| Missions | `GET missions/today`, `GET missions/{id}`, `POST missions/{id}/start`, `POST missions/{id}/complete`, `POST missions/{id}/abandon`, `POST missions/{id}/hint`, `POST missions/{id}/adapt` |
| Assessment | `GET assessments/{id}`, `POST assessments/{id}/submit`, `GET assessments/{id}/results` |
| Momentum | `GET momentum/current`, `GET momentum/history` |
| Comeback | `GET comeback/status`, `POST comeback/check-in`, `POST comeback/confirm-friction`, `GET comeback/plan`, `POST comeback/plan/start`, `POST comeback/steps/{id}/complete`, `GET comeback/result`, `GET comeback/download` |
| Competencies | `GET competencies`, `GET competencies/progress` |
| Demo | `POST demo/login`, `POST demo/simulate`, `POST demo/reset`, `GET analytics` |

`state` aggregates the signed-in learner’s dashboard. Some grouped read endpoints return the same complete path or momentum envelope rather than separate serializers. `PATCH goals/{id}` currently validates a complete goal form. Final recovery submission uses `assessments/{id}/submit` with `recovery_plan_id` and a confidence report, not a free-standing completion flag.

## Optional AI gateway

The app is fully usable without an AI provider. No live AI calls were made during validation, and no AI credentials are bundled. The configured endpoint is a **structured JSON gateway**, not a raw chat-completions URL.

Export `AI_SERVICE_URL` and `AI_SERVICE_KEY` in the server process environment. The gateway must use HTTPS, accept the following POST contract, call your selected model, and return a JSON object directly matching `response_schema`:

```json
{
  "task": "personalize_plan",
  "prompt_version": "progrez-v1",
  "instruction": "...",
  "context": {"intervention": "PREREQUISITE_BRIDGE", "source_material": "..."},
  "response_schema": {"type": "object", "properties": {"...": "..."}}
}
```

The key is sent as an Authorization bearer token. Supported tasks:

- `diagnostic`: recommended session length and notes, bounded by the learner’s time budget.
- `friction_interpretation`: a confirmation question; accepted only for the deterministic engine’s selected friction. It cannot override the rule score or change state.
- `personalize_plan`: title, rationale, and encouragement. Curriculum content, durations, strategy, and scoring remain application-controlled.
- `adapt_explanation`: a source-bound explanation, exposed through the API. The source reference must match the supplied lesson.

Responses are Pydantic-validated with unknown fields rejected, length/range limits, taxonomy checks, an eight-second timeout, and a 32 KB limit. Failure uses curated content. Model output never writes database state directly. The UI identifies whether plan copy came from the curated template or the optional AI service.

## Configuration and deployment

`.env.example` documents the environment variables; it is a reference file, **not automatically loaded**. In PowerShell, set values using `$env:NAME = 'value'` before starting Django.

For PostgreSQL, set `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, and `POSTGRES_PORT`, then run migrations. The PostgreSQL configuration is included but local validation used SQLite.

For a hosted deployment:

1. Install the pinned-range dependencies in a Python environment.
2. Set `DEBUG=false`, a unique strong `SECRET_KEY`, and explicit `ALLOWED_HOSTS`.
3. Configure PostgreSQL, migrate, and seed content. Demo routes are disabled with `DEBUG=false`.
4. Run `python manage.py collectstatic --noinput` and serve static assets through the host/reverse proxy.
5. Serve `config.wsgi:application` using your host’s WSGI server behind HTTPS. Django’s local development server is not the production entrypoint.
6. Create an administrator with `python manage.py createsuperuser`.
7. Optionally schedule `refresh_momentum` and connect the AI gateway.

No public deployment, email delivery, password-reset email provider, paid services, or external scheduler is configured by this deliverable. Keep the existing local database if you want to retain learner records.

## MVP limits

- Python lessons provide code reasoning and scored checkpoints; no arbitrary code execution or independent project grading is included.

- English curriculum and interface. Django locale middleware and language fields are present; translation catalogs and translated curriculum are not included.
- Text download supports offline reading; offline completion synchronization and a service-worker PWA are not implemented.
- Assessment scoring is deterministic and intentionally small, not a validated psychometric diagnostic or automatic project grader.
- Recovery is evaluated immediately after the final checkpoint for the hackathon. The stored seven-day expiry is advisory; it does not automatically cancel a learner’s plan.
- No custom ML model or vector search. No payment, marketplace, social network, or certificate system.
- Optional model integration is an adapter contract; a live gateway/provider must be supplied to enable model-generated copy.

The source archive excludes the local learner database, sessions, passwords, environment files, logs, and runtime caches.

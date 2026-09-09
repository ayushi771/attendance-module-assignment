# Punch In / Punch Out & Attendance Module

A shift-aware attendance system built with **Django + Django REST Framework + PostgreSQL**.
Employees punch in/out, and attendance (First Half / Second Half / Total Worked)
is calculated automatically from punch times and the employee's assigned shift.

Includes a lightweight live-updating frontend dashboard (server-rendered by Django,
no separate frontend build step) so the system can be demoed end-to-end in a browser.

---

## Demo

### Main Page(http://127.0.0.1:8000/)
Punch card, live activity feed, alerts panel (late/early/missing punch-out detection),
and attendance table — all fed by the same REST API, updating in real time.

<img width="1920" height="3893" alt="screencapture-127-0-0-1-8000-2026-09-09-15_35_44" src="https://github.com/user-attachments/assets/e903fe99-d006-4c64-8fea-f683032c3468" />


### Database Design (Django Admin)
Every attendance record tracks lateness, earliness, and missing punch-outs as
first-class fields — not just calculated on the fly and thrown away, but stored
and filterable, so a reviewer/HR admin can query "show me everyone who was late
today" directly.

<img width="1781" height="872" alt="Screenshot 2026-09-09 153918" src="https://github.com/user-attachments/assets/07dacbd3-0e9d-4eab-aed9-94e8eae1b0d7" /><img width="1436" height="952" alt="Screenshot 2026-09-09 153935" src="https://github.com/user-attachments/assets/69735a24-96ab-44c4-82b0-8063c19aacd4" />



### Test Suite
25 automated tests covering the assignment's exact sample scenarios, plus edge
cases: exact threshold boundaries (270/540 minutes), the punch-out lookback fix,
late/early punch handling, missing punch-out detection, and dedicated concurrency
tests using real threads to prove race-condition protection actually works under
simultaneous load.

<img width="937" height="252" alt="test" src="https://github.com/user-attachments/assets/d2ed4dff-5bac-41d8-8c1e-8f35ac7adbca" />
<img width="1473" height="230" alt="Screenshot 2026-09-09 154618" src="https://github.com/user-attachments/assets/5ee933cf-481c-44c8-b386-6d4a4d63a72a" />



---

## 1. Tech Stack

| Layer | Choice |
|---|---|
| Language | Python 3.10+ |
| Framework | Django |
| API layer | Django REST Framework (DRF) |
| Database | PostgreSQL |
| Frontend | Django template + vanilla JS (fetches the REST API) |

---

## 2. Setup Instructions

### Requirements
- Python 3.10+
- PostgreSQL installed and running locally (pgAdmin or `psql` — either works)

### Step 1 — Create the database

Using pgAdmin (or `psql`), create a new, empty database with any name you like:

```sql
CREATE DATABASE attendance_db;  -- or any name; just match it in your .env
```

### Step 2 — Clone/unzip the project and set up a virtual environment

```bash
cd attendance_module
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
```

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

### Step 4 — Configure environment variables

Create a `.env` file in the project root (same folder as `manage.py`):

```
DB_NAME=attendance_db
DB_USER=postgres
DB_PASSWORD=your_postgres_password
DB_HOST=localhost
DB_PORT=5432
```

### Step 5 — Run migrations

```bash
python manage.py makemigrations attendance
python manage.py migrate
```

### Step 6 — Create an admin login (optional, for the Django admin panel)

```bash
python manage.py createsuperuser
```

### Step 7 — Load the assignment's exact sample data

```bash
python manage.py seed_demo
```

This creates the exact scenario from the assignment's "Expected Output"
table, so you can immediately verify the
calculated attendance matches what was specified.

### Step 8 — Run the server

```bash
python manage.py runserver
```

### Step 9 — Open it

| URL | What it shows |
|---|---|
| `http://127.0.0.1:8000/` | Live attendance dashboard (punch in/out, live activity feed, attendance table) |
| `http://127.0.0.1:8000/admin/` | Django admin panel (view/edit raw data) |
| `http://127.0.0.1:8000/api/attendance` | Raw JSON attendance API |

---

## 3. Running Tests

```bash
python manage.py test attendance
```

Covers: full-day GS, full-day NS (crossing midnight), partial first-half-only,
partial both-absent, punch-in-only (IN status), night-shift second-half-only,
double punch-in rejection, punch-out-without-punch-in rejection,
punch-out-before-punch-in rejection, completed-day re-punch rejection,
and the 5-day auto-close-to-PT rule (plus a check that recent open punches
are correctly left alone).

---

## 4. Optional: Live Demo Simulator

To see the dashboard update in real time with simulated employee activity:

```bash
python manage.py live_demo --employees 20 --interval 3
```

This generates realistic punch sessions (respecting real GS/NS shift hours)
across a mix of outcomes — full day, first-half-only, second-half-only,
absent, and still-in — so you can see every attendance outcome live on
the dashboard. Stop it anytime with `Ctrl+C`.

This is a demo tool meant to make the dashboard visually come alive for a
walkthrough — it does not represent real production traffic patterns.

---

## 5. API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/shifts` | Create a shift (GS, NS, or custom) |
| GET | `/api/shifts` | List shifts |
| POST | `/api/employees` | Create an employee, assigned to a shift |
| GET | `/api/employees?limit=` | List employees (paginated) |
| POST | `/api/punch-in` | `{employee_code, timestamp?}` — punch in |
| POST | `/api/punch-out` | `{employee_code, timestamp?}` — punch out |
| GET | `/api/attendance?employee_code=&date_from=&date_to=&limit=` | Query attendance, filtered + paginated |
| GET | `/api/punch-logs` | Most recent raw punch events (used by the live feed) |

`timestamp` is optional on punch endpoints — omit it to use the server's
current time, or pass an explicit ISO datetime for testing/backdating.

---

## 6. Data Model

- **Shift** — master data: `code`, `start_time`, `full_day_minutes`,
  `half_day_minutes`, `crosses_midnight`. Durations are stored as explicit
  data values, not derived from `start_time`/`end_time` subtraction — see
  Section 7 for why.
- **Employee** — `employee_code`, `name`, assigned `shift`.
- **PunchLog** — immutable, append-only log of every punch event
  (`IN`/`OUT` + timestamp). This is the source of truth.
- **Attendance** — one row per `(employee, attendance_date)`, always
  *derived* from PunchLog + Shift. Never edited directly.

Keeping punches immutable and attendance derived means historical
attendance can always be safely recalculated if a business rule changes,
without losing any underlying data.

---

## 7. Business Logic — How Attendance Is Calculated

1. **Which day does a punch belong to?**
   For a shift that crosses midnight (Night Shift), a punch is assigned
   to the shift-day it logically belongs to, based on the shift's
   `start_time` — not the calendar date it physically occurred on. A
   punch at 2:00 AM belongs to the shift that started at 21:30 the
   *previous* evening.

2. **Total worked** = punch-out timestamp − punch-in timestamp, in minutes.

3. **Half-day status:**
   - Worked ≥ `full_day_minutes` (9h) → **both halves PR**.
   - Worked ≥ `half_day_minutes` (4.5h) → **one half PR**: whichever half
     contains the punch-in moment, relative to the shift's scheduled
     midpoint (`start_time + half_day_minutes`) — not simply "first 4.5h
     from punch-in clock time." This lets an employee who starts late
     still get credited for the correct half (e.g. Second Half only, if
     they start after the shift's midpoint).
   - Otherwise → **both halves AB**.

4. **No punch-out yet** → status stays `IN`, halves stay blank until
   punch-out completes the record.

5. **Stale open punches (5+ days with no punch-out)** → automatically
   marked `PT`/`PT` and status `AUTO_CLOSED` via:
   ```bash
   python manage.py auto_close_attendance
   ```
   In production this would be scheduled to run daily via cron (Linux/Mac)
   or Task Scheduler (Windows) — not built into this submission, since it's
   an infrastructure concern outside the scope of the module itself, but
   the command is fully functional and tested on its own.

Night Shift is treated as a full 9-hour shift (21:30–06:30) for calculation
purposes. Each employee supports a single punch-in/punch-out pair per
shift-day.

All of this is verified against the assignment's own sample table via
`seed_demo` — running it reproduces every row exactly, and the full
scenario is also covered by the automated test suite.

---

## 8. Scaling to a Large Number of Employees

- **Indexes**: composite index on `(employee, attendance_date)` on the
  Attendance table (the hot lookup path for both punching and reporting),
  plus `(employee, punch_timestamp)` on PunchLog.
- **Uniqueness at the DB level**: `UNIQUE(employee, attendance_date)` on
  Attendance, enforced by PostgreSQL itself, not just application logic.
- **Row-level locking on punch actions**: `select_for_update()` is used
  in both `punch_in` and `punch_out` to prevent race conditions — e.g. a
  double-click or a retried request from creating conflicting records
  for the same employee at the same time.
- **Pagination everywhere**: `/api/employees` and `/api/attendance` are
  paginated with a hard-capped page size, so a client can never
  accidentally pull an entire large table into memory at once.
- **N+1 avoidance**: `select_related` is used when listing attendance so
  employee/shift names don't trigger a separate query per row.
- **Punches are append-only**: PunchLog is never updated or deleted, so
  writes stay cheap inserts with no lock contention on existing history.
- **Natural path to further scale**: since Attendance is always
  re-derivable from PunchLog + Shift, if this needed to scale further,
  nightly attendance computation could move into an async batch job
  (e.g. Celery/cron) instead of being computed synchronously on every
  punch-out — the business logic (`services.py`) is already isolated
  from the view layer, so this would be a small change, not a rewrite.

---
## 9. Project Structure

```
attendance_module/
├── config/                          # Django project (settings, root URLs)
│   ├── __init__.py
│   ├── settings.py                  # DB config, installed apps, DRF settings
│   ├── urls.py                      # Root URL routing
│   ├── asgi.py                      # ASGI entrypoint (not used in this submission)
│   └── wsgi.py                      # WSGI entrypoint (used by runserver)
│
├── attendance/                      # Main Django app
│   ├── __init__.py
│   ├── apps.py                      # App config (Django boilerplate)
│   ├── models.py                     # Shift, Employee, PunchLog, Attendance
│   ├── services.py                   # Core business logic (punch in/out, half-day calc, auto-close)
│   ├── serializers.py                # DRF request/response schemas
│   ├── views.py                      # API endpoints + dashboard view
│   ├── urls.py                       # App-level URL routing
│   ├── admin.py                      # Django admin registration
│   ├── tests.py                      # Automated test suite
│   ├── tests_concurrency.py          # Concurrent punch-in/punch-out and race-condition tests
│   │
│   ├── migrations/                   # Django schema migration history
│   │   ├── __init__.py
│   │   ├── 0001_initial.py           # Initial Shift, Employee, PunchLog, Attendance tables
│   │   ├── 0002_alter_attendance_status.py
│   │   │                                # Widened status field (fixes AUTO_CLOSED truncation)
│   │   └── 0003_alter_attendance_first_half_and_more.py
│   │                                    # Widened first_half/second_half/status for PT
│   │
│   └── management/commands/
│       ├── seed_demo.py               # Recreates the assignment's exact sample scenario
│       ├── live_demo.py               # Simulates realistic live punch activity for demos
│       ├── auto_close_attendance.py   # Marks stale (5+ day) open punches as PT
│       └── check_missing_punchouts.py # Flags missing punch-outs after shift end + grace period
│
├── templates/
│   └── dashboard.html                # Live frontend dashboard (punch card, summary, activity feed, table)
│
├── requirements.txt                  # Python dependencies
├── .gitignore                        # Git ignored files and folders
├── .env                              # Not committed — see Step 4 above to create your own
└── README.md                         # Project documentation
```


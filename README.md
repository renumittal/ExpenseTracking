# Expense Tracker (Phase 1)

Construction project expense tracking system. Django project `expense_tracker`, app `core`, database on Supabase (managed Postgres).

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in real values
```

### Database

`DATABASE_URL` in `.env` is parsed with `dj-database-url`. Use the **pooled / transaction-mode**
Supabase connection string (port 6543) for normal app runtime.

For `makemigrations` / `migrate`, use the **direct** connection string instead (port 5432) —
the transaction pooler doesn't support all session-level features Django's migration DDL
can need:

```bash
DATABASE_URL="$DIRECT_DATABASE_URL" python manage.py migrate
```

If `DATABASE_URL` is left empty, the project falls back to a local SQLite database
(`db.sqlite3`), which is useful for first-time setup before Supabase credentials exist.

### Run

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Admin panel: http://127.0.0.1:8000/admin/

## Data model overview

- **Project** — a construction project, with owners and managers linked via
  `ProjectOwner` / `ProjectManager` (an owner or manager can be on multiple projects).
- **Owner / Manager / Labour / Contractor / Supplier** — the parties involved.
- **ContractorContract** — a contract between a project and a contractor;
  `paid_amount` / `balance` are computed from related `ExpenseTransaction` rows.
- **ExpenseTransaction** — the single source of truth for all money spent on a
  project. Never hard-deleted; cancel via `.cancel()` which sets `status=CANCELLED`.
- **ManagerFund** — money handed to a manager to spend on labour. This is *not*
  itself an expense.
- **ManagerLabourDistribution** — when a manager actually pays a labourer from a
  fund, saving this record automatically creates a matching `ExpenseTransaction`
  (category=LABOUR) so the spend rolls into Labour Expense totals exactly once.
  See the docstring on `ManagerLabourDistribution` in `core/models.py` for why
  `ManagerFund` itself must never be counted as a separate expense.

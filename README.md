# Sales Order

A small full-stack app to create sales orders in Sage Evolution.

- **Backend**: FastAPI + pythonnet against `Pastel.Evolution.dll` for writes; SQLAlchemy/pyodbc for lookup reads.
- **Frontend**: React + Vite + TypeScript.

## Prerequisites

- Windows (pythonnet + Evolution DLL are Windows-only).
- Python 3.10+ (32-bit or 64-bit must match the Evolution DLL — Evolution 11.x is 32-bit).
- Node 18+.
- `ODBC Driver 18 for SQL Server` installed.
- Access to the SQL Server hosting `SageCommon` + the company DB.
- A valid Evolution license.

## Backend setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env    # then edit values if needed
uvicorn main:app --reload --port 8000
```

Health check: <http://localhost:8000/health>. OpenAPI docs: <http://localhost:8000/docs>.

## Frontend setup

```powershell
cd frontend
npm install
npm run dev
```

Vite proxies `/api/*` → `http://localhost:8000` so no CORS drama in dev.

Open <http://localhost:5173>.

## Notes on lookup SQL

`backend/routers/lookups.py` queries these Evolution tables:

- `Client` — customers + addresses
- `StkItem` + `WhseItem` — inventory + qty on hand
- `Warehouses` — warehouses
- `_btblTaxRates` — tax types
- `Project`, `Agent`, `SettlementTermsMaster`, `_etblOrderStatuses` — optional dropdowns

Optional lookups (`projects`, `representatives`, `settlement-terms`, `order-statuses`)
silently return `[]` if the table name doesn't match your Evolution version. If a
dropdown is empty, adjust the SQL in [`backend/routers/lookups.py`](backend/routers/lookups.py).

## What "Place Order" does

Calls `POST /sales-orders` which builds a `SDK.SalesOrder`, adds `OrderDetail`
lines, and calls `.Save()`. The returned `OrderNo` is shown in the success banner.

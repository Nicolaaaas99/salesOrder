from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware

from routers import auth, lookups, sales_orders, debug, history, contact_log, route_overview
from connection import connect

app = FastAPI(title="Sales Order API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api")
api.include_router(auth.router)
api.include_router(lookups.router)
api.include_router(sales_orders.router)
api.include_router(debug.router)
api.include_router(history.router)
api.include_router(contact_log.router)
api.include_router(route_overview.router)
app.include_router(api)


@app.on_event("startup")
def startup_event():
    try:
        connect()
    except Exception as e:
        print(f"WARNING: Evolution SDK failed to connect on startup: {e}")


@app.get("/api/health")
def health():
    return {"status": "ok"}

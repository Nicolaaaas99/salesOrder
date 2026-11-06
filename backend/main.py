import logging
import sys
import time

from fastapi import FastAPI, APIRouter, Request
from fastapi.middleware.cors import CORSMiddleware

import config
from request_context import current_request_path
from routers import auth, lookups, sales_orders, debug, history, contact_log, analytics, targets, admin
from connection import connect

# Log to stdout so IIS httpPlatformHandler writes it to backend\logs\stdout_*.log
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
perf_logger = logging.getLogger("perf")

app = FastAPI(title="Sales Order API")


@app.middleware("http")
async def _timing_middleware(request: Request, call_next):
    """Tag every SQL statement with the API path (for the SLOW SQL log) and
    log any request slower than SLOW_REQUEST_SECONDS."""
    token = current_request_path.set(f"{request.method} {request.url.path}")
    started = time.perf_counter()
    try:
        return await call_next(request)
    finally:
        elapsed = time.perf_counter() - started
        if elapsed >= config.SLOW_REQUEST_SECONDS:
            perf_logger.warning(
                "SLOW REQUEST %.2fs %s %s", elapsed, request.method, request.url.path
            )
        current_request_path.reset(token)

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
api.include_router(analytics.router)
api.include_router(targets.router)
api.include_router(admin.router)
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

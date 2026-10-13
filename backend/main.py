from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import auth, lookups, sales_orders, debug, history
from connection import connect

app = FastAPI(title="Sales Order API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(lookups.router)
app.include_router(sales_orders.router)
app.include_router(debug.router)
app.include_router(history.router)


@app.on_event("startup")
def startup_event():
    try:
        connect()
    except Exception as e:
        print(f"WARNING: Evolution SDK failed to connect on startup: {e}")


@app.get("/health")
def health():
    return {"status": "ok"}

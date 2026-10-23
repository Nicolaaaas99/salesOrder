from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

import config
from auth import get_current_user
from database import get_db
from services.sales_order_service import (
    dump_salesorder_properties,
    dump_orderdetail_properties,
)

router = APIRouter(
    prefix="/debug",
    tags=["debug"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/db-info")
def db_info(db: Session = Depends(get_db)):
    """Diagnostic: what DB is the app actually connected to, and can it see the _vw_SO* views?"""
    row = db.execute(
        text("""
            SELECT DB_NAME() AS current_db,
                   @@SERVERNAME AS server_name,
                   SUSER_NAME() AS login_name
        """)
    ).mappings().first()

    views = db.execute(
        text("SELECT name FROM [SOAssistant].sys.views WHERE name LIKE '_vw_SO%' ORDER BY name")
    ).mappings().all()

    return {
        "company": {
            "env_server": config.SERVER,
            "env_database": config.COMPANY_DATABASE,
            "actual_server_name": row["server_name"],
            "actual_db_name": row["current_db"],
            "login_name": row["login_name"],
            "so_views_found": [v["name"] for v in views],
        },
        "common": {
            "env_server": config.COMMON_SERVER,
            "env_database": config.COMMON_DATABASE,
            "env_username": config.COMMON_USERNAME,
        },
    }


@router.get("/salesorder-properties")
def get_salesorder_properties():
    return {"properties": dump_salesorder_properties()}


@router.get("/orderdetail-properties")
def get_orderdetail_properties():
    return {"properties": dump_orderdetail_properties()}


@router.get("/branch-explore")
def branch_explore():
    """
    Full SDK introspection for branch-context APIs. Lists:
      - every type in the Pastel.Evolution assembly whose name contains 'branch'
      - static + instance members (properties, methods, fields) of each
      - static branch-related members of DatabaseContext
      - static branch-related members of SDK.Branch
      - anything on the SalesOrder instance that mentions 'branch'
    """
    import clr
    from connection import get_sdk

    SDK = get_sdk()
    out: dict = {}

    def dump_type(t) -> dict:
        try:
            static_props = [p.Name for p in t.GetProperties() if p.GetGetMethod() and p.GetGetMethod().IsStatic]
            inst_props   = [p.Name for p in t.GetProperties() if not (p.GetGetMethod() and p.GetGetMethod().IsStatic)]
            static_methods = sorted({m.Name for m in t.GetMethods() if m.IsStatic})
            inst_methods   = sorted({m.Name for m in t.GetMethods() if not m.IsStatic and not m.IsSpecialName})
            static_fields  = [f.Name for f in t.GetFields() if f.IsStatic]
            return {
                "full_name": t.FullName,
                "is_static": t.IsAbstract and t.IsSealed,
                "static_properties": static_props,
                "instance_properties": inst_props,
                "static_methods": static_methods,
                "instance_methods": inst_methods,
                "static_fields": static_fields,
            }
        except Exception as e:
            return {"error": str(e)}

    # 1. Every type in the SDK assembly with 'branch' in name
    try:
        asm = SDK.SalesOrder().GetType().Assembly
        branch_types = {}
        for t in asm.GetTypes():
            name = t.FullName or ""
            if "branch" in name.lower():
                branch_types[name] = dump_type(t)
        out["branch_types"] = branch_types
    except Exception as e:
        out["branch_types_error"] = str(e)

    # 2. DatabaseContext (static class — needs clr.GetClrType)
    try:
        from Pastel.Evolution import DatabaseContext
        db_type = clr.GetClrType(DatabaseContext)
        info = dump_type(db_type)
        info["branch_related"] = {
            "static_props":   [p for p in info["static_properties"]   if "branch" in p.lower()],
            "instance_props": [p for p in info["instance_properties"] if "branch" in p.lower()],
            "static_methods": [m for m in info["static_methods"]       if "branch" in m.lower()],
            "instance_methods":[m for m in info["instance_methods"]    if "branch" in m.lower()],
            "static_fields":  [f for f in info["static_fields"]        if "branch" in f.lower()],
        }
        out["DatabaseContext"] = info
    except Exception as e:
        out["DatabaseContext_error"] = str(e)

    # 3. SDK.Branch class
    try:
        branch_cls = getattr(SDK, "Branch", None)
        if branch_cls is not None:
            branch_type = clr.GetClrType(branch_cls)
            out["SDK.Branch"] = dump_type(branch_type)
    except Exception as e:
        out["SDK.Branch_error"] = str(e)

    # 4. Live SalesOrder — any property/method mentioning branch
    try:
        so = SDK.SalesOrder()
        so_type = so.GetType()
        so_branch_props = [p.Name for p in so_type.GetProperties() if "branch" in p.Name.lower()]
        so_branch_methods = sorted({m.Name for m in so_type.GetMethods() if "branch" in m.Name.lower()})
        out["SalesOrder_branch_members"] = {
            "properties": so_branch_props,
            "methods": so_branch_methods,
        }
    except Exception as e:
        out["SalesOrder_error"] = str(e)

    # 5. Speculative other places
    for candidate in ("Company", "Global", "Configuration", "Login", "Session", "SystemState"):
        cls = getattr(SDK, candidate, None)
        if cls is None:
            continue
        try:
            t = clr.GetClrType(cls)
            info = dump_type(t)
            info["branch_related"] = {
                "static_props":   [p for p in info["static_properties"]   if "branch" in p.lower()],
                "instance_props": [p for p in info["instance_properties"] if "branch" in p.lower()],
                "static_methods": [m for m in info["static_methods"]       if "branch" in m.lower()],
                "instance_methods":[m for m in info["instance_methods"]    if "branch" in m.lower()],
            }
            # Only include if anything branch-related turned up (to keep response readable)
            if any(info["branch_related"].values()):
                out[f"SDK.{candidate}"] = info
        except Exception as e:
            out[f"SDK.{candidate}_error"] = str(e)

    return out


@router.get("/branch-info/{account}")
def branch_info(account: str, db: Session = Depends(get_db)):
    """
    Diagnostic for branch accounting. For a given customer account, dumps:
      - which columns on Client contain 'branch' in the name (and their values)
      - all tables in the DB whose name contains 'branch'
      - all types in the Pastel.Evolution SDK whose name contains 'branch'
      - properties on SalesOrder / DatabaseContext / Customer that mention 'branch'
    """
    # 1. Branch-related columns on Client
    branch_cols = db.execute(
        text("""
            SELECT COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = 'Client' AND LOWER(COLUMN_NAME) LIKE '%branch%'
            ORDER BY COLUMN_NAME
        """)
    ).mappings().all()

    client_branch_values = {}
    if branch_cols:
        cols = ", ".join(f"[{c['COLUMN_NAME']}]" for c in branch_cols)
        row = db.execute(
            text(f"SELECT {cols} FROM Client WHERE Account = :account"),
            {"account": account},
        ).mappings().first()
        client_branch_values = dict(row) if row else {}

    # 2. Branch-related tables
    branch_tables = db.execute(
        text("""
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE LOWER(TABLE_NAME) LIKE '%branch%'
            ORDER BY TABLE_NAME
        """)
    ).mappings().all()

    # 3. SDK types + properties mentioning 'branch'
    sdk_types: list[str] = []
    sdk_branch_props: dict[str, list[str]] = {}
    try:
        from connection import get_sdk
        SDK = get_sdk()
        asm = SDK.SalesOrder().GetType().Assembly
        for t in asm.GetTypes():
            name = t.FullName or ""
            if "branch" in name.lower():
                sdk_types.append(name)

        for cls_name in ("SalesOrder", "OrderBase", "Customer", "DatabaseContext"):
            cls = getattr(SDK, cls_name, None)
            if cls is None:
                continue
            try:
                # Instance properties: instantiate empty
                try:
                    inst = cls()
                    props = [p.Name for p in inst.GetType().GetProperties()
                             if "branch" in p.Name.lower()]
                except Exception:
                    # DatabaseContext etc are static — inspect via .Type
                    props = [p.Name for p in cls.GetType().GetProperties()
                             if "branch" in p.Name.lower()]
                if props:
                    sdk_branch_props[cls_name] = props
            except Exception as e:
                sdk_branch_props[cls_name] = [f"(error: {e})"]
    except Exception as e:
        sdk_types = [f"(SDK introspection failed: {e})"]

    return {
        "customer": account,
        "client_branch_columns": [dict(c) for c in branch_cols],
        "client_branch_values": client_branch_values,
        "branch_related_tables": [t["TABLE_NAME"] for t in branch_tables],
        "sdk_branch_types": sdk_types,
        "sdk_branch_properties": sdk_branch_props,
    }

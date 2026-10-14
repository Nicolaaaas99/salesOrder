from datetime import date as PyDate
from typing import Optional

from sqlalchemy import text

from auth import apply_agent_to_context
from connection import get_sdk
from database import SessionLocal
import schemas


_so_props_dumped = False
_od_props_dumped = False


def _to_dotnet_date(d: PyDate):
    from System import DateTime
    return DateTime(d.year, d.month, d.day)


def _has_dotnet_property(obj, name: str) -> bool:
    try:
        return obj.GetType().GetProperty(name) is not None
    except Exception:
        return False


def _dotnet_property_names(obj):
    try:
        return sorted(p.Name for p in obj.GetType().GetProperties())
    except Exception:
        return []


def _dump_properties(obj, label: str):
    print(f"\n[sales-order] === {label} .NET properties ===")
    try:
        for p in obj.GetType().GetProperties():
            print(f"  {p.Name}: {p.PropertyType.Name}")
    except Exception as e:
        print(f"  (error: {e})")
    print(f"[sales-order] === end {label} properties ===\n")


def _dump_so_once(obj):
    global _so_props_dumped
    if _so_props_dumped: return
    _so_props_dumped = True
    _dump_properties(obj, "SalesOrder")


def _dump_od_once(obj):
    global _od_props_dumped
    if _od_props_dumped: return
    _od_props_dumped = True
    _dump_properties(obj, "OrderDetail")


def _set_first(obj, attrs, value, label: str) -> bool:
    for attr in attrs:
        if _has_dotnet_property(obj, attr):
            try:
                setattr(obj, attr, value)
                readback = getattr(obj, attr, None)
                print(f"[sales-order] {label}: set .{attr}={value!r}, readback={readback!r}")
                return True
            except Exception as e:
                print(f"[sales-order] {label}: .{attr} exists but setter failed: {e}")

    print(f"[sales-order] {label}: none of {attrs} exist on {obj.GetType().Name}. Available:")
    for name in _dotnet_property_names(obj):
        print(f"  - {name}")
    return False


def _try_set(obj, attr: str, value) -> bool:
    if not _has_dotnet_property(obj, attr):
        print(f"[sales-order] .{attr} not on {obj.GetType().Name}, skipped")
        return False
    try:
        setattr(obj, attr, value)
        return True
    except Exception as e:
        print(f"[sales-order] could not set {attr}={value!r}: {e}")
        return False


def _get_customer_branch(account: str) -> tuple[Optional[str], Optional[int]]:
    """Return (branch_code, branch_id) the customer is linked to, or (None, None)."""
    with SessionLocal() as db:
        row = db.execute(
            text(
                """
                SELECT b.cBranchCode AS code, b.idBranch AS id
                FROM dbo.Client c
                LEFT JOIN dbo._etblBranch b ON b.idBranch = c.Client_iBranchID
                WHERE c.Account = :account
                """
            ),
            {"account": account},
        ).mappings().first()
    if not row or not row["code"]:
        return None, None
    return row["code"].strip(), int(row["id"])


def _branch_from_lines(payload: schemas.SalesOrderCreate, SDK) -> Optional[str]:
    """When the customer isn't branch-linked, infer the branch from the first
    line whose warehouse is branch-scoped. The SDK's Warehouse object exposes
    the branch it belongs to, so we can read it without hitting the DB."""
    for line in payload.lines:
        code = (line.warehouse_code or "").strip()
        if not code:
            continue
        try:
            wh = SDK.Warehouse(code)
            br = getattr(wh, "Branch", None)
            br_code = getattr(br, "Code", None) if br is not None else None
            if br_code:
                print(f"[branch] inferred {br_code!r} from warehouse {code!r}")
                return str(br_code).strip()
        except Exception as e:
            print(f"[branch] could not read branch from warehouse {code!r}: {e}")
    return None


def _set_context_branch(branch_code: Optional[str], SDK) -> bool:
    """
    Switch the DatabaseContext's current branch. This is what
    `BranchedRecordBase.ValidateBranchIsLocal` checks against when
    assigning a branched customer to a sales order.
    Returns True on success.
    """
    if not branch_code:
        return False
    try:
        from Pastel.Evolution import DatabaseContext
        branch = SDK.Branch.FindByCode(branch_code)
        if branch is None:
            print(f"[branch] Branch.FindByCode({branch_code!r}) returned None")
            return False
        DatabaseContext.SetBranchContext(branch)
        print(f"[branch] DatabaseContext.SetBranchContext({branch_code!r})")
        return True
    except Exception as e:
        print(f"[branch] SetBranchContext({branch_code!r}) failed: {e}")
        return False


def _reset_context_branch_to_global(SDK) -> None:
    """Reset the DatabaseContext branch to Global so the pooled thread
    doesn't stay stuck on a specific branch for the next request.

    pythonnet mishandles `SDK.Branch.Global` (returns the Type, not an
    instance), so we invoke the static getter directly via reflection.
    """
    import clr
    from Pastel.Evolution import DatabaseContext

    try:
        branch_type = clr.GetClrType(SDK.Branch)
        prop = branch_type.GetProperty("Global")
        if prop is None:
            print("[branch] Global property not found via reflection")
            return
        global_branch = prop.GetValue(None)  # static → no instance
        DatabaseContext.SetBranchContext(global_branch)
        print("[branch] reset context to Global (via reflection)")
    except Exception as e:
        print(f"[branch] Global reset via reflection failed: {e}")


def _try_set_user_field(obj, field: str, value) -> bool:
    """Try setting a user-defined field. Attempts the native type first
    (e.g. float for uf* fields), then falls back to string."""
    for candidate in (value, str(value)):
        try:
            obj.SetUserField(field, candidate)
            print(f"[sales-order] SetUserField({field!r}, {candidate!r}) OK")
            return True
        except Exception:
            continue
    print(f"[sales-order] SetUserField({field!r}) failed for all types")
    return False


def _apply_lines(so, payload: schemas.SalesOrderCreate, SDK):
    """Populate `so.Detail` from payload lines. Assumes `so.Detail` is empty."""
    for idx, line in enumerate(payload.lines, start=1):
        print(f"[sales-order] line {idx}: stock_code={line.stock_code!r} qty={line.quantity} whse={line.warehouse_code!r}")
        od = SDK.OrderDetail()
        _dump_od_once(od)
        try:
            od.TaxMode = so.TaxMode
        except Exception as e:
            print(f"[sales-order] could not sync detail TaxMode: {e}")
        so.Detail.Add(od)
        try:
            od.InventoryItem = SDK.InventoryItem(line.stock_code)
        except Exception as e:
            # Wrap with the culprit stock code so we don't have to guess
            raise RuntimeError(
                f"Cannot load stock item '{line.stock_code}': {e}"
            ) from e

        if line.description:
            od.Description = line.description[:100]

        if line.warehouse_code:
            _try_set_warehouse(od, line.warehouse_code, SDK)

        od.Quantity         = float(line.quantity)
        od.UnitSellingPrice = float(line.unit_price)

        if line.discount_percent:
            _set_first(od,
                ("DiscountPercent", "DiscountPercentage", "LineDiscount",
                 "LineDiscountPercent", "Discount"),
                float(line.discount_percent), "line discount")

        if line.tax_type_id is not None:
            _try_set_tax_type(od, int(line.tax_type_id), SDK)

        if line.project_code:
            _try_set_project(od, line.project_code, SDK)


def _apply_header(so, payload: schemas.SalesOrderCreate, SDK):
    from System import DateTime

    tax_mode = (payload.tax_mode or "Exclusive").strip().lower()
    so.TaxMode = SDK.TaxMode.Inclusive if tax_mode == "inclusive" else SDK.TaxMode.Exclusive

    if payload.description:
        so.Description = payload.description[:40]

    so.OrderDate   = _to_dotnet_date(payload.order_date)   if payload.order_date   else DateTime.Now
    so.DueDate     = _to_dotnet_date(payload.due_date)     if payload.due_date     else so.OrderDate
    so.InvoiceDate = _to_dotnet_date(payload.invoice_date) if payload.invoice_date else so.OrderDate

    if payload.external_order:
        so.ExternalOrderNo = payload.external_order

    if payload.default_project_code:
        _try_set_project(so, payload.default_project_code, SDK)

    if payload.discount_percent:
        _set_first(so,
            ("SettlementDiscountPercent", "SettlementDiscount", "DiscountPercent", "Discount"),
            float(payload.discount_percent), "document discount")


def _clear_detail(so) -> None:
    """Best-effort emptying of `so.Detail`. Different SDK versions expose different APIs."""
    detail = so.Detail
    try:
        detail.Clear()
        return
    except Exception as e:
        print(f"[sales-order] detail.Clear() failed: {e}")

    try:
        count = int(getattr(detail, "Count", 0))
        for _ in range(count):
            try:
                detail.RemoveAt(0)
            except Exception:
                detail.Remove(detail[0])
        return
    except Exception as e:
        print(f"[sales-order] iterative detail removal failed: {e}")


def _load_sales_order_by_no(order_no: str, SDK):
    """Try several SDK constructors/finders to load an existing sales order."""
    errors = []
    for ctor_arg in (order_no, str(order_no)):
        try:
            so = SDK.SalesOrder(ctor_arg)
            return so
        except Exception as e:
            errors.append(f"SalesOrder({ctor_arg!r}): {e}")

    for finder_name in ("FindByOrderNo", "FindByOrderNumber", "Load", "LoadByOrderNo"):
        finder = getattr(SDK.SalesOrder, finder_name, None)
        if finder is None:
            continue
        try:
            so = finder(order_no)
            if so is not None:
                return so
        except Exception as e:
            errors.append(f"{finder_name}({order_no!r}): {e}")

    raise RuntimeError(
        f"Could not load Evolution sales order '{order_no}'. Tried: " + " | ".join(errors)
    )


def create_sales_order(
    payload: schemas.SalesOrderCreate,
    agent_username: str,
) -> schemas.SalesOrderCreateResponse:
    import traceback

    SDK = get_sdk()
    # Stamp the operator who's placing the order into Evolution's audit
    # columns. DatabaseContext.CurrentAgent is thread-local, same as the
    # connection, so this needs re-applying per request.
    apply_agent_to_context(SDK, agent_username)

    branch_code, branch_id = _get_customer_branch(payload.customer_account)
    print(f"[branch] customer {payload.customer_account!r} -> code={branch_code!r} id={branch_id!r}")
    if not branch_code:
        branch_code = _branch_from_lines(payload, SDK)
    branch_switched = _set_context_branch(branch_code, SDK)

    try:
        so = SDK.SalesOrder()
        _dump_so_once(so)

        try:
            so.Customer = SDK.Customer(payload.customer_account)
            print(f"[sales-order] so.Customer set to {payload.customer_account!r}")
        except Exception as e:
            print(f"[sales-order] so.Customer set FAILED: {e}")
            raise

        try:
            _apply_header(so, payload, SDK)
        except Exception as e:
            print(f"[sales-order] _apply_header FAILED: {e}\n{traceback.format_exc()}")
            raise

        try:
            _apply_lines(so, payload, SDK)
        except Exception as e:
            print(f"[sales-order] _apply_lines FAILED: {e}\n{traceback.format_exc()}")
            raise

        try:
            so.Save()
            print("[sales-order] so.Save() OK")
        except Exception as e:
            print(f"[sales-order] so.Save() FAILED: {e}\n{traceback.format_exc()}")
            raise

        order_no = getattr(so, "OrderNo", None) or getattr(so, "OrderNumber", None)
        if not order_no:
            raise RuntimeError("Sales order saved but no OrderNo returned from Evolution.")

        invoice_no = getattr(so, "InvoiceNo", None) or getattr(so, "InvoiceNumber", None)

        return schemas.SalesOrderCreateResponse(
            order_no=str(order_no),
            invoice_no=str(invoice_no) if invoice_no else None,
        )
    finally:
        # Always release the branch context so the pooled worker thread
        # doesn't stay stuck on '01.HOF' for the next request.
        if branch_switched:
            _reset_context_branch_to_global(SDK)


def update_sales_order(
    order_no: str,
    payload: schemas.SalesOrderCreate,
    agent_username: str,
) -> schemas.SalesOrderCreateResponse:
    SDK = get_sdk()
    apply_agent_to_context(SDK, agent_username)

    branch_code, branch_id = _get_customer_branch(payload.customer_account)
    print(f"[branch] customer {payload.customer_account!r} -> code={branch_code!r} id={branch_id!r}")
    if not branch_code:
        branch_code = _branch_from_lines(payload, SDK)
    branch_switched = _set_context_branch(branch_code, SDK)

    try:
        so = _load_sales_order_by_no(order_no, SDK)
        _dump_so_once(so)

        try:
            so.Customer = SDK.Customer(payload.customer_account)
        except Exception as e:
            print(f"[sales-order] could not change Customer on update: {e}")

        _apply_header(so, payload, SDK)
        _clear_detail(so)
        _apply_lines(so, payload, SDK)

        so.Save()

        saved_no = getattr(so, "OrderNo", None) or getattr(so, "OrderNumber", None) or order_no
        invoice_no = getattr(so, "InvoiceNo", None) or getattr(so, "InvoiceNumber", None)

        return schemas.SalesOrderCreateResponse(
            order_no=str(saved_no),
            invoice_no=str(invoice_no) if invoice_no else None,
        )
    finally:
        if branch_switched:
            _reset_context_branch_to_global(SDK)


def _try_set_project(obj, code: str, SDK):
    try:
        obj.Project = SDK.Project(code)
    except Exception as e:
        print(f"[sales-order] could not set Project={code!r}: {e}")


def _try_set_warehouse(od, code: str, SDK):
    try:
        od.Warehouse = SDK.Warehouse(code)
    except Exception as e:
        print(f"[sales-order] could not set Warehouse={code!r}: {e}")


def _try_set_tax_type(od, tax_id: int, SDK):
    # OrderDetail.TaxType property is of .NET type `TaxRate` (not `TaxType`,
    # which is an enum in the SDK). Construct via TaxRate; fall back to TaxType
    # for SDK builds where the class name differs.
    for ctor_name in ("TaxRate", "TaxType"):
        ctor = getattr(SDK, ctor_name, None)
        if ctor is None:
            continue
        try:
            od.TaxType = ctor(tax_id)
            return
        except Exception as e:
            print(f"[sales-order] SDK.{ctor_name}({tax_id!r}) failed: {e}")
    print(f"[sales-order] could not set TaxType={tax_id!r}")


def dump_salesorder_properties() -> list:
    SDK = get_sdk()
    so = SDK.SalesOrder()
    try:
        return [{"name": p.Name, "type": p.PropertyType.Name}
                for p in so.GetType().GetProperties()]
    except Exception as e:
        return [{"error": str(e)}]


def dump_orderdetail_properties() -> list:
    SDK = get_sdk()
    od = SDK.OrderDetail()
    try:
        return [{"name": p.Name, "type": p.PropertyType.Name}
                for p in od.GetType().GetProperties()]
    except Exception as e:
        return [{"error": str(e)}]

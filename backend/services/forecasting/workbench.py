"""Target Workbench data (Phase 5): plan vs actual for one week (Mon-Sun),
rolled up Company -> DC -> Route -> Customer -> Category -> SKU. A category
filter can also be applied at any level ("Milk across Makhado").

A "planned leaf" is any (customer, SKU) with an active forecast or a
materialised target in the week. For each leaf:

    forecast   active forecast (latest run), 0 if only a target exists
    target     effective target (fTargetQty, override included) if a target
               row exists, otherwise the forecast (no management ask)
    delivered  invoiced in the week (PostST, credit notes subtract)
    on_order   open sales-order qty with delivery in the week
    committed  delivered + on_order

Delivery to unplanned leaves isn't counted -- the workbench measures the
plan. Values are in units, or litres (x StkItem.ulIISize) on request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

LEVELS = ("dist_centre", "route", "customer", "category", "sku")


@dataclass
class WorkbenchLeaf:
    customer: str
    sku: str
    dist_centre: Optional[str]
    route: Optional[str]
    category: Optional[str]
    forecast: float = 0.0
    target_id: Optional[int] = None
    target: Optional[float] = None          # effective target if a row exists
    override: Optional[float] = None
    source: Optional[str] = None            # SCENARIO | OVERRIDE | FORECAST
    is_flagged: bool = False
    is_locked: bool = False
    delivered: float = 0.0
    on_order: float = 0.0

    @property
    def plan_target(self) -> float:
        return self.target if self.target is not None else self.forecast

    def dim(self, level: str) -> str:
        return {"dist_centre": self.dist_centre, "route": self.route,
                "customer": self.customer, "category": self.category,
                "sku": self.sku}[level] or ""


@dataclass
class WorkbenchRow:
    key: str
    label: str = ""
    forecast: float = 0.0
    target: float = 0.0
    delivered: float = 0.0
    on_order: float = 0.0
    leaf_count: int = 0
    targeted_count: int = 0
    flagged_count: int = 0
    override_count: int = 0
    # SKU level only -- the single leaf behind the row
    target_id: Optional[int] = None
    override: Optional[float] = None
    source: Optional[str] = None
    is_locked: bool = False

    @property
    def committed(self) -> float:
        return self.delivered + self.on_order

    def add(self, lf: WorkbenchLeaf, factor: float = 1.0) -> None:
        self.forecast += lf.forecast * factor
        self.target += lf.plan_target * factor
        self.delivered += lf.delivered * factor
        self.on_order += lf.on_order * factor
        self.leaf_count += 1
        self.targeted_count += 1 if lf.target_id is not None and lf.source != "FORECAST" else 0
        self.flagged_count += 1 if lf.is_flagged else 0
        self.override_count += 1 if lf.override is not None else 0


def _norm(v: Optional[str]) -> str:
    return (v or "").strip().upper()


def rollup(
    leaves: list[WorkbenchLeaf],
    level: str,
    filters: dict[str, str],
    factors: Optional[dict[str, float]] = None,
) -> tuple[WorkbenchRow, list[WorkbenchRow]]:
    """Filter leaves by the drill path and group by `level`. Pure.

    `filters` = {level: value} for the levels above `level`.
    `factors` = {sku: multiplier} (litre conversion); missing -> 1.
    Returns (totals, rows sorted by target desc).
    """
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    factors = factors or {}
    total = WorkbenchRow(key="__total__", label="Total")
    groups: dict[str, WorkbenchRow] = {}
    for lf in leaves:
        if any(_norm(lf.dim(k)) != _norm(v) for k, v in filters.items()):
            continue
        f = factors.get(lf.sku, 1.0)
        total.add(lf, f)
        key = lf.dim(level)
        row = groups.setdefault(key, WorkbenchRow(key=key, label=key))
        row.add(lf, f)
        if level == "sku":
            row.target_id = lf.target_id
            row.override = lf.override
            row.source = lf.source
            row.is_locked = lf.is_locked
    rows = sorted(groups.values(), key=lambda r: (-r.target, r.key))
    return total, rows


# --- DB --------------------------------------------------------------------------

def load_leaves(db: Session, week: date) -> list[WorkbenchLeaf]:
    """All planned leaves for `week` with delivered / on-order attached."""
    rows = db.execute(text("""
        WITH fc AS (
            SELECT cCustomerAccount, cStockCode, cRoute, cDistCentre, cCategory, fPredictedQty
              FROM (SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY cCustomerAccount, cStockCode
                        ORDER BY idForecastRun DESC, idForecast DESC) AS rn
                      FROM [SOAssistant].dbo._tb_SOForecast
                     WHERE bIsActive = 1 AND dWeekStart = :w) x
             WHERE rn = 1
        ), tg AS (
            SELECT idScenarioTarget, cCustomerAccount, cStockCode, cRoute, cDistCentre, cCategory,
                   fTargetQty, fOverrideQty, cSource, bIsFlagged, bIsLocked
              FROM [SOAssistant].dbo._tb_SOScenarioTarget
             WHERE dWeekStart = :w
        )
        SELECT COALESCE(fc.cCustomerAccount, tg.cCustomerAccount) AS cust,
               COALESCE(fc.cStockCode, tg.cStockCode)             AS sku,
               COALESCE(fc.cDistCentre, tg.cDistCentre)           AS dc,
               COALESCE(fc.cRoute, tg.cRoute)                     AS route,
               COALESCE(fc.cCategory, tg.cCategory)               AS cat,
               fc.fPredictedQty, tg.idScenarioTarget, tg.fTargetQty, tg.fOverrideQty,
               tg.cSource, tg.bIsFlagged, tg.bIsLocked
          FROM fc FULL OUTER JOIN tg
            ON tg.cCustomerAccount = fc.cCustomerAccount AND tg.cStockCode = fc.cStockCode
    """), {"w": week}).mappings().all()

    leaves: dict[tuple[str, str], WorkbenchLeaf] = {}
    for r in rows:
        lf = WorkbenchLeaf(
            customer=r["cust"], sku=r["sku"], dist_centre=r["dc"], route=r["route"],
            category=r["cat"],
            forecast=float(r["fPredictedQty"] or 0),
            target_id=int(r["idScenarioTarget"]) if r["idScenarioTarget"] is not None else None,
            target=float(r["fTargetQty"]) if r["fTargetQty"] is not None else None,
            override=float(r["fOverrideQty"]) if r["fOverrideQty"] is not None else None,
            source=r["cSource"], is_flagged=bool(r["bIsFlagged"]), is_locked=bool(r["bIsLocked"]),
        )
        leaves[(_norm(lf.customer), _norm(lf.sku))] = lf
    if not leaves:
        return []

    params = {"ws": week, "we": week + timedelta(days=7)}
    for r in db.execute(text("""
        SELECT CL.Account AS cust, STK.Code AS sku,
               SUM(CASE WHEN ST.Credit > 0 THEN ST.Quantity ELSE -ST.Quantity END) AS qty
          FROM [BA_00_LPD_CON].dbo.PostST ST
          JOIN [BA_00_LPD_CON].dbo.Client  CL  ON CL.DCLink     = ST.DrCrAccount
          JOIN [BA_00_LPD_CON].dbo.StkItem STK ON STK.StockLink = ST.AccountLink
         WHERE ST.Id IN ('Crn', 'OInv', 'Inv') AND ST.TxDate >= :ws AND ST.TxDate < :we
         GROUP BY CL.Account, STK.Code
    """), params).mappings():
        lf = leaves.get((_norm(r["cust"]), _norm(r["sku"])))
        if lf:
            lf.delivered = max(0.0, float(r["qty"] or 0))

    for r in db.execute(text("""
        SELECT CL.Account AS cust, STK.Code AS sku,
               SUM(LIN.fQuantity - LIN.fQtyProcessed) AS qty
          FROM [BA_00_LPD_CON].dbo._btblInvoiceLines LIN
          JOIN [BA_00_LPD_CON].dbo.InvNum  NUM ON NUM.AutoIndex = LIN.iInvoiceID
          JOIN [BA_00_LPD_CON].dbo.Client  CL  ON CL.DCLink     = NUM.AccountID
          JOIN [BA_00_LPD_CON].dbo.StkItem STK ON STK.StockLink = LIN.iStockCodeID
         WHERE NUM.DocType = 4 AND NUM.DocState = 1
           AND COALESCE(NUM.DeliveryDate, NUM.OrderDate) >= :ws
           AND COALESCE(NUM.DeliveryDate, NUM.OrderDate) <  :we
         GROUP BY CL.Account, STK.Code
    """), params).mappings():
        lf = leaves.get((_norm(r["cust"]), _norm(r["sku"])))
        if lf:
            lf.on_order = max(0.0, float(r["qty"] or 0))
    return list(leaves.values())


def load_labels(db: Session, level: str, keys: list[str]) -> dict[str, str]:
    """Human labels for customer / SKU rows."""
    if not keys or level not in ("customer", "sku"):
        return {}
    sql = ("SELECT Account AS k, Name AS v FROM [BA_00_LPD_CON].dbo.Client WHERE Account IN :keys"
           if level == "customer" else
           "SELECT Code AS k, Description_1 AS v FROM [BA_00_LPD_CON].dbo.StkItem WHERE Code IN :keys")
    out: dict[str, str] = {}
    for i in range(0, len(keys), 900):
        for r in db.execute(text(sql).bindparams(bindparam("keys", expanding=True)),
                            {"keys": keys[i:i + 900]}).mappings():
            out[_norm(r["k"])] = (r["v"] or "").strip()
    return out


def load_litre_factors(db: Session, skus: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in range(0, len(skus), 900):
        for r in db.execute(text("""
            SELECT Code, ISNULL(ulIISize, 0) AS size FROM [BA_00_LPD_CON].dbo.StkItem WHERE Code IN :c
        """).bindparams(bindparam("c", expanding=True)), {"c": skus[i:i + 900]}).mappings():
            out[r["Code"]] = float(r["size"] or 0)
    return out

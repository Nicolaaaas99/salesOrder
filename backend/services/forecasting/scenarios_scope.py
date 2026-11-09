"""Scope + leaf types shared by the scenario service and the target
resolver (split out of scenarios.py so resolver.py can import them without
a cycle). scenarios.py re-exports everything here."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date
from typing import Iterable, Optional

LeafKey = tuple[str, str]               # (customer, sku)
LeafWeekKey = tuple[str, str, date]     # (customer, sku, week)


class ScenarioError(Exception):
    """Business-rule violation. `status_code` maps to the HTTP response."""

    def __init__(self, message: str, status_code: int = 400, detail: object = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail if detail is not None else message


# --- scope -------------------------------------------------------------------

_SCOPE_DIMS = {
    # json key   -> _tb_SOForecast column
    "distCentre": "cDistCentre",
    "route": "cRoute",
    "customer": "cCustomerAccount",
    "category": "cCategory",
    "sku": "cStockCode",
    "salesRep": "cSalesRep",
    "telesales": "cTelesales",
}


def _norm(v: str) -> str:
    # SQL Server's default collation is case-insensitive and ignores
    # trailing spaces; match that in Python-side comparisons.
    return (v or "").strip().upper()


@dataclass(frozen=True)
class ScenarioScope:
    dist_centre: tuple[str, ...] = ()
    route: tuple[str, ...] = ()
    customer: tuple[str, ...] = ()
    category: tuple[str, ...] = ()
    sku: tuple[str, ...] = ()
    sales_rep: tuple[str, ...] = ()
    telesales: tuple[str, ...] = ()
    exclude_leaves: tuple[LeafKey, ...] = ()

    _ATTR = {
        "distCentre": "dist_centre", "route": "route", "customer": "customer",
        "category": "category", "sku": "sku", "salesRep": "sales_rep",
        "telesales": "telesales",
    }

    def dim_filters(self) -> dict[str, tuple[str, ...]]:
        """{forecast column: allowed values} for every non-empty dimension."""
        out = {}
        for key, col in _SCOPE_DIMS.items():
            vals = getattr(self, self._ATTR[key])
            if vals:
                out[col] = vals
        return out

    def matches(self, leaf: "ForecastLeaf") -> bool:
        """Python-side equivalent of the SQL scope filter (plus exclusions).
        Scope attribute names mirror ForecastLeaf's."""
        for attr in self._ATTR.values():
            allowed = getattr(self, attr)
            if allowed and _norm(getattr(leaf, attr)) not in {_norm(a) for a in allowed}:
                return False
        return not self.is_excluded(leaf.customer, leaf.sku)

    def is_excluded(self, customer: str, sku: str) -> bool:
        k = (_norm(customer), _norm(sku))
        return any((_norm(c), _norm(s)) == k for c, s in self.exclude_leaves)

    def with_exclusions(self, leaves: Iterable[LeafKey]) -> "ScenarioScope":
        merged = list(self.exclude_leaves)
        seen = {(_norm(c), _norm(s)) for c, s in merged}
        for c, s in leaves:
            if (_norm(c), _norm(s)) not in seen:
                seen.add((_norm(c), _norm(s)))
                merged.append((c, s))
        return replace(self, exclude_leaves=tuple(merged))

    def to_dict(self) -> dict:
        out: dict = {}
        for key in _SCOPE_DIMS:
            vals = getattr(self, self._ATTR[key])
            if vals:
                out[key] = list(vals)
        if self.exclude_leaves:
            out["excludeLeaves"] = [{"customer": c, "sku": s} for c, s in self.exclude_leaves]
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: Optional[dict], strict: bool = False) -> "ScenarioScope":
        d = d or {}
        known = set(_SCOPE_DIMS) | {"excludeLeaves"}
        unknown = set(d) - known
        if strict and unknown:
            raise ScenarioError(f"Unknown scope key(s): {', '.join(sorted(unknown))}. "
                                f"Allowed: {', '.join(sorted(known))}")
        kwargs: dict = {}
        for key in _SCOPE_DIMS:
            raw = d.get(key) or []
            if isinstance(raw, str):
                raw = [raw]
            vals = tuple(dict.fromkeys(str(v).strip() for v in raw if str(v).strip()))
            kwargs[cls._ATTR[key]] = vals
        excl = []
        for item in d.get("excludeLeaves") or []:
            c, s = str(item.get("customer", "")).strip(), str(item.get("sku", "")).strip()
            if c and s:
                excl.append((c, s))
        kwargs["exclude_leaves"] = tuple(excl)
        return cls(**kwargs)

    @classmethod
    def from_json(cls, raw: Optional[str]) -> "ScenarioScope":
        return cls.from_dict(json.loads(raw) if raw else {})



@dataclass(frozen=True)
class ForecastLeaf:
    """The active forecast for one leaf-week, with its denormalised dims."""
    customer: str
    sku: str
    week: date
    forecast_id: Optional[int]
    forecast_qty: float
    route: Optional[str] = None
    dist_centre: Optional[str] = None
    category: Optional[str] = None
    telesales: Optional[str] = None
    sales_rep: Optional[str] = None

    @property
    def key(self) -> LeafWeekKey:
        return (self.customer, self.sku, self.week)

"""Target resolver: turns every active scenario + override for one week into
a target per customer-product, so that targets add up at every level.

Decided 2026-11-06 ("keep the parent total, rebalance"):

  * Every scenario is a TARGET ON A NODE -- the set of customer-products its
    scope matches (e.g. "Makhado DC", "SPA227", "Milk in Makhado").
  * Its goal for the week comes from the forecast of that node only:
        PERCENT         goal = forecast total x (1 + value/100)
        ABSOLUTE_DELTA  goal = forecast total + value
        FIXED_QTY       goal = value
    in units, or in litres (qty x StkItem.ulIISize) when measure = LITRES.
  * Nested targets are PINNED. Inside a node, the targets of the nodes
    within it are taken off its goal first and what is left is shared over
    the remaining customer-products in proportion to their forecast. So
    "Makhado = 250 000 L" still adds up to 250 000 L after "SPA227 = 5 000 L"
    is set inside it -- the other customers absorb the difference.
  * Overrides and locked rows are pinned customer-products (the innermost
    level). A locked row keeps its current target (goalposts don't move).
  * When the targets inside a node add up to more than the node's own goal,
    that's a CONFLICT -- reported, and blocks approval.
  * Two scopes that only partly overlap (neither contains the other): the
    newer scenario wins on the shared customer-products; the older one keeps
    its rate on the rest. Reported as a warning.
  * In a LITRES node, products with no litre size can't take a share of a
    litre goal -- they keep their own value and are counted in a warning.

Pure -- no database. The service (scenarios.py) loads the inputs and
writes the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .scenarios_scope import ForecastLeaf, LeafWeekKey, ScenarioScope

MEASURE_UNITS = "UNITS"
MEASURE_LITRES = "LITRES"
MEASURES = (MEASURE_UNITS, MEASURE_LITRES)

_EPS = 1e-6


@dataclass(frozen=True)
class GoalSpec:
    """One active scenario as a target on a node."""
    scenario_id: int
    scope: ScenarioScope
    lever_type: str          # PERCENT | ABSOLUTE_DELTA | FIXED_QTY
    lever_value: float
    measure: str = MEASURE_UNITS
    name: str = ""


@dataclass
class Conflict:
    scenario_id: int
    week: date
    goal: float
    inner_total: float
    measure: str
    message: str


@dataclass
class ResolvedLeaf:
    qty: float
    scenario_ids: list[int]
    pinned: bool = False


@dataclass
class WeekResolution:
    week: date
    values: dict[LeafWeekKey, ResolvedLeaf] = field(default_factory=dict)
    goals: dict[int, float] = field(default_factory=dict)      # scenario -> goal (its measure)
    conflicts: list[Conflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Node:
    members: frozenset          # leaf keys
    goal: float                 # in `measure`
    measure: str
    scenario_id: Optional[int]  # None for a pinned leaf
    pinned_qty: Optional[float] = None   # units, pinned leaves only
    children: list["_Node"] = field(default_factory=list)


def goal_for(spec: GoalSpec, forecast_total: float) -> float:
    """The node's goal for the week, in its own measure. Never negative."""
    if spec.lever_type == "PERCENT":
        g = forecast_total * (1.0 + spec.lever_value / 100.0)
    elif spec.lever_type == "ABSOLUTE_DELTA":
        g = forecast_total + spec.lever_value
    elif spec.lever_type == "FIXED_QTY":
        g = spec.lever_value
    else:
        raise ValueError(f"Unknown lever type {spec.lever_type!r}")
    return max(g, 0.0)


def resolve_week(
    week: date,
    leaves: list[ForecastLeaf],
    goals: list[GoalSpec],
    pinned: Optional[dict[LeafWeekKey, float]] = None,
    litre_factor: Optional[dict[str, float]] = None,
) -> WeekResolution:
    """Resolve one week. `leaves` must include every customer-product in any
    goal's scope (their forecasts set the goals and the shares) plus every
    pinned customer-product. `pinned` = {key: units} for overrides and
    locked rows. Keys in the result cover every leaf passed in."""
    pinned = pinned or {}
    litre_factor = litre_factor or {}
    res = WeekResolution(week=week)
    by_key = {lf.key: lf for lf in leaves}

    def factor(key: LeafWeekKey, measure: str) -> float:
        if measure == MEASURE_LITRES:
            return float(litre_factor.get(by_key[key].sku, 0.0) or 0.0)
        return 1.0

    def fc_total(keys, measure: str) -> float:
        return sum(max(by_key[k].forecast_qty, 0.0) * factor(k, measure) for k in keys)

    # 1. Each goal's node and its goal, from the forecast of its full scope.
    nodes: list[_Node] = []
    for spec in sorted(goals, key=lambda g: g.scenario_id):
        members = frozenset(lf.key for lf in leaves if spec.scope.matches(lf))
        if not members:
            res.warnings.append(f"{_label(spec)} matches no forecast for week {week.isoformat()}")
            continue
        g = goal_for(spec, fc_total(members, spec.measure))
        res.goals[spec.scenario_id] = g
        nodes.append(_Node(members, g, spec.measure, spec.scenario_id))

    # 2. Partial overlaps: the newer scenario wins on the shared part; the
    #    older one keeps its rate (goal per forecast) on what's left.
    #    Identical scopes: the newer one replaces the older one entirely.
    names = {g.scenario_id: _label(g) for g in goals}
    for _ in range(len(nodes) + 1):      # trimming can expose a new partial overlap; repeat until stable
        if not _trim_overlaps(nodes, names, week, fc_total, res):
            break
    nodes = [n for n in nodes if n.members]

    # 3. Pinned customer-products are the innermost nodes.
    for key, qty in pinned.items():
        if key in by_key:
            nodes.append(_Node(frozenset([key]), max(qty, 0.0), MEASURE_UNITS, None, pinned_qty=max(qty, 0.0)))

    # 4. Build the forest: each node's parent is the smallest earlier node
    #    that contains it. Larger first; on equal size the scenario wins
    #    over a pin, and the newer scenario sits inside the older one.
    order = sorted(nodes, key=lambda n: (-len(n.members), n.scenario_id is None, n.scenario_id or 0))
    roots: list[_Node] = []
    placed: list[_Node] = []                          # scenario nodes already in the forest
    containing: dict[LeafWeekKey, list[_Node]] = {}   # leaf -> scenario nodes holding it, outer first
    for n in order:
        if n.scenario_id is None:
            chain = containing.get(next(iter(n.members)))
            (chain[-1].children if chain else roots).append(n)
            continue
        parent = None
        for cand in placed:
            if n.members <= cand.members and (parent is None or len(cand.members) <= len(parent.members)):
                parent = cand
        (parent.children if parent else roots).append(n)
        placed.append(n)
        for k in n.members:
            containing.setdefault(k, []).append(n)

    # 5. Solve inside-out, then fill in everything else from the forecast.
    values: dict[LeafWeekKey, float] = {}

    def solve(node: _Node) -> None:
        for c in node.children:
            solve(c)
        if node.pinned_qty is not None:
            values[next(iter(node.members))] = node.pinned_qty
            return
        inner = set().union(*(c.members for c in node.children)) if node.children else set()
        free = [k for k in node.members if k not in inner]
        used = sum(values[k] * factor(k, node.measure) for k in inner)
        remainder = node.goal - used
        spreadable = [k for k in free if factor(k, node.measure) > 0]
        no_size = [k for k in free if factor(k, node.measure) <= 0]
        for k in no_size:
            values[k] = max(by_key[k].forecast_qty, 0.0)
        if no_size:
            res.warnings.append(f"{names.get(node.scenario_id, '')}: {len(no_size)} product(s) have no "
                                f"litre size and keep their forecast in week {week.isoformat()}")
        base = fc_total(spreadable, node.measure)
        unit = "L" if node.measure == MEASURE_LITRES else "units"
        if remainder < -_EPS * max(1.0, node.goal):
            res.conflicts.append(Conflict(
                node.scenario_id, week, node.goal, used, node.measure,
                f"{names.get(node.scenario_id, '')}: the targets inside it add up to {used:,.1f} {unit}, "
                f"more than its own target of {node.goal:,.1f} {unit} in week {week.isoformat()}"))
            for k in spreadable:
                values[k] = 0.0
            return
        if base <= _EPS:
            for k in spreadable:
                values[k] = 0.0
            if remainder > _EPS * max(1.0, node.goal):
                if spreadable:
                    res.warnings.append(f"{names.get(node.scenario_id, '')}: {remainder:,.1f} {unit} could not "
                                        f"be shared out in week {week.isoformat()} -- the remaining "
                                        f"customer-products have no forecast")
                else:
                    res.conflicts.append(Conflict(
                        node.scenario_id, week, node.goal, used, node.measure,
                        f"{names.get(node.scenario_id, '')}: every customer-product in it has its own "
                        f"target, and those add up to {used:,.1f} {unit} instead of "
                        f"{node.goal:,.1f} {unit} in week {week.isoformat()}"))
            return
        rate = remainder / base          # per forecast unit, in the node's measure
        for k in spreadable:
            values[k] = max(by_key[k].forecast_qty, 0.0) * rate

    for r in roots:
        solve(r)

    for lf in leaves:
        ids = [n.scenario_id for n in containing.get(lf.key, [])]
        qty = values.get(lf.key, max(lf.forecast_qty, 0.0))
        res.values[lf.key] = ResolvedLeaf(round(max(qty, 0.0), 4), sorted(ids), lf.key in pinned)
    return res


def _trim_overlaps(nodes, names, week, fc_total, res) -> bool:
    """One pass of the partial-overlap rule. Returns True if anything changed."""
    changed = False
    for i, newer in enumerate(nodes):
        for older in nodes[:i]:
            if not older.members:
                continue
            shared = older.members & newer.members
            if not shared:
                continue
            if older.members == newer.members:
                res.warnings.append(f"{names[newer.scenario_id]} replaces {names[older.scenario_id]} "
                                    f"(same customer-products) in week {week.isoformat()}")
                older.members, older.goal = frozenset(), 0.0
                changed = True
                continue
            if older.members <= newer.members or newer.members <= older.members:
                continue   # nested -- handled by pinning
            before = fc_total(older.members, older.measure)
            older.members = older.members - shared
            after = fc_total(older.members, older.measure)
            older.goal = older.goal * (after / before) if before > _EPS else 0.0
            changed = True
            res.warnings.append(f"{names[newer.scenario_id]} and {names[older.scenario_id]} overlap on "
                                f"{len(shared)} customer-product(s) in week {week.isoformat()}; "
                                f"the newer one wins there")
    return changed


def _label(spec: GoalSpec) -> str:
    return f'"{spec.name}"' if spec.name else f"Scenario #{spec.scenario_id}"

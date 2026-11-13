"""Order Import: PDF text parsing + validation rules.

The parser is tested on the text of a real Boxer purchase order
(fixtures/boxer_284529.txt). Validation is tested with the DB-facing
helpers monkeypatched, so the rules (store check, mapping precedence,
price tolerance, totals) are exercised without SQL Server.
"""
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import main
import schemas
from database import get_db
from routers import order_import as router_mod
from services.order_import import validate as v
from services.order_import.models import ItemMapping
from services.order_import.parsers import (
    OrderParseError,
    parse_order_text,
)

FIXTURE = Path(__file__).parent / "fixtures" / "boxer_284529.txt"


@pytest.fixture
def boxer():
    return parse_order_text([FIXTURE.read_text(encoding="utf-8")])


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_boxer_header(boxer):
    assert boxer.format_name == "BOXER"
    assert boxer.order_no == "284529"
    assert boxer.order_date == date(2026, 11, 6)
    assert boxer.delivery_from == date(2026, 11, 9)
    assert boxer.delivery_to == date(2026, 11, 9)
    assert boxer.store_code == "X105"
    assert boxer.store_name == "105 - Bochum"
    assert boxer.warnings == []


def test_boxer_totals(boxer):
    assert boxer.total_qty == 1340
    assert boxer.total_excl == pytest.approx(36847.46)
    assert boxer.total_vat == pytest.approx(1570.34)
    assert boxer.total_incl == pytest.approx(38417.80)


def test_boxer_lines(boxer):
    assert len(boxer.lines) == 25
    assert sum(l.qty for l in boxer.lines) == 1340
    assert sum(l.line_incl for l in boxer.lines) == pytest.approx(38417.80)
    assert all(l.parse_warning is None for l in boxer.lines)

    first = boxer.lines[0]
    assert (first.supplier_code, first.item_code) == ("311", "41157016")
    assert first.qty == 50                      # Qty (Unit), not Qty (Pack)
    assert first.unit_excl == pytest.approx(12.2609)
    assert first.unit_incl == pytest.approx(14.10)
    assert first.vat_rate == pytest.approx(15.0)
    assert "Hygienik Dairy Fruits of Eden" in first.description
    assert "100% Juice" in first.description

    zero_rated = boxer.lines[3]
    assert (zero_rated.supplier_code, zero_rated.qty) == ("710", 100)
    assert zero_rated.vat_rate == 0
    assert zero_rated.line_incl == pytest.approx(6150.0)

    codes = [l.supplier_code for l in boxer.lines]
    assert "08D020" in codes and "999000" in codes and "12" in codes


def test_numbers_on_first_line_with_wrapped_description():
    """pdfplumber can put the numbers on the row's first line and the
    wrapped description on the next one — both orders must parse."""
    text = FIXTURE.read_text(encoding="utf-8").replace(
        "207873 311 41157016Hygienik Dairy Fruits of Eden\n100% Juice\n"
        "Guava 20% 500.00ml 10 Case 15.0 141.00 12.2609 14.1000 0.0000 0.0000 5 50 0 613.0400 91.9600 705.0000\n",
        "207873 311 41157016Hygienik Dairy Fruits of Eden Guava 20% 500.00ml 10 Case 15.0 141.00 "
        "12.2609 14.1000 0.0000 0.0000 5 50 0 613.0400 91.9600 705.0000\n100% Juice\n",
    )
    doc = parse_order_text([text])
    line = doc.lines[0]
    assert line.qty == 50 and line.line_incl == pytest.approx(705.0)
    assert line.description.endswith("100% Juice")
    assert len(doc.lines) == 25


def test_line_maths_that_dont_add_up_are_flagged():
    text = FIXTURE.read_text(encoding="utf-8").replace(
        "5 50 0 613.0400 91.9600 705.0000", "5 55 0 613.0400 91.9600 705.0000", 1
    )
    doc = parse_order_text([text])
    assert doc.lines[0].parse_warning


def test_unknown_layout_rejected():
    with pytest.raises(OrderParseError):
        parse_order_text(["Some other retailer\nPURCHASE ORDER\n"])


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

CUSTOMER = {
    "account": "BOX07D", "name": "Boxer Bochum", "dclink": 123,
    "store_code": "X105", "corp_group": "Boxer", "def_tax_type_id": 1,
}


def _stock_for(codes):
    return {
        c.upper(): {
            "code": c, "description": f"Item {c}", "is_whse_item": True,
            "tax_type_id": None, "warehouse_codes": ["01.HOF_MAK"],
        }
        for c in codes
        if c not in {"08D020", "12"}       # not our codes
    }


@pytest.fixture
def evo(monkeypatch, boxer):
    """Evolution as the PO expects it: every one of our codes priced exactly
    as Boxer printed it; VAT per line as on the PO."""
    state = {"mappings": [], "duplicates": [], "price_override": {}}
    by_code = {l.supplier_code: l for l in boxer.lines}

    def prices(db, account, codes, as_of, warehouse_code=None):
        out = {}
        for c in codes:
            l = by_code.get(c)
            if l is None:
                l = next(x for x in boxer.lines if x.supplier_code in {"08D020", "12"}
                         and state["mapped_from"].get(c) == x.supplier_code)
            excl, incl = state["price_override"].get(c, (round(l.unit_excl, 2), l.unit_incl))
            out[c] = schemas.CustomerPrice(stock_code=c, excl_price=excl, incl_price=incl,
                                           source="Price List",
                                           price_list_broken=c in state.get("broken", ()))
        return out

    def stock_info(db, codes):
        info = _stock_for(codes)
        for c, d in info.items():
            l = by_code.get(c) or next(
                (x for x in boxer.lines if state["mapped_from"].get(c) == x.supplier_code), None)
            d["tax_type_id"] = 1 if (l and l.vat_rate) else 2
        return info

    state["mapped_from"] = {}
    monkeypatch.setattr(v, "is_corporate_customer", lambda db, a: True)
    monkeypatch.setattr(v, "load_pack_fixes", lambda db, scope: state.get("pack_fixes", []))
    monkeypatch.setattr(v, "load_mappings", lambda db, scope: state["mappings"])
    monkeypatch.setattr(v, "_stock_info", stock_info)
    monkeypatch.setattr(v, "resolve_customer_prices", prices)
    monkeypatch.setattr(v, "_tax_rates", lambda db, ids: {1: 15.0, 2: 0.0})
    monkeypatch.setattr(v, "_duplicate_orders", lambda db, dc, ext: state["duplicates"])
    monkeypatch.setattr(v, "_customers_with_store_code", lambda db, code: [])
    state["pending"] = []
    monkeypatch.setattr(v, "_pending_reviews", lambda db, acc, po: state["pending"])
    return state


def _map(state, supplier_code, stock_code, item_code=""):
    state["mapped_from"][stock_code] = supplier_code
    state["mappings"].insert(0, ItemMapping(
        id=len(state["mappings"]) + 1, scope="GROUP:Boxer",
        supplier_code=supplier_code, item_code=item_code, stock_code=stock_code,
    ))


def _run(boxer, customer=CUSTOMER, delivery=date(2026, 11, 9), overrides=None,
         supervisor=False):
    return v.validate_import(None, dict(customer), boxer, delivery, "01.HOF_MAK",
                             overrides, supervisor)


def _line(r, code):
    return next(l for l in r.lines if l.supplier_code == code)


def test_unmatched_lines_block(evo, boxer):
    r = _run(boxer)
    unmatched = [l for l in r.lines if l.status == "UNMATCHED"]
    assert {l.supplier_code for l in unmatched} == {"08D020", "12"}
    assert not r.can_create
    assert any(i.code == "LINES" for i in r.issues)


def test_all_mapped_and_priced_can_create(evo, boxer):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    r = _run(boxer)
    assert [l.status for l in r.lines if l.status != "OK"] == ["EXCLUDED"]   # crates
    assert r.store_check == "MATCH"
    assert r.totals.agrees, r.totals
    assert abs(r.totals.difference_incl) <= r.totals.tolerance
    assert r.can_create, [i.message for i in r.issues]
    mapped = {l.supplier_code: l for l in r.lines}
    assert mapped["12"].match_source == "MAPPING" and mapped["12"].stock_code == "012"
    assert mapped["311"].match_source == "DIRECT"
    assert all(l.warehouse_code == "01.HOF_MAK" for l in r.lines if not l.excluded)


def test_two_lines_on_one_stock_item_block(evo, boxer):
    """08D020 mapped to 311, which line '311' already matches directly: both
    lines are flagged and the order can't be created or submitted."""
    _map(evo, "12", "012")
    evo["mappings"].insert(0, ItemMapping(
        id=99, scope="GROUP:Boxer", supplier_code="08D020", item_code="", stock_code="311",
    ))
    r = _run(boxer)
    a, b = _line(r, "311"), _line(r, "08D020")
    assert a.status == b.status == "ITEM_ISSUE"
    assert f"also on line {b.line_no}" in a.issues[0]
    assert f"also on line {a.line_no}" in b.issues[0]
    assert not r.can_create and not r.can_submit_review


def test_our_price_above_po_needs_review(evo, boxer):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    evo["price_override"]["311"] = (12.52, 14.40)
    r = _run(boxer)
    line = _line(r, "311")
    assert line.status == "PRICE_MISMATCH" and line.needs_review
    assert not r.can_create and r.can_submit_review


def test_excl_deal_truncated_incl_charges_ours(evo, boxer):
    """Deal captured EXCL at R12.26: _vw_SOActiveDeals gives incl
    FLOOR(14.099) = R14.09, 1c under the PO's R14.10. The PO may be up to 5c
    above ours: our price is charged automatically, still flagged."""
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    evo["price_override"]["311"] = (12.26, 14.09)
    r = _run(boxer)
    line = _line(r, "311")
    assert line.status == "OK", line.issues
    assert line.price_differs and line.price_auto and line.price_choice == "EVOLUTION"
    assert line.unit_incl == 14.09 and line.line_incl == 704.50
    assert r.totals.price_adjust_incl == -0.50
    assert r.totals.agrees and r.can_create


def test_incl_deal_charges_exactly_the_deal_price(evo, boxer):
    """Deal captured INCL at R11.60 (excl FLOOR'd to R10.08). The old
    exclusive save charged 10.08 x 1.15 = R11.59; the inclusive save
    charges the deal's R11.60 — matching the PO, line = 40 x 11.60."""
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    evo["price_override"]["620"] = (10.08, 11.60)
    r = _run(boxer)
    line = _line(r, "620")
    assert line.status == "OK", line.issues
    assert not line.price_differs
    assert line.evo_unit_incl == 11.60
    assert line.line_incl == 464.00


def test_price_list_stored_incl_is_what_inclusive_charges(evo, boxer):
    """Price list R10.07 excl / R11.59 incl: inclusive charges the stored
    R11.59 (as Evolution does), not 10.07 x 1.15 = R11.58."""
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    evo["price_override"]["620"] = (10.07, 11.59)
    r = _run(boxer)
    line = _line(r, "620")
    assert line.evo_unit_incl == 11.59
    # PO says R11.60: 1c above ours, so ours is charged automatically.
    assert line.status == "OK" and line.price_auto and line.unit_incl == 11.59


# --- crates -------------------------------------------------------------------

def test_crates_left_off_the_order_and_the_po_total(evo, boxer):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    r = _run(boxer)
    crate = _line(r, "999000")
    assert crate.excluded and crate.status == "EXCLUDED"
    assert crate.line_incl == 0 and crate.unit_incl is None
    assert r.totals.excluded_incl == 34.80 and r.totals.excluded_qty == 2
    assert r.totals.expected_incl == round(boxer.total_incl - 34.80, 2)
    assert r.totals.agrees and r.can_create
    # The parse check still counts the crate line (nothing missed).
    assert r.totals.parsed_incl == boxer.total_incl


# --- PO price vs Evolution price (rules 2026-11-10) ------------------------------

@pytest.mark.parametrize("evo_incl", [14.09, 14.05])        # PO 1c / 5c above ours
def test_po_up_to_5c_above_ours_charges_ours_automatically(evo, boxer, evo_incl):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    evo["price_override"]["311"] = (round(evo_incl / 1.15, 2), evo_incl)
    r = _run(boxer)
    line = _line(r, "311")
    assert line.status == "OK", line.issues
    assert line.price_differs and line.price_auto and not line.needs_review
    assert line.price_choice == "EVOLUTION" and line.unit_incl == evo_incl
    assert r.totals.agrees and r.can_create and not r.can_submit_review


@pytest.mark.parametrize("evo_incl", [14.04, 14.11, 14.40])   # PO 6c above / ours above
def test_bigger_differences_need_review(evo, boxer, evo_incl):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    evo["price_override"]["311"] = (round(evo_incl / 1.15, 2), evo_incl)
    r = _run(boxer)
    line = _line(r, "311")
    assert line.status == "PRICE_MISMATCH" and line.needs_review
    assert "above" in line.issues[0] and "review" not in line.issues[0]
    assert not r.can_create and r.can_submit_review


def test_agent_overrides_are_ignored(evo, boxer):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    evo["price_override"]["311"] = (12.52, 14.40)
    no = _line(_run(boxer), "311").line_no
    r = _run(boxer, overrides={no: 14.10}, supervisor=False)
    assert _line(r, "311").status == "PRICE_MISMATCH" and not r.can_create
    assert not r.can_override_prices


@pytest.mark.parametrize("price, choice", [(14.10, "PO"), (14.40, "EVOLUTION"),
                                           (14.00, "OVERRIDE")])
def test_supervisor_sets_the_price(evo, boxer, price, choice):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    evo["price_override"]["311"] = (12.52, 14.40)       # ours 30c above the PO
    no = _line(_run(boxer), "311").line_no
    r = _run(boxer, overrides={no: price}, supervisor=True)
    line = _line(r, "311")
    assert line.status == "OK", line.issues
    assert line.price_choice == choice and line.unit_incl == price
    assert line.line_incl == round(50 * price, 2)
    # Above the PO: allowed for a supervisor, but marked as a claim risk.
    assert bool(line.notes) == (price > 14.10)
    assert r.totals.agrees and r.can_create


def test_review_not_offered_when_other_problems(evo, boxer):
    evo["price_override"]["311"] = (12.52, 14.40)
    r = _run(boxer)                                     # 3 lines still unmapped
    assert not r.can_create and not r.can_submit_review


def test_pending_review_warns(evo, boxer):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    evo["pending"] = [7]
    r = _run(boxer)
    issue = next(i for i in r.issues if i.code == "IN_REVIEW")
    assert not issue.blocking and "#7" in issue.message
    assert r.pending_reviews == [7]


def test_create_as_agent_needs_review_supervisor_creates(client, evo, boxer, monkeypatch):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    evo["price_override"]["311"] = (12.52, 14.40)       # needs review
    evo["price_override"]["321"] = (12.25, 14.09)       # 1c: ours, automatic
    no_311 = _line(_run(boxer), "311").line_no
    captured = {}

    def fake_place(order, db, username, app_version):
        captured["order"] = order
        return schemas.SalesOrderCreateResponse(order_no="SO1")

    def fake_log(db, result, customer, order_no, username):
        captured["log"] = [(l.stock_code, l.price_choice, l.price_auto, l.unit_incl)
                           for l in result.lines if l.price_differs]

    monkeypatch.setattr(router_mod, "place_order", fake_place)
    monkeypatch.setattr(router_mod, "log_price_decisions", fake_log)

    body = _create_body(boxer)
    body["price_overrides"] = {str(no_311): 14.10}      # ignored for an agent
    r = client.post("/api/order-import/create", json=body)
    assert r.status_code == 400 and "review" in r.json()["detail"]

    ROLE["role"] = "SUPERVISOR"
    r = client.post("/api/order-import/create", json=body)
    assert r.status_code == 201, r.text
    lines = {l.stock_code: l for l in captured["order"].lines}
    assert "999000" not in lines
    assert lines["311"].unit_price == 14.10
    assert lines["321"].unit_price == 14.09
    assert sorted(captured["log"]) == [("311", "PO", False, 14.10),
                                       ("321", "EVOLUTION", True, 14.09)]


def test_submit_review(client, evo, boxer, monkeypatch):
    from services.order_import import store
    from services.order_import.models import ImportReviewDetail
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    evo["price_override"]["311"] = (12.52, 14.40)
    saved = {}

    def fake_create(db, result, document, order_date, description, note, username):
        saved.update(lines=sum(l.needs_review for l in result.lines),
                     note=note, user=username, descr=description)
        return 5

    monkeypatch.setattr(store, "create_review", fake_create)
    monkeypatch.setattr(store, "link_document", lambda *a, **k: None)
    monkeypatch.setattr(store, "get_review", lambda db, rid: ImportReviewDetail(
        id=rid, status="PENDING", customer_account="BOX07D", document=boxer))
    body = {**_create_body(boxer), "note": "PO price is old", "description": "Boxer wk46"}
    r = client.post("/api/order-import/reviews", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["id"] == 5
    assert saved == {"lines": 1, "note": "PO price is old", "user": "tester",
                     "descr": "Boxer wk46"}

    evo["pending"] = [5]                               # second submit of the same PO
    assert client.post("/api/order-import/reviews", json=body).status_code == 409


def test_submit_review_refused_when_other_problems(client, evo, boxer):
    evo["price_override"]["311"] = (12.52, 14.40)    # and 3 lines unmapped
    r = client.post("/api/order-import/reviews", json=_create_body(boxer))
    assert r.status_code == 400


def test_review_create_and_reject_are_supervisor_only(client, evo, boxer):
    body = {**_create_body(boxer), "review_id": 5}
    assert client.post("/api/order-import/create", json=body).status_code == 403
    r = client.post("/api/order-import/reviews/5/reject", json={"note": "no"})
    assert r.status_code == 403


def test_price_decision_log_failure_never_raises(boxer):
    from services.order_import.models import ImportLineResult, ImportTotals, ImportValidation

    class BrokenDb:
        def execute(self, *a, **k):
            raise RuntimeError("table missing")

        def rollback(self):
            raise RuntimeError("no connection")

    res = ImportValidation(
        document=boxer, customer_account="BOX07D", price_date=date(2026, 11, 9),
        totals=ImportTotals(),
        lines=[ImportLineResult(line_no=1, stock_code="311", qty=50, price_differs=True,
                                price_choice="PO", evo_unit_excl=12.25, evo_unit_incl=14.09,
                                doc_unit_incl=14.10, unit_incl=14.10)],
    )
    assert v.log_price_decisions(BrokenDb(), res, dict(CUSTOMER), "SO1", "tester") == 0


def test_inclusive_unit_price_rule():
    from services.pricing import inclusive_unit_price as f
    assert f(10.07, 11.59, 15) == 11.59      # stored incl trusted (1c gap)
    assert f(12.26, 14.09, 15) == 14.09      # FLOOR'd deal incl trusted
    assert f(10.08, 11.60, 15) == 11.60      # incl-captured deal
    assert f(10.07, 11.59, 0) == 10.07       # 0% VAT line: never charge the 15% incl
    assert f(25.90, 25.90, 15) == 29.79      # incl clearly not for 15%: excl + VAT
    assert f(12.26, None, 15) == 14.10       # no incl: excl + VAT, half-up
    assert f(12.26, 0, 15) == 14.10


def test_price_list_row_broken_rule():
    from services.pricing import price_list_row_broken as f
    assert f("Price List", 25.90, 25.90, 15)          # MOO099: incl not for 15%
    assert f("Price List", 25.90, 0, 15)              # no incl at all
    assert not f("Price List", 10.07, 11.59, 15)      # 1c gap: Evolution's own incl
    assert not f("Price List", 26.43, 30.39, 15)      # 2c gap: still trusted
    assert f("Price List", 26.43, 30.42, 15)          # 3c gap: flagged
    assert not f("Price List", 55.80, 55.80, 0)       # 0%-VAT item, consistent
    assert not f("Customer Deal", 25.90, 25.90, 15)   # deals are never flagged
    assert not f("Group Deal", 25.90, 25.90, 15)
    assert not f("Price List", 25.90, 25.90, None)    # unknown item VAT: no flag


def test_broken_price_list_row_warns_but_never_blocks(evo, boxer):
    """MOO099-type row on an import line: flagged, still charged excl + VAT,
    and the order can still be created."""
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    evo["price_override"]["620"] = (10.08, 11.60)
    evo["broken"] = {"620"}
    r = _run(boxer)
    line = next(l for l in r.lines if l.supplier_code == "620")
    assert line.price_list_broken
    assert line.status == "OK", line.issues
    assert line.evo_unit_incl == 11.60
    assert r.can_create


def test_cents_round_half_up():
    assert v._cents(33.4995) == 33.5
    assert v._cents(29.13 * 1.15) == 33.5
    assert v._cents(14.099) == 14.10


def test_store_mismatch_blocks(evo, boxer):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    r = _run(boxer, customer={**CUSTOMER, "store_code": "X999"})
    assert r.store_check == "MISMATCH"
    assert not r.can_create


def test_missing_store_code_warns_only(evo, boxer):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    r = _run(boxer, customer={**CUSTOMER, "store_code": ""})
    assert r.store_check == "UNVERIFIED"
    assert r.can_create


def test_duplicate_po_warns_only(evo, boxer):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    evo["duplicates"] = ["SO012345"]
    r = _run(boxer)
    dup = next(i for i in r.issues if i.code == "DUPLICATE")
    assert not dup.blocking and "SO012345" in dup.message
    assert r.can_create


def test_mapping_precedence():
    maps = [
        ItemMapping(id=3, scope="s", supplier_code="", item_code="41157016", stock_code="C"),
        ItemMapping(id=2, scope="s", supplier_code="311", item_code="", stock_code="B"),
        ItemMapping(id=1, scope="s", supplier_code="311", item_code="41157016", stock_code="A"),
    ]
    assert v.find_mapping(maps, "311", "41157016").stock_code == "A"
    assert v.find_mapping(maps, "311", "99999999").stock_code == "B"
    assert v.find_mapping(maps, "", "41157016").stock_code == "C"
    assert v.find_mapping(maps, "999", "11111111") is None


def test_mapping_scope():
    assert v.mapping_scope(CUSTOMER) == "GROUP:Boxer"
    assert v.mapping_scope({**CUSTOMER, "corp_group": "Non Corporate"}) == "ACCOUNT:BOX07D"


# ---------------------------------------------------------------------------
# Create endpoint: server re-validates and refuses
# ---------------------------------------------------------------------------

ROLE = {"role": "AGENT"}


@pytest.fixture
def client(monkeypatch):
    ROLE["role"] = "AGENT"
    main.app.dependency_overrides[get_db] = lambda: object()
    main.app.dependency_overrides[auth.get_current_user] = lambda: "tester"
    main.app.dependency_overrides[auth.get_current_user_role] = lambda: ROLE["role"]
    monkeypatch.setattr(router_mod, "load_customer", lambda db, a: dict(CUSTOMER))
    monkeypatch.setattr(router_mod, "_default_warehouse_for", lambda db, u: "01.HOF_MAK")
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def _create_body(boxer, confirmed=True):
    return {
        "customer_account": "BOX07D",
        "document": boxer.model_dump(mode="json"),
        "delivery_date": "2026-11-09",
        "order_date": "2026-11-06",
        "dates_confirmed": confirmed,
    }


def _mapping_body(stock="152"):
    return {"customer_account": "BOX07D", "supplier_code": "154", "item_code": "89818020",
            "description": "Limpopo Hyjo", "stock_code": stock}


def test_mapping_to_a_stock_item_already_mapped_is_refused(client, monkeypatch):
    saved = []
    monkeypatch.setattr(router_mod, "_stock_info", lambda db, codes: _stock_for(codes))
    monkeypatch.setattr(router_mod, "upsert_mapping", lambda *a: saved.append(a) or 1)
    monkeypatch.setattr(router_mod, "mapping_conflict", lambda db, s, c, i, st: {
        "cCustCode": "08D020", "cCustItemCode": "86899001",
        "cCustDescription": "Dew Drop Medium Cream Maas 2.00kg",
    })
    r = client.post("/api/order-import/mappings", json=_mapping_body())
    assert r.status_code == 409
    assert "08D020 / 86899001" in r.json()["detail"]
    assert saved == []

    monkeypatch.setattr(router_mod, "mapping_conflict", lambda *a: None)
    r = client.post("/api/order-import/mappings", json=_mapping_body())
    assert r.status_code == 200 and len(saved) == 1


def test_pending_review_count_is_for_supervisors_only(client, monkeypatch):
    calls = []
    monkeypatch.setattr(router_mod.store, "count_pending_reviews",
                        lambda db: calls.append(1) or 3)
    assert client.get("/api/order-import/reviews/pending-count").json() == {"pending": 0}
    assert calls == []                       # agents never run the query
    ROLE["role"] = "SUPERVISOR"
    assert client.get("/api/order-import/reviews/pending-count").json() == {"pending": 3}


def test_store_check_mismatch_offers_the_right_customer(client, monkeypatch):
    from services.order_import.models import StoreMatch
    monkeypatch.setattr(
        v, "_customers_with_store_code",
        lambda db, code: [StoreMatch(account="BOX07D", name="Boxer Bochum")],
    )
    monkeypatch.setattr(
        router_mod, "load_customer",
        lambda db, a: {**CUSTOMER, "account": "BOX09A", "store_code": "X109"},
    )
    r = client.get("/api/order-import/store-check",
                   params={"account": "BOX09A", "store_code": "X105"})
    assert r.status_code == 200
    body = r.json()
    assert body["store_check"] == "MISMATCH"
    assert body["store_code_customers"] == [{"account": "BOX07D", "name": "Boxer Bochum"}]


def test_store_check_match(client):
    r = client.get("/api/order-import/store-check",
                   params={"account": "BOX07D", "store_code": "x105"})
    assert r.json()["store_check"] == "MATCH"


def test_create_requires_confirmed_dates(client, boxer):
    r = client.post("/api/order-import/create", json=_create_body(boxer, confirmed=False))
    assert r.status_code == 400
    assert "dates" in r.json()["detail"]


def test_create_refused_when_validation_fails(client, evo, boxer, monkeypatch):
    placed = []
    monkeypatch.setattr(router_mod, "place_order", lambda *a, **k: placed.append(1))
    r = client.post("/api/order-import/create", json=_create_body(boxer))
    assert r.status_code == 400
    assert placed == []


def test_create_places_order_with_evolution_prices(client, evo, boxer, monkeypatch):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    captured = {}

    def fake_place(order, db, username, app_version):
        captured["order"] = order
        return schemas.SalesOrderCreateResponse(order_no="SO1")

    monkeypatch.setattr(router_mod, "place_order", fake_place)
    r = client.post("/api/order-import/create", json=_create_body(boxer))
    assert r.status_code == 201, r.text
    order = captured["order"]
    assert order.source == "ORDER_IMPORT"
    assert order.external_order == "284529"
    assert order.delivery_date == date(2026, 11, 9)
    assert len(order.lines) == 24          # crates left off
    first = order.lines[0]
    # Inclusive save: the unit price sent is the incl Evolution charges.
    assert order.tax_mode == "Inclusive"
    assert (first.stock_code, first.quantity, first.unit_price) == ("311", 50, 14.10)
    assert order.description == "Sales Order"


def test_create_uses_the_description(client, evo, boxer, monkeypatch):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    captured = {}
    monkeypatch.setattr(router_mod, "place_order", lambda order, **k: (
        captured.update(order=order) or schemas.SalesOrderCreateResponse(order_no="SO1")))
    body = {**_create_body(boxer), "description": "  Boxer Bochum week 46 order  "}
    assert client.post("/api/order-import/create", json=body).status_code == 201
    assert captured["order"].description == "Boxer Bochum week 46 order"


# ---------------------------------------------------------------------------
# Pick n Pay
# ---------------------------------------------------------------------------

PNP_FIXTURE = Path(__file__).parent / "fixtures" / "pnp_4773898239.txt"


@pytest.fixture
def pnp():
    return parse_order_text([PNP_FIXTURE.read_text(encoding="utf-8")])


def test_pnp_header(pnp):
    assert pnp.format_name == "PICKNPAY"
    assert pnp.order_no == "4773898239"
    assert pnp.order_date == date(2026, 11, 9)
    assert pnp.delivery_from == pnp.delivery_to == date(2026, 11, 10)
    assert pnp.store_code == "NC46"
    assert pnp.store_name == "PnP EM Lebo Mall"
    assert pnp.total_incl == pytest.approx(3488.32)
    assert pnp.total_qty is None and pnp.warnings == []


def test_pnp_lines(pnp):
    assert len(pnp.lines) == 12
    assert sum(l.line_incl for l in pnp.lines) == pytest.approx(3488.32)
    maas = pnp.lines[0]
    assert (maas.supplier_code, maas.item_code, maas.barcode) == ("11", "247173", "6003989004315")
    assert (maas.pack_qty, maas.pack_size, maas.qty) == (4, 12, 48)   # units = packs x size
    assert maas.line_incl == pytest.approx(1437.60)                    # "1 437,60"
    assert maas.unit_incl == pytest.approx(29.95)
    assert not maas.vat_known
    choc = pnp.lines[-1]                                               # wrapped description
    assert choc.supplier_code == "628" and choc.qty == 12
    assert choc.description == "HYJO DRINKING YOGHURT CHOCOLATE 350ML"
    assert choc.unit_incl == pytest.approx(11.9033)
    assert all(l.parse_warning is None for l in pnp.lines)


def test_pnp_numbers_before_the_wrapped_description():
    text = PNP_FIXTURE.read_text(encoding="utf-8").replace(
        "628 954253 6003989003431 HYJO DRINKING YOGHURT CHOCOLATE\n350ML\n142,84 6 2\n",
        "628 954253 6003989003431 HYJO DRINKING YOGHURT CHOCOLATE 142,84 6 2\n350ML\n",
    )
    choc = parse_order_text([text]).lines[-1]
    assert choc.description == "HYJO DRINKING YOGHURT CHOCOLATE 350ML"
    assert choc.line_incl == pytest.approx(142.84) and choc.qty == 12


def test_pnp_text_without_spaces_in_labels():
    text = PNP_FIXTURE.read_text(encoding="utf-8").replace(
        "PnP Delivery Site: NC46 - PnP EM Lebo Mall", "PnPDeliverySite: NC46-PnPEMLeboMall"
    )
    doc = parse_order_text([text])
    assert doc.store_code == "NC46" and doc.format_name == "PICKNPAY"


@pytest.fixture
def evo_pnp(monkeypatch, pnp):
    """Every PnP code is one of ours, 15% VAT, priced at the PO unit price
    rounded down to the cent (628: 11.90)."""
    import math
    state = {"price": {l.supplier_code: math.floor(l.unit_incl * 100 + 1e-6) / 100
                       for l in pnp.lines}}

    def prices(db, account, codes, as_of, warehouse_code=None):
        return {c: schemas.CustomerPrice(stock_code=c, excl_price=round(state["price"][c] / 1.15, 2),
                                         incl_price=state["price"][c], source="Price List")
                for c in codes if c in state["price"]}

    def stock_info(db, codes):
        return {c.upper(): {"code": c, "description": f"Item {c}", "is_whse_item": True,
                            "tax_type_id": 1, "warehouse_codes": ["01.HOF_MAK"]} for c in codes}

    monkeypatch.setattr(v, "is_corporate_customer", lambda db, a: True)
    monkeypatch.setattr(v, "load_pack_fixes", lambda db, scope: state.get("pack_fixes", []))
    monkeypatch.setattr(v, "load_mappings", lambda db, scope: [])
    monkeypatch.setattr(v, "_stock_info", stock_info)
    monkeypatch.setattr(v, "resolve_customer_prices", prices)
    monkeypatch.setattr(v, "_tax_rates", lambda db, ids: {1: 15.0})
    monkeypatch.setattr(v, "_duplicate_orders", lambda db, dc, ext: [])
    monkeypatch.setattr(v, "_customers_with_store_code", lambda db, code: [])
    monkeypatch.setattr(v, "_pending_reviews", lambda db, acc, po: [])
    return state


PNP_CUSTOMER = {"account": "PNP046", "name": "PnP Lebo Mall", "dclink": 9,
                "store_code": "NC46", "corp_group": "Pick n Pay", "def_tax_type_id": 1}


def test_pnp_validates_and_can_create(evo_pnp, pnp):
    r = _run(pnp, customer=PNP_CUSTOMER, delivery=date(2026, 11, 10))
    assert r.store_check == "MATCH"
    assert all(l.status == "OK" for l in r.lines), [(l.supplier_code, l.issues) for l in r.lines]
    choc = _line(r, "628")
    # 12 x 11.90 = 142.80: never more than the PO line's 142.84
    assert choc.unit_incl == 11.90 and choc.line_incl == pytest.approx(142.80)
    assert choc.doc_pack_qty == 2 and choc.doc_pack_size == 6
    assert choc.doc_unit_excl == pytest.approx(11.9033 / 1.15, abs=1e-4)   # from the item's VAT
    assert r.totals.agrees and r.can_create


def test_pnp_fractional_po_price_is_never_rounded_up(evo_pnp, pnp):
    """PO 11.9033 / unit: ours at 11.91 would charge 142.92 > 142.84."""
    evo_pnp["price"]["628"] = 11.91
    choc = _line(_run(pnp, customer=PNP_CUSTOMER, delivery=date(2026, 11, 10)), "628")
    assert choc.needs_review and choc.status == "PRICE_MISMATCH"


def test_supervisor_po_price_is_rounded_down(evo_pnp, pnp):
    evo_pnp["price"]["628"] = 11.50
    no = _line(_run(pnp, customer=PNP_CUSTOMER), "628").line_no
    r = _run(pnp, customer=PNP_CUSTOMER, overrides={no: 11.9033}, supervisor=True)
    choc = _line(r, "628")
    assert choc.unit_incl == 11.90 and choc.price_choice == "PO"


def test_supervisor_can_pick_ours_1c_above_a_fractional_po(evo_pnp, pnp):
    # PO 11.9033 (charged 11.90), ours 11.91: picking ours must not snap
    # back to the PO price.
    evo_pnp["price"]["628"] = 11.91
    no = _line(_run(pnp, customer=PNP_CUSTOMER), "628").line_no
    r = _run(pnp, customer=PNP_CUSTOMER, overrides={no: 11.91}, supervisor=True)
    choc = _line(r, "628")
    assert choc.unit_incl == 11.91 and choc.price_choice == "EVOLUTION"


def test_supervisor_can_pick_ours_1c_above_the_po(evo, boxer):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    evo["price_override"]["311"] = (12.27, 14.11)       # ours 1c above the PO
    no = _line(_run(boxer), "311").line_no
    for price, choice in [(14.10, "PO"), (14.11, "EVOLUTION"), (14.12, "OVERRIDE")]:
        line = _line(_run(boxer, overrides={no: price}, supervisor=True), "311")
        assert line.price_choice == choice and line.unit_incl == price, (price, line)


# ---------------------------------------------------------------------------
# Source document of an existing order
# ---------------------------------------------------------------------------

class _DocDb:
    def __init__(self, row=None, fail=False):
        self.row, self.fail, self.rolled_back, self.params = row, fail, False, None

    def execute(self, stmt, params=None):
        if self.fail:
            raise RuntimeError("Invalid object name '_tb_SOImportDocument'")
        self.params = params
        row = self.row

        class _R:
            def first(self_inner):
                return row
        return _R()

    def rollback(self):
        self.rolled_back = True


def test_document_for_order_found():
    from services.order_import import store
    db = _DocDb(row=(42,))
    assert store.document_for_order(db, "SO123") == 42
    assert db.params == {"ord": "SO123"}


def test_document_for_order_none_and_missing_table():
    from services.order_import import store
    assert store.document_for_order(_DocDb(row=None), "SO123") is None
    assert store.document_for_order(_DocDb(row=(1,)), "") is None
    db = _DocDb(fail=True)
    assert store.document_for_order(db, "SO123") is None   # never breaks opening an order
    assert db.rolled_back




# ---------------------------------------------------------------------------
# OK Foods
# ---------------------------------------------------------------------------

OK_FIXTURE = Path(__file__).parent / "fixtures" / "ok_bakers_15244.txt"


def _ok_pages(text=None):
    return (text or OK_FIXTURE.read_text(encoding="utf-8")).split("<<PAGE>>\n")


@pytest.fixture
def okdoc():
    return parse_order_text(_ok_pages())


def test_ok_header(okdoc):
    assert okdoc.format_name == "OK"
    assert okdoc.order_no == ""                       # not on the PDF: typed by the user
    assert okdoc.store_code == ""                     # in the sender's e-mail, not the PDF
    assert okdoc.order_date == date(2026, 11, 6)      # "Order 2026/11/06 10:04"
    assert okdoc.delivery_from == okdoc.delivery_to == date(2026, 11, 9)   # "Deliver Bef"
    assert (okdoc.total_excl, okdoc.total_vat, okdoc.total_incl) == (6124.34, 688.91, 6813.25)
    assert okdoc.warnings == []


def test_ok_lines(okdoc):
    assert len(okdoc.lines) == 31
    assert round(sum(l.line_excl for l in okdoc.lines), 2) == 6124.34
    assert all(l.parse_warning is None for l in okdoc.lines)
    assert all(l.prices_excl and not l.vat_known and l.unit_incl == 0 for l in okdoc.lines)
    first = okdoc.lines[0]
    assert (first.supplier_code, first.barcode, first.qty) == ("624", "6001988000994", 2)
    assert first.description == "HYJO DAIRY SNACK BANANA 1 x 350ml (ß)"
    case = next(l for l in okdoc.lines if l.supplier_code == "L152")
    assert (case.pack_qty, case.pack_size, case.qty) == (2, 10, 20)   # 2 cases of 10
    assert case.unit_excl == pytest.approx(17.20)
    orange = next(l for l in okdoc.lines if l.supplier_code == "336")
    assert orange.unit_excl == pytest.approx(10.4175)                 # 125.01 / 12, not the list 10.42
    wrapped = next(l for l in okdoc.lines if l.supplier_code == "142")
    assert wrapped.description == "LIMPOPO FOE YOGHURT FRUIT L/FAT APRICOT 1 x" and wrapped.qty == 6
    assert next(l for l in okdoc.lines if l.supplier_code == "019").line_excl == 1261.92


def test_ok_footer_in_reading_order():
    """pdfplumber may give the footer in reading order instead of scrambled."""
    text = OK_FIXTURE.read_text(encoding="utf-8")
    a = text.index("Item Count:")
    b = text.index("Arch - Version 27.18.26051.3 Page 2 of 2")
    text = (text[:a] + "Item Count: 358.00 User entered Sub Total: 0.00 Sub Total: 6 124.34\n"
            "User entered Tax: 0.00 Tax: 688.91\nUser entered Total: 0.00 Total: 6 813.25\n"
            + text[b:])
    doc = parse_order_text(_ok_pages(text))
    assert (doc.total_excl, doc.total_incl) == (6124.34, 6813.25)


def test_ok_bonus_qty_is_flagged():
    text = OK_FIXTURE.read_text(encoding="utf-8").replace(
        "(ß) 1 2 0 OKFL10989", "(ß) 1 2 1 OKFL10989", 1)
    assert "bonus" in parse_order_text(_ok_pages(text)).lines[0].parse_warning


OK_CUSTOMER = {"account": "OKM005", "name": "OK EXPRESS BAKERS GARAGE - 2545", "dclink": 7505,
               "store_code": "02545", "corp_group": "OK", "def_tax_type_id": 1}
ZERO_RATED = {"019", "013", "005"}


@pytest.fixture
def evo_ok(monkeypatch, okdoc):
    """Our codes are Your Code without the "L" (cases mapped), 15% VAT except
    milk / maas / goat's milk; ours = the PO's excl + VAT, to the cent."""
    state = {"price": {}, "mappings": []}
    for l in okdoc.lines:
        code = l.supplier_code.lstrip("L")
        rate = 0 if code in ZERO_RATED else 15
        state["price"][code] = v._cents(round(l.unit_excl * (1 + rate / 100), 6))

    def prices(db, account, codes, as_of, warehouse_code=None):
        out = {}
        for c in codes:
            incl = state["price"][c]
            rate = 0 if c in ZERO_RATED else 15
            out[c] = schemas.CustomerPrice(stock_code=c, excl_price=round(incl / (1 + rate / 100), 2),
                                           incl_price=incl, source="Price List")
        return out

    def stock_info(db, codes):
        return {c.upper(): {"code": c, "description": f"Item {c}", "is_whse_item": True,
                            "tax_type_id": 2 if c in ZERO_RATED else 1,
                            "warehouse_codes": ["01.HOF_MAK"]}
                for c in codes if c in state["price"]}

    monkeypatch.setattr(v, "is_corporate_customer", lambda db, a: True)
    monkeypatch.setattr(v, "load_pack_fixes", lambda db, scope: state.get("pack_fixes", []))
    monkeypatch.setattr(v, "load_mappings", lambda db, scope: state["mappings"])
    monkeypatch.setattr(v, "_stock_info", stock_info)
    monkeypatch.setattr(v, "resolve_customer_prices", prices)
    monkeypatch.setattr(v, "_tax_rates", lambda db, ids: {1: 15.0, 2: 0.0})
    monkeypatch.setattr(v, "_duplicate_orders", lambda db, dc, ext: [])
    monkeypatch.setattr(v, "_customers_with_store_code", lambda db, code: [])
    monkeypatch.setattr(v, "_pending_reviews", lambda db, acc, po: [])
    return state


def _map_cases(state):
    for i, code in enumerate(("L152", "L154", "L161", "L552", "L550"), start=1):
        state["mappings"].append(ItemMapping(id=i, scope="GROUP:OK", supplier_code=code,
                                             item_code="", stock_code=code[1:]))


def test_ok_cases_need_mapping_once(evo_ok, okdoc):
    r = _run(okdoc, customer=OK_CUSTOMER)
    unmatched = sorted(l.supplier_code for l in r.lines if l.status == "UNMATCHED")
    assert unmatched == ["L152", "L154", "L161", "L550", "L552"]
    assert r.store_check == "UNVERIFIED"
    assert "e-mail" in next(i.message for i in r.issues if i.code == "STORE_UNVERIFIED")


def test_ok_po_number_must_be_typed(evo_ok, okdoc):
    _map_cases(evo_ok)
    r = _run(okdoc, customer=OK_CUSTOMER)
    assert any(i.code == "NO_PO_NUMBER" and i.blocking for i in r.issues)
    assert not r.can_create
    typed = okdoc.model_copy(update={"order_no": "ND0527113327", "order_no_typed": True})
    r = _run(typed, customer=OK_CUSTOMER)
    assert not any(i.code == "NO_PO_NUMBER" for i in r.issues)
    assert all(l.status == "OK" for l in r.lines), [(l.supplier_code, l.issues) for l in r.lines]
    assert r.totals.agrees and r.can_create, (r.totals, [i.message for i in r.issues])


def test_ok_incl_from_the_items_vat(evo_ok, okdoc):
    _map_cases(evo_ok)
    r = _run(okdoc.model_copy(update={"order_no": "X1"}), customer=OK_CUSTOMER)
    banana = _line(r, "624")
    assert banana.doc_unit_incl == 9.97 and banana.unit_incl == 9.97        # 8.67 ex + 15%
    goat = _line(r, "019")                                     # zero-rated
    assert goat.doc_unit_incl == pytest.approx(26.29) and goat.doc_vat_rate == 0
    case = _line(r, "L152")
    assert case.stock_code == "152" and case.qty == 20 and case.unit_incl == 19.78


def test_ok_ours_above_po_needs_review(evo_ok, okdoc):
    evo_ok["price"]["082"] = 11.10                             # PO 9.59 ex -> 11.0285 incl
    _map_cases(evo_ok)
    line = _line(_run(okdoc.model_copy(update={"order_no": "X1"}), customer=OK_CUSTOMER), "082")
    assert line.status == "PRICE_MISMATCH" and line.needs_review


def test_ok_missed_line_is_caught(evo_ok):
    text = OK_FIXTURE.read_text(encoding="utf-8").replace(
        "6003989000461 013 LIMPOPO MAAS", "XX 013 LIMPOPO MAAS")   # row no longer recognised
    doc = parse_order_text(_ok_pages(text)).model_copy(update={"order_no": "X1"})
    r = _run(doc, customer=OK_CUSTOMER)
    assert any(i.code == "PARSE_TOTALS" and "excl" in i.message for i in r.issues)
    assert not r.can_create


def test_ok_excl_price_matching_ours_is_not_a_1c_overcharge(evo_ok, okdoc):
    """162: PO 186.26 / 20 = 9.313 ex -> 10.70995 incl; ours 9.31 ex = R10.71.
    Same price: rounding the PO's incl DOWN to 10.70 would wrongly say ours
    is 1c above the PO."""
    evo_ok["price"]["162"] = 10.71
    _map_cases(evo_ok)
    line = _line(_run(okdoc.model_copy(update={"order_no": "X1"}), customer=OK_CUSTOMER), "162")
    assert line.doc_unit_incl == 10.71 and line.status == "OK" and not line.needs_review


def test_ok_ours_1c_above_in_excl_is_still_caught(evo_ok, okdoc):
    """626: PO 8.66 ex (R9.96); ours 8.67 ex (R9.97): really 1c above."""
    evo_ok["price"]["626"] = 9.97
    _map_cases(evo_ok)
    line = _line(_run(okdoc.model_copy(update={"order_no": "X1"}), customer=OK_CUSTOMER), "626")
    assert line.status == "PRICE_MISMATCH" and line.needs_review


# ---------------------------------------------------------------------------
# Spar
# ---------------------------------------------------------------------------

from services.order_import.parsers import DESCRIPTION_KEY_PREFIX, description_key  # noqa: E402

SPAR_FIXTURE = Path(__file__).parent / "fixtures" / "spar_144843.txt"
SPAR_ZERO_RATED = {"001", "002", "003", "005", "007"}       # milk
SPAR_CUSTOMER = {"account": "SPA279", "name": "DWARSLOOP SUPERSPAR - 60130", "dclink": 9001,
                 "store_code": "60130", "corp_group": "Spar", "def_tax_type_id": 1}


def _spar_pages(text=None):
    return (text or SPAR_FIXTURE.read_text(encoding="utf-8")).split("<<PAGE>>\n")


@pytest.fixture
def spar():
    return parse_order_text(_spar_pages())


def test_spar_header(spar):
    assert spar.format_name == "SPAR"
    assert spar.order_no == "144843"
    assert (spar.store_code, spar.store_name) == ("60130", "Dwarsloop SUPERSPAR")
    assert spar.order_date == date(2026, 11, 11)
    assert spar.delivery_from == spar.delivery_to == date(2026, 11, 13)
    assert spar.total_excl == 14583.49 and spar.total_incl is None
    assert spar.total_qty == 648                        # TOTALS 158 cases, in units
    assert spar.warnings == []


def test_spar_header_labels_side_by_side():
    """pdfplumber may give "Order Number : 144843" on one line."""
    text = SPAR_FIXTURE.read_text(encoding="utf-8").replace(
        "Order Number\nDelivery Date\n: 144843\n13/11/26\n",
        "Order Number : 144843\nOrder Date : 11/11/26\nDelivery Date : 13/11/26\n", 1)
    doc = parse_order_text(_spar_pages(text))
    assert (doc.order_no, doc.order_date, doc.delivery_from) == (
        "144843", date(2026, 11, 11), date(2026, 11, 13))


def test_spar_lines(spar):
    assert len(spar.lines) == 55
    assert round(sum(l.line_excl for l in spar.lines), 2) == 14583.49
    assert all(l.parse_warning is None for l in spar.lines)
    assert all(l.prices_excl and l.unit_incl == 0 for l in spar.lines)
    codes = [l.supplier_code for l in spar.lines if l.supplier_code]
    assert len(codes) == 26 and {"002", "003", "005", "059"} <= set(codes)   # zeros put back
    milk = next(l for l in spar.lines if l.supplier_code == "002")
    assert (milk.pack_size, milk.pack_qty, milk.qty, milk.unit_excl) == (12, 2, 24, 32.30)
    yog = next(l for l in spar.lines if l.supplier_code == "628")
    assert yog.unit_excl == pytest.approx(8.9567) and yog.line_excl == 107.48
    four_l = next(l for l in spar.lines if l.supplier_code == "001")
    assert (four_l.qty, four_l.pack_size) == (5, None)                      # pack 1


def test_spar_lines_without_a_code_are_keyed_on_description_and_size(spar):
    nocode = [l for l in spar.lines if not l.supplier_code]
    assert len(nocode) == 29
    assert all(l.item_code.startswith(DESCRIPTION_KEY_PREFIX) and len(l.item_code) <= 40
               for l in nocode)
    assert len({l.item_code for l in nocode}) == 29                         # all different
    maas = nocode[7]
    assert maas.description == "LIMPIE MAAS 2L (2LT)"
    assert maas.item_code == description_key("LIMPIE MAAS 2L", "2LT")
    # Same text in other case / spacing: same key; another size: another key.
    assert description_key("limpie  maas 2l", "2lt") == maas.item_code
    assert description_key("LIMPIE MAAS 2L", "1LT") != maas.item_code
    # The size isn't repeated when the description already ends with it.
    assert nocode[0].description == "SUPER LIMPIE TROPICAL PUNCH 500ML"


def test_spar_free_stock_and_bad_value_are_flagged():
    text = SPAR_FIXTURE.read_text(encoding="utf-8")
    free = text.replace("624 LIMP DRINK YOG BANANA HYG 350ML 6 53.7402 0,00 0,00 C 2 107.4800 N",
                        "624 LIMP DRINK YOG BANANA HYG 350ML 6 53.7402 0,00 0,00 C 2 107.4800 Y", 1)
    doc = parse_order_text(_spar_pages(free))
    assert "free stock" in next(l for l in doc.lines if l.supplier_code == "624").parse_warning
    bad = text.replace("C 5 274.9500 N", "C 5 247.9500 N", 1)
    doc = parse_order_text(_spar_pages(bad))
    assert "misread" in next(l for l in doc.lines if l.supplier_code == "001").parse_warning


@pytest.fixture
def evo_spar(monkeypatch, spar):
    """Ours = the PO's excl + VAT, to the cent (milk zero-rated). Lines
    without a code are mapped to made-up stock codes N01..N29."""
    state = {"price": {}, "mappings": [], "nocode": {}}
    n = 0
    for l in spar.lines:
        code = l.supplier_code
        if not code:
            n += 1
            code = f"N{n:02d}"
            state["nocode"][l.item_code] = code
        rate = 0 if code in SPAR_ZERO_RATED else 15
        state["price"][code] = v._cents(round(l.unit_excl * (1 + rate / 100), 6))

    def prices(db, account, codes, as_of, warehouse_code=None):
        out = {}
        for c in codes:
            incl = state["price"][c]
            rate = 0 if c in SPAR_ZERO_RATED else 15
            out[c] = schemas.CustomerPrice(stock_code=c, excl_price=round(incl / (1 + rate / 100), 2),
                                           incl_price=incl, source="Price List")
        return out

    def stock_info(db, codes):
        return {c.upper(): {"code": c, "description": f"Item {c}", "is_whse_item": True,
                            "tax_type_id": 2 if c in SPAR_ZERO_RATED else 1,
                            "warehouse_codes": ["01.HOF_MAK"]}
                for c in codes if c in state["price"]}

    monkeypatch.setattr(v, "is_corporate_customer", lambda db, a: True)
    monkeypatch.setattr(v, "load_pack_fixes", lambda db, scope: state.get("pack_fixes", []))
    monkeypatch.setattr(v, "load_mappings", lambda db, scope: state["mappings"])
    monkeypatch.setattr(v, "_stock_info", stock_info)
    monkeypatch.setattr(v, "resolve_customer_prices", prices)
    monkeypatch.setattr(v, "_tax_rates", lambda db, ids: {1: 15.0, 2: 0.0})
    monkeypatch.setattr(v, "_duplicate_orders", lambda db, dc, ext: [])
    monkeypatch.setattr(v, "_customers_with_store_code", lambda db, code: [])
    monkeypatch.setattr(v, "_pending_reviews", lambda db, acc, po: [])
    return state


def _map_nocode(state):
    for i, (key, code) in enumerate(state["nocode"].items(), start=1):
        state["mappings"].append(ItemMapping(id=i, scope="GROUP:Spar", supplier_code="",
                                             item_code=key, stock_code=code))


def _spar_run(doc):
    return _run(doc, customer=SPAR_CUSTOMER, delivery=date(2026, 11, 13))


def test_spar_lines_without_a_code_need_mapping_once(evo_spar, spar):
    r = _spar_run(spar)
    unmatched = [l for l in r.lines if l.status == "UNMATCHED"]
    assert len(unmatched) == 29 and all(not l.supplier_code for l in unmatched)
    assert "No supplier code" in unmatched[0].issues[0]
    assert r.store_check == "MATCH"
    assert not r.can_create
    # The PO's excl total is still checked: nothing was missed.
    assert not any(i.code == "PARSE_TOTALS" for i in r.issues)


def test_spar_mapped_order_can_be_created(evo_spar, spar):
    _map_nocode(evo_spar)
    r = _spar_run(spar)
    assert all(l.status == "OK" for l in r.lines), [(l.description, l.issues) for l in r.lines]
    assert r.totals.doc_incl_from_lines
    assert r.totals.doc_incl == round(sum(l.doc_line_incl for l in r.lines), 2)
    assert r.totals.agrees and r.can_create, (r.totals, [i.message for i in r.issues])
    milk = _line(r, "002")
    assert milk.doc_unit_incl == 32.30 and milk.doc_vat_rate == 0           # zero-rated
    yog = _line(r, "628")
    assert yog.doc_unit_incl == 10.30 and yog.unit_incl == 10.30            # 8.9567 ex + 15%


def test_spar_ours_above_po_needs_review(evo_spar, spar):
    _map_nocode(evo_spar)
    evo_spar["price"]["628"] = 11.10
    line = _line(_spar_run(spar), "628")
    assert line.status == "PRICE_MISMATCH" and line.needs_review


def test_spar_missed_line_is_caught(evo_spar):
    text = SPAR_FIXTURE.read_text(encoding="utf-8").replace(
        "LIMPIE MAAS 2L 2LT 12", "LIMPIE MAAS 2L 2LT twelve", 1)       # row no longer read
    doc = parse_order_text(_spar_pages(text))
    assert any("158" in w for w in doc.warnings)                           # TOTALS cases
    r = _spar_run(doc)
    assert any(i.code == "PARSE_TOTALS" and "excl" in i.message for i in r.issues)
    assert not r.can_create


# ---------------------------------------------------------------------------
# Pack sizes: the PO's vs ours (StkItem.ulIIPerCrate)
# ---------------------------------------------------------------------------

from services.order_import.models import PackFix  # noqa: E402


def _our_packs(monkeypatch, packs, default=None):
    """Give the stock stub our pack sizes ({stock code: pack})."""
    inner = v._stock_info

    def stock_info(db, codes):
        info = inner(db, codes)
        for d in info.values():
            p = packs.get(d["code"], default)
            if p is not None:
                d["pack_size"] = p
        return info
    monkeypatch.setattr(v, "_stock_info", stock_info)


def _spar_line(r, description_start):
    return next(l for l in r.lines if l.description.startswith(description_start))


def test_pack_int_reads_ulIIPerCrate_like_the_rest_of_the_app():
    assert [v.pack_int(x) for x in (None, "", " ", "0", "-3", "6", " 10 ", "6.0", "abc")] == [
        1, 1, 1, 1, 1, 6, 10, 6, 1]


def test_boxer_pack_size_is_units_over_packs(boxer):
    yog = next(l for l in boxer.lines if l.supplier_code == "620")
    assert (yog.pack_size, yog.pack_qty, yog.qty) == (10, 4, 40)
    maas = next(l for l in boxer.lines if l.supplier_code == "014")
    assert (maas.pack_size, maas.pack_qty) == (None, None)        # 1 Case: singles


def test_pack_conflict_rounds_down_to_our_crates(evo_spar, spar, monkeypatch):
    """PO: 1 x 10 = 10 units; our crate is 6: 6 go on the order, flagged."""
    _map_nocode(evo_spar)
    _our_packs(monkeypatch, {"336": 6})
    r = _spar_run(spar)
    line = _line(r, "336")
    assert line.status == "OK" and line.pack_conflict and not line.pack_blocks
    assert (line.qty, line.qty_before_pack) == (6, 10)
    assert line.our_pack_size == 6 and line.doc_pack_size == 10 and not line.off_pack
    assert line.line_incl == round(6 * line.unit_incl, 2)
    assert any("10 units rounded down to 6" in n for n in line.notes)
    assert r.totals.pack_adjust_qty == -4
    assert r.totals.pack_round_incl == pytest.approx(-round(4 * line.doc_unit_incl, 2), abs=0.011)
    assert r.totals.agrees and r.can_create, (r.totals, [i.message for i in r.issues])


def test_pack_rounding_to_nothing_blocks(evo_spar, spar, monkeypatch):
    _map_nocode(evo_spar)
    _our_packs(monkeypatch, {"336": 12})                # 10 units < one crate of 12
    r = _spar_run(spar)
    line = _line(r, "336")
    assert line.status == "PACK_MISMATCH" and line.pack_blocks and line.qty == 0
    assert "leaves nothing" in line.issues[0]
    assert not r.can_create and not r.can_submit_review


def test_pack_not_set_on_our_side_is_flagged_not_rounded(evo_spar, spar, monkeypatch):
    _map_nocode(evo_spar)
    _our_packs(monkeypatch, {"336": 1})
    r = _spar_run(spar)
    line = _line(r, "336")
    assert line.pack_conflict and line.qty == 10 and line.qty_before_pack is None
    assert any("isn't set" in n for n in line.notes) and r.can_create


def test_same_pack_is_fine(evo_spar, spar, monkeypatch):
    _map_nocode(evo_spar)
    _our_packs(monkeypatch, {"620": 6, "002": 12})
    r = _spar_run(spar)
    assert not _line(r, "620").pack_conflict and not _line(r, "620").off_pack
    assert r.can_create


def test_singles_that_dont_fill_our_crates_only_warn(evo_spar, spar, monkeypatch):
    _map_nocode(evo_spar)
    _our_packs(monkeypatch, {"001": 6})                # PO: 5 x pack 1 = 5 units
    r = _spar_run(spar)
    line = _line(r, "001")
    assert line.status == "OK" and line.off_pack and not line.pack_conflict
    assert any("off-pack" in n for n in line.notes)
    assert r.can_create


def test_price_review_with_a_rounded_pack(evo_spar, spar, monkeypatch):
    _map_nocode(evo_spar)
    evo_spar["price"]["336"] = 12.00                   # ours above the PO's 11.20
    _our_packs(monkeypatch, {"336": 6})
    r = _spar_run(spar)
    line = _line(r, "336")
    assert line.status == "PRICE_MISMATCH" and line.needs_review and line.qty == 6
    assert r.can_submit_review


def test_accepted_pack_decision_still_rounds(evo_spar, spar, monkeypatch):
    """"Both are right" from before the round-down rule changes nothing now."""
    _map_nocode(evo_spar)
    _our_packs(monkeypatch, {"336": 6})
    evo_spar["pack_fixes"] = [PackFix(id=7, scope="GROUP:Spar", supplier_code="336",
                                      item_code="", doc_pack=10, pack=10)]
    r = _spar_run(spar)
    line = _line(r, "336")
    assert line.pack_fix == "ACCEPTED" and line.qty == 6 and line.pack_conflict
    assert r.can_create


def test_corrected_their_pack_rereads_qty_and_price(evo_spar, spar, monkeypatch):
    """Spar's Vanilla Custard 2lt: pack 1 x 6 at R174.78 is really 6 cases of 6."""
    _map_nocode(evo_spar)
    custard = _spar_line(_spar_run(spar), "LIMP Hyjo DR Yog Vanilla Custard")
    stock = custard.stock_code
    evo_spar["price"][stock] = 33.50                   # 29.13 ex + 15%
    _our_packs(monkeypatch, {stock: 6})
    evo_spar["pack_fixes"] = [PackFix(id=3, scope="GROUP:Spar", supplier_code="",
                                      item_code=custard.item_code, doc_pack=1, pack=6)]
    r = _spar_run(spar)
    line = _spar_line(r, "LIMP Hyjo DR Yog Vanilla Custard")
    assert line.pack_fix == "CORRECTED" and line.doc_pack_printed == 1 and line.doc_qty_printed == 6
    assert (line.qty, line.doc_pack_size, line.doc_pack_qty) == (36, 6, 6)
    assert line.doc_unit_excl == pytest.approx(29.13) and line.doc_line_excl == 1048.68
    assert line.status == "OK" and line.unit_incl == 33.50 and not line.off_pack
    assert r.totals.pack_adjust_qty == 30
    assert r.totals.agrees and r.can_create, (r.totals, [i.message for i in r.issues])


def test_correction_stops_when_the_retailer_fixes_its_pack(evo_spar, spar, monkeypatch):
    _map_nocode(evo_spar)
    _our_packs(monkeypatch, {"336": 6})
    # Remembered for "Spar prints 12": this PO prints 10, so it doesn't apply.
    evo_spar["pack_fixes"] = [PackFix(id=7, scope="GROUP:Spar", supplier_code="336",
                                      item_code="", doc_pack=12, pack=6)]
    line = _line(_spar_run(spar), "336")
    assert line.pack_fix is None and line.pack_conflict and line.qty == 6


def test_boxer_pack_conflict(evo, boxer, monkeypatch):
    _map(evo, "08D020", "020")
    _map(evo, "12", "012")
    _our_packs(monkeypatch, {"620": 6}, default=None)
    r = _run(boxer)
    yog = _line(r, "620")                               # 4 x 10 = 40 -> 36
    assert yog.pack_conflict and yog.status == "OK" and (yog.qty, yog.qty_before_pack) == (36, 40)
    assert r.totals.pack_adjust_qty == -4
    assert r.totals.agrees, (r.totals, [i.message for i in r.issues])


class _TxDb:
    def __init__(self):
        self.committed = self.rolled_back = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _pack_fix_body(pack=6):
    return {"customer_account": "BOX07D", "supplier_code": "620", "item_code": "87593010",
            "doc_pack": 10, "pack": pack, "description": "Hyjo 350ml", "stock_code": "620"}


def test_pack_fix_endpoints_are_supervisor_only(client, monkeypatch):
    saved = []
    monkeypatch.setattr(router_mod, "upsert_pack_fix", lambda *a: saved.append(a) or 5)
    monkeypatch.setattr(router_mod, "set_our_pack", lambda *a: saved.append(a) or "6")
    assert client.post("/api/order-import/pack-fixes", json=_pack_fix_body()).status_code == 403
    r = client.post("/api/order-import/our-pack", json={"stock_code": "620", "pack": 10})
    assert r.status_code == 403
    assert client.delete("/api/order-import/pack-fixes/5").status_code == 403
    assert saved == []


def test_supervisor_corrects_their_pack(client, monkeypatch):
    db = _TxDb()
    main.app.dependency_overrides[get_db] = lambda: db
    ROLE["role"] = "SUPERVISOR"
    saved = []
    monkeypatch.setattr(router_mod, "upsert_pack_fix",
                        lambda d, scope, req, user: saved.append((scope, req.pack, user)) or 5)
    r = client.post("/api/order-import/pack-fixes", json=_pack_fix_body())
    assert r.status_code == 200, r.text
    assert r.json()["id"] == 5 and saved == [("GROUP:Boxer", 6, "tester")] and db.committed
    assert client.post("/api/order-import/pack-fixes",
                       json=_pack_fix_body(pack=2.5)).status_code == 400


def test_supervisor_sets_our_pack(client, monkeypatch):
    db = _TxDb()
    main.app.dependency_overrides[get_db] = lambda: db
    ROLE["role"] = "SUPERVISOR"
    calls = []
    monkeypatch.setattr(router_mod, "set_our_pack",
                        lambda d, req, user: calls.append((req.stock_code, req.pack, user)) or "6")
    r = client.post("/api/order-import/our-pack",
                    json={"stock_code": "620", "pack": 10, "po_number": "284529"})
    assert r.status_code == 200, r.text
    assert r.json() == {"stock_code": "620", "old_pack": "6", "new_pack": 10}
    assert calls == [("620", 10, "tester")] and db.committed
    assert client.post("/api/order-import/our-pack",
                       json={"stock_code": "620", "pack": 0}).status_code == 400

    def denied(*a):
        raise RuntimeError("The UPDATE permission was denied on the column 'ulIIPerCrate'")
    monkeypatch.setattr(router_mod, "set_our_pack", denied)
    r = client.post("/api/order-import/our-pack", json={"stock_code": "620", "pack": 10})
    assert r.status_code == 503 and "grants" in r.json()["detail"] and db.rolled_back


class _SqlDb:
    """Records the SQL; StkItem has one row for 620 (pack '6')."""

    def __init__(self):
        self.sql = []

    def execute(self, stmt, params=None):
        self.sql.append((str(stmt), params))
        db = self

        class _R:
            def mappings(self):
                return self

            def all(self):
                return [{"Code": "620", "ulIIPerCrate": "6"}] if "SELECT" in db.sql[-1][0] else []
        return _R()


def test_set_our_pack_updates_evolution_and_logs_it():
    from services.order_import.models import OurPackRequest
    db = _SqlDb()
    old = v.set_our_pack(db, OurPackRequest(stock_code="620", pack=10, po_number="284529"), "sup")
    assert old == "6"
    update = next(p for s, p in db.sql if s.startswith("UPDATE dbo.StkItem"))
    assert update == {"pack": "10", "code": "620"}                 # VARCHAR column
    audit = next(p for s, p in db.sql if "_tb_SOPackSizeAudit" in s)
    assert (audit["old"], audit["new"], audit["user"], audit["po"]) == ("6", "10", "sup", "284529")


def test_boxer_printed_pack_is_cross_checked():
    """The Size column ("10" in "10 Case") must agree with Qty (Unit) / Qty (Pack)."""
    text = FIXTURE.read_text(encoding="utf-8").replace(
        "Strawberry 350.00ml 10 Case", "Strawberry 350.00ml 12 Case", 1)
    doc = parse_order_text(text.split("<<PAGE>>\n"))
    yog = next(l for l in doc.lines if l.supplier_code == "620")
    assert yog.pack_size == 12 and "Size 12 doesn't agree" in yog.parse_warning

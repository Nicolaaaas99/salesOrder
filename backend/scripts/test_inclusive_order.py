"""Tax-mode test: does an INCLUSIVE sales order charge exactly the incl price?

Creates ONE real (unprocessed) sales order in Evolution through the same SDK
path the app uses, then reads back what Evolution stored and checks each line.
Run it once before the app switches its saves to inclusive mode.

Usage:
    cd c:\\Dev\\salesOrder\\backend
    .\\.venv\\Scripts\\python.exe scripts\\test_inclusive_order.py --yes
    .\\.venv\\Scripts\\python.exe scripts\\test_inclusive_order.py --yes ^
        --customer BOX07D --whse 01.HOF_MAK --qty 40 ^
        --item 620=11.59 --item 311=14.10 --item 161=18.40

    --item CODE=INCL   unit price INCL VAT to send (repeatable). Pick at least
                       one item whose price-list excl and incl disagree (620:
                       R10.07 excl / R11.59 incl) and one excl-captured deal.
    --mode             Inclusive (default) or Exclusive, to compare.
    --yes              required: this CREATES an order in Evolution.

Afterwards cancel the test order (Order History -> Cancel, or in Evolution).
It is marked "TAX MODE TEST - CANCEL" with external order no TAXTEST.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402

import schemas  # noqa: E402
from database import SessionLocal  # noqa: E402
from routers.sales_orders import DETAIL_HEADER_SQL, DETAIL_LINES_SQL  # noqa: E402
from services.sales_order_service import create_sales_order  # noqa: E402

USER = "tax_mode_test"


def cents(x: float) -> Decimal:
    return Decimal(str(round(x, 6))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_items(raw: list[str]) -> list[tuple[str, float]]:
    out = []
    for item in raw:
        code, _, price = item.partition("=")
        if not code.strip() or not price.strip():
            sys.exit(f"Bad --item {item!r}: expected CODE=INCL, e.g. 620=11.59")
        out.append((code.strip(), float(price)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--customer", default="BOX07D")
    ap.add_argument("--whse", default="01.HOF_MAK")
    ap.add_argument("--qty", type=float, default=40)
    ap.add_argument("--item", action="append", default=[])
    ap.add_argument("--mode", choices=["Inclusive", "Exclusive"], default="Inclusive")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    items = parse_items(args.item or ["620=11.59", "311=14.10"])
    if not args.yes:
        sys.exit("This creates a real sales order in Evolution. Re-run with --yes.")

    payload = schemas.SalesOrderCreate(
        customer_account=args.customer,
        order_date=date.today(),
        due_date=date.today(),
        invoice_date=date.today(),
        delivery_date=date.today(),
        external_order="TAXTEST",
        tax_mode=args.mode,
        description="TAX MODE TEST - CANCEL",
        lines=[
            schemas.SalesOrderLineInput(
                stock_code=code, warehouse_code=args.whse,
                quantity=args.qty, unit_price=incl,
            )
            for code, incl in items
        ],
    )
    print(f"Creating a {args.mode} test order for {args.customer} ({len(items)} lines)...")
    result = create_sales_order(payload, agent_username=USER)
    print(f"Created order {result.order_no}\n")

    db = SessionLocal()
    try:
        header = db.execute(text(DETAIL_HEADER_SQL), {"order_no": result.order_no}).mappings().first()
        if not header:
            sys.exit(f"Order {result.order_no} not found when reading back.")
        lines = db.execute(text(DETAIL_LINES_SQL), {"auto_index": header["auto_index"]}).mappings().all()
        totals = db.execute(
            text("SELECT InvTotExcl, InvTotTax, InvTotIncl FROM InvNum WHERE AutoIndex = :a"),
            {"a": header["auto_index"]},
        ).mappings().first()
    finally:
        db.close()

    print(f"Order {result.order_no}: TaxInclusive = {header['tax_inclusive']}")
    print(f"{'Item':<8}{'Sent':>9}{'UnitExcl':>11}{'UnitIncl':>10}"
          f"{'LineExcl':>11}{'LineTax':>10}{'LineIncl':>11}{'Expected':>11}  Result")
    sent = dict(items)
    all_ok = True
    for r in lines:
        code = r["stock_code"]
        price = sent.get(code, 0.0)
        qty = float(r["quantity"] or 0)
        line_incl = float(r["line_total_incl"] or 0)
        unit_incl = float(r["unit_price_incl"] or 0)
        if args.mode == "Inclusive":
            expected = cents(qty * price)
            ok = abs(unit_incl - price) < 0.005 and abs(cents(line_incl) - expected) < Decimal("0.005")
            exp_txt, res_txt = f"{float(expected):.2f}", ("OK" if ok else "DIFFERENT")
        else:
            ok = True  # Exclusive run is for comparison only
            exp_txt, res_txt = "-", ""
        all_ok &= ok
        print(f"{code:<8}{price:>9.4f}{float(r['unit_price_excl'] or 0):>11.4f}{unit_incl:>10.4f}"
              f"{float(r['line_total_excl'] or 0):>11.2f}{float(r['line_total_tax'] or 0):>10.2f}"
              f"{line_incl:>11.2f}{exp_txt:>11}  {res_txt}")

    if totals:
        print(f"\nOrder totals: excl {float(totals['InvTotExcl'] or 0):.2f}, "
              f"VAT {float(totals['InvTotTax'] or 0):.2f}, incl {float(totals['InvTotIncl'] or 0):.2f}")
    if args.mode == "Inclusive":
        print("\nPASS: Evolution charges exactly the incl prices sent." if all_ok
              else "\nCHECK: some lines differ from qty x incl. Send this output back.")
    print(f"\nRemember to cancel test order {result.order_no}.")


if __name__ == "__main__":
    main()

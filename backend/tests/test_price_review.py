"""Price Review: PO-difference status uses the import's own thresholds."""
import pytest

from routers.price_review import difference_status


@pytest.mark.parametrize("diff_now, status", [
    (None, "PROBLEM"),      # no price for the customer today
    (0.0, "FIXED"),
    (0.004, "FIXED"),
    (0.01, "TOLERANCE"),    # PO 1c above ours: charged ours automatically
    (0.05, "TOLERANCE"),    # exactly 5c is allowed
    (0.06, "PROBLEM"),
    (-0.01, "PROBLEM"),     # ours above the PO: claim risk
    (-0.89, "PROBLEM"),
])
def test_difference_status(diff_now, status):
    assert difference_status(diff_now) == status


def test_group_name_from_scope():
    from routers.price_review import _group_name
    assert _group_name("GROUP:Boxer") == "Boxer"
    assert _group_name("ACCOUNT:CASH01") == "Non Corporate"
    assert _group_name("") == "Non Corporate"


def _row(**kw):
    base = dict(
        idPriceDecision=7, cCustomerAccount="BOX07D ", cPONumber="284529",
        cOrderNum="01.HOFSO894332", cStockCode="321", Description="Yoghurt",
        cWhseCode="01.HOF_MAK", PriceList="Boxer", cPriceSource="Price List",
        fQty=12.0, fEvoIncl=14.99, fPOIncl=14.10, fChargedIncl=14.10,
        cChoice="PO", bAuto=0, ArchivedAt=None, cArchiveStatus=None,
        cArchivedBy=None, fOursAtArchive=None, cSourceAtArchive=None, cArchiveNote=None,
    )
    base.update(kw)
    return base


def test_open_line_is_a_problem_until_repriced():
    from routers.price_review import _line
    l = _line(_row())
    assert l.status == "PROBLEM" and not l.archived
    assert l.customer_account == "BOX07D"
    assert l.charged == "PO" and l.charged_price == 14.10


def test_archived_line_keeps_ours_at_archive():
    from routers.price_review import _line
    l = _line(_row(ArchivedAt="2026-11-10 09:00", cArchiveStatus="TOLERANCE",
                   cArchivedBy="system", fOursAtArchive=14.09,
                   cSourceAtArchive="Price List", cChoice="EVOLUTION", bAuto=1))
    assert l.archived and l.status == "TOLERANCE"
    assert l.our_price_now == 14.09 and l.diff_now == 0.01
    assert l.charged == "OURS" and l.charged_auto


def test_dismissed_line_has_no_price_now():
    from routers.price_review import _line
    l = _line(_row(ArchivedAt="2026-11-10 09:00", cArchiveStatus="DISMISSED",
                   cArchiveNote="Customer error"))
    assert l.status == "DISMISSED" and l.archive_note == "Customer error"
    assert l.our_price_now is None and l.diff_now is None


@pytest.mark.parametrize("diff_now, auto, status", [
    (0.01, False, "FIXED"),      # was a problem, fixed to 1c under the PO
    (0.0, False, "FIXED"),       # was a problem, fixed to match exactly
    (0.01, True, "TOLERANCE"),   # was already within 5c at import
    (0.0, True, "TOLERANCE"),
    (0.08, False, "PROBLEM"),
    (-0.89, True, "PROBLEM"),    # an auto line can become a problem again
    (None, False, "PROBLEM"),
])
def test_line_status_splits_settled_by_import(diff_now, auto, status):
    from routers.price_review import line_status
    assert line_status(diff_now, auto) == status


# ---------------------------------------------------------------------------
# Dismiss: a supervisor chooses not to fix a difference, with a reason
# ---------------------------------------------------------------------------

class _DismissDb:
    def __init__(self, rowcount=1):
        self.rowcount, self.params, self.committed = rowcount, [], False

    def execute(self, stmt, params=None):
        self.params.append(params)
        rc = self.rowcount

        class _R:
            rowcount = rc

            def mappings(self_inner):
                return self_inner

            def first(self_inner):
                return _row(ArchivedAt="2026-11-10 10:00", cArchiveStatus="DISMISSED",
                            cArchivedBy="Sup", cArchiveNote="Customer error")
        return _R()

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


def test_dismiss_needs_a_reason():
    from fastapi import HTTPException
    from routers.price_review import DismissRequest, dismiss_line
    with pytest.raises(HTTPException) as e:
        dismiss_line(7, DismissRequest(reason="  "), db=_DismissDb(), username="Sup")
    assert e.value.status_code == 400


def test_dismiss_records_who_and_why():
    from routers.price_review import DismissRequest, dismiss_line
    db = _DismissDb()
    line = dismiss_line(7, DismissRequest(reason="  Customer   error "), db=db, username="Sup")
    assert db.committed
    assert db.params[0] == {"id": 7, "user": "Sup", "reason": "Customer error"}
    assert line.status == "DISMISSED" and line.archive_note == "Customer error"


def test_dismiss_a_line_that_is_no_longer_open():
    from fastapi import HTTPException
    from routers.price_review import DismissRequest, dismiss_line
    with pytest.raises(HTTPException) as e:
        dismiss_line(7, DismissRequest(reason="Customer error"), db=_DismissDb(rowcount=0),
                     username="Sup")
    assert e.value.status_code == 409

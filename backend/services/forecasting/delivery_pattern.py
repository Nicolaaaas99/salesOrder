"""Learn customer-level day-of-week delivery mix.

Given a sequence of (delivery_date, qty) observations, produces a
7-tuple of day-share weights that sum to 1.0. The target cascade uses
this to split a weekly forecast into daily suggestions in proportion to
the customer's actual delivery cadence.

Behaviour:
  - A customer that only takes delivery on Mon/Wed/Fri gets a vector
    like (Mon=0.4, Wed=0.3, Fri=0.3, others=0). Based on recent history.
  - Low-sample customers (< DELIVERY_PATTERN_MED_WEEKS) land on
    confidence LOW; the engine uses an even split across days the
    customer did deliver on, rather than extrapolating from 2 weeks.
  - Zero observations -> cannot learn; caller must handle (even split
    across business days as a default).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from . import config
from . import weeks


# weekday() values: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
_WEEKDAY_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class DeliveryPattern:
    mon_share: float
    tue_share: float
    wed_share: float
    thu_share: float
    fri_share: float
    sat_share: float
    sun_share: float
    weeks_sampled: int
    confidence: str              # LOW | MED | HIGH

    def share_for(self, d: date) -> float:
        """Return the share allocated to the day-of-week of `d`."""
        idx = d.weekday()  # 0..6 Mon..Sun
        return (
            self.mon_share, self.tue_share, self.wed_share,
            self.thu_share, self.fri_share, self.sat_share, self.sun_share,
        )[idx]


def _confidence(weeks_sampled: int) -> str:
    if weeks_sampled >= config.DELIVERY_PATTERN_HIGH_WEEKS:
        return "HIGH"
    if weeks_sampled >= config.DELIVERY_PATTERN_MED_WEEKS:
        return "MED"
    return "LOW"


def learn(
    deliveries: Iterable[tuple[date, float]],
    as_of: date,
    lookback_weeks: int = config.DELIVERY_PATTERN_LOOKBACK_WEEKS,
) -> DeliveryPattern:
    """Compute delivery day-share weights from historical deliveries.

    Args:
        deliveries: iterable of (delivery_date, qty). qty must be > 0 for
                    the delivery to count -- a zero-qty row is a cancelled
                    or empty line and should not shift the pattern.
        as_of: anchor date. Deliveries older than `lookback_weeks` weeks
               before `as_of`'s week are ignored.
        lookback_weeks: how far to look back.

    Returns:
        A normalised DeliveryPattern. Shares sum to 1.0 (or all zeros if
        no deliveries fell in the window -- caller must handle that
        edge case).
    """
    window_start = weeks.week_start(as_of) - timedelta(days=7 * lookback_weeks)
    # Totals per weekday
    totals = [0.0] * 7
    weeks_seen: set[date] = set()

    for d, qty in deliveries:
        if qty <= 0:
            continue
        if d < window_start:
            continue
        totals[d.weekday()] += qty
        weeks_seen.add(weeks.week_start(d))

    total = sum(totals)
    if total == 0:
        return DeliveryPattern(
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
            weeks_sampled=0,
            confidence="LOW",
        )

    shares = [t / total for t in totals]

    return DeliveryPattern(
        mon_share=round(shares[0], 4),
        tue_share=round(shares[1], 4),
        wed_share=round(shares[2], 4),
        thu_share=round(shares[3], 4),
        fri_share=round(shares[4], 4),
        sat_share=round(shares[5], 4),
        sun_share=round(shares[6], 4),
        weeks_sampled=len(weeks_seen),
        confidence=_confidence(len(weeks_seen)),
    )

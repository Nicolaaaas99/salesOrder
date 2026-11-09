# Forecasting & Targets: how it works, worked by hand

This guide explains what the forecasting module does, how to use it, and how to check that it's right.

Every calculation is worked by hand. The script below runs the **real code** on exactly the same numbers, so you can check each answer:

```powershell
cd c:\Dev\salesOrder\backend
.\.venv\Scripts\python.exe scripts\learn_forecasting_examples.py        # all 17 examples
.\.venv\Scripts\python.exe scripts\learn_forecasting_examples.py 11     # just example 11
```

Each example prints a `CHECK … hand = … code = … OK` line. The script touches no database. If a line ever says `MISMATCH`, the code no longer does what this guide says.

---

## Part 1: The big idea

### Four numbers, kept separate

For every **customer × product × week** the system keeps four different numbers:

| Number | Question it answers | Who decides it | Where it lives |
|---|---|---|---|
| **Forecast** | What will this customer probably buy? | The models (maths) | `_tb_SOForecast` |
| **Target** | What do we *want* them to buy? | A manager, through a scenario | `_tb_SOScenarioTarget` |
| **Suggestion** | What should the rep put on *this* order? | The Order Assistant (target or forecast, minus what's already ordered) | `_tb_SOOrderSuggestion` |
| **Actual** | What was really delivered? | Evolution invoices (PostST) | PostST |

They're kept apart on purpose. That way you can tell *why* a week went wrong:
- the forecast was off → the model needs work
- the target was too ambitious → the scenario was too aggressive
- the rep didn't order what was suggested → it's a sales-execution issue
- we couldn't deliver → it's a stock issue

### The weekly loop

```
 invoices (PostST)
      │
      ▼
 1. FORECAST  ── models look at each customer×product's history ──► forecast per week
      │
      ▼
 2. SCENARIO  ── manager: "+5% Milk at Polokwane for November" ──► preview
      │           system flags anything that looks unreasonable
      ▼
 3. REVIEW    ── manager accepts or excludes each flagged item ──► approve
      │
      ▼
 4. TARGET    ── written per customer×product×week (manager can override one)
      │
      ▼
 5. REP       ── Order Assistant shows 🎯 suggestion = target − already ordered
      │           rep orders; their decision (accepted / changed / ignored) is saved
      ▼
 6. WORKBENCH ── manager watches forecast vs target vs ordered vs delivered
```

**Not automatic yet (that's Phase 6):** step 1 only runs when you start the forecast smoke-test script. There's no nightly job yet.

### Words you'll see

| Word | Meaning |
|---|---|
| **Leaf** | One customer × one product (e.g. SPA227 × 323). A **leaf-week** adds a week. |
| **Week** | ISO week, Monday to Sunday. Every week is named by its Monday's date (and shown as e.g. "Wk 46"). |
| **W0, W-1, W+1** | This week, last week, next week. |
| **Pack** | `ulIIPerCrate`. Orders come in whole packs. |
| **True demand** | Delivered + whatever we failed to deliver (short deliveries). Models learn from this, so a stockout doesn't teach them "the customer wants less". |
| **Fallback** | The forecast to use when a model can't produce one (too little history). |

---

## Part 2: One example customer, start to finish

All the examples use two fictional customers buying **Milk 2L (pack of 6)**. "Today" is **Tuesday 3 Nov 2026**. Your real invoice history starts on **Saturday 1 Aug 2026**.

**STEADY** buys most weeks. Weeks W-13 … W-1 (oldest first):

```
40  38  42  40  36  40  0  44  40  38  42  41  40
```

**OCCASIONAL** buys every few weeks:

```
60   0   0  55   0   0  0  65   0   0  60   0   0
```

---

## Part 3: The models, by hand

### Example 1: Weeks run Monday to Sunday

Today is Tuesday 3 Nov, so this week (W0) starts **Monday 2 Nov**. Next week (W+1) is 9 Nov, and W-13 is 3 Aug.

Invoices start on Saturday 1 Aug. That's part of the week of Mon 27 Jul, which only has two days of data, so the first **full** week is **3 Aug**. Counting 3 Aug … 26 Oct gives **13 weeks of real data**.

> The system only counts weeks it actually has data for. Weeks before 1 Aug are *unknown*, not "bought nothing". Counting them as zeros used to make steady buyers look occasional; that was fixed.

### Example 2: How "lumpy" are the order sizes? (CV²)

CV² measures how much order **sizes** vary. Ignore the zero weeks, then compute:

> CV² = variance ÷ mean²   (variance = average of (value − mean)²)

- Sizes `10, 30, 10, 30`: mean 20. The deviations are −10, +10, −10, +10, so their squares are 100 each. Variance = 400 ÷ 4 = 100. **CV² = 100 ÷ 20² = 0.25**: fairly even sizes.
- Sizes `5, 40, 5, 30`: mean 20. The deviations are −15, +20, −15, +10, so the squares are 225, 400, 225, 100. Variance = 950 ÷ 4 = 237.5. **CV² = 237.5 ÷ 400 = 0.59**: very uneven.

The cut-off is **0.49**.

### Example 3: Classify STEADY (ADI and CV² together)

ADI measures how **often** the customer buys:

> ADI = weeks observed ÷ weeks with a sale

STEADY bought in 12 of 13 weeks, so **ADI = 13 ÷ 12 = 1.08**. Its sizes (36–44) barely vary: **CV² ≈ 0.0025**.

The classifier puts every leaf into one of four boxes (the Syntetos–Boylan quadrants):

|  | sizes even (CV² < 0.49) | sizes uneven (CV² ≥ 0.49) |
|---|---|---|
| **buys often** (ADI < 1.32) | **SMOOTH** → base model | **ERRATIC** → seasonal model* |
| **buys rarely** (ADI ≥ 1.32) | **INTERMITTENT** → Croston* | **LUMPY** → Croston* |

\* These models aren't switched on yet. See Example 7 for what happens instead.

STEADY is **SMOOTH**, so its recommended model is the **base model**. A leaf also needs **12+ weeks with sales** before the system trusts a model fitted to it alone. STEADY has 12, so no fallback is needed.

There's one extra rule: with fewer than 2 weeks with sales, or fewer than 4 weeks observed, the leaf is **INSUFFICIENT**.

### Example 4: Classify OCCASIONAL

OCCASIONAL bought in 4 of 13 weeks, so **ADI = 13 ÷ 4 = 3.25**. Its sizes (55–65) are even. That makes it **INTERMITTENT**, and the recommended model is Croston, which isn't switched on.

It has only 4 weeks with sales (fewer than 12), so the fallback reason is **SPARSE_HISTORY**.

(Under the old bug, which divided 52 weeks by 4, ADI was 13.)

### Example 5: The base model (what reps have always seen)

> **Forecast = median of the weeks with sales among the last 6 *completed* weeks, rounded half-up.**

For STEADY, the last 6 completed weeks (W-6 … W-1) are `44 40 38 42 41 40`. Sorted: `38 40 40 41 42 44`. With an even count, the median is the average of the middle two: (40 + 41) ÷ 2 = 40.5. Rounded half-up, that's **41**. Rounding half-up means .5 always goes up; this isn't "banker's" rounding, which would round 40.5 to 40.

The same **41** applies to every week of the 4-week horizon (W+1 … W+4). The base model gives a flat level, not a trend.

> Working this example by hand found a bug, which is now fixed. The engine slid the 6-week window forward with the week it was forecasting, so next week's forecast lost a real week (it gave 40, not 41) and forecasts further out gave up. The forecast smoke test now writes 800 forecasts for 200 leaves (4 weeks each); it used to write 770.

The model needs **at least 3 weeks with sales** in the 6 weeks.

### Example 6: The base model gives up on OCCASIONAL

OCCASIONAL's last 6 weeks are `65 0 0 60 0 0`. That's only **2** weeks with sales, fewer than 3, so the base model returns nothing.

### Example 7: Fallback: the customer's own rate

The leaf still needs a forecast. The recommended model (Croston) is off, so the base model carries the fallback. **If the customer has any sales of their own**, the fallback is their average per week, empty weeks included:

> (60 + 55 + 65 + 60) ÷ 13 weeks = 240 ÷ 13 = **18.46 per week**

That looks small next to orders of 60, but they order about every 3 weeks: 60 ÷ 3 ≈ 20 per week. Over a month it adds up correctly.

It's marked `bIsFallback = 1`, with grade **D**.

**Confidence grades:**

| | own model fitted | fallback |
|---|---|---|
| SMOOTH | A | C |
| ERRATIC | B | D |
| INTERMITTENT | C | D |
| LUMPY | D | D |
| INSUFFICIENT | F | F |

### Example 8: Fallback: peer average × customer size (no own sales)

This is only used when a customer has **no** sales of the product in the window. It needs two numbers:

1. **Peer average**: the product's average weekly quantity across every customer-week where it sold. For example, `20, 20, 30, 10, 20` gives an average of **20**.
2. **Customer size**: this customer's total in the product's category, divided by the *median* customer's total. For example, totals `300, 600, 600, 1200, 2000` have a median of 600. Our customer bought 1200, so the size is 1200 ÷ 600 = **2.0**.

> Fallback = 20 × 2.0 = **40 per week**

In practice this rule rarely runs today. A leaf only gets forecast if the customer bought the product in the last 52 weeks, so it always has some own sales.

### Example 9: Seasonal model (built, not switched on)

> Forecast = same week last year × (last 16 weeks ÷ the same 16 weeks a year earlier)

For example, same week last year = 100. The last 16 weeks totalled 1,760, against 1,600 a year earlier, so the trend is 1,760 ÷ 1,600 = 1.10. The forecast is 100 × 1.10 = **110**.

It needs a full year of history, so it stays off until you have one. Models never switch themselves on. See the Model Leaderboard's promotion rules.

### Example 10: Delivery-day pattern (recorded, not used yet)

For each customer, the system records how their deliveries split across weekdays. For example, 10 weeks of Mon 40 / Wed 30 / Fri 30 gives shares of **Mon 0.4, Wed 0.3, Fri 0.3**. Ten weeks of data earns confidence MED: 8+ weeks is MED and 20+ is HIGH.

You decided suggestions use "the remaining week on this order" rather than splitting by day (decision #8), so these shares are stored for later but don't affect anything yet.

---

## Part 4: Scenarios and flags, by hand

A **scenario** says: *for these customers / products / routes / DCs, over these weeks, change the target like this.*

### Example 11: +10% on STEADY, and why it gets flagged

Forecast 41 × 1.10 = **target 45.1**.

The system then runs four **reasonableness checks**. The first is: **is the target above the customer's usual maximum (P95)?** Take their weeks with sales (zeros ignored), sorted:

```
36  38  38  40  40  40  40  40  41  42  42  44      (12 values, positions 0–11)
```

P95 sits at position 0.95 × (12 − 1) = **10.45**, which falls between position 10 (42) and position 11 (44):

> P95 = 42 + 0.45 × (44 − 42) = **42.9**

The target 45.1 is above 42.9, so the leaf is flagged **"Above usual max (P95)"**. Other levers:

| Lever | Target | Flagged? |
|---|---|---|
| +10% | 45.1 | yes |
| +5% | 43.05 | yes (still above 42.9) |
| +4% | 42.64 | no |

> **Takeaway:** on a very steady customer, almost *any* increase gets flagged, because they've never bought more. That's intended: it asks a manager to confirm a stretch target. If it proves too strict, the threshold lives in `config.py`.

### Example 12: Spreading a fixed amount ("Add per week" / "Weekly total")

Three customers are in scope, with forecasts A = 60, B = 30, C = 10 (total 100). Their shares are 0.6, 0.3 and 0.1.

- **Add per week +30**: A gets +18, B +9, C +3, giving targets **78, 39, 13**.
- **Weekly total 150**: A 90, B 45, C 15, giving targets **90, 45, 15**.

The amount is split in proportion to each customer's forecast, separately for each week.

### Example 13: A target inside another target

A target can be set on any level. When one sits inside another, **the inner one is fixed and the outer total still holds**: the rest of the outer node shares what's left, in proportion to forecast.

Makhado has three customers with next week's forecasts **A 200, B 500, C 300** (total 1 000).

- Scenario #1: Makhado weekly total = **1 200**. On its own: A 240, B 600, C 360.
- Scenario #2: customer A weekly total = **300**. A is now fixed at 300, so B and C share 1 200 − 300 = **900** by forecast (500 : 300): B = 900 × 500/800 = **562.5**, C = 900 × 300/800 = **337.5**. Makhado still adds up to 1 200.

It doesn't matter which scenario was created first. If the targets inside a node add up to more than the node's own target (say A = 1 300 inside Makhado = 1 200), that's a **conflict** and the scenario can't be approved until one of them changes.

### Example 14: The other three checks

| Check | Example | Result |
|---|---|---|
| **More than 25% above the same week last year** | target 130 vs 100 last year = +30% | flagged |
| **New product**: first invoiced < 13 weeks ago, and the scenario changes it | first invoice 9 Sep = 7.6 weeks ago | flagged |
| (same check) | first invoice 3 Aug, within 4 weeks of records starting, so "was already selling" | not flagged |
| **Below one pack** | target 4, pack of 6 | flagged |

**Approving.** Every flagged customer × product needs a decision: **Accept** (keep the target) or **Exclude** (leave it out of this scenario; it keeps its forecast). Approval is refused until every flagged leaf has one. Each decision is recorded in the audit log.

Two more rules:
- **Override:** after approval, a manager can override one customer-product's target (with a reason). Like any inner target it's fixed, and the rest of the node rebalances around it.
- **Lock:** weeks that have already started are locked, and changing them needs an extra "break lock" tick.

---

## Part 5: What the rep sees, by hand

### Example 15: From a weekly number to the order quantity

> **Suggested = (weekly number − already ordered or invoiced this week), in whole packs**

The weekly number is the **target** if there is one, otherwise the usual weekly median. Targets round **up** to a full pack (so the target is met); normal suggestions round to the **nearest** pack.

| Situation | Sum | Packs of 6 | Suggested |
|---|---|---|---|
| no target, median 41, 12 already on order | 41 − 12 = 29 | 4.83 → nearest 5 | **30** |
| target 45.1, nothing ordered yet | 45.1 | 7.52 → up 8 | **48** |
| target 45.1, 15 already covered | 30.1 | 5.02 → up 6 | **36** |
| same with nearest rounding (the old rule) | 30.1 | 5.02 → 5 | 30, which would miss the target |
| target 45.1, Monday's order already took 36 | 9.1 | 1.52 → up 2 | **12** |

On screen, a target-driven suggestion shows **🎯** in the Sug column. Hovering shows *forecast → target (+%, which scenario) → already ordered → suggested now*. The **"This week's targets"** strip above the draft lines shows *ordered so far / target* per product, and updates as the rep types.

### Example 16: What gets recorded when the order is placed

Every suggestion shown is saved. When the rep places the order, each gets a decision:

| Shown | Ordered | Decision |
|---|---|---|
| Milk 2L: 36 | 36 | **ACCEPTED** |
| Maas 1L: 12 | 18 | **MODIFIED** |
| Juice: 6 | — | **REJECTED** |
| (not suggested) Yoghurt | 12 | **MANUAL_ADD** |

This is how you'll later measure whether reps follow suggestions, separately from whether the forecasts were good.

---

## Part 6: The Target Workbench, by hand

### Example 17

For one week, the Workbench rolls up every customer × product that has a forecast or a target:

- **Target** = the target if there is one, otherwise the forecast (no ask means "expect the forecast").
- **Committed** = delivered this week + open orders delivering this week.

STEADY (target 45.1, 36 on order) plus OCCASIONAL (forecast 18.46, no target):

> Target total = 45.1 + 18.46 = **63.56**
> Committed = 36 + 0 = **36**, which is 57% of target

You click down **Company → DC → Route → Customer → Product**. Product rows with a target have a ✏️ button to override. Use **Units**, not Litres, when overriding.

---

## Part 7: How to test it yourself

### Level 1: Check the maths (2 minutes, no database)

```powershell
cd c:\Dev\salesOrder\backend
.\.venv\Scripts\python.exe scripts\learn_forecasting_examples.py     # expect: 40 of 40 checks match
.\.venv\Scripts\python.exe -m pytest tests/forecasting -q             # expect: 213 passed
```

### Level 2: Check against your database (scripts clean up after themselves)

| Script | What it proves | Expect |
|---|---|---|
| `scripts\run_forecast_smoke_test.py --limit 200` | Forecasts get created | `0 all models declined`, 800 forecasts |
| `scripts\run_scenario_smoke_test.py --dc Makhado --category Juice --approve` | Scenarios: preview, flags, approve, cancel | targets merged, then deleted |
| `scripts\run_suggestion_smoke_test.py --customer SPA227 --sku 323 --as-of 2026-11-09` | Target → 🎯 suggestion → recorded decisions | target 12, pack 10 → **suggested 20**, `RESULT: PASS` |

### Level 3: Use the screens (as a SUPERVISOR or ADMIN)

This uses one real leaf, so you can work out every number before you look. Pick a customer × product that has a forecast for next week. In SSMS:

```sql
SELECT TOP 20 cCustomerAccount, cStockCode, dWeekStart, fPredictedQty, cDemandPattern, cConfidenceGrade, bIsFallback
  FROM [SOAssistant].dbo._tb_SOForecast
 WHERE bIsActive = 1 AND dWeekStart = '2026-11-09'
 ORDER BY fPredictedQty DESC;
```

Then:

1. **Analytics → Scenarios → New scenario.** Set Customer = your customer, Product = your product, **% change** = 20, period = 9 Nov to 15 Nov. Save draft, then **Preview**.
   *Work it out first:* target = forecast × 1.2. Will it be flagged? Look at that customer's weekly history for the product (P95, Example 11).
2. Accept the flag (if any) → **Approve**. *Expect:* "1 targets written".
3. **Analytics → Target Workbench**, week of 9 Nov (Wk 46). Click down to your customer, then your product. *Expect:* forecast and target as you calculated, and a ✏️ button.
4. Try an override, e.g. 25 with a reason. The row should show 25 and "overridden". Then **Clear override**: it should go back to forecast × 1.2.
5. **Order Assistant:** log in with a session date in that week (e.g. Mon 9 Nov) and open the customer. *Expect:* 🎯 on the product. Suggested = (target − already ordered this week), rounded **up** to a pack. Hover to see the chain, and check the strip.
6. *(Optional, creates a real order in Evolution.)* Place the order. Then in SSMS:
   ```sql
   SELECT cStockCode, fSuggestedQty, fAcceptedQty, cDecision, cOrderNum
     FROM [SOAssistant].dbo._tb_SOOrderSuggestion
    WHERE cCustomerAccount = '<customer>' ORDER BY idOrderSuggestion DESC;
   ```
7. **Clean up:** Scenarios → open your scenario → **Cancel scenario** (with a reason). The target disappears from the Workbench and the 🎯 disappears from the Order Assistant.

If any number differs from what you worked out, note the customer, product and week. That's exactly the information needed to track it down.

### Level 4: Look under the hood (SSMS)

```sql
-- How a leaf was classified
SELECT * FROM [SOAssistant].dbo._tb_SODemandClassification WHERE cCustomerAccount = 'SPA227';
-- Its active forecasts
SELECT * FROM [SOAssistant].dbo._tb_SOForecast WHERE cCustomerAccount = 'SPA227' AND bIsActive = 1;
-- Targets and why they were flagged
SELECT * FROM [SOAssistant].dbo._tb_SOScenarioTarget WHERE cCustomerAccount = 'SPA227';
-- Who did what (create / approve with each flag decision / cancel / override)
SELECT TOP 50 * FROM [SOAssistant].dbo._tb_SOForecastAudit ORDER BY idForecastAudit DESC;
```

---

## Part 8: What it doesn't do yet

- **Forecasts aren't automatic.** They only exist for leaves the smoke test has processed. The Phase 6 nightly job will forecast every leaf. Until then, much of the Workbench will be empty.
- **Targets only cover leaves with a forecast**, meaning customer × product pairs bought in the last 52 weeks. You can't yet set a target for a product a customer has never bought.
- **The Model Leaderboard has no accuracy figures** until Phase 6 scores forecasts against actuals.
- **The Opportunity Inbox** only has the two test tasks until Phase 6's weekly detector creates real ones.
- **Only the base model is switched on.** The seasonal model needs a year of history, and Croston isn't built yet.
- **The year-over-year check can't fire** until you have a year of invoices.

**Where the settings live:** every threshold in this guide (6 weeks, 3 sales, 12 weeks, 1.32, 0.49, P95, 25%, 13 weeks, 4 weeks, 1 pack) is in `backend/services/forecasting/config.py`.

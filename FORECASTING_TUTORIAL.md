# Forecasting: a step-by-step tutorial

**Go slowly.** Do one lesson per sitting, with pen and paper. Each lesson has a worked example (followed step by step), an exercise (your turn), and the answers, hidden until you click.

When you've done an exercise, run the checker. It shows the **real software's** answer next to yours:

```powershell
cd c:\Dev\salesOrder\backend
.\.venv\Scripts\python.exe scripts\tutorial_check.py 2      # checks lesson 2 (any number 1–10)
```

It touches no data. Every line should end in `OK`.

- **Part 0** is a map: what exists and what it changes.
- **Part 1** is the maths: 10 small lessons.
- **Part 2** is the software: a click-by-click walkthrough on one real customer.
- **Part 3** is a one-page cheat sheet.

---

# Part 0: The map (read this first)

### Nothing has gone live

All the forecasting work exists **only on this computer**. None of it has been committed or deployed, so the people using the app today don't have any of it.

### What was built, in plain words

| Piece | What it does | Who sees it | Does anything by itself? |
|---|---|---|---|
| **Forecast engine** | Works out "what will this customer probably buy of this product next week" | Nobody directly | **No.** It only runs when you run `run_forecast_smoke_test.py` (the nightly job is a later phase). |
| **Scenarios** | A manager says "+20% Cranberry for this customer next week" | Supervisors and admins | **No.** It sits as a draft until a manager approves it. |
| **Targets** | The result of an approved scenario: one number per customer × product × week | Supervisors (Workbench), reps (🎯) | Only exist after approval. |
| **Order Assistant changes** | Shows 🎯 and a "This week's targets" strip | Reps | **Only** where an approved target exists. Otherwise it looks and calculates exactly as before. It also quietly records which suggestions were shown and what the rep did. |
| **4 management screens** | Workbench, Scenarios, Inbox, Leaderboard | Supervisors and admins (Analytics tab) | No, they're views. |

### Your off-switches

1. **Don't approve scenarios** (or cancel active ones). Reps then see nothing new.
2. **Hide the four management screens:** set `FORECASTING_UI_ENABLED = false` in `frontend/src/featureFlags.ts`.
3. **Nothing ships** until you decide to commit and deploy it.

---

# Part 1: The maths in 10 small lessons

### Lesson 1: Weeks run Monday to Sunday

**In one sentence:** every week runs Monday to Sunday and is named after its Monday; "this week" is the Monday on or before today.

**Worked example:** Wednesday 4 Nov 2026. Count back to the Monday: Tue 3, **Mon 2 Nov**. So it belongs to the week of 2 Nov (Wk 45).

**Your turn:** which week do these belong to?
a) Sat 7 Nov  b) Sun 8 Nov  c) Mon 9 Nov

<details><summary>Answers</summary>

a) 2 Nov  b) 2 Nov (a Sunday is the last day of its week)  c) 9 Nov
</details>

**In the software:** the Workbench shows the week as e.g. "Wk 46 · 9–15 Nov". A target belongs to one week.

---

### Lesson 2: The usual weekly quantity (the "base forecast")

**In one sentence:** look at the last 6 **finished** weeks, ignore the weeks with no sale, and take the middle value (the median).

**Rules:**
- You need **at least 3** weeks with a sale, otherwise there's no answer.
- With an even number of values, the median is the average of the middle two.
- A **.5** always rounds **up**.

**Worked example:** the last 6 weeks were `10, 0, 12, 8, 0, 10`.
1. Drop the zeros: `10, 12, 8, 10` (4 weeks, which is enough).
2. Sort them: `8, 10, 10, 12`.
3. The middle two are 10 and 10, so (10 + 10) ÷ 2 = **10**.

**Worked example 2:** `0, 0, 5, 0, 0, 7`. Only 2 weeks had a sale, so there's **no forecast** (Lesson 3 covers what happens then).

**Your turn:**
a) `20, 22, 0, 18, 21, 0`
b) `6, 9, 0, 3, 0, 0`

<details><summary>Answers</summary>

a) Drop the zeros and sort: 18, 20, 21, 22. The middle two are 20 and 21, so 20.5, which rounds up to **21**.
b) Drop the zeros and sort: 3, 6, 9. With an odd count, the middle value is **6**.
</details>

**In the software:** this is the "Forecast" column in the Workbench. It's also the number reps have always been suggested; it hasn't changed.

---

### Lesson 3: Not enough recent sales? Use the customer's own weekly rate

**In one sentence:** if Lesson 2 gives no answer, divide everything the customer bought by the number of weeks we have data for.

**Worked example:** a customer bought 30 twice in the 12 weeks we have data for. 30 + 30 = 60, and 60 ÷ 12 = **5 per week**.

Five looks small next to an order of 30, but they only order about every 6 weeks, and 30 ÷ 6 = 5. Over time it adds up correctly.

**Your turn:** a customer bought 24 three times in 12 weeks.

<details><summary>Answer</summary>

24 × 3 = 72, and 72 ÷ 12 = **6 per week**.
</details>

**In the software:** these forecasts are marked as a "fallback" in the database (`bIsFallback = 1`) and get a lower grade (Lesson 4).

> If a customer never bought the product at all, there's a third rule: the product's average across all customers × how big this customer is. It's rarely needed today.

---

### Lesson 4 (optional): The pattern label and grade letter

**In one sentence:** the system labels each customer × product by *how often* they buy and *how even* their order sizes are, then gives the forecast a grade letter.

These labels **don't change any quantity**. They're just there to show how much to trust a forecast.

**How often:** weeks we have data for ÷ weeks with a sale. **1.32 or more** means "buys rarely".

**Worked example:** 10 every other week for 12 weeks gives 12 ÷ 6 = **2.0**, so the customer buys rarely. Their sizes are always 10, which is perfectly even. Label: **INTERMITTENT**.

**Your turn:** 10 in 11 of 12 weeks.

<details><summary>Answer</summary>

12 ÷ 11 = **1.09**, which is below 1.32, so they buy often. Sizes are even, so the label is **SMOOTH**.
</details>

**Grades:** steady buyer = **A**. Occasional buyer = **C**. Any fallback (Lesson 3) = **D**. Almost no history = **F**.

---

### Lesson 5: A "% change" scenario

**In one sentence:** the target is the forecast × (1 + the percentage).

**Worked example:** forecast 10, +20%: 10 × 1.20 = **12**.

**Your turn:**
a) forecast 25, +10%
b) forecast 40, −25%

<details><summary>Answers</summary>

a) 25 × 1.10 = **27.5**  b) 40 × 0.75 = **30**
</details>

**In the software:** Scenarios → New scenario → Lever "% change".

---

### Lesson 6: Spreading an amount across customers

**In one sentence:** "Add per week" and "Weekly total" share an amount between customers in proportion to their forecasts.

**Worked example:** two customers with forecasts 30 and 10 (total 40). Their shares are 30/40 = 0.75 and 10/40 = 0.25.
- **Add per week 20:** the first gets 20 × 0.75 = 15, the second 20 × 0.25 = 5. Targets: **45 and 15**.
- **Weekly total 60:** the first gets 60 × 0.75 = **45**, the second 60 × 0.25 = **15**.

They give the same answer here because 40 + 20 = 60.

**Your turn:** forecasts 20, 20 and 10.
a) Add per week 10
b) Weekly total 100

<details><summary>Answers</summary>

The shares are 0.4, 0.4 and 0.2.
a) 10 is split as 4, 4 and 2, so the targets are **24, 24, 12**.
b) 100 is split as **40, 40, 20**.
</details>

---

### Lesson 7: Warning flags

**In one sentence:** before a scenario can be approved, the software checks each target for four warning signs. A manager must say **Accept** or **Exclude** for every flagged one.

**Flag 1: above the customer's usual maximum.** The "usual maximum" is close to the biggest week they've had (statisticians call it P95).

To work it out, take their weeks with a sale, sorted. Go to position **0.95 × (count − 1)**, counting from 0. If that lands between two values, move that fraction of the way from the lower one to the next.

**Worked example 1:** `10, 10, 10, 10, 10`. Every value is 10, so the usual max is **10**. A target of 12 is **flagged**.

**Worked example 2:** `8, 10, 12` (positions 0, 1, 2). The position is 0.95 × 2 = 1.9, which is 90% of the way from position 1 (value 10) to position 2 (value 12): 10 + 0.9 × (12 − 10) = **11.8**. A target of 11 is OK; a target of 12 is flagged.

**The other three flags:**

| Flag | Fires when… |
|---|---|
| New product | the product was first invoiced less than **13 weeks** ago, and the scenario changes it |
| More than 25% vs last year | the target is more than 25% above the same week last year (needs a year of data, so it can't fire yet) |
| Below 1 pack | the target is more than 0 but smaller than one pack |

**Your turn:**
a) Sales weeks `5, 5, 10`. What's the usual max? Is a target of 9 flagged? A target of 10?
b) Pack of 6, target 4: flagged?
c) Pack of 6, target 0: flagged?
d) Product first invoiced 5 weeks ago, and the scenario changes it: flagged?
e) First invoiced 20 weeks ago: flagged?

<details><summary>Answers</summary>

a) The position is 1.9: 5 + 0.9 × (10 − 5) = **9.5**. A target of 9 is **not** flagged; 10 **is** flagged.
b) **Yes** (4 is less than one pack).
c) **No** (zero means "no ask").
d) **Yes.**
e) **No.**
</details>

> **Good to know:** on a very steady customer almost any increase gets flagged, because they've never bought more. That's on purpose; it makes a manager confirm the stretch.

---

### Lesson 8: The quantity the rep is offered

**In one sentence:** the weekly number minus what's already ordered or invoiced this week, turned into whole packs.

- The **weekly number** is the target if there is one (shown with 🎯); otherwise it's the usual quantity from Lesson 2.
- **With a target, round UP** to a full pack, so the target is met.
- **Without one, round to the nearest** pack, as before.

**Worked example 1:** target 12, nothing ordered yet, pack of 10. 12 ÷ 10 = 1.2 packs, which rounds up to 2 packs = **20** 🎯.

**Worked example 2:** no target, usual quantity 12, pack of 10. 1.2 packs is nearest to 1 pack = **10**.

**Your turn** (all with a pack of 6):
a) Target 30, already ordered 12
b) Target 31, already ordered 12
c) No target, usual 31, already ordered 12
d) Target 30, already ordered 36

<details><summary>Answers</summary>

a) 30 − 12 = 18, which is exactly 3 packs: **18**
b) 31 − 12 = 19, which is 3.17 packs, up to 4: **24**
c) 19, 3.17 packs, nearest is 3: **18**
d) 30 − 36 is negative, so **nothing is suggested** (the target is already met)
</details>

**In the software:** the Sug column in the Order Assistant. Hover a 🎯 to see this exact sum.

---

### Lesson 9: What's recorded when the rep places the order

| The rep was shown | The rep ordered | Recorded as |
|---|---|---|
| 20 | 20 | **ACCEPTED** |
| 10 | 5 | **MODIFIED** (any different amount) |
| 6 | nothing | **REJECTED** |
| (not suggested) | 12 | **MANUAL_ADD** |

**Your turn:** shown 18, ordered 24?

<details><summary>Answer</summary>

**MODIFIED**
</details>

**Why it matters:** later you can see whether reps follow suggestions, separately from whether the forecasts were good.

---

### Lesson 10: The Workbench totals

**Rules:**
- **Target** = the target if there is one, otherwise the forecast.
- **Committed** = already delivered this week + open orders for this week.

**Worked example:** two products for one customer.

| Product | Forecast | Target | Delivered | On order | Committed |
|---|---|---|---|---|---|
| A | 10 | 12 (scenario) | 0 | 20 | 20 |
| B | 30 | — so use 30 | 15 | 0 | 15 |
| **Total** | **40** | **42** | | | **35** |

Committed vs target: 35 ÷ 42 = **83%**.

---

# Part 2: The software, click by click

We use **one real customer from your database**, so you can predict every number before you see it. I checked these facts against the database on 3 Nov 2026.

| Fact | Value |
|---|---|
| Customer | **SPA227: SPAR LULEKANI - 60109** |
| Route / DC | LIM MUFARO PH1 (ONE) / Makhado |
| Product | **323: FOE Cranberry 100% 500 ML** (Juice), **pack of 10**, 0.5 L each |
| History | bought **10** in 7 of the last 13 weeks (never more, never less) |
| Forecast | **10 per week** for the weeks of 8, 15 and 22 Nov (Lesson 2: the last 6 weeks were 10, 0, 0, 10, 10, 10, so the median is 10) |
| Label / grade | INTERMITTENT, grade C (Lesson 4: 13 ÷ 7 = 1.86) |
| Already ordered | nothing; no targets yet |

> **Doing this after 9 Nov 2026?** Pick another customer × product that has a forecast for next week (run this in SSMS, changing the date to next Monday), then redo the sums for its numbers:
> ```sql
> SELECT TOP 20 cCustomerAccount, cStockCode, dWeekStart, fPredictedQty, cDemandPattern
>   FROM [SOAssistant].dbo._tb_SOForecast
>  WHERE bIsActive = 1 AND dWeekStart = '2026-11-09' ORDER BY cCustomerAccount;
> ```
> No rows for next week? Create forecasts first: `.\.venv\Scripts\python.exe scripts\run_forecast_smoke_test.py --limit 200`

### Before you start

1. **Start the backend:** `cd c:\Dev\salesOrder\backend`, then `uvicorn main:app --reload --port 8000`
2. **Start the frontend:** `cd c:\Dev\salesOrder\frontend`, then `npm run dev`, then open <http://localhost:5173>
3. **Log in** as a **Supervisor** or **Admin**. For session date, pick **Monday 9 Nov 2026**. That puts you in the week of 9 Nov (Wk 46), the week we'll target. (To change the session date later, log out and back in.)

---

### Tutorial A: Look before you touch (Target Workbench)

1. Open the **Analytics** tab. *You should see four new cards:* Target Workbench, Scenarios, Opportunity Inbox, Model Leaderboard.
2. Click **Target Workbench**. It opens on the week of your session date (**9 Nov**).
3. Click **Makhado**, then **LIM MUFARO PH1 (ONE)**, then **SPAR LULEKANI - 60109**.
4. Find **323 / FOE Cranberry 100% 500 ML**.
   *You should see:* Forecast **10**, Target **10**, vs fc **0%** in grey (no change yet), Delivered 0, On order 0, and "forecast only" in the last column.
   **Why is the target 10?** There's no target yet, so the target is the forecast (Lesson 10).
5. Click **Litres**. *You should see* Forecast **5** (10 × 0.5 L). Switch back to **Units**.

---

### Tutorial B: Create a scenario and preview it

1. Analytics → **Scenarios** → **New scenario**.
2. Fill in:
   - **Name:** `Tutorial: Cranberry +20%`
   - **Lever:** `% change`, **Change %:** `20`
   - **From:** 08/11/2026, **To:** 14/11/2026
   - **Customer:** type `SPA227` and pick it from the list
   - **Product:** type `323` and pick it
   - Leave every other scope field empty.
3. Click **Preview** (it saves the draft first).

*Work it out first:* forecast 10 × 1.20 = **12** (Lesson 5). Flagged? The history is always 10, so the usual max is **10**. 12 is above it, so **yes, flagged** (Lesson 7). It's not a new product, and there's no last-year data.

*You should see:*
- **Status:** PREVIEW
- **Leaves / leaf-weeks:** 1 / 1
- **Forecast:** 10, **Target:** 12 (+20.0% vs forecast)
- **Flagged leaves:** 1, "1 still to review"
- **Review table:** `SPA227 × 323`, flag **"Above usual max (P95)"**, reason *"Target 12.00 exceeds the P95 of the last 52 weeks' demand (10.00)"*
- The **Approve** button is greyed out (one decision is still missing).

---

### Tutorial C: Review the flag and approve

1. Click **Accept** on the SPA227 × 323 row. The button turns green and Approve unlocks.
2. Click **Approve (1 targets)**.
   *You should see:* "Approved — 1 targets written…", and the status becomes **ACTIVE**.
3. Close the window. The scenario now shows **ACTIVE** in the list.

> Try the wrong way once: on a new scenario, press Approve without reviewing. It refuses and highlights the unreviewed rows in red.

---

### Tutorial D: See the target, override it, undo it

1. Open **Target Workbench** again and click down to SPAR LULEKANI → 323.
   *You should see:* Forecast **10**, Target **12**, vs fc **+20%**, Committed **0%**, a ✏️ button.
2. Click ✏️. Enter **15** with reason `tutorial test`, then **Save override**.
   *You should see:* Target **15** and "overridden".
3. Click ✏️ again → **Clear override** (it needs a reason, e.g. `undo`).
   *You should see:* Target back to **12**.

---

### Tutorial E: What the rep sees

1. Open the **Order Assistant** tab. Pick route **LIM MUFARO PH1 (ONE)**, then customer **SPA227**.
2. Open the **Juice** category and find **323**.

*Work it out first:* target 12, nothing ordered, pack of 10. 1.2 packs rounds **up** to 2 packs, so **20** (Lesson 8). Without the target it would have been 10.

*You should see:*
- **Sug column:** 🎯 **20**
- **Hovering the 🎯 shows:**
  ```
  🎯 Management target for this week
  Forecast: 10
  Target: 12 (+20% vs forecast)
  From: Tutorial: Cranberry +20%
  (Usual weekly qty: 10)
  Suggested now: 20
  ```
- **In the draft panel:** a strip "🎯 This week's targets, 0 of 1 met", with the row `323 … 0.00 / 12.00`.

3. Click **+** on product 323 to add it to the draft.
   *You should see:* the strip turns green, "1 of 1 met", `20.00 / 12.00`.
4. **Stop here** unless you want a real order in Evolution. To remove the draft line, click the bin icon or **Clear all**.

> *(Optional)* If you do place the order, check what was recorded in SSMS:
> ```sql
> SELECT TOP 5 cStockCode, fSuggestedQty, fAcceptedQty, cDecision, cOrderNum
>   FROM [SOAssistant].dbo._tb_SOOrderSuggestion
>  WHERE cCustomerAccount = 'SPA227' ORDER BY idOrderSuggestion DESC;
> ```
> Expect 323 with suggested 20, accepted 20, **ACCEPTED** (Lesson 9).

---

### Tutorial F: Clean up

1. Analytics → **Scenarios** → open **Tutorial: Cranberry +20%** → **Cancel scenario** → reason `tutorial done` → **Confirm cancel**.
   *You should see:* "Cancelled — 0 targets recomputed, 1 removed".
2. **Check it's gone:**
   - In the Workbench, SPA227 × 323 is back to Target **10**, "forecast only".
   - In the Order Assistant, after reselecting the customer, 323 shows a plain **10** (no 🎯) and the strip has disappeared.

---

### Tutorial G: The other two screens (quick look)

- **Opportunity Inbox:** 2 test tasks from the original setup. Try **Start**, then **Resolve** with a note, then switch the filter to **Closed** to see it, and **Reopen**. Real tasks arrive in a later phase.
- **Model Leaderboard:** 5 models, only **Base median** enabled, with no accuracy figures yet (these come once actual sales are scored in a later phase).

---

### If something doesn't match

| You see | Likely reason |
|---|---|
| No forecasting cards in Analytics | You're logged in as an Agent, or `FORECASTING_UI_ENABLED` is false |
| Workbench empty for the week | No forecasts for that week yet; run the forecast smoke test |
| SPA227 / 323 not offered in the scope picker | No active forecast for it any more; pick another leaf (see the SQL above) |
| No 🎯 in the Order Assistant | The session date isn't in the target's week (log out and back in), or the scenario isn't ACTIVE |
| A number differs from your sum | Note the customer, product, week and both numbers, and send them over; that's exactly what's needed to track it down |

---

# Part 3: Cheat sheet

| What | Formula | Lesson |
|---|---|---|
| Week | the Monday on or before the date (Mon–Sun) | 1 |
| Usual weekly qty | median of the weeks with a sale among the last 6 finished weeks (needs 3; .5 rounds up) | 2 |
| Fallback | total bought ÷ weeks of data | 3 |
| % scenario | forecast × (1 + %) | 5 |
| Add / total scenario | split by each customer's share of the forecast | 6 |
| Usual max (P95) | position 0.95 × (n − 1) in the sorted sales weeks | 7 |
| Rep's quantity | (weekly number − already ordered this week) ÷ pack; **up** with 🎯, **nearest** without | 8 |
| Workbench target | target if set, else forecast | 10 |
| Committed | delivered + on order this week | 10 |

Every threshold (6 weeks, 3 sales, 1.32, P95, 25%, 13 weeks, 1 pack) lives in `backend/services/forecasting/config.py`.

For the deeper version, with every model including the advanced ones, see [FORECASTING_GUIDE.md](FORECASTING_GUIDE.md).

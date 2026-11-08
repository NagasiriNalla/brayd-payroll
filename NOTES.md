# Brayd Payroll Assessment — Implementation Notes

## 1. Assumptions Made

Where Brayd's written specifications did not explicitly define an edge condition, the following engineering assumptions were made:

- **Negative Worked Minutes on Overnight Shifts (Fatima Begum / E04)**:
  - Brayd's rules mandate: *"An event's day is the IST calendar date of its device_time"* and *"Worked minutes = latest OUT − earliest IN"*.
  - For workers on overnight shifts (e.g., 22:00 to 06:00 IST), the morning clock-out (e.g., 06:05) and evening clock-in (e.g., 21:55) land on the same calendar day. Hence, `latest OUT - earliest IN` evaluates to a negative integer (around -950 minutes).
  - The specification states: *"240 worked minutes or more is a full day; fewer is a half day."* Because -950 is strictly fewer than 240, this was evaluated as a half day, and an explicit audit flag `NEGATIVE_WORKED_MINUTES (OUT precedes IN)` was logged on each occurrence.
  - On Mondays following a Sunday off, an IN exists with no morning OUT, triggering `MISSING_PUNCH` (absent) per specification.
- **Precision and Rounding**:
  - Daily rate (`monthly_gross / 31`) and hourly rate (`daily_rate / 8`) were maintained at full decimal precision (`Decimal`) during intermediate arithmetic.
  - Each individual line item (`Base Pay`, `Overtime Pay`, `Late Deduction`, `Arrears`) is rounded to the nearest paisa (`0.01`, halves round up via `ROUND_HALF_UP`).
  - Final Net Pay is rounded to the nearest integer rupee (`1`, halves round up) from the sum of line items.
- **Arrears Placement**:
  - The retroactive adjustment (`recomputed net − locked net`) from late-uploaded events is stored directly as an `ARREARS` line item on the employee's November 2026 (`2026-11`) payslip with 0 attendance days, leaving the locked October payslip completely intact.

---

## 2. Explanation of Employees Returned by Report Query 2

Query 2 flags all employees whose October 2026 net pay is below 75% of their monthly gross:

1. **Arjun Reddy (`E03`) — Gross: ₹27,900 | Net: ₹17,123 (61.37%)**
   - **Assessment**: **CORRECT (Expected behavior)**.
   - **Reason**: Arjun joined the company mid-month on **October 13, 2026**. Per Brayd rules, all days prior to `join_date` are ignored, and Sundays are only paid if on or after the join date. In October, Arjun was employed for only 19 calendar days (17 full days worked + 2 Sundays on/after join date = 19 paid days). His prorated base pay (19/31 of gross) plus 8 minutes of overtime yields ₹17,123, which is legitimately ~61.37% of gross.

2. **Fatima Begum (`E04`) — Gross: ₹18,600 | Net: ₹9,300 (50.00%)**
   - **Assessment**: **BUG (Flaw in Brayd's attendance specification)**.
   - **Reason**: Fatima was scheduled for the night shift starting at `22:00 IST`. She regularly attended all shifts, clocking in around 21:55 and clocking out around 06:05 the next morning. However, Brayd's rules partition events strictly by calendar date rather than shift window. On each working calendar day, her morning OUT precedes her evening IN, producing negative worked minutes and classifying full shifts as half days. Furthermore, her Monday shifts lack a preceding morning OUT, triggering false `MISSING_PUNCH` absences. This low pay is an artifact of the calendar-day rule failing for night shifts.

---

## 3. How Duplicate Rows Were Prevented

- **Clock Events**:
  - At the schema level, `clock_events.event_id` is declared as `PRIMARY KEY`.
  - Both `payroll.py load` and `payroll.py late` execute `INSERT OR IGNORE INTO clock_events (...)`. When a device re-uploads an existing `event_id`, SQLite silently ignores the duplicate.
- **Payslips & Line Items**:
  - `payslips` enforces a database-level composite constraint `UNIQUE(employee_id, month)`.
  - `payroll.py run` executes an `UPSERT` (`INSERT ... ON CONFLICT(employee_id, month) DO UPDATE SET ...`).
  - Child table `payslip_line_items` uses `UNIQUE(payslip_id, line_type)` with foreign key cascade. Existing line items are replaced within a single atomic transaction before inserting new ones.
  - In `payroll.py late`, the arrears for November are recalculated and overwritten (`ON CONFLICT DO UPDATE SET`), ensuring that calling `late late_events.csv` multiple times never doubles the arrears.

---

## 4. Bug Encountered During Development

- **The Bug**:
  - When printing payslips on Windows PowerShell, Python threw a terminal crash:
    `UnicodeEncodeError: 'charmap' codec can't encode character '\u20b9' in position 326: character maps to <undefined>`.
- **Root Cause & Resolution**:
  - Windows consoles by default initialize stdout using legacy code page cp1252, which lacks the Indian Rupee symbol (`₹`, `\u20b9`).
  - By inspecting the traceback, I observed that the crash occurred in `print(format_payslip(...))`.
  - I resolved this cleanly at script initialization using `sys.stdout.reconfigure(encoding="utf-8")`, enabling portable UTF-8 encoding across Windows, Linux, and macOS without requiring any third-party packages or environment flag workarounds.

---

## 5. What I Would Change With One More Day

1. **Shift-Aware Punch Pairing (Overnight Shift Support)**:
   - Instead of partitioning events by raw calendar date, group events into shift windows defined by `shift_start_ist` (e.g., matching IN punches around 22:00 with OUT punches around 06:00 next morning). This would properly pay night shift workers like Fatima Begum.
2. **CSV / JSON Export CLI Flag**:
   - Add a `--format=json` or `--format=csv` option to `payroll.py run` to export monthly payroll summaries for finance and banking integration.
3. **Audit Log Table**:
   - Add an `audit_log` table recording timestamps, operator IDs, lock events, and retroactive adjustments whenever payroll recalculations or lock operations occur.
4. **Data Integrity Pre-Flight Checks**:
   - Add an automated anomaly detector that scans for impossible punch pairs (e.g., IN after IN without OUT, interval < 10 mins) and exports an exception report for HR before monthly lock.

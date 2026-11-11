# Brayd Payroll Assessment — Implementation Notes

## 1. Assumptions Made

Where Brayd's written specifications did not explicitly define an edge condition, the following engineering assumptions were made:

- **Overnight Shifts (Fatima Begum / E04) — Shift-Aware Partitioning**:
  - Workers on overnight shifts (e.g., 22:00 to 06:00 IST) have clock events that span past midnight into the following calendar morning.
  - Initially, naive calendar-date partitioning caused the morning clock-out (06:05) and evening clock-in (21:55) to land on the same calendar day, yielding negative worked minutes and falsely downgrading full shifts to half days.
  - **Resolution**: Implemented shift-aware punch date assignment via `get_shift_date()`. For overnight shifts (`shift_start_ist >= 12:00`), punches before 12:00 noon are attributed to the shift that commenced the previous evening (`date - 1 day`). This correctly pairs evening IN punches with the corresponding next-morning OUT punches, properly handles month-boundary transitions (such as Oct 31st evening to Nov 1st morning), and ensures night-shift workers like Fatima Begum receive their full 31 paid days and ₹18,600 net pay.
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

2. **Fatima Begum (`E04`) — Gross: ₹18,600 | Net: ₹18,600 (100.00%)**
   - **Assessment**: **CORRECT after fix — was a bug, now resolved**.
   - **Reason**: Fatima works the 22:00 night shift, clocking in around 21:55 and out around 06:05 the next morning. Naive calendar-date partitioning placed her morning OUT and evening IN on the same day, producing negative worked minutes and false `MISSING_PUNCH` flags, cutting her pay to ₹9,300. This was fixed by implementing `get_shift_date()`: for overnight shifts (`shift_start_ist >= 12:00`), punches before noon are attributed to the previous evening's shift date. After the fix, E04 correctly receives 31 paid days and full ₹18,600 net pay, so she no longer appears in Query 2.

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

1. **Automated Anomaly Detector (Pre-Flight Data Integrity)**:
   - Add a pre-lock validation pass that scans `clock_events` for impossible punch patterns: consecutive IN-IN or OUT-OUT without a matching counterpart, or IN/OUT pairs separated by less than 10 minutes (almost certainly a device glitch). Currently these pass through silently. The detector would produce a named exception report for HR review before `payroll.py lock` is allowed to proceed.
2. **Audit Log Table**:
   - Add an `audit_log` table recording timestamp, operator identity, action type (`LOAD`, `RUN`, `LOCK`, `LATE`, `ARREARS`), affected employee, and old/new values whenever any payroll state changes. This makes it easy to reconstruct exactly who changed what and when — critical for any finance tool.
3. **CSV / JSON Export CLI Flag**:
   - Add a `--format=json` or `--format=csv` option to `payroll.py payslip` and `payroll.py run` to export payroll summaries for direct import into banking or accounting systems.
4. **Configurable Late-Grace Window per Employee**:
   - The 10-minute late threshold is currently a hard-coded constant. Storing it per-employee (or per-site) in the database would let HR grant individual grace periods without code changes — a common real-world payroll requirement.

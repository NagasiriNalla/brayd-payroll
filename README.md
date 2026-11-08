# Brayd Payroll Calculator

A lightweight, robust, and self-contained payroll calculation engine for field workers built with Python 3 and SQLite. Designed for offline phone punch synchronization, month-end locking, and retroactive late event adjustments.

---

## Requirements & Setup

- **Python**: Version 3.10 or higher.
- **Database**: SQLite (built directly into Python standard library, zero setup required).
- **Test Runner**: `pytest` (optional, for running test suite).

No external cloud services, web servers, or paid dependencies required.

---

## Project Structure

```text
├── payroll.py                      # Main payroll CLI and calculation engine
├── test_payroll.py                 # Automated pytest test suite
├── report.sql                      # Part 3 raw SQL queries for locked October payroll
├── NOTES.md                        # Implementation notes, assumptions, and analysis
├── employees.csv                   # Employee master data
├── clock_events.csv                # Primary October punch events
├── late_events.csv                 # Late-uploaded punch events
└── brayd_payroll_assessment_data/  # Original assessment datasets
```

---

## CLI Usage Instructions

### 1. Load Data
Loads employee master data (`employees.csv`) and clock punches (`clock_events.csv`) into `payroll.db`. Running it multiple times is idempotent (no duplicate rows created).

```bash
python payroll.py load
```

### 2. Run Payroll
Calculates payroll for the specified month (e.g. October 2026) and persists all payslips and line items. Running it repeatedly produces identical results without duplicates.

```bash
python payroll.py run 2026-10
```

### 3. Generate Payslip
Displays a formatted payslip for a specific employee including paid days, late days, overtime minutes, detailed line items, net pay, and audit flags.

```bash
python payroll.py payslip E01 2026-10
python payroll.py payslip E02 2026-10
python payroll.py payslip E03 2026-10
python payroll.py payslip E04 2026-10
python payroll.py payslip E05 2026-10
```

### 4. Lock a Month
Locks the specified month. After locking, `run 2026-10` strictly refuses to modify the locked payroll.

```bash
python payroll.py lock 2026-10
```

### 5. Load Late Events & Calculate Arrears
Loads late-arriving events without modifying locked October payslips. Recomputes the affected month and posts the retroactive adjustment (`recomputed net − locked net`) as an arrears line item on that employee's November 2026 payslip. Running this repeatedly will never double arrears.

```bash
python payroll.py late late_events.csv
python payroll.py payslip E02 2026-11
```

---

## Running Automated Tests

Run the test suite using `pytest`:

```bash
python -m pytest -v
```

The test suite covers:
1. **Payslip Idempotency**: Proves that running `run_payroll` twice does not duplicate payslips or line items.
2. **Data Load Idempotency**: Proves that running `load_data` multiple times does not insert duplicate employees or events.
3. **Lock Protection**: Verifies that locked months reject subsequent execution with `PermissionError`.
4. **Late Events & Arrears**: Verifies that late events never modify locked October payslips, compute arrears onto November, and do not double arrears when loaded twice.
5. **Attendance & Pay Rule Engine**: Unit tests edge cases including join date filtering, Sunday paid offs, missing punch flags, 240m full/half day thresholds, late thresholds (>10m), overtime (>540m @ 1.5x), and rounding rules.

---

## October 2026 Payslip Outputs (All 5 Employees)

Below are the exact console outputs from `python payroll.py payslip <id> 2026-10`:

### Employee E01: Ravi Kumar

```text
================================================================
                    PAYSLIP FOR OCTOBER 2026                    
================================================================
Employee ID      : E01
Name             : Ravi Kumar
Site             : Kokapet
Shift Start (IST): 09:00
Monthly Gross    : ₹24,000.00
Daily Rate       : ₹774.19  (31 days)
Hourly Rate      : ₹96.77
----------------------------------------------------------------
ATTENDANCE SUMMARY:
  Paid Days      : 28.5
  Late Days      : 2
  Overtime       : 29 minutes
  Flags          : 2026-10-14: MISSING_PUNCH (IN only)
----------------------------------------------------------------
LINE ITEMS:
  BASE_PAY        :  ₹ 22064.52  (Base Pay (28.5 paid days @ ₹774.19/day))
  OVERTIME        :  ₹    70.16  (Overtime Pay (29 mins @ ₹145.16/hr))
  LATE_DEDUCTION  :  ₹     0.00  (Late Deduction (0.0 days for 2 late days))
----------------------------------------------------------------
NET PAY          :  ₹22,135
================================================================
```

### Employee E02: Lakshmi Devi

```text
================================================================
                    PAYSLIP FOR OCTOBER 2026                    
================================================================
Employee ID      : E02
Name             : Lakshmi Devi
Site             : Kokapet
Shift Start (IST): 09:00
Monthly Gross    : ₹21,500.00
Daily Rate       : ₹693.55  (31 days)
Hourly Rate      : ₹86.69
----------------------------------------------------------------
ATTENDANCE SUMMARY:
  Paid Days      : 29.0
  Late Days      : 3
  Overtime       : 0 minutes
  Flags          : None
----------------------------------------------------------------
LINE ITEMS:
  BASE_PAY        :  ₹ 20112.90  (Base Pay (29.0 paid days @ ₹693.55/day))
  OVERTIME        :  ₹     0.00  (Overtime Pay (0 mins @ ₹130.04/hr))
  LATE_DEDUCTION  : -₹   346.77  (Late Deduction (0.5 days for 3 late days))
----------------------------------------------------------------
NET PAY          :  ₹19,766
================================================================
```

### Employee E03: Arjun Reddy

```text
================================================================
                    PAYSLIP FOR OCTOBER 2026                    
================================================================
Employee ID      : E03
Name             : Arjun Reddy
Site             : Gachibowli
Shift Start (IST): 09:00
Monthly Gross    : ₹27,900.00
Daily Rate       : ₹900.00  (31 days)
Hourly Rate      : ₹112.50
----------------------------------------------------------------
ATTENDANCE SUMMARY:
  Paid Days      : 19.0
  Late Days      : 0
  Overtime       : 8 minutes
  Flags          : None
----------------------------------------------------------------
LINE ITEMS:
  BASE_PAY        :  ₹ 17100.00  (Base Pay (19.0 paid days @ ₹900.00/day))
  OVERTIME        :  ₹    22.50  (Overtime Pay (8 mins @ ₹168.75/hr))
  LATE_DEDUCTION  :  ₹     0.00  (Late Deduction (0.0 days for 0 late days))
----------------------------------------------------------------
NET PAY          :  ₹17,123
================================================================
```

### Employee E04: Fatima Begum

```text
================================================================
                    PAYSLIP FOR OCTOBER 2026                    
================================================================
Employee ID      : E04
Name             : Fatima Begum
Site             : Gachibowli
Shift Start (IST): 22:00
Monthly Gross    : ₹18,600.00
Daily Rate       : ₹600.00  (31 days)
Hourly Rate      : ₹75.00
----------------------------------------------------------------
ATTENDANCE SUMMARY:
  Paid Days      : 15.5
  Late Days      : 1
  Overtime       : 0 minutes
  Flags          : 2026-10-01: NEGATIVE_WORKED_MINUTES (-952m, OUT precedes IN); 2026-10-02: NEGATIVE_WORKED_MINUTES (-954m, OUT precedes IN); 2026-10-03: NEGATIVE_WORKED_MINUTES (-941m, OUT precedes IN); 2026-10-05: MISSING_PUNCH (IN only); 2026-10-06: NEGATIVE_WORKED_MINUTES (-941m, OUT precedes IN); 2026-10-07: NEGATIVE_WORKED_MINUTES (-936m, OUT precedes IN); 2026-10-08: NEGATIVE_WORKED_MINUTES (-976m, OUT precedes IN); 2026-10-09: NEGATIVE_WORKED_MINUTES (-920m, OUT precedes IN); 2026-10-10: NEGATIVE_WORKED_MINUTES (-950m, OUT precedes IN); 2026-10-12: MISSING_PUNCH (IN only); 2026-10-13: NEGATIVE_WORKED_MINUTES (-958m, OUT precedes IN); 2026-10-14: NEGATIVE_WORKED_MINUTES (-952m, OUT precedes IN); 2026-10-15: NEGATIVE_WORKED_MINUTES (-943m, OUT precedes IN); 2026-10-16: NEGATIVE_WORKED_MINUTES (-953m, OUT precedes IN); 2026-10-17: NEGATIVE_WORKED_MINUTES (-950m, OUT precedes IN); 2026-10-19: MISSING_PUNCH (IN only); 2026-10-20: NEGATIVE_WORKED_MINUTES (-949m, OUT precedes IN); 2026-10-21: NEGATIVE_WORKED_MINUTES (-957m, OUT precedes IN); 2026-10-22: NEGATIVE_WORKED_MINUTES (-946m, OUT precedes IN); 2026-10-23: NEGATIVE_WORKED_MINUTES (-937m, OUT precedes IN); 2026-10-24: NEGATIVE_WORKED_MINUTES (-967m, OUT precedes IN); 2026-10-26: MISSING_PUNCH (IN only); 2026-10-27: NEGATIVE_WORKED_MINUTES (-950m, OUT precedes IN); 2026-10-28: NEGATIVE_WORKED_MINUTES (-933m, OUT precedes IN); 2026-10-29: NEGATIVE_WORKED_MINUTES (-955m, OUT precedes IN); 2026-10-30: NEGATIVE_WORKED_MINUTES (-948m, OUT precedes IN); 2026-10-31: NEGATIVE_WORKED_MINUTES (-959m, OUT precedes IN)
----------------------------------------------------------------
LINE ITEMS:
  BASE_PAY        :  ₹  9300.00  (Base Pay (15.5 paid days @ ₹600.00/day))
  OVERTIME        :  ₹     0.00  (Overtime Pay (0 mins @ ₹112.50/hr))
  LATE_DEDUCTION  :  ₹     0.00  (Late Deduction (0.0 days for 1 late days))
----------------------------------------------------------------
NET PAY          :  ₹9,300
================================================================
```

### Employee E05: Suresh Naidu

```text
================================================================
                    PAYSLIP FOR OCTOBER 2026                    
================================================================
Employee ID      : E05
Name             : Suresh Naidu
Site             : Kokapet
Shift Start (IST): 07:00
Monthly Gross    : ₹31,000.00
Daily Rate       : ₹1,000.00  (31 days)
Hourly Rate      : ₹125.00
----------------------------------------------------------------
ATTENDANCE SUMMARY:
  Paid Days      : 31.0
  Late Days      : 6
  Overtime       : 1219 minutes
  Flags          : None
----------------------------------------------------------------
LINE ITEMS:
  BASE_PAY        :  ₹ 31000.00  (Base Pay (31.0 paid days @ ₹1000.00/day))
  OVERTIME        :  ₹  3809.38  (Overtime Pay (1219 mins @ ₹187.50/hr))
  LATE_DEDUCTION  : -₹  1000.00  (Late Deduction (1.0 days for 6 late days))
----------------------------------------------------------------
NET PAY          :  ₹33,809
================================================================
```

---

## SQL Reports (`report.sql`)

Two raw SQL queries are available in `report.sql`:

1. **Total Net Pay per Site**:
   - `Kokapet`: ₹75,710
   - `Gachibowli`: ₹26,423

2. **Employees with Net Pay < 75% of Gross**:
   - `E04` (Fatima Begum): Gross ₹18,600 | Net ₹9,300 (50.00%)
   - `E03` (Arjun Reddy): Gross ₹27,900 | Net ₹17,123 (61.37%)

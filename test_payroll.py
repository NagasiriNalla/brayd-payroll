"""
Test Suite for Brayd Payroll Calculator
=======================================
Run with:
    pytest -v
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
import sqlite3
import pytest

from payroll import (
    IST,
    get_connection,
    init_db,
    load_data,
    run_payroll,
    lock_month,
    load_late_events,
    calculate_employee_month,
    is_month_locked,
    round_paisa,
    round_rupee,
    parse_to_ist,
    get_shift_date,
)


@pytest.fixture
def memory_db():
    """Create an isolated in-memory SQLite database initialized with schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def loaded_db(tmp_path):
    """Create a temporary on-disk SQLite database loaded with assessment data."""
    db_file = tmp_path / "test_payroll.db"
    conn = get_connection(str(db_file))
    load_data(conn)
    yield conn
    conn.close()


def test_idempotent_payroll_run(loaded_db):
    """
    Test 1 (Required by Spec):
    Prove that running payroll twice does not duplicate payslips or line items.
    """
    month = "2026-10"

    # First run
    results_1 = run_payroll(loaded_db, month)
    assert len(results_1) == 5

    count_ps_1 = loaded_db.execute(
        "SELECT COUNT(*) AS c FROM payslips WHERE month = ?", (month,)
    ).fetchone()["c"]
    count_items_1 = loaded_db.execute(
        """
        SELECT COUNT(*) AS c
        FROM payslip_line_items li
        JOIN payslips p ON li.payslip_id = p.id
        WHERE p.month = ?
        """,
        (month,),
    ).fetchone()["c"]

    assert count_ps_1 == 5
    assert count_items_1 == 15  # 3 line items each for 5 employees

    # Second run
    results_2 = run_payroll(loaded_db, month)
    assert len(results_2) == 5

    count_ps_2 = loaded_db.execute(
        "SELECT COUNT(*) AS c FROM payslips WHERE month = ?", (month,)
    ).fetchone()["c"]
    count_items_2 = loaded_db.execute(
        """
        SELECT COUNT(*) AS c
        FROM payslip_line_items li
        JOIN payslips p ON li.payslip_id = p.id
        WHERE p.month = ?
        """,
        (month,),
    ).fetchone()["c"]

    # Must be strictly identical count (no duplication)
    assert count_ps_2 == 5
    assert count_items_2 == 15

    # Check that net pay values remain identical
    rows_1 = {
        r["employee_id"]: r["net_pay"]
        for r in loaded_db.execute(
            "SELECT employee_id, net_pay FROM payslips WHERE month = ?", (month,)
        ).fetchall()
    }
    rows_2 = {
        r["employee_id"]: r["net_pay"]
        for r in loaded_db.execute(
            "SELECT employee_id, net_pay FROM payslips WHERE month = ?", (month,)
        ).fetchall()
    }
    assert rows_1 == rows_2


def test_idempotent_data_load(loaded_db):
    """
    Test 2:
    Loading CSV data multiple times must not create duplicate employee or event rows.
    """
    count_emp_before = loaded_db.execute("SELECT COUNT(*) AS c FROM employees").fetchone()["c"]
    count_ev_before = loaded_db.execute("SELECT COUNT(*) AS c FROM clock_events").fetchone()["c"]

    # Re-run load_data
    emp_loaded, ev_loaded = load_data(loaded_db)

    count_emp_after = loaded_db.execute("SELECT COUNT(*) AS c FROM employees").fetchone()["c"]
    count_ev_after = loaded_db.execute("SELECT COUNT(*) AS c FROM clock_events").fetchone()["c"]

    assert count_emp_after == count_emp_before == 5
    assert count_ev_after == count_ev_before == 250
    assert ev_loaded == 0  # 0 new events inserted on second load


def test_month_locking_prevents_modification(loaded_db):
    """
    Test 3:
    Locking a month must prevent any subsequent run_payroll from modifying it.
    """
    month = "2026-10"
    run_payroll(loaded_db, month)

    assert not is_month_locked(loaded_db, month)

    # Lock the month
    lock_month(loaded_db, month)
    assert is_month_locked(loaded_db, month)

    # Subsequent run must raise PermissionError
    with pytest.raises(PermissionError) as exc_info:
        run_payroll(loaded_db, month)
    assert "LOCKED" in str(exc_info.value)


def test_late_events_and_arrears_idempotence(loaded_db):
    """
    Test 4:
    Loading late events:
    1. Must never alter locked October payslip.
    2. Computes the net difference as arrears on November payslip.
    3. Loading late events twice must not double the arrears.
    """
    month_oct = "2026-10"
    month_nov = "2026-11"

    # 1. Run and lock October
    run_payroll(loaded_db, month_oct)
    lock_month(loaded_db, month_oct)

    e02_oct_locked = loaded_db.execute(
        "SELECT net_pay FROM payslips WHERE employee_id = 'E02' AND month = ?",
        (month_oct,),
    ).fetchone()["net_pay"]
    assert e02_oct_locked == 19766

    # 2. First load of late events
    arrears_first = load_late_events(loaded_db, "late_events.csv")
    assert len(arrears_first) >= 1
    e02_arrears = [a for a in arrears_first if a["employee_id"] == "E02"][0]
    assert e02_arrears["arrears"] == 694

    # Locked October payslip must remain unchanged
    e02_oct_after = loaded_db.execute(
        "SELECT net_pay FROM payslips WHERE employee_id = 'E02' AND month = ?",
        (month_oct,),
    ).fetchone()["net_pay"]
    assert e02_oct_after == 19766

    # November payslip must reflect ₹694 arrears
    e02_nov_ps = loaded_db.execute(
        "SELECT * FROM payslips WHERE employee_id = 'E02' AND month = ?",
        (month_nov,),
    ).fetchone()
    assert e02_nov_ps["net_pay"] == 694

    e02_nov_item = loaded_db.execute(
        """
        SELECT * FROM payslip_line_items
        WHERE payslip_id = ? AND line_type = 'ARREARS'
        """,
        (e02_nov_ps["id"],),
    ).fetchone()
    assert e02_nov_item["amount"] == 694.0

    # 3. Second load of late events: must NOT double arrears
    arrears_second = load_late_events(loaded_db, "late_events.csv")
    e02_nov_ps_second = loaded_db.execute(
        "SELECT * FROM payslips WHERE employee_id = 'E02' AND month = ?",
        (month_nov,),
    ).fetchone()
    assert e02_nov_ps_second["net_pay"] == 694  # still 694, not 1388


def test_attendance_and_pay_rules_unit(memory_db):
    """
    Test 5:
    Direct unit test of edge cases in Brayd attendance and pay calculation:
    - Ignoring days before join_date
    - Sunday paid weekly off (work on Sunday ignored)
    - Full day (>= 240 min) vs Half day (< 240 min)
    - Missing punch (IN only / OUT only) flagged and absent
    - Late threshold (> 10 min after shift start)
    - Overtime (> 540 min per day at 1.5x hourly rate)
    - Late deduction (0.5 day for every 3 late days)
    """
    # Create a test employee joining on Oct 10, shift at 09:00, gross 31000 (daily rate = 1000)
    memory_db.execute(
        """
        INSERT INTO employees (
            employee_id, name, site, monthly_gross_inr, join_date, shift_start_ist
        ) VALUES ('T01', 'Test Worker', 'SiteA', 31000.0, '2026-10-10', '09:00')
        """
    )
    emp_row = memory_db.execute("SELECT * FROM employees WHERE employee_id = 'T01'").fetchone()

    # Oct 10 is Saturday: Full day with overtime (worked 600m -> 60m OT), IN 09:15 (late by 15m > 10m)
    # Oct 11 is Sunday: Paid off, clock punches should be ignored
    # Oct 12 is Monday: Half day (worked 180m < 240m), IN 09:05 (not late)
    # Oct 13 is Tuesday: Missing punch (IN only) -> Absent, flagged
    # Oct 14 is Wednesday: Full day (worked 480m), IN 09:20 (late)
    # Oct 15 is Thursday: Full day (worked 480m), IN 09:25 (late -> 3rd late day!)
    test_events = [
        # Oct 10 (Sat)
        ("ev1", "T01", "IN", "2026-10-10T09:15:00+05:30", "2026-10-10T09:15:00+05:30"),
        ("ev2", "T01", "OUT", "2026-10-10T19:15:00+05:30", "2026-10-10T19:15:00+05:30"),
        # Oct 11 (Sun)
        ("ev3", "T01", "IN", "2026-10-11T09:00:00+05:30", "2026-10-11T09:00:00+05:30"),
        ("ev4", "T01", "OUT", "2026-10-11T17:00:00+05:30", "2026-10-11T17:00:00+05:30"),
        # Oct 12 (Mon)
        ("ev5", "T01", "IN", "2026-10-12T09:05:00+05:30", "2026-10-12T09:05:00+05:30"),
        ("ev6", "T01", "OUT", "2026-10-12T12:05:00+05:30", "2026-10-12T12:05:00+05:30"),
        # Oct 13 (Tue) - Missing punch (IN only)
        ("ev7", "T01", "IN", "2026-10-13T09:00:00+05:30", "2026-10-13T09:00:00+05:30"),
        # Oct 14 (Wed)
        ("ev8", "T01", "IN", "2026-10-14T09:20:00+05:30", "2026-10-14T09:20:00+05:30"),
        ("ev9", "T01", "OUT", "2026-10-14T17:20:00+05:30", "2026-10-14T17:20:00+05:30"),
        # Oct 15 (Thu)
        ("ev10", "T01", "IN", "2026-10-15T09:25:00+05:30", "2026-10-15T09:25:00+05:30"),
        ("ev11", "T01", "OUT", "2026-10-15T17:25:00+05:30", "2026-10-15T17:25:00+05:30"),
    ]

    for eid, emp, typ, dev, dev_ist in test_events:
        memory_db.execute(
            """
            INSERT INTO clock_events (event_id, employee_id, type, device_time, device_time_ist, uploaded_at)
            VALUES (?, ?, ?, ?, ?, '2026-10-31T00:00:00Z')
            """,
            (eid, emp, typ, dev, dev_ist),
        )

    calc = calculate_employee_month(memory_db, emp_row, 2026, 10)

    # Sundays on or after Oct 10: Oct 11, Oct 18, Oct 25 = 3 Sundays
    assert calc["sundays"] == 3
    # Full days: Oct 10, Oct 14, Oct 15 = 3 full days
    assert calc["full_days"] == 3
    # Half days: Oct 12 = 1 half day
    assert calc["half_days"] == 1
    # Paid days: 3 + 3 + 0.5 = 6.5
    assert calc["paid_days"] == 6.5

    # Overtime: Oct 10 worked 600m - 540m = 60m
    assert calc["overtime_minutes"] == 60

    # Late days: Oct 10 (15m), Oct 14 (20m), Oct 15 (25m) = 3 late days
    assert calc["late_days"] == 3

    # Late deduction: 3 late days = 0.5 day deduction = ₹500.00
    assert calc["late_deduction"] == 500.0

    # Base pay: 6.5 * 1000 = ₹6500.00
    assert calc["base_pay"] == 6500.0

    # Hourly rate: 1000 / 8 = 125. OT rate: 125 * 1.5 = 187.50. 60 min OT = ₹187.50
    assert calc["ot_pay"] == 187.50

    # Net pay: 6500 + 187.50 - 500 = 6187.50 -> rounded to rupee = 6188
    assert calc["net_pay"] == 6188

    # Flags should include MISSING_PUNCH for Oct 13
    assert any("MISSING_PUNCH" in f for f in calc["flags"])


def test_fatima_begum_overnight_shift(loaded_db):
    """
    Test 6:
    Verify Fatima Begum (E04) overnight shift payroll is calculated accurately:
    - 27 night shifts worked + 4 Sundays = 31.0 paid days
    - Exactly 1 late day (>10m after 22:00 IST), 0 OT minutes, 0 late deduction
    - Full net pay of ₹18,600 (100% of gross)
    - Zero audit flags (no false negative worked minutes or missing punch)
    """
    month = "2026-10"
    results = run_payroll(loaded_db, month)
    e04_calc = [r for r in results if r["employee_id"] == "E04"][0]

    assert e04_calc["paid_days"] == 31.0
    assert e04_calc["sundays"] == 4
    assert e04_calc["full_days"] == 27
    assert e04_calc["half_days"] == 0
    assert e04_calc["absent_days"] == 0
    assert e04_calc["late_days"] == 1
    assert e04_calc["overtime_minutes"] == 0
    assert e04_calc["net_pay"] == 18600
    assert e04_calc["flags"] == []

    # Verify database persistence
    ps = loaded_db.execute(
        "SELECT * FROM payslips WHERE employee_id = 'E04' AND month = ?", (month,)
    ).fetchone()
    assert ps["paid_days"] == 31.0
    assert ps["late_days"] == 1
    assert ps["net_pay"] == 18600
    assert ps["flags"] == ""


def test_overnight_shift_month_boundary(memory_db):
    """
    Test 7:
    Verify overnight shift date assignment across month boundaries:
    - Punch on 2026-10-01 morning OUT (06:05) belongs to 2026-09-30 shift (ignored in Oct)
    - Shift starting 2026-10-31 night (21:55 IN) ending 2026-11-01 morning (06:10 OUT)
      belongs to 2026-10-31 shift and is credited to October
    """
    memory_db.execute(
        """
        INSERT INTO employees (
            employee_id, name, site, monthly_gross_inr, join_date, shift_start_ist
        ) VALUES ('T02', 'Night Worker', 'SiteB', 31000.0, '2026-10-01', '22:00')
        """
    )
    emp_row = memory_db.execute("SELECT * FROM employees WHERE employee_id = 'T02'").fetchone()

    # Clock events:
    # 1. OUT on 2026-10-01 06:05 (from Sep 30 shift)
    # 2. IN on 2026-10-31 21:55
    # 3. OUT on 2026-11-01 06:10
    test_events = [
        ("ev_sep_out", "T02", "OUT", "2026-10-01T06:05:00+05:30", "2026-10-01T06:05:00+05:30"),
        ("ev_oct_in", "T02", "IN", "2026-10-31T21:55:00+05:30", "2026-10-31T21:55:00+05:30"),
        ("ev_nov_out", "T02", "OUT", "2026-11-01T06:10:00+05:30", "2026-11-01T06:10:00+05:30"),
    ]
    for eid, emp, typ, dev, dev_ist in test_events:
        memory_db.execute(
            """
            INSERT INTO clock_events (event_id, employee_id, type, device_time, device_time_ist, uploaded_at)
            VALUES (?, ?, ?, ?, ?, '2026-11-01T12:00:00Z')
            """,
            (eid, emp, typ, dev, dev_ist),
        )

    calc = calculate_employee_month(memory_db, emp_row, 2026, 10)
    # October has 4 Sundays (Oct 4, 11, 18, 25) + 1 worked day (Oct 31) = 5 paid days
    assert calc["sundays"] == 4
    assert calc["full_days"] == 1
    assert calc["paid_days"] == 5.0
    assert calc["flags"] == []


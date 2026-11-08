"""
Brayd Payroll Calculator
========================
A small, self-contained payroll calculation engine for field workers.
Built with Python standard library and SQLite.
"""

from __future__ import annotations

import csv
import calendar
import os
import sys

# Ensure UTF-8 output on all platforms including Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from datetime import datetime, date, time, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sqlite3

# Indian Standard Time (UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_DB_PATH = "payroll.db"


def round_paisa(value: Decimal | float | int) -> Decimal:
    """Round monetary line item to nearest paisa (2 decimal places), halves round up."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def round_rupee(value: Decimal | float | int) -> Decimal:
    """Round net pay to nearest rupee (integer), halves round up."""
    return Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def parse_to_ist(ts_str: str) -> datetime:
    """
    Parse an ISO timestamp (supporting both Z/UTC and explicit offsets like +05:30)
    and convert it to an aware datetime in Indian Standard Time (IST).
    """
    cleaned = ts_str.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        # Assume UTC if no timezone is provided
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open and initialize a connection to the SQLite database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create database tables and unique indices if they do not exist."""
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS employees (
                employee_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                site TEXT NOT NULL,
                monthly_gross_inr REAL NOT NULL,
                join_date TEXT NOT NULL,
                shift_start_ist TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS clock_events (
                event_id TEXT PRIMARY KEY,
                employee_id TEXT NOT NULL REFERENCES employees(employee_id),
                type TEXT NOT NULL,
                device_time TEXT NOT NULL,
                device_time_ist TEXT NOT NULL,
                uploaded_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS locked_months (
                month TEXT PRIMARY KEY,
                locked_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payslips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT NOT NULL REFERENCES employees(employee_id),
                month TEXT NOT NULL,
                paid_days REAL NOT NULL,
                late_days INTEGER NOT NULL,
                overtime_minutes INTEGER NOT NULL,
                net_pay INTEGER NOT NULL,
                flags TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(employee_id, month)
            );

            CREATE TABLE IF NOT EXISTS payslip_line_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payslip_id INTEGER NOT NULL REFERENCES payslips(id) ON DELETE CASCADE,
                line_type TEXT NOT NULL,
                description TEXT NOT NULL,
                amount REAL NOT NULL,
                UNIQUE(payslip_id, line_type)
            );
        """)


def resolve_file(filename: str) -> Path:
    """Find a file in the current directory or data subdirectories."""
    candidate = Path(filename)
    if candidate.is_file():
        return candidate
    sub_candidate = Path("brayd_payroll_assessment_data") / filename
    if sub_candidate.is_file():
        return sub_candidate
    parent_sub = Path("..") / "brayd_payroll_assessment_data" / filename
    if parent_sub.is_file():
        return parent_sub
    return candidate


def load_data(
    conn: sqlite3.Connection,
    employees_path: str = "employees.csv",
    clock_events_path: str = "clock_events.csv",
) -> tuple[int, int]:
    """
    Load employees.csv and clock_events.csv into the database.
    Running it repeatedly does not duplicate rows (idempotent).
    Returns (num_employees_loaded, num_events_loaded).
    """
    emp_file = resolve_file(employees_path)
    if not emp_file.exists():
        raise FileNotFoundError(f"Employees file not found: {emp_file}")

    events_file = resolve_file(clock_events_path)
    if not events_file.exists():
        raise FileNotFoundError(f"Clock events file not found: {events_file}")

    emp_count = 0
    with open(emp_file, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        with conn:
            for row in reader:
                conn.execute(
                    """
                    INSERT INTO employees (
                        employee_id, name, site, monthly_gross_inr, join_date, shift_start_ist
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(employee_id) DO UPDATE SET
                        name = excluded.name,
                        site = excluded.site,
                        monthly_gross_inr = excluded.monthly_gross_inr,
                        join_date = excluded.join_date,
                        shift_start_ist = excluded.shift_start_ist
                    """,
                    (
                        row["employee_id"].strip(),
                        row["name"].strip(),
                        row["site"].strip(),
                        float(row["monthly_gross_inr"]),
                        row["join_date"].strip(),
                        row["shift_start_ist"].strip(),
                    ),
                )
                emp_count += 1

    event_count = 0
    with open(events_file, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        with conn:
            for row in reader:
                eid = row["event_id"].strip()
                emp_id = row["employee_id"].strip()
                ev_type = row["type"].strip().upper()
                raw_time = row["device_time"].strip()
                uploaded_at = row["uploaded_at"].strip()

                dt_ist = parse_to_ist(raw_time)
                ist_str = dt_ist.isoformat()

                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO clock_events (
                        event_id, employee_id, type, device_time, device_time_ist, uploaded_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (eid, emp_id, ev_type, raw_time, ist_str, uploaded_at),
                )
                if cur.rowcount > 0:
                    event_count += 1

    return emp_count, event_count


def calculate_employee_month(
    conn: sqlite3.Connection,
    employee_row: sqlite3.Row,
    year: int,
    month: int,
) -> dict:
    """
    Calculate attendance, line items, and net pay for a single employee in a given month.
    Follows Brayd's exact pay & attendance rules.
    """
    emp_id = employee_row["employee_id"]
    gross = Decimal(str(employee_row["monthly_gross_inr"]))
    join_date = date.fromisoformat(employee_row["join_date"])
    shift_h, shift_m = map(int, employee_row["shift_start_ist"].split(":"))

    num_days_in_month = calendar.monthrange(year, month)[1]
    daily_rate = gross / Decimal(num_days_in_month)

    # Fetch all clock events for this employee in IST month range
    month_str = f"{year:04d}-{month:02d}"
    cursor = conn.execute(
        """
        SELECT event_id, type, device_time_ist
        FROM clock_events
        WHERE employee_id = ?
        ORDER BY device_time_ist ASC
        """,
        (emp_id,),
    )
    all_events = cursor.fetchall()

    # Partition events by their IST calendar date
    day_events: dict[date, list[tuple[datetime, str, str]]] = {}
    for ev in all_events:
        dt = datetime.fromisoformat(ev["device_time_ist"])
        if dt.year == year and dt.month == month:
            d = dt.date()
            day_events.setdefault(d, []).append((dt, ev["type"], ev["event_id"]))

    sundays = 0
    full_days = 0
    half_days = 0
    absent_days = 0
    late_days = 0
    total_ot_minutes = 0
    flags: list[str] = []

    for day_num in range(1, num_days_in_month + 1):
        cal_date = date(year, month, day_num)

        # Ignore days before join_date
        if cal_date < join_date:
            continue

        # Sundays are paid weekly offs. Work on Sunday is ignored.
        if cal_date.weekday() == 6:  # Sunday
            sundays += 1
            continue

        events_on_day = day_events.get(cal_date, [])
        if not events_on_day:
            absent_days += 1
            continue

        in_times = [dt for dt, typ, _ in events_on_day if typ == "IN"]
        out_times = [dt for dt, typ, _ in events_on_day if typ == "OUT"]

        # An IN without an OUT or an OUT without an IN is absent and flagged MISSING_PUNCH
        if not in_times or not out_times:
            absent_days += 1
            punch_info = "IN only" if in_times else "OUT only"
            flags.append(f"{cal_date}: MISSING_PUNCH ({punch_info})")
            continue

        earliest_in = min(in_times)
        latest_out = max(out_times)
        worked_seconds = (latest_out - earliest_in).total_seconds()
        worked_minutes = int(worked_seconds // 60)

        # 240 worked minutes or more is full day; fewer is half day.
        # Note on overnight shifts: if OUT precedes IN on calendar date,
        # worked_minutes is negative. Literal rule "fewer is a half day" applies.
        if worked_minutes >= 240:
            full_days += 1
            is_worked_day = True
        else:
            half_days += 1
            is_worked_day = True
            if worked_minutes < 0:
                flags.append(
                    f"{cal_date}: NEGATIVE_WORKED_MINUTES ({worked_minutes}m, OUT precedes IN)"
                )

        # Late day check: earliest IN > 10 mins after shift start, counted only on full or half days
        shift_dt = earliest_in.replace(
            hour=shift_h, minute=shift_m, second=0, microsecond=0
        )
        late_diff_minutes = int((earliest_in - shift_dt).total_seconds() // 60)
        if is_worked_day and late_diff_minutes > 10:
            late_days += 1

        # Overtime: worked minutes beyond 540 in a day
        if worked_minutes > 540:
            total_ot_minutes += (worked_minutes - 540)

    # Pay computation
    paid_days = Decimal(sundays) + Decimal(full_days) + (Decimal("0.5") * Decimal(half_days))
    base_pay = round_paisa(daily_rate * paid_days)

    hourly_rate = daily_rate / Decimal(8)
    ot_rate = hourly_rate * Decimal("1.5")
    ot_pay = round_paisa(ot_rate * (Decimal(total_ot_minutes) / Decimal(60)))

    # Late deduction: 0.5 day pay for every 3 late days
    late_deduct_days = Decimal(late_days // 3) * Decimal("0.5")
    late_deduction = round_paisa(daily_rate * late_deduct_days)

    net_pay = round_rupee(base_pay + ot_pay - late_deduction)

    line_items = [
        {
            "line_type": "BASE_PAY",
            "description": f"Base Pay ({paid_days} paid days @ ₹{daily_rate:.2f}/day)",
            "amount": float(base_pay),
        },
        {
            "line_type": "OVERTIME",
            "description": f"Overtime Pay ({total_ot_minutes} mins @ ₹{ot_rate:.2f}/hr)",
            "amount": float(ot_pay),
        },
        {
            "line_type": "LATE_DEDUCTION",
            "description": f"Late Deduction ({late_deduct_days} days for {late_days} late days)",
            "amount": float(late_deduction),
        },
    ]

    return {
        "employee_id": emp_id,
        "month": month_str,
        "paid_days": float(paid_days),
        "sundays": sundays,
        "full_days": full_days,
        "half_days": half_days,
        "absent_days": absent_days,
        "late_days": late_days,
        "overtime_minutes": total_ot_minutes,
        "daily_rate": float(daily_rate),
        "hourly_rate": float(hourly_rate),
        "base_pay": float(base_pay),
        "ot_pay": float(ot_pay),
        "late_deduction": float(late_deduction),
        "net_pay": int(net_pay),
        "flags": flags,
        "line_items": line_items,
    }


def is_month_locked(conn: sqlite3.Connection, month_str: str) -> bool:
    """Check if payroll calculation for a month is locked."""
    cur = conn.execute("SELECT 1 FROM locked_months WHERE month = ?", (month_str,))
    return cur.fetchone() is not None


def lock_month(conn: sqlite3.Connection, month_str: str) -> None:
    """Lock a payroll month to prevent future recomputation."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO locked_months (month, locked_at)
            VALUES (?, ?)
            ON CONFLICT(month) DO UPDATE SET locked_at = excluded.locked_at
            """,
            (month_str, now_iso),
        )


def run_payroll(conn: sqlite3.Connection, month_str: str) -> list[dict]:
    """
    Calculate and persist payslips for all employees for the given month.
    Refuses to run if the month is locked.
    Running it repeatedly produces identical results without duplicates.
    """
    if is_month_locked(conn, month_str):
        raise PermissionError(
            f"Payroll for month {month_str} is LOCKED and cannot be modified."
        )

    parts = month_str.split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid month format: '{month_str}'. Expected 'YYYY-MM'.")
    year, month = int(parts[0]), int(parts[1])

    cursor = conn.execute("SELECT * FROM employees ORDER BY employee_id ASC")
    employees = cursor.fetchall()

    results = []
    now_iso = datetime.now(timezone.utc).isoformat()

    with conn:
        for emp in employees:
            calc = calculate_employee_month(conn, emp, year, month)
            flags_str = "; ".join(calc["flags"]) if calc["flags"] else ""

            # Upsert payslip
            conn.execute(
                """
                INSERT INTO payslips (
                    employee_id, month, paid_days, late_days, overtime_minutes,
                    net_pay, flags, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(employee_id, month) DO UPDATE SET
                    paid_days = excluded.paid_days,
                    late_days = excluded.late_days,
                    overtime_minutes = excluded.overtime_minutes,
                    net_pay = excluded.net_pay,
                    flags = excluded.flags,
                    updated_at = excluded.updated_at
                """,
                (
                    calc["employee_id"],
                    month_str,
                    calc["paid_days"],
                    calc["late_days"],
                    calc["overtime_minutes"],
                    calc["net_pay"],
                    flags_str,
                    now_iso,
                    now_iso,
                ),
            )

            # Retrieve payslip ID
            ps_row = conn.execute(
                "SELECT id FROM payslips WHERE employee_id = ? AND month = ?",
                (calc["employee_id"], month_str),
            ).fetchone()
            payslip_id = ps_row["id"]

            # Replace line items cleanly
            conn.execute(
                "DELETE FROM payslip_line_items WHERE payslip_id = ?",
                (payslip_id,),
            )
            for item in calc["line_items"]:
                conn.execute(
                    """
                    INSERT INTO payslip_line_items (
                        payslip_id, line_type, description, amount
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        payslip_id,
                        item["line_type"],
                        item["description"],
                        item["amount"],
                    ),
                )

            results.append(calc)

    return results


def load_late_events(
    conn: sqlite3.Connection,
    late_events_path: str = "late_events.csv",
) -> list[dict]:
    """
    Load late_events.csv without altering locked payslips.
    For each affected employee, recomputes the locked month, calculates arrears,
    and stores/updates the arrears line on that employee's November 2026 payslip.
    Idempotent: running multiple times never doubles arrears.
    """
    late_file = resolve_file(late_events_path)
    if not late_file.exists():
        raise FileNotFoundError(f"Late events file not found: {late_file}")

    affected_employees = set()
    new_events_count = 0

    with open(late_file, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        with conn:
            for row in reader:
                eid = row["event_id"].strip()
                emp_id = row["employee_id"].strip()
                ev_type = row["type"].strip().upper()
                raw_time = row["device_time"].strip()
                uploaded_at = row["uploaded_at"].strip()

                dt_ist = parse_to_ist(raw_time)
                ist_str = dt_ist.isoformat()
                affected_employees.add(emp_id)

                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO clock_events (
                        event_id, employee_id, type, device_time, device_time_ist, uploaded_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (eid, emp_id, ev_type, raw_time, ist_str, uploaded_at),
                )
                if cur.rowcount > 0:
                    new_events_count += 1

    arrears_records = []
    now_iso = datetime.now(timezone.utc).isoformat()
    locked_month_str = "2026-10"
    next_month_str = "2026-11"

    for emp_id in sorted(affected_employees):
        emp_row = conn.execute(
            "SELECT * FROM employees WHERE employee_id = ?", (emp_id,)
        ).fetchone()
        if not emp_row:
            continue

        # Fetch locked October payslip
        locked_ps = conn.execute(
            "SELECT * FROM payslips WHERE employee_id = ? AND month = ?",
            (emp_id, locked_month_str),
        ).fetchone()

        if not locked_ps:
            # If October wasn't computed yet, calculate it as locked
            locked_net = 0
        else:
            locked_net = locked_ps["net_pay"]

        # Recompute what October should have paid with late events included
        recomputed = calculate_employee_month(conn, emp_row, 2026, 10)
        recomputed_net = recomputed["net_pay"]
        arrears_amount = recomputed_net - locked_net

        # Upsert arrears onto November 2026 payslip
        with conn:
            conn.execute(
                """
                INSERT INTO payslips (
                    employee_id, month, paid_days, late_days, overtime_minutes,
                    net_pay, flags, created_at, updated_at
                ) VALUES (?, ?, 0, 0, 0, ?, ?, ?, ?)
                ON CONFLICT(employee_id, month) DO UPDATE SET
                    net_pay = excluded.net_pay,
                    flags = excluded.flags,
                    updated_at = excluded.updated_at
                """,
                (
                    emp_id,
                    next_month_str,
                    arrears_amount,
                    f"ARREARS_ADJUSTMENT: Recomputed from {locked_month_str} late events",
                    now_iso,
                    now_iso,
                ),
            )

            ps_nov = conn.execute(
                "SELECT id FROM payslips WHERE employee_id = ? AND month = ?",
                (emp_id, next_month_str),
            ).fetchone()
            nov_payslip_id = ps_nov["id"]

            conn.execute(
                "DELETE FROM payslip_line_items WHERE payslip_id = ? AND line_type = 'ARREARS'",
                (nov_payslip_id,),
            )
            conn.execute(
                """
                INSERT INTO payslip_line_items (
                    payslip_id, line_type, description, amount
                ) VALUES (?, 'ARREARS', ?, ?)
                """,
                (
                    nov_payslip_id,
                    f"Arrears adjustment from {locked_month_str} late events",
                    float(arrears_amount),
                ),
            )

        arrears_records.append(
            {
                "employee_id": emp_id,
                "locked_net": locked_net,
                "recomputed_net": recomputed_net,
                "arrears": arrears_amount,
            }
        )

    return arrears_records


def format_payslip(conn: sqlite3.Connection, employee_id: str, month_str: str) -> str:
    """Format a human-readable payslip for console output."""
    emp = conn.execute(
        "SELECT * FROM employees WHERE employee_id = ?", (employee_id,)
    ).fetchone()
    if not emp:
        return f"Error: Employee '{employee_id}' not found."

    ps = conn.execute(
        "SELECT * FROM payslips WHERE employee_id = ? AND month = ?",
        (employee_id, month_str),
    ).fetchone()
    if not ps:
        return f"Error: No payslip found for employee '{employee_id}' and month '{month_str}'."

    line_items = conn.execute(
        "SELECT * FROM payslip_line_items WHERE payslip_id = ? ORDER BY id ASC",
        (ps["id"],),
    ).fetchall()

    year_val, month_val = map(int, month_str.split("-"))
    month_name = calendar.month_name[month_val]
    days_in_m = calendar.monthrange(year_val, month_val)[1]
    gross = Decimal(str(emp["monthly_gross_inr"]))
    daily_rate = gross / Decimal(days_in_m)
    hourly_rate = daily_rate / Decimal(8)

    lines = []
    lines.append("=" * 64)
    lines.append(f"PAYSLIP FOR {month_name.upper()} {year_val}".center(64))
    lines.append("=" * 64)
    lines.append(f"Employee ID      : {emp['employee_id']}")
    lines.append(f"Name             : {emp['name']}")
    lines.append(f"Site             : {emp['site']}")
    lines.append(f"Shift Start (IST): {emp['shift_start_ist']}")
    lines.append(f"Monthly Gross    : ₹{gross:,.2f}")
    lines.append(f"Daily Rate       : ₹{daily_rate:,.2f}  ({days_in_m} days)")
    lines.append(f"Hourly Rate      : ₹{hourly_rate:,.2f}")
    lines.append("-" * 64)
    lines.append("ATTENDANCE SUMMARY:")
    lines.append(f"  Paid Days      : {ps['paid_days']}")
    lines.append(f"  Late Days      : {ps['late_days']}")
    lines.append(f"  Overtime       : {ps['overtime_minutes']} minutes")
    flags_val = ps["flags"]
    if flags_val:
        lines.append(f"  Flags          : {flags_val}")
    else:
        lines.append("  Flags          : None")
    lines.append("-" * 64)
    lines.append("LINE ITEMS:")
    for item in line_items:
        amt = item["amount"]
        sign = "-" if item["line_type"] == "LATE_DEDUCTION" and amt > 0 else " "
        lines.append(
            f"  {item['line_type']:<15} : {sign}₹{abs(amt):>9.2f}  ({item['description']})"
        )
    lines.append("-" * 64)
    lines.append(f"NET PAY          :  ₹{ps['net_pay']:,}")
    lines.append("=" * 64)

    return "\n".join(lines)


def main() -> None:
    """CLI Entry point supporting all Brayd payroll commands."""
    args = sys.argv[1:]
    if not args:
        print("Brayd Payroll CLI")
        print("Usage:")
        print("  python payroll.py load")
        print("  python payroll.py run <YYYY-MM>")
        print("  python payroll.py payslip <employee_id> <YYYY-MM>")
        print("  python payroll.py lock <YYYY-MM>")
        print("  python payroll.py late <late_events.csv>")
        sys.exit(0)

    cmd = args[0].lower()
    conn = get_connection()

    try:
        if cmd == "load":
            emp_count, ev_count = load_data(conn)
            print(f"Loaded {emp_count} employees and {ev_count} new clock events into {DEFAULT_DB_PATH}.")

        elif cmd == "run":
            if len(args) < 2:
                print("Error: Missing month. Usage: python payroll.py run <YYYY-MM>")
                sys.exit(1)
            month_str = args[1].strip()
            results = run_payroll(conn, month_str)
            print(f"Successfully calculated and saved {len(results)} payslips for {month_str}.")

        elif cmd == "payslip":
            if len(args) < 3:
                print("Error: Usage: python payroll.py payslip <employee_id> <YYYY-MM>")
                sys.exit(1)
            emp_id = args[1].strip()
            month_str = args[2].strip()
            print(format_payslip(conn, emp_id, month_str))

        elif cmd == "lock":
            if len(args) < 2:
                print("Error: Missing month. Usage: python payroll.py lock <YYYY-MM>")
                sys.exit(1)
            month_str = args[1].strip()
            lock_month(conn, month_str)
            print(f"Payroll for {month_str} has been LOCKED. Further runs are prohibited.")

        elif cmd == "late":
            late_file = args[1].strip() if len(args) > 1 else "late_events.csv"
            arrears = load_late_events(conn, late_file)
            print(f"Loaded late events from {late_file}.")
            for rec in arrears:
                print(
                    f"Employee {rec['employee_id']}: locked net ₹{rec['locked_net']}, "
                    f"recomputed net ₹{rec['recomputed_net']} -> Arrears ₹{rec['arrears']} recorded on 2026-11 payslip."
                )

        else:
            print(f"Unknown command: '{cmd}'.")
            sys.exit(1)

    except PermissionError as pe:
        print(f"Error: {pe}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

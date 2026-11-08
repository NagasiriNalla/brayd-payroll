-- ==============================================================================
-- Brayd Payroll Assessment - Part 3: SQL Report
-- Database: payroll.db (SQLite)
-- Target Period: October 2026 ('2026-10') Locked Payslips
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- Query 1: Total net pay per site, highest first
-- ------------------------------------------------------------------------------
SELECT 
    e.site,
    SUM(p.net_pay) AS total_net_pay
FROM payslips p
JOIN employees e 
    ON p.employee_id = e.employee_id
WHERE p.month = '2026-10'
GROUP BY e.site
ORDER BY total_net_pay DESC;


-- ------------------------------------------------------------------------------
-- Query 2: Every employee whose net pay is below 75% of their monthly gross,
--          showing gross, net, and the percentage
-- ------------------------------------------------------------------------------
SELECT 
    e.employee_id,
    e.name,
    e.monthly_gross_inr AS monthly_gross,
    p.net_pay,
    ROUND((CAST(p.net_pay AS REAL) * 100.0) / e.monthly_gross_inr, 2) AS pct_of_gross
FROM payslips p
JOIN employees e 
    ON p.employee_id = e.employee_id
WHERE p.month = '2026-10'
  AND (CAST(p.net_pay AS REAL) / e.monthly_gross_inr) < 0.75
ORDER BY pct_of_gross ASC;

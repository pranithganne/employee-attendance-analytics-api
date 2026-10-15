# DECISIONS.md

1. Indexes. I created a unique index on `employees.emp_code` to prevent duplicate employee codes, plus a compound index on `department` and `joined_on` for employee queries. On attendance, a unique `(emp_code, date)` index prevents duplicate daily records. I also added indexes for date-based attendance listing, date/status filtering, and date-based late-minute queries. I would avoid adding a standalone `status` index without evidence that it improves the actual query workload.

2. **Punch-in race.** Two requests can both check for an existing record before either inserts. The unique `(emp_code, date)` index is the final safeguard: only one insert can succeed. The other request must handle the duplicate-key error and return HTTP 409.

3. **Ties.** The late leaderboard sorts by total late minutes descending and employee code ascending. This makes the result deterministic when employees have equal late-minute totals and the limit cuts through a tie.

4. **Headcount.** The department summary starts from employee records and joins attendance data, rather than counting only attendance logs. This allows employees with no attendance logs to remain represented in the headcount.

5. **Scaling further.** If the system had to handle 100 times more data, I would benchmark the main queries with realistic data, inspect execution statistics, and tune indexes based on measured workload and query plans.
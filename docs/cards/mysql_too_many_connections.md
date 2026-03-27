# mysql_too_many_connections

## Event
MySQL reports too many connections and application requests begin to fail.

## Possible Causes
- connection leak in application code
- traffic spike exhausting connection pool
- max_connections too low for workload
- slow queries causing connections to remain occupied

## Suggested Checks
- inspect active connections and connection state breakdown
- inspect application pool usage and timeout settings
- inspect slow query log and lock contention
- inspect recent traffic changes or release impact

## References
- MySQL connection management docs
- MySQL performance troubleshooting guides

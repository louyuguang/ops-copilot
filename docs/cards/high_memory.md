# high_memory

## Event
Service memory usage remains above 85% for 15 minutes.

## Possible Causes
- memory leak
- cache growth beyond expectation
- insufficient memory limits
- large in-memory workload or batch processing

## Suggested Checks
- inspect memory trend over time
- inspect GC behavior and allocation patterns
- inspect recent releases and feature flags
- inspect restart / OOM kill history

## References
- Kubernetes memory troubleshooting docs
- Runtime-specific memory tuning guides

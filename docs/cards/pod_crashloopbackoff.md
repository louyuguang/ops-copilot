# pod_crashloopbackoff

## Event
A pod repeatedly restarts and enters CrashLoopBackOff.

## Possible Causes
- application startup failure
- invalid configuration or missing secret
- dependency unavailable during startup
- command / entrypoint error

## Suggested Checks
- inspect pod describe output and recent events
- inspect container logs from current and previous runs
- inspect environment variables, config maps, and secrets
- inspect readiness / liveness probe settings

## References
- Kubernetes CrashLoopBackOff troubleshooting docs
- Container startup debugging guides

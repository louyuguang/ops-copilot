# high_cpu

## Event
Service CPU usage remains above 90% for 10 minutes.

## Possible Causes
- traffic spike
- inefficient code path
- low CPU limit or resource saturation
- blocked downstream dependency causing thread buildup

## Suggested Checks
- inspect QPS and latency trends
- inspect recent deployments or config changes
- inspect application logs for hot paths or errors
- inspect pod restarts, throttling, or OOM indicators

## References
- Kubernetes resource management docs
- General service CPU saturation troubleshooting guides

# nginx_5xx_spike

## Event
Nginx 5xx responses increase significantly in a short period.

## Possible Causes
- upstream service unavailable or overloaded
- upstream timeout
- bad deployment or config change
- insufficient gateway resources

## Suggested Checks
- inspect 5xx type distribution and timing
- inspect upstream service health and latency
- inspect nginx error logs and upstream timeout errors
- inspect recent deployment or config changes

## References
- Nginx upstream troubleshooting docs
- General HTTP 5xx debugging guides

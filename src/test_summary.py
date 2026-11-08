from daily_summary import build_summary
from stats import log_event

log_event("message", 111)
log_event("message", 111)
log_event("message", 222)
log_event("handoff", 222, "refund request")

print(build_summary())
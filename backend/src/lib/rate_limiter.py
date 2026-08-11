"""
Shared slowapi Limiter instance.

Extracted from main.py so route modules (e.g. saas_auth.py) can apply
@limiter.limit(...) to their own endpoints without a circular import
(main.py imports those routers; a route module importing `limiter` back out
of main.py would import main.py while it's still mid-import). main.py still
owns wiring this into app.state and the RateLimitExceeded exception handler -
this module only holds the shared instance itself.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

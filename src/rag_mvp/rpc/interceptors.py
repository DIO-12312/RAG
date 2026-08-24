"""gRPC interceptor boundary.

Request IDs, deadlines, default-tenant injection, and exception mapping are
implemented alongside the first application RPCs in Milestone B. Keeping this
module transport-only prevents import-time adapter construction.
"""

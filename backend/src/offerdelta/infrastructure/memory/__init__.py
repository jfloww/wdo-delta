"""In-memory adapters.

Real implementations of the application's ports, not test doubles. They back
the deployed demo today and will keep backing tests once PostgreSQL and
DynamoDB arrive, so the ports stay honest — a port only used by one adapter
tends to grow that adapter's shape.
"""

# app/security/__init__.py
"""
Security components for Sentinel.

Modules:
  pii.py        — account ID hashing (SHA-256 + salt), PAN masking
  retention.py  — data retention purge (order/trade rows)
  access_log.py — evidence access audit log (EvidenceAccessLog model)
"""

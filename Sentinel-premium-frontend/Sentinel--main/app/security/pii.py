"""
PII Protection Utilities
=========================

Provides two distinct operations that serve different purposes and must
NOT be confused with each other:

  hash_account_identifier()  — one-way, for storage and logging
  mask_pan()                 — reversible display redaction, NOT a hash

LEGAL/REGULATORY CONTEXT (Hard Rule #4)
-----------------------------------------
We are NOT claiming GDPR applies. This is an Indian system.

What we ARE certain of:
  1. SEBI Circular CIR/ISD/1/2011 and subsequent surveillance circulars
     impose a general confidentiality obligation on surveillance data:
     exchanges and intermediaries must prevent unauthorised disclosure
     of account-level information gathered in the course of surveillance.
  2. The Information Technology (Reasonable Security Practices and
     Procedures and Sensitive Personal Data or Information) Rules, 2011
     (under IT Act 2000, Section 43A) defines "sensitive personal data
     or information" to include financial information such as bank account
     or credit card details. PAN numbers and brokerage account IDs linked
     to financial transactions may fall under this category — but we do
     NOT state this definitively; a compliance lawyer should confirm.
  3. SEBI Regulations themselves require that investigation files be kept
     confidential. Storing raw account IDs in application logs, access
     logs, and analytics pipelines increases the blast radius if those
     systems are compromised — hashing limits that damage.

What we are NOT claiming:
  - That hashing provides GDPR-equivalent "pseudonymisation".
  - That this implementation satisfies any specific SEBI PII rule
    (no such rule is known to the authors as of 2024).
  - That this is "fully secure" (see SECURITY POSTURE note below).

SECURITY POSTURE (Hard Rule #5)
---------------------------------
These functions protect against ACCIDENTAL LEAKAGE, not determined attackers:

  PROTECTS AGAINST accidental leakage:
    - Application logs containing raw account IDs ending up in log
      aggregation platforms with broad internal access.
    - Analysts accidentally emailing evidence CSVs with raw IDs.
    - Hash collisions are negligible for SHA-256 at this scale.

  DOES NOT PROTECT against:
    - A determined attacker with access to both the hash AND a list of
      known account IDs (they can hash-and-compare, i.e. a targeted
      dictionary attack). Salting raises the cost significantly but does
      not eliminate this threat if the attacker knows the salt.
    - An insider who has legitimate access to the unhashed `account_id`
      column in the database.
    - A compromised server where both the salt (env var) and the DB are
      accessible simultaneously.

In other words: this is a defence-in-depth measure for a low-adversary
environment, NOT a cryptographic guarantee of anonymity.

HASH ALGORITHM
---------------
SHA-256 (hashlib.sha256) with a per-deployment salt from an environment
variable (ACCOUNT_ID_SALT).

Why SHA-256 and not bcrypt/scrypt?
  bcrypt/scrypt are designed for password hashing where the human-chosen
  password is low-entropy and must resist brute force. Account IDs from
  an exchange are system-generated alphanumeric strings with higher
  entropy — SHA-256 with a salt is adequate for this use case and orders
  of magnitude faster, which matters when hashing millions of order rows.

Why salting?
  Unsalted SHA-256 of "client_id = AB1234567" is always the same across
  all deployments. An attacker who obtains a hash list and knows the
  NSE account ID format (two alpha + seven digits) can pre-compute all
  ~677 million combinations in minutes. A per-deployment salt makes this
  pre-computation worthless — they would need the salt to compute any
  candidate hashes.

Why NOT store the salt in source code?
  Committing the salt to git means everyone with repo access has the
  salt. The salt must come from an environment variable so it can be
  rotated, kept out of version control, and managed by ops rather than
  embedded in the application binary.
"""

import hashlib
import hmac
import os
import re
from typing import Optional


# ── Salt ─────────────────────────────────────────────────────────────────────
# Must be set in the environment before processing any real account data.
# Do NOT hardcode a fallback salt here — that would defeat the purpose.
# Failing loudly on missing config is better than silently using a
# predictable salt that turns hashing into security theater.
_ENV_SALT_KEY = "ACCOUNT_ID_SALT"
_SENTINEL_CHARS = 64  # expected minimum output hex chars from SHA-256 (= 64)


def _get_salt() -> str:
    """
    Retrieve the per-deployment salt from the environment.

    Raises RuntimeError if the environment variable is not set or is
    shorter than 16 characters (a salt shorter than 16 chars offers
    negligible protection over no salt).
    """
    salt = os.environ.get(_ENV_SALT_KEY, "")
    if not salt:
        raise RuntimeError(
            f"Environment variable '{_ENV_SALT_KEY}' is not set. "
            "Set it to a random string of at least 16 characters before "
            "processing real account data. "
            "Example (bash): export ACCOUNT_ID_SALT=$(openssl rand -hex 32)"
        )
    if len(salt) < 16:
        raise RuntimeError(
            f"'{_ENV_SALT_KEY}' is only {len(salt)} characters long. "
            "Minimum 16 characters required to provide meaningful salting."
        )
    return salt


def hash_account_identifier(raw_id: str, salt: Optional[str] = None) -> str:
    """
    Produce a salted SHA-256 hash of an account identifier for storage
    and logging contexts where the raw ID must not appear in plaintext.

    Parameters
    ----------
    raw_id
        The raw account identifier (e.g. broker client ID, exchange member ID).
        Must be a non-empty string.
    salt
        Optional explicit salt. If None, reads from ACCOUNT_ID_SALT env var.
        Providing an explicit salt is useful in tests — in production,
        always use the env var default.

    Returns
    -------
    str
        A 64-character hex string (SHA-256 digest of salt + raw_id).
        The output format is: hex(SHA-256(salt || ":" || raw_id)).

    Raises
    ------
    ValueError
        If raw_id is empty or whitespace-only. An empty hash would be
        meaningless and could accidentally match other empty-string inputs.
    RuntimeError
        If salt is None and ACCOUNT_ID_SALT env var is not set.

    IMPORTANT: This is a ONE-WAY operation. You cannot recover raw_id
    from the hash. Store the raw_id separately in a protected column
    if you ever need to look it up (see models.py design notes).
    """
    if not raw_id or not raw_id.strip():
        raise ValueError(
            "hash_account_identifier: raw_id must be a non-empty string. "
            "An empty account ID hash would produce misleading results."
        )

    effective_salt = salt if salt is not None else _get_salt()
    # Canonicalize: strip whitespace to prevent trivial bypass via padding
    canonical = raw_id.strip()
    payload = f"{effective_salt}:{canonical}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    assert len(digest) == _SENTINEL_CHARS, \
        f"Unexpected SHA-256 output length: {len(digest)}"
    return digest


def mask_pan(pan: str) -> str:
    """
    Return a display-safe version of a PAN that shows only the last 4
    characters, replacing all other alphanumeric characters with 'X'.

    Use this in any context where the full PAN is not strictly needed:
      - Evidence log display headers
      - SAR report summaries
      - Dashboard alert cards

    Do NOT use this as a storage mechanism. Masking is reversible if the
    original PAN is available (it is just a display format). It is not
    a hash and provides NO protection against someone with access to the
    original PAN column.

    Parameters
    ----------
    pan
        A PAN string (expected: 10 characters, ABCDE1234F format, but
        the function handles arbitrary-length strings defensively).

    Returns
    -------
    str
        The masked PAN with only the last 4 characters visible.
        e.g. "ABCDE1234F" → "XXXXXXX234F" (last 4 shown, rest Xed).

    SECURITY POSTURE: protects against accidental display in logs/screens.
    Does NOT protect raw PAN in database or memory.
    """
    if not pan:
        return ""

    # Strip whitespace — a PAN with leading/trailing spaces is the same PAN
    pan = pan.strip()

    if len(pan) <= 4:
        # PAN shorter than 4 chars: show fully — masking would reveal nothing
        # extra anyway. This handles edge cases defensively.
        return pan

    visible_tail = pan[-4:]
    masked_head = "X" * (len(pan) - 4)
    return masked_head + visible_tail


def is_valid_pan_format(pan: str) -> bool:
    """
    Validate that a string matches the Indian PAN format (AAAAA9999A).
    Used to check before masking/hashing so callers know they have the
    right data type.

    Format: 5 alpha + 4 digit + 1 alpha (case-insensitive).
    Source: Income Tax Department of India, PAN card specification.

    Returns True if valid, False otherwise — does NOT raise.
    """
    if not pan:
        return False
    pattern = r"^[A-Za-z]{5}[0-9]{4}[A-Za-z]{1}$"
    return bool(re.match(pattern, pan.strip()))

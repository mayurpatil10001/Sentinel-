# SECURITY: Purge Credentials from Git History
## Status: PENDING USER ACTION — do not proceed to step 3 until you confirm step 1 and 2

> **NOTE:** The actual secret values are NOT written in this file.  
> Refer to your Fyers developer portal (myapi.fyers.in) for the current
> App ID (`S08D27**`) and the OLD secret that needs purging.  
> The secret scanner in `tests/test_security_secrets.py` will fail if the
> real values appear in any tracked file.

---

## What needs purging

Two values appear in commits `720b9ad`, `6f68912`, and the diff of `93f727e`:

| Item | Placeholder | Commit(s) |
|---|---|---|
| Fyers App ID | `<FYERS_APP_ID>` | 720b9ad, 6f68912, 93f727e diff |
| Fyers App Secret (old) | `<FYERS_APP_SECRET_OLD>` | 720b9ad, 6f68912, 93f727e diff |

Reproduce with (substituting the real values from your portal):
```powershell
git log --all --oneline -S "<FYERS_APP_SECRET_OLD>" --source
git log --all --oneline -S "<FYERS_APP_ID>" --source
```

---

## Step 1 — YOU MUST DO THIS FIRST (cannot be automated)

1. Log in to [myapi.fyers.in](https://myapi.fyers.in)
2. **Rotate the App Secret** — generate a new secret for your app
3. **Revoke all existing access tokens** (the old secret is now invalid)
4. Update your `.env` file (or environment) with `FYERS_SECRET_ID=<new_secret>`
5. Reply "done" so the history rewrite can proceed

> **Why first?** Once a secret appears in git history it must be treated as
> compromised regardless of the history rewrite, because anyone who cloned the
> repo before the rewrite has the secret. Rotating the secret makes the leaked
> value useless.

---

## Step 2 — Reproduce E1 (verify the secret is in history)

```powershell
cd d:\Sentinel
git fetch --unshallow  # only if shallow clone
# substitute <FYERS_APP_SECRET_OLD> and <FYERS_APP_ID> with the real values:
git log --all --oneline -S "<FYERS_APP_SECRET_OLD>" --source
git log --all --oneline -S "<FYERS_APP_ID>" --source
```

Both should return commits. After the rewrite, both should return nothing.

---

## Step 3 — History rewrite (run AFTER step 1 confirmation)

Install `git-filter-repo` if not present:
```powershell
pip install git-filter-repo
```

Create a replacements file `_secrets_to_purge.txt` **outside the repo** (do NOT commit this file).  
**You** must write the real values in this file — they are not stored here:
```
<FYERS_APP_ID>==>REDACTED_APPID
<FYERS_APP_SECRET_OLD>==>REDACTED_SECRET
```

Run the rewrite on all branches:
```powershell
cd d:\Sentinel
git filter-repo --replace-text _secrets_to_purge.txt --force
```

Verify the secrets are gone (substitute real values):
```powershell
git log --all --oneline -S "<FYERS_APP_SECRET_OLD>" --source
git log --all --oneline -S "<FYERS_APP_ID>" --source
# Both must return NO output
```

Then delete the temp file (it contains real secrets):
```powershell
Remove-Item _secrets_to_purge.txt
```

---

## Step 4 — Force-push all branches and have every clone re-fetch

```powershell
# Force push to remote (this rewrites public history — warn all collaborators)
git push origin --force --all
git push origin --force --tags
```

**Every existing clone** must then run:
```powershell
git fetch origin
git reset --hard origin/main
# or: delete and re-clone
```

---

## Step 5 — Verify (paste the output to confirm)

```powershell
# Substitute real values when running:
git log --all --oneline -S "<FYERS_APP_SECRET_OLD>" --source
git log --all --oneline -S "<FYERS_APP_ID>" --source
git ls-files | Select-String "token"
git check-ignore -v data/fyers_token.json .fyers_token.json
```

Expected output: nothing for the first two commands; gitignore entries for the last two.

---

## What was done in code (already committed)

- `.gitignore` now covers `data/fyers_token.json`, `**/fyers_token.json`, and all `*.token.json`
- `data/ingest/fyers_auth.py`: token default path is `~/.sentinel/fyers_token.json`
  (outside the repo), overridable via `SENTINEL_TOKEN_PATH` env var
- Token is explicitly documented as **plain JSON, NOT encrypted**
- File permissions restricted to owner-only on save (best-effort on Windows)
- The word "encrypted" has been removed from all code comments

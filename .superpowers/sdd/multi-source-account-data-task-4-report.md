# Task 4 Report: Durable Import Preview and Safe Content Identity Resolution

## Status

Implemented on branch `workspace-freeze-20260706` from base commit `1270f24`.

## What Changed

- Added `backend/app/services/data_import/identity.py` for exact content identity matching with:
  - strong match order: external content ID, then canonicalized share URL
  - provisional-only title + published timestamp matching
  - NFKC + whitespace normalization only
  - no cross-account matching
- Added `backend/app/services/data_import/service.py` for:
  - durable preview creation using existing parser/adapter contracts
  - org/account scope assertions on service inputs
  - SHA-256 based preview reuse within the same org/account/source/template
  - atomic artifact write with cleanup on DB failure
  - persisted `DataImportRow` and `DataConflict` records for manual resolution flows
  - idempotent row resolution with audit fields on `DataConflict`
- Added `backend/tests/test_content_identity_matching.py`
- Added `backend/tests/test_data_import_preview.py`

## Verification

Ran:

```bash
cd backend
python -m pytest tests/test_data_import_preview.py tests/test_content_identity_matching.py -q
python -m ruff check app/services/data_import/identity.py app/services/data_import/service.py tests/test_content_identity_matching.py tests/test_data_import_preview.py
```

Results:

- `11 passed in 3.30s`
- `All checks passed!`

## Notes

- Preview artifact bytes are stored only on disk under `account-data/{org_id}/{account_id}/{batch_id}/{sha256}.{ext}`.
- Reuse is intentionally limited to the same org/account/source/template and only for uncommitted, non-revoked previews.
- A single provisional candidate remains `NEEDS_RESOLUTION`; it is never auto-promoted to a strong committed link.

## Concerns

- Share URL canonicalization is intentionally conservative: it normalizes scheme/host/default ports, trims trailing slash, and drops query/fragment. If platform-specific URL variants appear later, add explicit canonicalization rules per platform rather than broad fuzzy matching.
- This task stops at preview durability and manual resolution. Commit-time projection into canonical platform content and metric snapshots still needs later tasks.

## Integrity Follow-up (2026-07-22)

Implemented review-driven hardening on top of commit `4285c16`:

- Added DB-backed active preview identity on `DataImportBatch.content_sha256` with partial uniqueness for active previews only.
- Added `PlatformContentRecord.canonical_share_url` and switched strong share URL matching to the canonical field.
- Added terminal manual-resolution audit fields on `DataImportRow`:
  - `resolution_outcome`
  - `resolved_by_id`
  - `resolved_at`
- Hardened `resolve_row_match` so only `NEEDS_RESOLUTION + OPEN conflict` can transition, `no_match` is explicit and auditable, and later different outcomes are rejected.
- Added recovery coverage for preview dedupe conflict handling, storage-write failure rollback, query-preserving URL matching, and timestamp normalization across `Z` and offset inputs.

### Exact Evidence

Ran on **Wednesday, July 22, 2026**:

```bash
cd backend
python -m pytest tests/test_data_import_preview.py tests/test_content_identity_matching.py tests/test_account_data_models.py tests/test_migrations.py -q
python -m ruff check app/models/account_data.py app/services/data_import/identity.py app/services/data_import/service.py tests/test_content_identity_matching.py tests/test_data_import_preview.py tests/test_account_data_models.py tests/test_migrations.py
```

Observed results:

- `37 passed, 1 warning in 7.91s`
- Warning: Alembic emitted a deprecation warning about missing `path_separator` in `alembic.ini`; no test failures.
- `All checks passed!`

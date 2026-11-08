# Offline removal intent ledger

`lumina_orion.removal.RemovalIntentLedger` records a requested removal without
deleting or hiding memory records. It is a request journal, not authorization,
a deletion receipt, or a restore gate. There is no apply command or model/API call.

```python
from lumina_orion.removal import RemovalIntentLedger

ledger = RemovalIntentLedger("invented-removal-intents.sqlite3")
request = ledger.record(
    "orion", "fixture-request-1",
    target_key="fixture:source-A",
    content_sha256="a" * 64,
)
assert request["version"] == 1
assert request["state"] == "requested"
requests = ledger.intents("orion")
```

The caller must supply an appropriate stable target key and a lowercase SHA-256
digest of the intended content version. The example digest is invented. The
ledger does not inspect files or verify that a supplied digest describes real
content. It stores no source text. Keys and digests can still identify or link
information; do not treat them as anonymisation.

The immutable identity is `(profile, request_id)`. Identical retries preserve
the original insertion ID and timestamp. Reusing that key for a changed target
or digest raises `ValueError` without overwriting the request. Profiles are
case-normalised; surrounding identity whitespace is stripped; control characters
are rejected. Unknown profiles, empty/oversized identities, and invalid digests
are rejected before insertion. `intents(profile)` returns only that profile's
requests in insertion order. A request cannot be cancelled or edited through
this first interface; revocation requires a separately designed event type.

The implementation adds only `removal_intents` and SQLite's sequence bookkeeping.
It does not migrate, rewrite, or remove existing notes. One immediate transaction
serialises request lookup/insertion, and the method returns after commit. SQLite
writer contention may wait up to ten seconds and then raise a database error;
callers must report failure rather than claim the request was saved.

## What the tests establish

Synthetic tests cover retry conflicts, competing writers, profile separation,
original note preservation, invalid fields, and fresh-process reads. Abrupt
subprocess exits just before commit and after return are checked in both DELETE
and WAL modes; reopening and retrying yields one committed request and preserves
another profile. These are process-interruption tests, not power-loss tests.

## Restore limitations and next step

The caller chooses the ledger path. A separate file is not automatically an
independent recovery authority. Restoring an old copy of this ledger also loses
newer requests and may reuse insertion IDs. IDs are not fencing tokens,
completeness proofs, or freshness guarantees. Profile filtering prevents
accidental mixing, not access by another process with file permissions.

No automatic matching against restored notes is implemented. The next bounded
step is a read-only restore check that uses explicit stable target/version
mapping, reports ambiguity and missing coverage, and is tested against an older
invented snapshot. Complete restore gating still needs trusted checkpoint
coverage, a current recovery boundary, and atomic application of decisions.
Dependency descendants, cached results, backups, exports, logs, untracked copies,
and physical erasure remain separate work. Do not run this against family
archives or assume that recording a request enforces removal.

Design references: [SQLite transactions](https://www.sqlite.org/lang_transaction.html)
and [AWS idempotent request identifiers](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/).

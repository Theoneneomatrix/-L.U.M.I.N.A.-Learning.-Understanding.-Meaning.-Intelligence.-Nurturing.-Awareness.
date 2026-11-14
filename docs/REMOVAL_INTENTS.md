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

No automatic extraction or matching against actual restored notes is implemented.
The pure read-only comparison below accepts explicit caller-supplied mappings.
Complete restore gating still needs trusted checkpoint coverage, a current
recovery boundary, and atomic application of decisions.
Dependency descendants, cached results, backups, exports, logs, untracked copies,
and physical erasure remain separate work. Do not run this against family
archives or assume that recording a request enforces removal.

Design references: [SQLite transactions](https://www.sqlite.org/lang_transaction.html)
and [AWS idempotent request identifiers](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/).

## Read-only comparison of declared restored records

`lumina_orion.restore.inspect_restore(profile, requests, restored_records)`
compares supplied lists of dictionaries without opening files or databases,
calling a model/network, mutating inputs, changing a schema, or applying removal.
Pass the version 1 requested records returned by `ledger.intents(profile)`, or a
mixed-profile list; only the selected profile participates.

Each restored mapping has `profile`, a unique per-profile `record_key`,
`target_key`, and a lowercase `content_sha256`. The caller supplies all of these.
The checker does not hash, authenticate, or verify original content.

```python
from lumina_orion.restore import inspect_restore

report = inspect_restore("orion", requests, [{
    "profile": "orion", "record_key": "fixture:restored-record",
    "target_key": "fixture:source-A", "content_sha256": "a" * 64,
}])
assert report["read_only"] is True
assert report["reopening"] == "not_assessed"
```

The report has version 1, the normalised profile, `read_only: true`,
`coverage: "caller_supplied_only"`, `reopening: "not_assessed"`, and findings
in supplied request order. It omits content, digests, targets, and foreign-profile
findings; request IDs and matching record keys remain potentially linkable.

| Finding | Meaning within the supplied mappings |
| --- | --- |
| `requested_version_present` | Exactly one record lists this target and requested digest. This is evidence of declared presence, not a deletion receipt. |
| `ambiguous_match` | More than one distinct record lists that exact target/version. |
| `different_version_listed` | The target is listed, but none of its supplied versions has the requested digest. |
| `target_not_listed` | No supplied record lists the target. Coverage may be incomplete. |

`matching_record_keys` is the sorted list of exact candidate record keys.
Other versions of the same target are not exact candidates. Duplicate selected
request IDs or selected record keys, unsupported request versions/states, invalid
profiles, malformed lists, controls in identities, and invalid selected digests
raise `ValueError`. Other-profile payloads do not contribute matches.

Neither missing matches, another version, nor an empty request list establishes
completed removal, current permission, complete coverage, or safe reopening.
A current request can expose a requested version in an older snapshot; restoring
an old ledger as well may hide newer intent. This function has no authority to
resolve that problem.

Seven synthetic tests cover these outcomes, profile collisions, invalid inputs,
old snapshots, unchanged original notes/database bytes, unchanged JSON input,
and identical fresh-process results. Their execution status belongs in the PR
checks; the existence of tests alone is not a passing result. There is no CLI,
archive adapter, content removal, restore gate, or destructive migration.

## Deterministic binding of declared history content

`lumina_orion.history_binding.bind_removal_history(profile, payload)` accepts
strict UTF-8 `bytes` of at most 1 MiB, normalizes the declaration below and returns
exactly `version: 1`, `scheme: lumina-removal-history-sha256-v1`, `profile`,
`history_boundary` and `history_sha256`. It performs no file/database/network I/O.
It identifies declared content, not independently current or complete history.
The following values and digest are invented:

```json
{
  "version": 1,
  "profile": "orion",
  "history_boundary": "fixture:A",
  "requests": [{
    "version": 1,
    "profile": "orion",
    "request_id": "fixture:r1",
    "target_key": "fixture:target",
    "content_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "state": "requested"
  }]
}
```

Every object accepts exactly the shown fields. Envelope and request versions are
integer 1 (not booleans); request state is exactly `requested`. All profiles must
match the selected known profile after normalization; mixed envelopes fail rather
than filter. Boundary and request IDs are nonempty control-free identities of at
most 200 characters, target keys at most 2,000, with edge whitespace stripped.
Digests are lowercase 64-character SHA-256 hex; underlying content is not hashed.
At most 10,000 requests are accepted, additionally constrained by the byte limit.
An empty list is allowed and does not assert absence of unlisted requests.

Request IDs must be unique after normalization. Requests are sorted by request ID
before hashing: they form a declared set, not insertion chronology or an event
stream. Reordering requests, object fields, whitespace, equivalent escapes and
profile case do not change the binding. Other case and Unicode scalar sequences
remain significant; no Unicode normalization occurs. This deliberately differs
from the snapshot manifest scheme, whose array ordering remains significant.
Unknown fields, duplicate JSON members, UTF-16/32 or BOMs, invalid UTF-8, unpaired
surrogates, fractional/exponent/nonfinite numbers, trailing JSON and excessive
parser nesting fail with `ValueError`. The input contract is bounded, not a
hostile-input sandbox.

The exact preimage is ASCII `lumina-removal-history-sha256-v1`, one NUL byte, then
ASCII `json.dumps(normalized_history, sort_keys=True, ensure_ascii=True,
separators=(",", ":"), allow_nan=False)` with no final newline. Hash it with SHA-256.
This project-specific scheme is not JCS or a raw-file checksum. A changed boundary,
profile, request identity, target, content digest, addition or omission changes
the binding. Changing these rules requires a new scheme identifier.

`require_history_binding(profile, payload, expected)` validates the exact five-field
expected binding and returns detached normalized history only on equality;
otherwise it raises `ValueError`. Select and retain `expected` independently.
Recomputing it from the candidate, or replacing both inputs, defeats the check.
The returned declaration is not a success/permission report. It has no authority,
freshness, approval or reopening claim, and it does not upgrade the separate
history-observation comparator. Existing observation schemas remain unchanged.

This API neither extracts a ledger nor stores a binding. Full ledger exports
contain extra local ID/time fields and must not be passed as this declaration.
A future adapter must define its explicit projection and coverage. No export is
automatically projected, no private paths/text/reviewer narratives are required,
and no metadata retention policy is introduced. Allowed identifiers/digests may
remain linkable. A history boundary label alone establishes neither chronology
nor provenance. Equal old declarations can still match while newer requests are
missing; a retained hash cannot reconstruct expired explanatory material.

Tests use invented fixtures for exact preimage, normalization, request-set order,
field changes, malformed/ambiguous JSON, input bounds, detached outputs, all five
profile scopes and expected-binding conflicts. Two fresh-process comparisons per
profile in DELETE/WAL fixtures preserve saved binding files, original note exports
and database bytes. The replay/omission negative control deliberately matches
when both inputs are replaced; it does not demonstrate rollback resistance.
No registry, live authority, note adapter, removal, restore gate, schema migration,
cryptographic authentication or action transaction is implemented.

Design references: [RFC 8259 section 4](https://www.rfc-editor.org/rfc/rfc8259.html#section-4)
describes duplicate-member interoperability risks; [RFC 9334 section 10.4](https://www.rfc-editor.org/rfc/rfc9334.html#section-10.4)
explains why even newly signed claims can contain older values. Our binding scheme
is an original local design and inherits no attestation guarantees.

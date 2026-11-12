# Declared snapshot manifest contract

`lumina_orion.snapshot.validate_snapshot_manifest(profile, manifest)` validates
the shape of an explicitly supplied version 1 declaration and returns a detached,
normalized dictionary. It makes no file, database, model, or network calls. It
does not create a snapshot or inspect actual notes. This contract is an input
boundary for future adapters, not a restore gate or a completeness certificate.

## Version 1 fields

Every dictionary accepts exactly the fields shown. Unknown fields fail rather
than silently carrying source text or unsupported claims. All data below is
invented; the digest is a placeholder, not a content hash computed by this API.

```json
{
  "version": 1,
  "profile": "orion",
  "snapshot_id": "fixture:snapshot-A",
  "removal_history_boundary": null,
  "coverage": {
    "scope": "profile_records",
    "declaration": "partial",
    "exclusions": ["fixture:unmapped-results"]
  },
  "records": [{
    "profile": "orion",
    "record_key": "fixture:record",
    "target_key": "fixture:target",
    "content_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }]
}
```

| Field | Contract and limit |
| --- | --- |
| `version` | Integer 1; booleans and unknown versions fail. |
| `profile` | A known journal profile, normalized as in the removal ledger. Must match the requested profile and every record. Mixed-profile envelopes fail, rather than silently filtering records. |
| `snapshot_id` | Nonempty caller-assigned version identity, at most 200 characters. A label does not enforce immutable content. |
| `removal_history_boundary` | Required nullable identity, at most 200 characters. Null means unspecified. A non-null value names the separately claimed removal-history boundary; it does not establish that this boundary exists, is independently current, or covers later decisions. |
| `coverage.scope` | Exactly `profile_records`, meaning only the supplied profile's declared record mapping. It never includes every archive, backup, export, cache, or log. |
| `coverage.declaration` | `partial` or `declared_complete`. Both are unverified caller assertions. |
| `coverage.exclusions` | Explicit list of at most 1,000 unique nonempty scope labels, at most 2,000 characters each. A complete declaration cannot list exclusions. An empty list alone does not prove complete coverage. |
| `records` | At most 10,000 dictionaries with exactly the four shown fields. Record keys are unique after normalization; different keys may still describe the same target/version. |

Identity strings reject control characters and trim surrounding whitespace.
Record and target keys are at most 2,000 characters; digests must be lowercase
64-character SHA-256 hex. Validation never verifies those digests against content.
Exclusion labels should avoid private paths or narrative content. Exact-field
validation cannot detect sensitive information placed inside permitted strings.

## Relationship to the read-only comparison

```python
from lumina_orion.snapshot import validate_snapshot_manifest
from lumina_orion.restore import inspect_restore

checked = validate_snapshot_manifest("orion", supplied_manifest)
report = inspect_restore("orion", supplied_requests, checked["records"])
assert report["coverage"] == "caller_supplied_only"
assert report["reopening"] == "not_assessed"
```

`declared_complete`, a non-null boundary, an empty mapping, or a valid envelope
never grants permission, confirms removal, or upgrades the comparison's coverage.
Changing a label cannot make old data current. A consumer must not silently
retarget an earlier review to a new snapshot under the same identity.

The shape validator alone defines no serialization/hashing convention. The
companion binding API below specifies normalized declaration hashing, but neither
API provides cryptographic authentication, a persistent identity registry,
boundary comparison, freshness authority, concurrent catch-up, or actual-note
extraction. Before an adapter, establish an independently current history
boundary and independently retain the reviewed binding. Missing,
stale, conflicting, or unverifiable evidence must remain unresolved outside this
shape validator. No application removal, restore operation, or migration exists.

## Validation evidence and privacy

Synthetic tests cover invalid shapes, cross-profile rejection, duplicate record keys,
detached outputs, coverage contradictions, null/old boundary labels, unchanged
original notes/database bytes, and two fresh-process JSON round trips. These are
schema and process tests, not power-loss or real-archive tests. The execution
result belongs in the pull request; test code alone is not a passing result.

Manifest identifiers and digests may be linkable; retain or expose only what an
authorized review needs. Do not treat a hash or opaque label as anonymity or access
control. Private archives must not be used as examples or committed to GitHub.

Design references: [SQLite's snapshot description](https://www.sqlite.org/backup.html)
and [RFC 6973 data minimization](https://www.rfc-editor.org/rfc/rfc6973.html#section-6.1).

## Deterministic declaration binding

`lumina_orion.snapshot_binding.bind_snapshot_manifest(profile, payload)` accepts
only UTF-8 `bytes` of at most 1 MiB and returns exactly `version` (integer 1),
`scheme` (`lumina-manifest-sha256-v1`), normalized `profile`, normalized
`snapshot_id`, and lowercase `manifest_sha256`. It performs no I/O. The input byte
limit can reject otherwise shape-valid large manifests; callers must not silently
truncate them. UTF-16/32, BOMs, invalid UTF-8, unpaired surrogates, duplicate object
members (even identical values or escaped equivalent names), fractional/exponent
numbers, nonfinite numbers, trailing documents, and invalid v1 shapes fail with
`ValueError`. Integer version 1 is the schema's only supported numeric value.
Excessive parser nesting also fails with `ValueError`. This is a bounded input
contract, not a general hostile-input sandbox.

The exact v1 preimage is ASCII `lumina-manifest-sha256-v1`, one NUL byte, then
the ASCII bytes of Python `json.dumps(normalized_manifest, sort_keys=True,
ensure_ascii=True, separators=(",", ":"), allow_nan=False)`, with no final newline.
The normalized manifest is the existing shape validator's detached result.
All object keys are fixed ASCII schema names. Hash that preimage with SHA-256.
This project-specific scheme is not RFC 8785 JCS. Changing its rules requires a
new scheme identifier.

Object member order, JSON formatting, equivalent escape spellings, identity
edge whitespace, and profile case do not affect this binding. Record/exclusion
array order remains significant. Other string case and Unicode scalar sequences
remain exact; there is no Unicode normalization, so precomposed and decomposed
spellings can differ. Every declared field, including profile, snapshot identity,
coverage, history boundary, and listed content digests, participates. This is an
exact binding to the **normalized declaration**, not a raw-file checksum or a
verification of the underlying notes/digests.

`require_snapshot_binding(profile, payload, expected)` validates a separately
saved binding and returns a detached normalized manifest only on an exact match;
otherwise it raises `ValueError`. A changed declaration under an old identity
conflicts. Store/select `expected` independently; recomputing it from the candidate
at verification time defeats the comparison. Replacing both values can pass.
No registry prevents identity reuse, no signature establishes authorship, and no
match establishes current history, trusted completeness, removal, or permission
to reopen. Hashes can remain linkable metadata. Persistence, current authority,
and actual-note mapping remain separate work.

Known vector: the empty partial Orion declaration with snapshot ID `fixture:A`,
null history boundary, and empty exclusions/records binds to
`be972fa2bb2460c0a7a53285a6e00d15463ed0f9bbd174bb2a1a50f9ee8da9f4`.
Tests cover this vector, changed fields, JSON ambiguity, Unicode, ordering,
profile separation, detached output, byte limits, saved-binding checks in fresh
processes, and preservation of invented originals in DELETE and WAL databases.
These process tests do not establish power-loss safety or trusted storage.

References: [RFC 8259 sections 4 and 8](https://www.rfc-editor.org/rfc/rfc8259.html)
explain duplicate-name and Unicode interoperability concerns; [Python's JSON
documentation](https://docs.python.org/3/library/json.html) describes duplicate
member defaults, `object_pairs_hook`, and encoder controls. The binding scheme
and its limits above are this project's design choices.

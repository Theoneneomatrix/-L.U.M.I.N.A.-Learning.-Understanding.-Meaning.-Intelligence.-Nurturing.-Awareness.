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

This validator defines no serialization/hashing convention, cryptographic
authentication, persistent identity registry, boundary comparison, freshness
authority, concurrent catch-up, or actual-note extraction. JSON examples show
structure only. Before an adapter, specify how the exact manifest/content version
is bound and how an independently current history boundary is established. Missing,
stale, conflicting, or unverifiable evidence must remain unresolved outside this
shape validator. No application removal, restore operation, or migration exists.

## Validation evidence and privacy

Synthetic tests cover invalid shapes, cross-profile rejection, duplicate keys,
detached outputs, coverage contradictions, null/old boundary labels, unchanged
original notes/database bytes, and two fresh-process JSON round trips. These are
schema and process tests, not power-loss or real-archive tests. The execution
result belongs in the pull request; test code alone is not a passing result.

Manifest identifiers and digests may be linkable; retain or expose only what an
authorized review needs. Do not treat a hash or opaque label as anonymity or access
control. Private archives must not be used as examples or committed to GitHub.

Design references: [SQLite's snapshot description](https://www.sqlite.org/backup.html)
and [RFC 6973 data minimization](https://www.rfc-editor.org/rfc/rfc6973.html#section-6.1).

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

## Persistent first-binding registry

`SnapshotBindingRegistry(path)` in `lumina_orion.snapshot_registry` adds only a
`snapshot_bindings` table to a caller-selected local SQLite database. It can use
a separate file from notes; separation alone does not establish trust. Existing
note tables and rows are not rewritten. The stored fields are exactly the five
binding fields above: no manifest payload, reviewer text, history, timestamp or
approval assertion is added. Identifiers and digests can still be sensitive and
linkable. This is not a complete audit or retention system.

- `record(profile, payload)` validates strict JSON using the existing scheme and
  commits the first binding for the normalized profile and snapshot ID. An
  identical retry returns that binding without replacing it; changed declarations
  under the same identity raise `ValueError`. The writer reservation covers lookup
  and insertion together; success returns after commit. Busy/I/O errors propagate.
- `get(profile, snapshot_id)` returns one detached binding or `None`. Queries are
  scoped; no enumeration API is provided. Profile scoping is not access control.
- `verify(profile, payload)` returns the detached normalized manifest only if it
  matches that identity's registered binding. Missing bindings raise `ValueError`
  and never auto-register. A match applies to the observed binding, not a later
  operation; verification does not reserve a transaction for subsequent action.

Choose and retain the registry independently of an untrusted candidate. Calling
`record` on first sight is a caller decision, not evidence that the candidate was
reviewed. There is no overwrite/delete API; this interface preserves first
bindings, but file owners can bypass it and old database copies can lose newer
bindings. A rolled-back or replaced registry is not detected. No registry match
establishes authorship, freshness, current authority, complete coverage, actual
note content, deletion, or restore permission. A new snapshot ID can register a
different declaration; that does not supersede an earlier review automatically.

Tests use only invented data and verify retries, conflicting concurrent writers,
all profile scopes, additive table creation alongside unchanged notes, detached
results, malformed-input rejection, exception rollback, fresh-process reads and
abrupt exits before/after commit in DELETE and WAL modes. They do not test power
loss, hostile file owners, untracked copies or actual archive integration.

## Read-only history observation contract

`inspect_snapshot_history(profile, payload, expected, observation=None)` in
`lumina_orion.snapshot_history` first requires the existing strict declaration
binding. It then compares the declared history boundary with one separately
supplied observation. It performs no I/O and stores nothing. A malformed binding,
unknown/extra observation field, unsupported version/state, cross-profile envelope,
or invalid identity raises `ValueError` rather than returning clearance.

The observation is exactly `{"version": 1, "profile": "orion", "state": "current",
"history_boundary": "fixture:boundary"}`. Version must
be integer 1, profile uses the existing normalization, and boundary is nullable
or a nonempty control-free identity of at most 200 characters. State is exactly
`current`, `stale`, or `unavailable`; `current` requires a non-null boundary.
These are **caller assertions**, not authenticated evidence. Obtain and select
observations independently; do not manufacture `current` from the candidate.
No timestamps, private paths, source payloads or reviewer narratives are required.
Permitted identifiers may still be sensitive; this API is not a retention policy.

Every report has exactly version, profile, snapshot_id, read_only (`True`),
history_status, authority (`caller_asserted_only`) and reopening (`not_assessed`).
Statuses are `observation_missing`, `observation_stale`, `observation_unavailable`,
`declaration_boundary_unspecified`, `boundary_conflict`, or
`boundary_matches_assertion`. Non-current states take precedence over boundary
comparison. Conflict reports disagreement, not which opaque boundary is newer.
Even a matching assertion never authorizes reopening, publication or deletion.
A valid complete declaration, saved digest or identical retry cannot override it.
This is a read-only contract component; it is not an application action gate.

Negative controls retain an old matching declaration/binding and supply a newer
boundary, producing a conflict; replaying both old declarations and observations
can still match. That second control demonstrates the unresolved trust problem,
not rollback detection. An independently current, authenticated observation and
safe ordering with future actions remain required before any action adapter.
No network service, authentication mechanism, destructive migration, note mapping
or apply operation is introduced. Recheck on retry; never cache a report as a
permission token. Tests verify profile scope, unchanged inputs/originals,
read-only behavior and fresh-process comparisons in DELETE/WAL fixtures.

Design reference: [etcd API guarantees](https://etcd.io/docs/v3.6/learning/api_guarantees/)
distinguishes linearizable reads, potentially stale serializable reads and watch
semantics. Those service guarantees motivate separating content identity from
currentness; they are not implemented or inherited by this offline comparator.

## Combined bound declaration inspection

`inspect_bound_restore(profile, snapshot_payload, expected_snapshot, *,
history_payload=None, expected_history=None)` in `lumina_orion.bound_restore`
composes the existing strict snapshot and removal-history binding checks with
`inspect_restore`. It reads supplied UTF-8 JSON bytes only, performs no I/O,
stores nothing and takes no action. Select both expected bindings separately
from the candidate declarations; recomputing them from an untrusted candidate
does not provide an independent check. Existing schemas and binding schemes
remain unchanged. See [the history declaration contract](REMOVAL_INTENTS.md).

The snapshot binding is always required. Supply both history arguments together
or omit both. Malformed JSON/schema/binding inputs, cross-profile declarations,
and conflicting non-null boundary labels raise `ValueError`. A valid history
binding is checked even when the snapshot boundary is unspecified. This avoids
hiding a conflicting binding behind an unresolved status.

| comparison_status | Meaning of findings |
| --- | --- |
| `history_missing` | `None`: no history comparison was performed. |
| `snapshot_boundary_unspecified` | `None`: supplied history matched its binding, but the snapshot has no boundary to associate with it. |
| `bound_declarations_compared` | A list of the existing comparison findings, including an empty list when the supplied history contains no requests. |

The report identifies `profile`, `snapshot_id`, `snapshot_binding`,
`history_binding` (or `None`) and `snapshot_history_boundary`. It retains the
normalized `declared_coverage` including exclusions. Its other fields are
`version` (1), `read_only` (`True`), `comparison_status`, `findings`,
`coverage` (`caller_supplied_only`), `authority` (`caller_asserted_only`),
`freshness` (`not_assessed`) and `reopening` (`not_assessed`). All nested results
are detached from inputs. No boolean approval or clearance field is emitted.

A `requested_version_present` finding means a requested version is **listed in
this declaration**, not removed or independently found in a database. Other
outcomes remain `ambiguous_match`, `different_version_listed` and
`target_not_listed`. An absent target is not proof of erasure. Even matching
boundary labels, two matching bindings, an empty result and `declared_complete`
never establish completeness, source authenticity, current history or permission
to restore. Boundary labels have no chronological ordering here. The separate
history-observation API is neither invoked nor upgraded to trusted authority.

Tests retain a negative control that replaces both incomplete declarations and
their expected bindings: comparison still succeeds while every authority limit
remains explicit. Other tests cover conflict rejection, all comparison outcomes,
missing history, unspecified boundaries, detached results, all five profile
scopes, repeatable fresh-process reports and unchanged fixture files/original
notes in DELETE and WAL databases. These are invented fixtures; the inspector
never opens those databases. No actual-note adapter, trusted history source,
action transaction, retention policy or power-loss protection is implemented.
Recompute inspection on retry; do not use a saved report as an authorization token.

## Fixture export projection

`project_fixture_declarations(profile, memory_export, saved_intents, mappings, *,
snapshot_id, history_boundary)` in `lumina_orion.fixture_projection` is a pure,
fixture-only bridge to the existing declaration schemas. It accepts already
supplied `MemoryBank.export(profile)` and `RemovalIntentLedger.intents(profile)`
values, never a path or database handle. It makes no reads, writes or API calls.
It returns only detached `snapshot` and `history` declarations, not expected
bindings or permission. Existing binding and inspection APIs can process them.

Every supplied note requires exactly one explicit mapping:

```json
{"note_id": 1, "record_key": "fixture:note1", "target_key": "fixture:target", "content_sha256": "<64 lowercase SHA-256 hex characters>"}
```

`note_id` is a positive integer in the supplied export. `record_key` identifies
the declared copy record; `target_key` states which removal target it represents.
Identities use the existing normalization and bounds. No target is guessed from
note text, source references, ordering, similar labels or local IDs. The supplied
digest must equal SHA-256 of the exact UTF-8 note text, without trimming or
Unicode normalization. This fixture convention hashes **text only**: metadata,
source, timestamps, dependency links and review status are not covered. Existing
removal requests must already use the same text-digest convention to match.
Equal text alone does not identify a note or validate a target assignment.

Missing/extra mappings, duplicate note IDs, repeated mappings, duplicate record
keys and duplicate `(target_key, content_sha256)` assignments raise `ValueError`.
Different text versions may share a target if their record keys differ. Foreign
profiles are rejected rather than filtered out. Notes require `id`, `profile`
and `text`; other export note fields are deliberately not projected or validated.
The export envelope has exactly `format`, `profile`, `records` and `results`.
The format must be `lumina-memory-v1`; lists are bounded to 10,000 entries and
note text to 20,000 characters. Snapshot/history strict Unicode and 1 MiB encoded
declaration limits also apply. Validation errors omit source text.

Saved intent rows require the existing eight export fields, including positive
local `id` and nonempty `created_at`. The output retains the six v1 request fields
and discards local ID/time. A timestamp is not evaluated as freshness or ordering.
Snapshot records sort by normalized record key; history requests use the existing
request-ID sorting. Caller-supplied snapshot identity and history boundary label
remain assertions. Their equality is not proof of a consistent database capture.

Coverage is always `partial`, with the fixed exclusion
`fixture:research-results-and-untracked-copies`, even when the supplied lists are
empty. Research results and untracked copies are not mapped. The adapter requires
coverage of all **supplied** notes; it cannot detect notes or requests omitted
before invocation, authenticate input provenance, or ensure that separate exports
came from the same read snapshot. It does not validate source metadata, revoke
requests, remove notes, pause retention, authorize restore, or integrate archives.

Tests use invented data, including actual temporary journal/ledger exports.
They cover all five profiles, text changes under reused IDs, Unicode byte
differences, missing/ambiguous mappings, schema/resource failures, shared-target
versions, detached values, empty-input and metadata negative controls, and two
fresh-process projections per profile in DELETE/WAL fixtures. Saved fixture bytes,
original notes and removal requests remain unchanged. These are process-restart
checks of a pure projection, not power-loss or concurrent-capture guarantees.

## Common read snapshot fixture

`tests/test_capture_consistency.py` adds a test-only capture experiment using the
existing note and request row serializers on one connection with an explicit
`BEGIN`. The first note query fixes the read snapshot; the request query runs
before that transaction ends. No production capture API is added, and separate
calls to `MemoryBank.export` and `RemovalIntentLedger.intents` remain separate
reads. Identical boundary labels cannot join their transaction histories.

The competing writer inserts an invented note and its removal request in one
transaction. In DELETE mode its first commit returns `SQLITE_BUSY` while the
reader holds the snapshot; after the reader closes, the retained write
transaction commits. In WAL mode the writer commits while the reader is active,
and that reader still captures the original note/request pair. Fresh reads then
see both additions. Coordination uses a queue and event with bounded waits,
without timing sleeps. All five profiles run in both modes.

The negative control ends the note export, commits the same writer transaction,
then reads requests. The projection accepts these individually valid inputs,
but its new request has `target_not_listed`: this mixed capture is not evidence
of removal. Both controls retain partial, caller-supplied coverage and unassessed
freshness and reopening. A consistent old snapshot is not current authority.

Two fresh processes per case re-inspect the saved capture and read the temporary
database. Saved capture bytes, original notes and requests (including IDs and
provenance), and other profiles remain unchanged; only the selected profile gets
the intentional additions. These are fixture tests of process restart and
concurrent capture, not rollback protection, trusted provenance, source-metadata
binding, complete archive coverage, power-loss safety or permission to act.

SQLite documents [historic read snapshots and WAL isolation](https://www.sqlite.org/isolation.html)
and [retrying a busy commit while its transaction remains active](https://www.sqlite.org/lang_transaction.html).

## Read only co located capture

`lumina_orion.capture.capture_journal(path, profile)` now reads an existing local
database containing both the journal and removal-request ledger. It returns a
detached version 1 dictionary with `profile`, `memory_export`, `saved_intents`,
`consistency: single_read_transaction`, `coverage: partial`,
`freshness: not_assessed` and `authority: not_assessed`.

The profile is normalized and validated before opening the file. An escaped
absolute file URI uses SQLite `mode=ro` and a private connection cache; caller
paths are ordinary paths, not configuration URIs. The connection starts an
explicit `BEGIN` before checking required tables and reading notes (including
originals, dependencies and review flags), saved research results and removal
requests. It closes on success or error. No MemoryBank or ledger constructor is
invoked, so missing files or tables are not initialized. Missing tables raise
`ValueError`; invalid profiles also raise `ValueError`. Missing/unreadable files,
lock failures and incompatible/corrupt schemas raise SQLite errors. No partial
result is returned on failure. Table presence is not a full schema audit.

All four tables (`memories`, `dependencies`, `research_results`,
`removal_intents`) must be in the same database. A ledger in another file is not
discovered or attached. Empty co-located tables can yield empty lists, still
with partial coverage; a missing ledger is an error rather than an empty list.
The helper does not write an export file or add capture timestamps. Stored
timestamps retain their original meanings and are not assessed for currentness.

This is a consistent read of the selected database, not complete archive
coverage or authenticated provenance. Explicit target mappings, text-only
fixture projection and expected bindings remain separate operations. No result
confirms removal, pauses retention or authorizes reopening. The earlier
separate-read negative control remains in the tests: calling the two older
export methods separately still does not provide a shared snapshot.

SQLite's [URI documentation](https://www.sqlite.org/uri.html) describes read-only
mode and warns that `immutable=1` skips change detection; this helper does not
assert immutability. Its [WAL documentation](https://www.sqlite.org/wal.html#read_only_databases)
explains that read-only WAL access can require existing or creatable `-wal` and
`-shm` coordination files. Read-only here means no journal/ledger SQL changes,
not a promise of zero filesystem side effects or a forensic acquisition tool.

Tests use invented temporary databases in DELETE/WAL modes for all five
profiles, preserve originals and other profiles during a competing writer,
verify saved results and review flags, and run two fresh-process captures per
profile/mode. They also verify detached values, escaped paths, missing files
and tables, SQL write rejection, connection cleanup after failure, and empty
versus unavailable ledgers. Production capture still does not establish current
authority, source-metadata binding, rollback protection or power-loss safety.

## Captured note metadata binding

`bind_captured_note(profile, note)` and
`require_note_binding(profile, note, expected)` in `lumina_orion.note_binding`
are pure offline functions for one supplied `capture_journal` note. They do not
open a file or change capture, export, projection, removal-request or snapshot
schemas. Select and retain the expected binding independently. Verification
returns a detached note or raises `ValueError`; malformed input also fails.

The v1 note schema requires exactly `id`, `profile`, `kind`, `text`, `source`,
`created_at`, `supersedes`, `derived_from`, `needs_review`, and `review_status`.
Missing and extra fields fail rather than silently dropping future metadata.
IDs are positive SQLite integers (booleans excluded); dependency lists contain
at most 100 unique IDs and cannot include the note itself. Text/source/time
fields are nonempty Unicode scalar strings bounded to 20,000/2,000/200
characters. Timestamps are opaque recorded strings, not validated dates.
Kinds and correction links follow the existing journal shape. Review markers
are validated for shape and local consistency, not recomputed against a graph.
Links do not prove that the referenced records exist or belong to this profile.
The supplied note's profile must be canonical and match the normalized caller
profile. Profile scoping is not access control.

Every listed field is hashed. Text, whitespace, Unicode sequences, recorded
timestamps and dependency order are preserved exactly; dictionary key order is
ignored. The encoding is Python JSON with sorted keys, `ensure_ascii=True`,
compact separators and `allow_nan=False`, limited to 256 KiB of ASCII bytes.
SHA-256 input is `b"lumina-captured-note-sha256-v1\x00"` followed by those bytes.
This project-specific scheme is not JCS. The binding has exactly `version: 1`,
`scheme: lumina-captured-note-sha256-v1`, `profile`, `note_id` and `note_sha256`.
It contains no source text but identifiers and hashes can still be linkable.

This is a separate metadata-inclusive scheme. Existing v1 removal-request
`content_sha256` remains SHA-256 of exact UTF-8 text only. Never substitute
`note_sha256` for that digest. Review flags can legitimately change when a new
correction arrives; a binding conflict does not diagnose corruption or decide
which assertion is true. Local IDs do not establish cross-database identity.
Saved research results, requests, external source contents, capture identity,
coverage and observation time are outside this per-note binding.

A match does not authenticate provenance, establish currentness or authority,
validate a source claim, discover untracked copies, authorize deletion or
reopening, or change retention. Replacing both the candidate and expected
binding bypasses the comparison; tests preserve that negative control. No
registry, automatic acceptance, migration or action adapter is added.

Tests cover a literal encoding vector, metadata-only and text changes, Unicode,
malformed fields, byte bounds, profile separation, detached output and the
unchanged v1 text digest. Invented DELETE/WAL fixtures run two fresh-process
comparisons per profile while preserving all database bytes and originals.
These checks establish restart determinism, not trusted storage or power-loss
safety. Next integration work must explicitly associate bindings with capture
and record identities without interpreting matches as permission.

Design references: [Python JSON encoder controls](https://docs.python.org/3/library/json.html)
and [RFC 8259 section 8.2](https://www.rfc-editor.org/rfc/rfc8259.html#section-8.2)
(on unpredictable handling of unpaired surrogates). The schema, encoding and
trust limits here are project design choices, not guarantees inherited from JSON.

## Declared capture note envelope

`bind_capture_notes(profile, capture, *, capture_id)` and
`require_capture_note_binding(profile, capture, expected, *, capture_id)` in
`lumina_orion.capture_binding` associate the **supplied note set** with a
caller-declared capture identity. Both are pure functions; neither opens the
journal, writes a registry, runs a check nor changes an existing schema. Use
`capture_journal` separately to obtain a capture. An input shaped like a capture
is still an assertion, not proof that this function performed its transaction.

The input requires exactly the current eight capture fields. Version must be
integer 1; canonical profile must match the normalized caller profile;
consistency/coverage/freshness/authority must retain the v1 values
`single_read_transaction`, `partial`, `not_assessed`, `not_assessed`. The export
requires exactly format/profile/records/results with `lumina-memory-v1` and the
same profile. Records, results and saved_intents must be lists of at most 10,000
rows. Only note rows receive content validation and hashing, using the existing
captured-note binding. Results and request row contents are deliberately unbound
and unvalidated. They never appear in verification output.

`capture_id` uses the existing nonempty control-free identity rules, is trimmed,
is bounded to 200 characters and must contain Unicode scalar values. It is not
an inferred path, trusted database identity, timestamp or chronological counter.
The envelope contains exactly version (1), scheme
`lumina-capture-notes-sha256-v1`, profile, capture_id, note_bindings and
notes_sha256. Each note association is an existing five-field v1 note binding.
Associations sort by positive local note ID; input note ordering is irrelevant.
Repeated IDs and foreign-profile rows/bindings fail. Empty supplied notes are
valid and do not imply that an archive is empty.

The envelope digest uses ASCII JSON with sorted keys, compact separators,
`ensure_ascii=True`, `allow_nan=False`, without the notes_sha256 field. Prefix
those bytes with `b"lumina-capture-notes-sha256-v1\x00"` and hash with SHA-256.
The encoded body is limited to 1 MiB in addition to per-note and row bounds.
This is a project-specific scheme, not JCS. It covers the capture label, profile
and exact set of note bindings. It does not cover full capture bytes, results,
requests, external source content or observational provenance.

Choose and retain the expected envelope independently. Verification validates
its exact schema, canonical sorted form and digest, then compares the candidate
note envelope with it. A missing/extra note, reused ID with changed metadata,
foreign profile, changed association or different capture label raises
`ValueError`. Success returns only detached validated notes sorted by ID, never
a full capture decorated as verified. Identifiers and digests remain linkable;
the absence of note text in the envelope is not anonymity or a retention policy.

Omitting a note before creating both candidate and expected envelopes remains
undetectable. Replacing both envelopes also bypasses the check. A caller can
assert a false capture label or copy valid notes from another database using
that label. A match cannot prove origin, complete coverage, a common snapshot,
current authority, graph truth, safe reopening, removal or permission. Local
IDs remain scoped assertions, not global identities. Nothing locks a separately
stored witness or orders a later action. Existing capture, projection and v1
text-only removal digests remain unchanged.

Six test methods cover a literal empty encoding vector, reordered inputs,
changed capture identity, missing/extra/duplicate notes, reused IDs, cross-profile
associations, malformed envelopes, resource limits and detached values. Explicit
negative controls retain unbound request/result changes and replacement or
omission of both candidate and expected inputs. Invented DELETE/WAL fixtures
verify twice in fresh processes for all five profiles; original database bytes,
notes, results, requests and older bindings remain unchanged. These process
checks do not establish power-loss safety or trusted storage.

Design references: [W3C PROV-DM collections and membership](https://www.w3.org/TR/prov-dm/#term-collection)
distinguishes collection provenance from member provenance; [Python JSON encoder
controls](https://docs.python.org/3.12/library/json.html) document key sorting and
encoding settings. This envelope is not an implementation of PROV or its trust
semantics. Its exact-set comparison is a separate project choice; PROV membership
assertions alone need not describe a complete set.

## Declared capture request envelope

The pure functions `bind_capture_requests(profile, capture, *, capture_id)` and
`require_capture_request_binding(profile, capture, expected, *, capture_id)`
in `lumina_orion.request_binding` bind exactly the supplied removal-request rows
to a declared capture ID and canonical profile. They perform no I/O and return
no verified notes or results. The shared v1 capture shape and list bounds are
checked, but note/result contents remain outside this binding.

Each request has exactly id, version, profile, request_id, target_key,
content_sha256, created_at and state. IDs are positive SQLite integers; version
is integer 1; profile must match; state is exactly `requested`. Request and
target identities must already be trimmed, control-free Unicode scalar text,
bounded to 200 and 2,000 characters. Recorded created_at text is preserved,
nonempty, scalar and bounded to 200 characters, not interpreted as a clock.
The existing lowercase v1 content_sha256 remains exact UTF-8 text hashing;
the new envelope digest must never replace it. Duplicate local IDs or request
IDs fail, even when the other identity differs. Multiple distinct requests for
one target remain valid.

The envelope contains exactly version (1), scheme
`lumina-capture-requests-sha256-v1`, profile, capture_id, requests and
requests_sha256. Capture identity follows the existing trimmed 200-character
scalar identity contract. Request rows sort by local ID; input row and key
order do not matter. All eight request fields, capture identity and profile are
covered. Encode the envelope without requests_sha256 as sorted-key ASCII JSON
with ensure_ascii=True, compact separators and allow_nan=False, bounded to
1 MiB and 10,000 rows. Hash prefix
`b"lumina-capture-requests-sha256-v1\x00"` plus those bytes using SHA-256.
This is a project-specific scheme, not JCS.

Verification checks the independently retained expected envelope's exact shape,
canonical sorted form and digest, then compares the candidate. Missing, extra,
changed, duplicate or foreign-profile requests and capture-label changes fail
with ValueError. Success returns detached request rows sorted by ID. These are
requests, **not completion receipts or authorization**. Empty rows do not prove
no request exists elsewhere. Local IDs and capture labels do not prove global
identity. Identifiers, target keys and digests remain potentially sensitive.

Replacing both candidate and expected, or omitting requests before constructing
both, still passes. Notes/results and external source contents remain unbound.
There is no authenticated provenance, common-snapshot proof, current authority,
rollback resistance, complete history, action ordering, removal, reopening or
retention policy. Existing schemas, capture behavior and note envelopes are
unchanged.

Six test methods exercise encoding, exact field changes, Unicode, identity/set
conflicts, malformed inputs, bounds, detached values and trust negative controls.
The restart fixture compares twice in fresh processes for all five profiles in
DELETE/WAL modes and checks original database bytes, all rows and existing
text-only digests. Test execution status belongs in the PR; the presence of
these tests does not establish that they passed.

Design references: [Python JSON encoder controls](https://docs.python.org/3.12/library/json.html)
and [W3C PROV-DM collections](https://www.w3.org/TR/prov-dm/#term-collection).
Collection/member provenance motivates recording associations; exact-set
comparison and the digest contract are project design decisions.

## Declared capture result envelope

The pure functions `bind_capture_results(profile, capture, *, capture_id)` and
`require_capture_result_binding(profile, capture, expected, *, capture_id)` in
`lumina_orion.result_binding` bind exactly the supplied saved research-result
rows to a declared capture ID and profile. They perform no I/O. The shared v1
capture shape and list bounds are checked; note and request contents remain
unbound. Existing note/request envelopes and capture behavior are unchanged.

Each exported result has exactly profile, run_id, step, manifest_sha256,
result_text, created_at and manifest. Profile must match. Run ID and step are
nonempty Unicode scalar text bounded to 200 characters; result text to 20,000;
created_at to 200. Text is preserved exactly, including whitespace, with no
Unicode normalization. Run/step follow the saved-result API's opaque-text
contract, not the trimmed removal-identity contract. created_at is an assertion,
not an interpreted clock. Duplicate (run_id, step) pairs fail; different steps
in one run and the same step in different runs are valid.

The existing version 1 input manifest validator and UTF-8 manifest encoding
are reused unchanged. The supplied lowercase manifest_sha256 must match those
bytes. The full nested manifest, including ordered sources/processing arrays,
is bound along with the result's other fields. Object key order is ignored;
array order is retained. The envelope digest never replaces the input digest
or the existing v1 text-only removal digest. No database schema is changed.

The envelope has exactly version (integer 1), scheme
`lumina-capture-results-sha256-v1`, profile, capture_id, results and
results_sha256. Capture ID follows the existing trimmed 200-character Unicode
scalar identity contract. Results sort by exact (run_id, step) strings. Encode
the envelope without results_sha256 as sorted-key ASCII JSON with
ensure_ascii=True, compact separators and allow_nan=False. Bound it to 1 MiB
and 10,000 rows. Hash `b"lumina-capture-results-sha256-v1\x00"` plus those
bytes using SHA-256. This is a project-specific Python encoding, not JCS or
a portable cross-language floating-point canonicalization standard.

Verification requires an independently retained expected envelope with exact
shape, sorted canonical form and valid digest. Missing, extra, changed,
duplicate or foreign-profile results and changed capture labels fail with
ValueError. Success returns detached result rows sorted by run/step, including
detached nested manifests. It does not return notes or requests as verified.

This compares supplied assertions, not source truth, authentic provenance,
common-snapshot proof, full history/coverage, current inputs, authorized reuse,
action ordering, removal or retention policy. Replacing both inputs or omitting
rows before constructing both still passes. Source locators/digests are not
fetched or authenticated; result/run/capture labels are not global identities.
Stored text and digests may be sensitive or linkable. Keep private material out
of public repositories. No full capture/reconciliation claim follows from
separate note, request and result envelopes.

Seven test methods cover literal empty encoding, key/row order, every result
field, nested manifest changes, Unicode, set conflicts, malformed/bounded
inputs, detachment and explicit trust negative controls. Invented fixtures
compare twice in fresh processes per profile in DELETE/WAL modes, checking
original database bytes and all rows plus unchanged input/text digests.
Execution results belong in the PR, not in an assumption that tests passed.

Encoding references: [Python JSON controls](https://docs.python.org/3.12/library/json.html)
and [hashlib byte inputs and hexadecimal digests](https://docs.python.org/3.12/library/hashlib.html).
Field selection, sorting, versioning and domain separation are project choices.

## Shared declared association verification

`verify_capture_bindings(profile, capture, *, capture_id, expected_notes,
expected_requests, expected_results)` in `lumina_orion.capture_verification`
compares all three independently retained envelopes against the same supplied
capture, canonical profile and trimmed capture ID. It calls the existing note,
request and result validators; every envelope must pass before a report returns.
Missing, malformed, tampered, foreign-profile or differently labelled envelopes
raise ValueError. Individually valid envelopes from differently labelled captures
cannot be combined into a successful report. Changed contents under an unchanged
label still conflict with retained bindings. Original envelope schemas, bounds,
hashes, input manifests and text-only removal digests remain unchanged.

The detached report has exactly version (integer 1), scheme
`lumina-capture-binding-report-v1`, profile, capture_id, status, scope, coverage,
common_snapshot, authenticity, freshness, authority, counts and bindings.
Status is `supplied_bindings_match`; scope is `supplied_note_request_result_rows`;
coverage is `partial`; common_snapshot is `not_proven`; authenticity, freshness
and authority are `not_assessed`. Counts contains notes, requests and results;
bindings contains notes_sha256, requests_sha256 and results_sha256 from the
verified expected envelopes. There is no combined digest or newly saved binding.
No row payload, source text, request outcome or permission is returned. Labels
and digests can still be sensitive or linkable.

All three sets may be empty and still match. That does not establish an empty
archive or complete coverage. Replacing candidate and expected inputs together,
or omitting rows before constructing both, remains undetectable. Separately
assembled rows can pass when bound under the same caller-declared label; the
negative control explicitly preserves `common_snapshot: not_proven` in this case.
The capture's `single_read_transaction` declaration is checked as required by
the existing validators, but is not independently authenticated. A successful
report does not prove a common collection event, source truth, authentic origin,
freshness, authorized reuse, complete history, action ordering or removal.

The function performs no I/O, registration, database initialization, migration,
cleanup or apply action. It adds no CLI, dependency, network or model access.
Six test methods cover exact report fields, empty sets, mixed associations,
foreign profiles, content conflicts, malformed inputs, detached reports and
explicit trust negative controls. All-five-profile DELETE/WAL fixtures compare
twice in fresh processes per profile/mode, preserving original database bytes,
all rows and existing input/text digests. Test execution results belong in the
PR; these descriptions alone are not passing-test evidence.

[W3C PROV-DM collections](https://www.w3.org/TR/prov-dm/#term-collection)
distinguishes collection membership from complete membership information.
This partial-report contract is a project design choice, not PROV conformance
or an implementation of authenticated provenance.

## Offline capture inspection command

From a checkout, the standard-library-only module reads four explicitly supplied
local UTF-8 JSON files and prints the existing partial report:

```bash
PYTHONPATH=src python -m lumina_orion.capture_inspect \
  --profile orion --capture-id fixture:capture \
  --capture capture.json --notes retained-notes.json \
  --requests retained-requests.json --results retained-results.json
```

The capture is the existing `capture_journal` export. Each retained file is its
separate existing note, request or result envelope, saved independently before
inspection. This command does not generate or retain envelopes. Constructing
expected envelopes from the candidate immediately before comparison cannot
detect replacement of both inputs. No network, model, credential, database,
home-directory default, registration, cleanup, apply or migration is involved.

Every file must contain exactly one JSON object, encoded as UTF-8 without a BOM,
and be at most 1 MiB including whitespace. The command reads at most that limit
plus one byte per file. This file-input ceiling is separate from existing
envelope byte limits; a larger otherwise valid capture needs a separately
reviewed input-bound change. Duplicate object keys at any nesting level,
including escape-equivalent names, are rejected rather than silently keeping
the last value. Non-finite numbers (including exponent overflow), malformed JSON,
invalid UTF-8, invalid schemas, mixed associations and content conflicts fail.
The bound does not promise a fixed CPU/time limit or eliminate filesystem races.
Use ordinary local files; this is not an atomic multi-file capture operation.

Exit 0 emits one JSON report only after all three comparisons pass. Exit 2 emits
no report on rejected input; the generic verification error on stderr omits
payloads, paths and offending key values. Argument errors also exit 2 with
argparse usage. No output file is created; redirecting stdout is a separate
caller action. Success is `supplied_bindings_match`, with `coverage: partial`,
`common_snapshot: not_proven` and authenticity/freshness/authority unassessed.
It neither reports removal success or failure nor grants action permission.
Capture labels and report digests may be sensitive or linkable.

The CLI tests include duplicate-key negative controls that ordinary `json.loads`
would silently accept, nested escaped keys, malformed/bounded files, mixed
envelopes, foreign profiles, changed rows, no action flags, and identical reports
after fresh-process restarts. Synthetic DELETE/WAL fixtures cover all five
profiles with unchanged original database bytes, rows and supplied file bytes.
These test descriptions are not execution results.

[Python JSON documentation](https://docs.python.org/3/library/json.html) describes
`object_pairs_hook` and `parse_constant`, warns about resource consumption, and
documents the default last-value behavior for repeated names. Rejecting these
inputs and selecting a 1 MiB ceiling are project policy choices.

## Read-only capture export command

The separate standard-library-only export module requires both an explicit
existing database path and profile; it has no default home-directory store:

```bash
PYTHONPATH=src python -m lumina_orion.capture_export \
  --db /explicit/path/to/synthetic-journal.sqlite3 --profile orion
```

It calls the existing `capture_journal` helper unchanged, using its read-only
SQLite connection and single read transaction. A successful exit 0 prints one
JSON capture to stdout, with existing record contents, attribution, review
markers, dependencies, saved results and request rows. It does not generate a
capture ID or envelope, register a binding, execute requests, clean up data,
initialize a store or write an output file. Redirecting stdout is a separate
caller action. Exports contain private payloads; do not publish real captures
or use family archives as examples. Output is data, never commands to execute.

Missing stores/directories, missing required tables, SQLite read errors, invalid
profiles and serialization errors exit 2 without a partial JSON export. Stderr
uses a generic export error without path, SQL or payload details. Argument errors
also exit 2 with usage. An empty existing store can export empty lists, but a
missing or unsupported store must not masquerade as a successful empty export.
The helper is not a complete schema, hostile-database or provenance audit.

Coverage remains partial; freshness and authority remain unassessed. Successful
export is not authentication, complete archive coverage, a removal outcome or
permission to act. It does not add evidence that was absent from stored records.
SQLite may need WAL/shared-memory coordination sidecars even with a read-only
connection. The module does not promise a side-effect-free directory or atomic
capture of separately stored files. It does not use `immutable=1` or bypass locks.

The export has no new output-size limit. The separate inspection command still
rejects any supplied file over 1 MiB, including whitespace; not every possible
export is therefore accepted by that command. Existing per-row/digest schemas
and helper behavior are unchanged. No network/model/API access or dependency
is added. The module does not import the council's API-calling CLI.

Tests exercise two fresh-process exports and inspections per profile/mode in
synthetic DELETE/WAL stores, preserving database bytes, all rows and retained
envelope bytes. They also cover missing/corrupt/incomplete stores, empty stores,
required arguments, unsupported action flags, read-only enforcement, no journal
constructor calls and serialization failure without partial output. Actual
execution results belong in the PR, not in these test descriptions.

SQLite documents [URI read-only mode](https://www.sqlite.org/uri.html) and
[read-only WAL constraints](https://www.sqlite.org/wal.html#read_only_databases).
Those mechanisms do not establish authenticity or current action authority.

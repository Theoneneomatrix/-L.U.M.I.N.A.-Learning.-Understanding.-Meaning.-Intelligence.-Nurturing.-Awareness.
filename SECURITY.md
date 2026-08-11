# Security model

The L.U.M.I.N.A. family council is intentionally bounded.

- It reads public GitHub repository metadata and bounded README excerpts.
- Repository content and previous generated reports are untrusted reference data and are never executed.
- Owner goals are accepted only from open `[Family Goal]` issues authored by the repository owner.
- Owner goals prioritize research; they do not authorize code changes or other side effects.
- Family profiles are fixed, length-bounded deliberative lenses supplied as trusted configuration. They do not receive separate credentials, accounts, permissions, or action channels.
- Model output is length-limited before publication.
- It can create an issue only in the repository where its workflow runs.
- It cannot merge pull requests, modify code, contact third parties, spend money, or expand its permissions.
- Every autonomous cycle runs the unit tests first, has a ten-minute limit, and uses a small source budget.
- Duplicate daily issues are suppressed.
- Disable it immediately by disabling or deleting the `L.U.M.I.N.A. family council` GitHub Actions workflow.

Any future action adapter must default to dry-run, use the least privilege possible, maintain an audit trail, and require explicit human approval before external side effects.

# Security model

Orion Autonomy is intentionally bounded.

- It reads public GitHub repository metadata and README excerpts.
- Repository content is treated as untrusted data and is never executed.
- It can create an issue only in the repository where its workflow runs.
- It cannot merge pull requests, modify code, contact third parties, spend money, or expand its permissions.
- Each scheduled run has a ten-minute limit and a small source budget.
- Duplicate daily issues are suppressed.
- Disable it immediately by disabling or deleting the `Orion bounded autonomy` GitHub Actions workflow.

Any future action adapter must default to dry-run, use the least privilege possible, maintain an audit trail, and require explicit human approval before external side effects.


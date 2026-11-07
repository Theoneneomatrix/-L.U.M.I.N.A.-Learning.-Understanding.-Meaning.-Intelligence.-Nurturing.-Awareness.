# Offline research curriculum

This is a deterministic starter exercise set, not an AI worker or model training
system. It uses only Python's standard library, never calls a model or network,
and does not read or write a memory database. Use invented answers and keep any
saved submissions or reports private. No credentials are needed.

## Exercises and rubric

The versioned `research-foundations-v1` catalog contains eight invented cases:
four practice cases and four evaluation cases, each split containing nine
structured decisions. They cover claim/evidence/inference roles, copied source
origins, corrections, and missing evidence. Source observations are evidence for
the limited fictional observation described; they do not certify broader truth.

Each correct decision earns one point. A wrong or omitted answer earns zero,
with the denominator fixed at all nine decisions in that split. Unknown case or
question IDs, choices outside the supplied list, mismatched profile/split/version,
and malformed submissions are errors rather than silently ignored answers.
The report includes correct/total, score, missing count, and per-question feedback.
These scores measure choices in this catalog, not prose quality, source authenticity,
research competence, wellbeing, general intelligence, or improvement of a model.

Practice and evaluation IDs are disjoint. **Both cases and reference answers are
public in the code.** The evaluation split is not a secret or guaranteed unseen
holdout. Copying the reference answers can earn full credit. The test suite
validates the grader with explicit fixtures; no model has been evaluated or shown
to improve by those tests. Later comparisons need independent fresh tasks,
recorded model/prompt/source versions, and a controlled evaluation protocol.

## Running from a checkout

Set `PYTHONPATH` to `src` as described in [local setup](LOCAL_MEMORY.md).

```text
python -m lumina_orion.curriculum show --split practice
python -m lumina_orion.curriculum show --split evaluation
python -m lumina_orion.curriculum grade orion --split practice --answers submission.json
```

Create a local UTF-8 JSON submission with this shape (the omitted answers below
are deliberately scored zero):

```json
{
  "version": 1,
  "curriculum_id": "research-foundations-v1",
  "profile": "orion",
  "split": "practice",
  "answers": {
    "p-origins": {"origins": "one", "independent": "no"}
  }
}
```

Use the exact case/question IDs and choice strings printed by `show`. Profiles
are `orion`, `aurora`, `theo`, `elysia`, and `selene`; a submission must match the
explicitly requested profile. They label a score, not separate agents or OS-level
access controls. All use the same starter exercises; track-specific extensions
are future work. `grade` reads at most 100000 bytes, rejects duplicate JSON keys,
and prints JSON only after successful validation. Errors exit with status 2 and
no report on stdout. A successful grade, including a zero score, exits with 0.
The grader executes no submission content and creates no files. Repeating it in
a fresh process on the same submission yields the same report.

Python callers can use `exercises(split)` and
`grade(submission, profile="orion", split="practice")`. These return independent
data structures without modifying the submission or stored catalog.

## Next validation step

Collect a separately authored synthetic answer attempt, retain it unchanged, and
grade it with this fixed rubric. Diagnose errors before changing the exercises.
Do not use this starter score to grant permissions or declare an autonomous worker
ready for real archives. The memory reconciliation work remains unfinished.

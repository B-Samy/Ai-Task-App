# Architecture notes

**Layers:** UI (`pages/`, `src/ui`) → `AppService` (all mutations + activity log) → `JsonStore` (only code that touches files).
Scheduling, dependency checks, habits, analytics and exports are pure functions over typed records, so they are unit-tested without Streamlit.
The LLM layer only produces *proposals* (plans / chat text). Hard constraints are enforced in Python.

## Priority score (documented, configurable in Settings)
```
score = w_urgency*urgency + w_priority*priority_norm + w_dependency*dependency_impact + w_overdue*overdue
urgency           = 1 / (1 + days_to_deadline)         task deadline, else goal deadline, else 0
priority_norm     = (task.priority + goal.priority)/10  (task.priority/5 if no goal)
dependency_impact = min(1, transitive_dependents/5)
overdue           = 1 if the deadline has passed
```
Defaults: 3.0 / 2.0 / 1.0 / 4.0. Ties break by deadline, creation time, ID, so ordering is deterministic.

## Scheduling algorithm
For each working day from the start date (default horizon 365 days): walk the day window with a cursor; repeatedly pick the highest-scoring *ready* task
(all prerequisites done or fully scheduled earlier, not skipped today, goal available today, goal daily minutes not exceeded, earliest-start reached),
place a block (max block length, no tiny trailing fragments, non-splittable tasks stay whole), advance the cursor by block + break. Daily capacity caps total minutes.
Estimates include the configured buffer %. Block IDs are deterministic (`uuid5(task|date|start)`) so re-running with the same inputs changes nothing.

## Rescheduling and history
`apply_schedule`: identical planned blocks stay untouched; replaced future planned blocks become `superseded`; past planned blocks never completed become `missed`;
completed blocks are never altered. Every application writes an activity entry. Large moves, displaced tasks, or new lateness are flagged `significant` and need confirmation.

## Data files (schema_version 1)
`{ "schema_version": 1, "items": [...] }` per file. Writes validate first, then write a temp file and `os.replace` it. A previous-version backup is kept per save.
Cross-file references (task→goal, prerequisites, block→task, habit→goal, log→habit) are checked on multi-collection saves and by diagnostics.

## ML
Target: actual/estimate ratio. Features known before work starts only (estimate, priority, prerequisite count, splittable, category). K-fold cross-validated MAE is compared against
a median-ratio baseline; reported numbers are real cross-validation results. Below the sample threshold the app reports "insufficient historical data".

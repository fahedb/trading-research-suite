# Experiment specification and tool use

The MCP tools expose the exact JSON schema. Start with `experiment_review` and
a partial object, for example:

```json
{
  "experiment_id": "volatility-return",
  "idea": "Does a large trailing range predict the next 20-bar return?",
  "kind": "predictive"
}
```

The response includes a normalized specification, `missing_details`, known
`integration_work`, `ready_to_freeze` and `spec_sha256`. It is read-only and can
work before a local data workspace has been configured. Unknown keys are errors;
empty or omitted decisions are not inferred. Always preserve the latest draft
in the external research workspace when filesystem tools are available.

## Fields

| Field | Meaning |
| --- | --- |
| `experiment_id`, `revision`, `supersedes_sha256` | Stable slug, positive revision, digest of the immediately preceding revision |
| `idea`, `kind`, `question`, `hypothesis` | The user's idea and research meaning; kind is predictive, simulation or descriptive |
| `signal_rule`, `observation_timing`, `outcome_and_horizon` | Exact observation/signal definition and when inputs/outcomes become known |
| `data` | Instrument, timeframe, period, explicit clock/unit, TRAIN or VALIDATION, source type/description, columns, missing-observation policy, and admitted identity when available |
| `components` | Each required feature, model, representation, decision, outcome, evaluator or data dependency with its definition, inputs, availability and evidence |
| `simulation` | Only for simulation: entry, exit, sizing, overlap, fill timing, conflicts, costs and methodology basis |
| `evaluation` | Metric, baseline, interpretation criterion, split plan, stopping rule, run count and resource limits |
| `open_questions`, `assumptions` | Unresolved decisions and explicit accepted/proposed conventions |

Descriptive studies do not require a predictive hypothesis, future outcome or
baseline. They still need a question, observed quantity, timing, data, metric and
bounded evaluation plan. Do not insert trading fields to satisfy a form.

Component availability is one of `unknown`, `available`, `derive`, `implement`,
`obtain`, `unsupported`. An available component needs an evidence reference to
its actual implementation or metadata; a matching registry name is not enough
to prove semantic equivalence. The validator checks the presence of evidence,
not whether a claim about source code is true.

Dataset identities can remain unresolved at design freeze. That produces an
integration task and cannot be used to authorize a run. Permitted roles in this
authoring workflow are TRAIN and VALIDATION; no protected-data unlock is exposed.
Source types are `admitted`, `personal_file`, `catalog`, `external_timed_feed`.
The last is recorded as unsupported by the current engine.

## Freeze and resume

After presenting the concrete design and resolving material unknowns, supply:

```text
experiment_freeze(
  spec = the exact reviewed object,
  approval = {
    spec_sha256: digest returned by review,
    user_message: the actual instruction covering this design,
    scope: integrate | integrate_and_run
  }
)
```

The MCP result is `FROZEN_FOR_INTEGRATION`, with `ready_to_run: false` even when
all components are listed as available. Nothing was executed or evaluated.
The record is stored at
`<workspace>/experiments/<experiment_id>/revision-NNNN.json`.

Repeated calls with the same specification and approval return the existing
record. Changing an existing revision or its approval is refused. For a change,
increment revision by one and use the prior `spec_sha256` as `supersedes_sha256`.
Use `experiment_get(experiment_id, revision)` to resume; it checks identity and
digest. It is not a tamper-proof signature system or an identity provider.

Keep integration progress in a separate `revision-NNNN-integration.md` beside
the frozen file when the host supports file writes. Record evidence and remaining
work without rewriting the agreed experiment or marking the engine as running.

The specification digest is not an engine experiment fingerprint. The executable
engine config, dataset identities, source commit and lock identity must be bound
later. A specification revision is not a run, a trial or a canonicality claim.

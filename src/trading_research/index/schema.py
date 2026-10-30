"""Closed SQLite schema for the disposable Phase-5 run index."""

from __future__ import annotations

__all__: list[str] = []

_SCHEMA_VERSION = "1"

_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = DELETE;
PRAGMA synchronous = FULL;

CREATE TABLE index_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE run (
  run_id TEXT PRIMARY KEY,
  experiment_fingerprint TEXT NOT NULL,
  attempt_number INTEGER NOT NULL,
  state TEXT NOT NULL,
  canonical INTEGER NOT NULL,
  outcome_class TEXT,
  evaluation_count INTEGER NOT NULL DEFAULT 0,
  policy_execution_count INTEGER NOT NULL DEFAULT 0,
  oos_touched INTEGER NOT NULL,
  lockbox_touched INTEGER NOT NULL,
  declared_lockbox_evaluation INTEGER NOT NULL,
  cost_semantics_unverified INTEGER NOT NULL DEFAULT 0,
  binding_id TEXT,
  binding_digest TEXT,
  execution_price_model TEXT,
  dataset_id TEXT,
  dataset_manifest_sha256 TEXT,
  dataset_role TEXT,
  platform_git_commit TEXT NOT NULL,
  platform_git_dirty INTEGER NOT NULL,
  platform_diff_sha256 TEXT,
  dependency_lock_sha256 TEXT NOT NULL,
  created_unix_ns INTEGER NOT NULL,
  started_unix_ns INTEGER,
  finished_unix_ns INTEGER,
  failure_reason TEXT,
  heartbeat_age_ns INTEGER NOT NULL,
  heartbeat_stale INTEGER NOT NULL,
  resolved_config_json TEXT NOT NULL,
  record_digest TEXT NOT NULL
);
CREATE INDEX run_fingerprint_idx ON run(experiment_fingerprint);
CREATE INDEX run_state_idx ON run(state, outcome_class);
CREATE INDEX run_protected_idx ON run(oos_touched, lockbox_touched);
CREATE INDEX run_finished_idx ON run(finished_unix_ns);

CREATE TABLE access_audit_ref (
  run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  session_id TEXT NOT NULL,
  audit_event_id TEXT,
  PRIMARY KEY (run_id, ordinal),
  UNIQUE (run_id, session_id),
  UNIQUE (audit_event_id)
);

CREATE TABLE evaluation (
  run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  entry_ordinal INTEGER NOT NULL,
  plane TEXT NOT NULL,
  fitted_state_identity TEXT NOT NULL,
  model_output_identity TEXT NOT NULL,
  evaluated_columns_json TEXT,
  outcome_plugin_id TEXT,
  outcome_version INTEGER,
  outcome_params_json TEXT,
  horizon_rows INTEGER,
  decision_delay_rows INTEGER,
  policy_plugin_id TEXT,
  policy_version INTEGER,
  policy_params_json TEXT,
  execution_spec_json TEXT,
  evaluator_plugin_id TEXT NOT NULL,
  evaluator_version INTEGER NOT NULL,
  evaluator_params_json TEXT NOT NULL,
  comparison_tolerance_json TEXT,
  cost_semantics_unverified INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, entry_ordinal)
);
CREATE INDEX evaluation_question_idx
  ON evaluation(
    plane,
    outcome_plugin_id,
    outcome_version,
    horizon_rows,
    decision_delay_rows,
    policy_plugin_id,
    policy_version,
    evaluator_plugin_id,
    evaluator_version
  );

CREATE TABLE metric (
  run_id TEXT NOT NULL,
  entry_ordinal INTEGER NOT NULL,
  metric_name TEXT NOT NULL,
  value_real REAL,
  value_bits_hex TEXT NOT NULL,
  effective_samples INTEGER NOT NULL,
  PRIMARY KEY (run_id, entry_ordinal, metric_name),
  FOREIGN KEY (run_id, entry_ordinal)
    REFERENCES evaluation(run_id, entry_ordinal) ON DELETE CASCADE
);
CREATE INDEX metric_name_value_idx ON metric(metric_name, value_real);

CREATE TABLE policy_execution (
  run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  plugin_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  params_json TEXT NOT NULL,
  intent_schema_id TEXT NOT NULL,
  intent_rows INTEGER NOT NULL,
  first_key INTEGER,
  last_key INTEGER,
  PRIMARY KEY (run_id, ordinal)
);

CREATE TABLE sweep (
  sweep_id TEXT PRIMARY KEY,
  spec_digest TEXT NOT NULL,
  git_commit TEXT NOT NULL,
  child_count INTEGER NOT NULL,
  duplicate_count INTEGER NOT NULL,
  created_unix_ns INTEGER NOT NULL
);

CREATE TABLE sweep_member (
  sweep_id TEXT NOT NULL REFERENCES sweep(sweep_id) ON DELETE CASCADE,
  child_ordinal INTEGER NOT NULL,
  experiment_fingerprint TEXT NOT NULL,
  PRIMARY KEY (sweep_id, child_ordinal),
  UNIQUE (sweep_id, experiment_fingerprint)
);
CREATE INDEX sweep_member_fingerprint_idx
  ON sweep_member(experiment_fingerprint);

CREATE TABLE scan_error (
  run_dir_name TEXT PRIMARY KEY,
  error_kind TEXT NOT NULL,
  detail TEXT NOT NULL
);
"""

_TABLES = (
    "access_audit_ref",
    "evaluation",
    "index_meta",
    "metric",
    "policy_execution",
    "run",
    "scan_error",
    "sweep",
    "sweep_member",
)

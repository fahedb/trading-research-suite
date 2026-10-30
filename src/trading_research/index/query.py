"""Fixed parameterized lookup and descriptive metric-comparison statements."""

from __future__ import annotations

import json as _json
import math as _math
import re as _re
import sqlite3 as _sqlite3
import struct as _struct
import typing as _typing
from dataclasses import dataclass as _dataclass

from trading_research.index.build import _IndexError, _open_fresh_index

if _typing.TYPE_CHECKING:
    from pathlib import Path as _Path

__all__: list[str] = []

_SHA256 = _re.compile(r"^[0-9a-f]{64}$")
_STATES = frozenset({"PENDING", "RUNNING", "COMPLETE", "FAILED", "INTERRUPTED"})
_OUTCOME_CLASSES = frozenset({"evaluated", "descriptive", "pre_evaluation_failure"})
_DATASET_ROLES = frozenset({"TRAIN", "VALIDATION", "OOS", "LOCKBOX"})
_PLANES = frozenset({"A", "B"})
_ORDERINGS = frozenset({"finished", "metric"})
_MAX_LIMIT = 10_000


@_dataclass(frozen=True, slots=True)
class _QueryResult:
    _rows: tuple[dict[str, object], ...]
    _scan_errors: tuple[dict[str, object], ...]


@_dataclass(frozen=True, slots=True)
class _ComparisonResult:
    _metric_name: str
    _plane: str | None
    _groups: tuple[dict[str, object], ...]
    _scan_errors: tuple[dict[str, object], ...]


def _query_runs(
    runs_root: _Path,
    *,
    states: tuple[str, ...] = (),
    outcome_classes: tuple[str, ...] = (),
    canonical: bool | None = None,
    fingerprints: tuple[str, ...] = (),
    sweep_id: str | None = None,
    dataset_roles: tuple[str, ...] = (),
    oos_touched: bool | None = None,
    lockbox_touched: bool | None = None,
    config_predicates: tuple[str, ...] = (),
    metric_name: str | None = None,
    plane: str | None = None,
    order_by: str = "finished",
    limit: int = 100,
    include_protected: bool = False,
) -> _QueryResult:
    checked_states = _validated_values(states, allowed=_STATES, label="state")
    checked_outcomes = _validated_values(
        outcome_classes,
        allowed=_OUTCOME_CLASSES,
        label="outcome class",
    )
    checked_fingerprints = tuple(_validated_fingerprint(value) for value in fingerprints)
    checked_roles = _validated_values(dataset_roles, allowed=_DATASET_ROLES, label="dataset role")
    checked_plane = _validated_optional_value(plane, allowed=_PLANES, label="plane")
    if type(order_by) is not str or order_by not in _ORDERINGS:
        raise _IndexError("order_by must be 'finished' or 'metric'")
    if type(limit) is not int or not 1 <= limit <= _MAX_LIMIT:
        raise _IndexError(f"limit must be an exact int from 1 through {_MAX_LIMIT}")
    for value, label in (
        (canonical, "canonical"),
        (oos_touched, "oos_touched"),
        (lockbox_touched, "lockbox_touched"),
    ):
        if value is not None and type(value) is not bool:
            raise _IndexError(f"{label} must be an exact bool or None")
    if type(include_protected) is not bool:
        raise _IndexError("include_protected must be an exact bool")
    if not include_protected and (oos_touched is True or lockbox_touched is True):
        raise _IndexError("positive protected filters require include_protected=True")
    metric = _validated_optional_text(metric_name, label="metric_name")
    sweep = _validated_optional_text(sweep_id, label="sweep_id")
    predicates = tuple(_config_predicate(value) for value in config_predicates)

    joins: list[str] = []
    conditions = ["1 = 1"]
    parameters: list[object] = []
    selecting_evaluations = metric is not None or checked_plane is not None
    if selecting_evaluations:
        joins.append("JOIN evaluation AS e ON e.run_id = r.run_id")
    if metric is not None:
        joins.append(
            "JOIN metric AS m ON m.run_id = e.run_id AND m.entry_ordinal = e.entry_ordinal"
        )
        conditions.append("m.metric_name = ?")
        parameters.append(metric)
    if checked_plane is not None:
        conditions.append("e.plane = ?")
        parameters.append(checked_plane)
    _append_in_filter(conditions, parameters, "r.state", checked_states)
    _append_in_filter(conditions, parameters, "r.outcome_class", checked_outcomes)
    _append_in_filter(conditions, parameters, "r.experiment_fingerprint", checked_fingerprints)
    _append_in_filter(conditions, parameters, "UPPER(r.dataset_role)", checked_roles)
    if canonical is not None:
        conditions.append("r.canonical = ?")
        parameters.append(int(canonical))
    if oos_touched is not None:
        conditions.append("r.oos_touched = ?")
        parameters.append(int(oos_touched))
    if lockbox_touched is not None:
        conditions.append("r.lockbox_touched = ?")
        parameters.append(int(lockbox_touched))
    if not include_protected:
        conditions.extend(("r.oos_touched = 0", "r.lockbox_touched = 0"))
    if sweep is not None:
        conditions.append(
            """
            EXISTS (
              SELECT 1 FROM sweep_member AS sm
              WHERE sm.sweep_id = ?
                AND sm.experiment_fingerprint = r.experiment_fingerprint
            )
            """
        )
        parameters.append(sweep)
    for json_path, expected in predicates:
        conditions.append("json_extract(r.resolved_config_json, ?) = ?")
        parameters.extend((json_path, expected))

    evaluation_columns = (
        """
        , e.entry_ordinal, e.plane, e.fitted_state_identity,
          e.cost_semantics_unverified
        """
        if selecting_evaluations
        else ""
    )
    metric_columns = (
        ", m.metric_name, m.value_real, m.value_bits_hex, m.effective_samples"
        if metric is not None
        else ""
    )
    if order_by == "metric":
        if metric is None:
            raise _IndexError("metric ordering requires metric_name")
        ordering = "(m.value_real IS NULL), m.value_real DESC, r.run_id, e.entry_ordinal"
    else:
        ordering = "(r.finished_unix_ns IS NULL), r.finished_unix_ns DESC, r.run_id"
    statement = f"""
        SELECT
          r.run_id, r.experiment_fingerprint, r.attempt_number, r.state,
          r.canonical, r.outcome_class, r.evaluation_count,
          r.policy_execution_count, r.oos_touched, r.lockbox_touched,
          r.dataset_id, r.dataset_role, r.created_unix_ns,
          r.started_unix_ns, r.finished_unix_ns, r.failure_reason
          {evaluation_columns}
          {metric_columns}
        FROM run AS r
        {' '.join(joins)}
        WHERE {' AND '.join(conditions)}
        ORDER BY {ordering}
        LIMIT ?
    """
    parameters.append(limit)
    connection = _open_fresh_index(runs_root, allow_scan_errors=True)
    try:
        connection.row_factory = _sqlite3.Row
        rows = tuple(_query_row_document(row) for row in connection.execute(statement, parameters))
        errors = _scan_error_documents(connection)
    finally:
        connection.close()
    return _QueryResult(rows, errors)


def _compare_metric(
    runs_root: _Path,
    *,
    metric_name: str,
    sweep_id: str | None = None,
    fingerprints: tuple[str, ...] = (),
    plane: str | None = None,
    include_protected: bool = False,
) -> _ComparisonResult:
    metric = _validated_optional_text(metric_name, label="metric_name")
    if metric is None:
        raise _IndexError("metric_name is required")
    checked_plane = _validated_optional_value(plane, allowed=_PLANES, label="plane")
    sweep = _validated_optional_text(sweep_id, label="sweep_id")
    checked_fingerprints = tuple(_validated_fingerprint(value) for value in fingerprints)
    if (sweep is None) == (not checked_fingerprints):
        raise _IndexError("comparison requires exactly one scope: sweep or fingerprints")
    if type(include_protected) is not bool:
        raise _IndexError("include_protected must be an exact bool")
    conditions = ["r.state = ?", "r.outcome_class = ?", "m.metric_name = ?"]
    parameters: list[object] = ["COMPLETE", "evaluated", metric]
    if checked_plane is not None:
        conditions.append("e.plane = ?")
        parameters.append(checked_plane)
    if not include_protected:
        conditions.extend(("r.oos_touched = 0", "r.lockbox_touched = 0"))
    if sweep is not None:
        conditions.append(
            """
            EXISTS (
              SELECT 1 FROM sweep_member AS sm
              WHERE sm.sweep_id = ?
                AND sm.experiment_fingerprint = r.experiment_fingerprint
            )
            """
        )
        parameters.append(sweep)
    else:
        _append_in_filter(
            conditions,
            parameters,
            "r.experiment_fingerprint",
            checked_fingerprints,
        )
    statement = f"""
        SELECT
          r.run_id, r.experiment_fingerprint, r.attempt_number,
          r.oos_touched, r.lockbox_touched,
          e.entry_ordinal, e.plane, e.fitted_state_identity,
          e.evaluated_columns_json, e.outcome_plugin_id, e.outcome_version,
          e.outcome_params_json, e.horizon_rows, e.decision_delay_rows,
          e.policy_plugin_id, e.policy_version, e.policy_params_json,
          e.execution_spec_json, e.evaluator_plugin_id, e.evaluator_version,
          e.evaluator_params_json, e.cost_semantics_unverified,
          m.metric_name, m.value_real, m.value_bits_hex, m.effective_samples
        FROM run AS r
        JOIN evaluation AS e ON e.run_id = r.run_id
        JOIN metric AS m ON m.run_id = e.run_id AND m.entry_ordinal = e.entry_ordinal
        WHERE {' AND '.join(conditions)}
        ORDER BY r.experiment_fingerprint, r.attempt_number,
                 e.entry_ordinal, r.run_id
    """
    connection = _open_fresh_index(runs_root, allow_scan_errors=True)
    try:
        connection.row_factory = _sqlite3.Row
        raw_rows = tuple(connection.execute(statement, parameters))
        errors = _scan_error_documents(connection)
    finally:
        connection.close()
    groups: dict[tuple[str, str, str], list[_sqlite3.Row]] = {}
    for row in raw_rows:
        question = _question_document(row)
        question_json = _canonical_json(question)
        key = (row["experiment_fingerprint"], row["plane"], question_json)
        groups.setdefault(key, []).append(row)
    documents = tuple(
        sorted(
            (
                _comparison_group_document(key, rows)
                for key, rows in groups.items()
            ),
            key=_comparison_sort_key,
        )
    )
    return _ComparisonResult(metric, checked_plane, documents, errors)


def _comparison_group_document(
    key: tuple[str, str, str],
    rows: list[_sqlite3.Row],
) -> dict[str, object]:
    fingerprint, plane, question_json = key
    folds: list[dict[str, object]] = []
    numeric_values: list[float] = []
    contains_nan = False
    for row in rows:
        numeric = _struct.unpack(">d", bytes.fromhex(row["value_bits_hex"]))[0]
        if _math.isnan(numeric):
            contains_nan = True
        else:
            numeric_values.append(numeric)
        folds.append(
            {
                "run_id": row["run_id"],
                "attempt_number": row["attempt_number"],
                "entry_ordinal": row["entry_ordinal"],
                "fitted_state_identity": row["fitted_state_identity"],
                "value": None if _math.isnan(numeric) else numeric,
                "value_float64_bits": row["value_bits_hex"],
                "effective_samples": row["effective_samples"],
            }
        )
    descriptive_mean = (
        None
        if contains_nan or not numeric_values
        else _math.fsum(numeric_values) / len(numeric_values)
    )
    return {
        "experiment_fingerprint": fingerprint,
        "plane": plane,
        "question": _json.loads(question_json),
        "fold_measurements": folds,
        "fold_measurement_count": len(folds),
        "descriptive_cross_fold_mean": descriptive_mean,
        "descriptive_summary_only": True,
        "contains_nan": contains_nan,
        "protected": any(bool(row["oos_touched"] or row["lockbox_touched"]) for row in rows),
        "cost_semantics_unverified": any(
            bool(row["cost_semantics_unverified"]) for row in rows
        ),
    }


def _comparison_sort_key(document: dict[str, object]) -> tuple[bool, float, str, str]:
    value = document["descriptive_cross_fold_mean"]
    missing = value is None
    if value is not None and type(value) is not float:
        raise _IndexError("comparison mean must be a float or None")
    numeric = 0.0 if value is None else -value
    return (
        missing,
        numeric,
        str(document["experiment_fingerprint"]),
        _canonical_json(document["question"]),
    )


def _question_document(row: _sqlite3.Row) -> dict[str, object]:
    evaluator = {
        "plugin_id": row["evaluator_plugin_id"],
        "version": row["evaluator_version"],
        "params": _json.loads(row["evaluator_params_json"]),
    }
    if row["plane"] == "A":
        return {
            "evaluated_output_columns": _json.loads(row["evaluated_columns_json"]),
            "outcome": {
                "plugin_id": row["outcome_plugin_id"],
                "version": row["outcome_version"],
                "params": _json.loads(row["outcome_params_json"]),
            },
            "horizon_rows": row["horizon_rows"],
            "decision_delay_rows": row["decision_delay_rows"],
            "evaluator": evaluator,
            "metric_name": row["metric_name"],
        }
    return {
        "policy": {
            "plugin_id": row["policy_plugin_id"],
            "version": row["policy_version"],
            "params": _json.loads(row["policy_params_json"]),
        },
        "execution_spec": _json.loads(row["execution_spec_json"]),
        "evaluator": evaluator,
        "metric_name": row["metric_name"],
    }


def _query_row_document(row: _sqlite3.Row) -> dict[str, object]:
    document = {key: row[key] for key in row.keys()}  # noqa: SIM118 - Row iterates values
    for name in (
        "canonical",
        "oos_touched",
        "lockbox_touched",
        "heartbeat_stale",
        "cost_semantics_unverified",
    ):
        if name in document:
            document[name] = bool(document[name])
    if "value_bits_hex" in document:
        numeric = _struct.unpack(">d", bytes.fromhex(str(document["value_bits_hex"])))[0]
        document["value"] = None if _math.isnan(numeric) else numeric
        document["value_is_nan"] = _math.isnan(numeric)
        document.pop("value_real", None)
    document["protected"] = bool(document["oos_touched"] or document["lockbox_touched"])
    return document


def _scan_error_documents(connection: _sqlite3.Connection) -> tuple[dict[str, object], ...]:
    rows = connection.execute(
        "SELECT run_dir_name, error_kind, detail FROM scan_error ORDER BY run_dir_name"
    ).fetchall()
    return tuple(
        {
            "run_dir_name": row[0],
            "error_kind": row[1],
            "detail": row[2],
        }
        for row in rows
    )


def _config_predicate(value: object) -> tuple[str, object]:
    if type(value) is not str or "=" not in value:
        raise _IndexError("config predicate must be <json_pointer>=<JSON value>")
    pointer, encoded = value.split("=", 1)
    path = _json_pointer_path(pointer)
    try:
        expected = _json.loads(encoded)
    except _json.JSONDecodeError:
        expected = encoded
    if type(expected) in {dict, list}:
        expected = _canonical_json(expected)
    elif type(expected) is bool:
        expected = int(expected)
    return path, expected


def _json_pointer_path(pointer: object) -> str:
    if type(pointer) is not str or not pointer.startswith("/"):
        raise _IndexError("config path must be an RFC-6901 JSON pointer")
    parts = pointer[1:].split("/")
    decoded: list[str] = []
    for part in parts:
        index = 0
        output = ""
        while index < len(part):
            if part[index] != "~":
                output += part[index]
                index += 1
                continue
            if index + 1 >= len(part) or part[index + 1] not in {"0", "1"}:
                raise _IndexError("config JSON pointer contains an invalid escape")
            output += "~" if part[index + 1] == "0" else "/"
            index += 2
        if not output or any(ord(character) < 32 for character in output):
            raise _IndexError("config JSON pointer segments must be non-empty printable text")
        decoded.append(output)
    return "$" + "".join(
        f'."{part.replace(chr(34), chr(92) + chr(34))}"' for part in decoded
    )


def _append_in_filter(
    conditions: list[str],
    parameters: list[object],
    column: str,
    values: tuple[str, ...],
) -> None:
    if not values:
        return
    placeholders = ",".join("?" for _value in values)
    conditions.append(f"{column} IN ({placeholders})")
    parameters.extend(values)


def _validated_values(
    values: object,
    *,
    allowed: frozenset[str],
    label: str,
) -> tuple[str, ...]:
    if type(values) is not tuple or any(type(value) is not str for value in values):
        raise _IndexError(f"{label} filters must be an exact tuple of strings")
    checked = tuple(values)
    if any(value not in allowed for value in checked):
        raise _IndexError(f"unsupported {label} filter")
    return checked


def _validated_optional_value(
    value: object,
    *,
    allowed: frozenset[str],
    label: str,
) -> str | None:
    if value is None:
        return None
    if type(value) is not str or value not in allowed:
        raise _IndexError(f"unsupported {label}")
    return value


def _validated_optional_text(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value.strip() or value != value.strip():
        raise _IndexError(f"{label} must be trimmed non-empty text")
    return value


def _validated_fingerprint(value: object) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise _IndexError("fingerprint must be 64 lowercase hexadecimal characters")
    return value


def _canonical_json(value: object) -> str:
    try:
        return _json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _IndexError("query value is not JSON-compatible") from exc

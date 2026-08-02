"""Suite runner package: corpus store, live suite runner, and the replay tier.

`store` has no sibling-component dependencies and is always importable;
`suite_runner` / `replay` import the concurrently-written sandbox / executors /
graders / scoring siblings, so they are exposed lazily here (PEP 562) — the
CLI's lint/report paths keep working before those land.
"""

from convoy_evals.runner.store import (  # noqa: F401
    AnswerKeyFile,
    LoadedEvalSet,
    answer_key_hash_matches,
    default_out_dir,
    empty_gate_report,
    events_file_name,
    harness_error_trial,
    load_answer_key,
    load_answer_key_file,
    load_eval_set,
    load_quarantine,
    load_scenario,
    parse_quarantine_yaml,
    read_jsonl_events,
    resolve_answer_key_path,
    sim_days_of,
    sort_events,
    sum_budget_debits,
    timestamp_slug,
    to_jsonl,
    verdicts_file_name,
    world_file_name,
)

_LAZY = {
    "run_suite": ("convoy_evals.runner.suite_runner", "run_suite"),
    "replay_suite": ("convoy_evals.runner.replay", "replay_suite"),
}


def __getattr__(name):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError("module {0!r} has no attribute {1!r}".format(__name__, name))
    import importlib

    module = importlib.import_module(target[0])
    return getattr(module, target[1])

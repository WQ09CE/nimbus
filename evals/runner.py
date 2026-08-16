"""Nimbus micro-eval runner — in-process harness evaluation.

Runs each task in evals/tasks/ against a real model, then scores two layers:
1. verifier pass/fail (deterministic, per-task verify.py)
2. harness metrics from the session event log (turns/steps/reasons/stalls/
   compactions/tokens) — the regression signal for core surgery.

Weak local models (gemma4/qwen) give a FREE smoke rail: their pass rates
wobble, so treat pass/fail as advisory there and read the harness metrics;
pass-rate truth comes from the sonnet rail.

Usage:
    python evals/runner.py                          # all tasks, default model
    python evals/runner.py --model ollama/qwen3.8:latest
    python evals/runner.py --tasks hello-tool,followup
    python evals/runner.py --baseline               # save as baseline for model
"""

import argparse
import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import tomllib

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from nimbus.adapters.llm_factory import create_llm_client  # noqa: E402
from nimbus.core.agent import AgentConfig, AgentOS  # noqa: E402
from nimbus.core.session_log import check_invariants  # noqa: E402
from nimbus.core.storage import SessionStorage  # noqa: E402

TASKS_DIR = Path(__file__).resolve().parent / "tasks"
BASELINES_DIR = Path(__file__).resolve().parent / "baselines"
DEFAULT_MODEL = "ollama/gemma4:12b-it-qat"


def _load_verifier(task_dir: Path):
    spec = importlib.util.spec_from_file_location(
        f"verify_{task_dir.name.replace('-', '_')}", task_dir / "verify.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.verify


def _metrics(log) -> dict:
    events = log.events
    counts: dict = {}
    for e in events:
        counts[e.type] = counts.get(e.type, 0) + 1
    reasons = [
        e.data.get("reason", {}).get("kind")
        for e in events
        if e.type == "turn/end"
    ]
    return {
        "events": len(events),
        "turns": counts.get("turn/start", 0),
        "steps": counts.get("step/start", 0),
        "tool_results": counts.get("tool/result", 0),
        "compactions": counts.get("compaction/applied", 0),
        "turn_end_reasons": reasons,
        "invariant_violations": check_invariants(events, allow_open_tail=True),
    }


async def _interrupt_after_first_tool(loop) -> None:
    """Consume the stream; pull the plug once the first tool result lands."""
    async for _event in loop.stream():
        if any(e.type == "tool/result" for e in loop.session_log.events):
            loop.request_interruption()


async def run_task(task_dir: Path, model: str, thinking_effort=None) -> dict:
    cfg = tomllib.loads((task_dir / "task.toml").read_text())
    goal = cfg["task"]["goal"]
    mode = cfg["task"].get("mode", "normal")
    timeout = cfg["task"].get("timeout_sec", 300)
    followups = cfg["task"].get("followups", [])
    agent_cfg = cfg.get("agent", {})

    run_dir = Path(tempfile.mkdtemp(prefix=f"nimbus-eval-{task_dir.name}-"))
    workspace = run_dir / "workspace"
    fixture = task_dir / "workspace"
    if fixture.exists():
        shutil.copytree(fixture, workspace)
    else:
        workspace.mkdir()
    storage = SessionStorage(str(run_dir / "sessions"))

    record: dict = {"task": task_dir.name, "model": model, "mode": mode}
    original_cwd = os.getcwd()
    os.chdir(workspace)
    llm = await create_llm_client(model=model, thinking_effort=thinking_effort)
    await llm.start()
    t0 = time.monotonic()
    try:
        config = AgentConfig(
            model=model,
            max_iterations=agent_cfg.get("max_iterations", 30),
            max_context_tokens=agent_cfg.get("max_context_tokens", 0),
        )
        agent = AgentOS(config=config, adapter=llm)
        loop = agent._build_loop(goal, session_id="eval", storage=storage)
        for msg in followups:
            loop.followup_queue.follow_up(msg)

        if mode == "interrupt_resume":
            await asyncio.wait_for(_interrupt_after_first_tool(loop), timeout=timeout)
            # Fork primitive (Phase 3): seed a new session from the parent's
            # log instead of hand-carrying initial_messages.
            dump = storage.fork_session(loop.session_id, "eval-resume") or {"messages": []}
            record["recovery_injected"] = sum(
                1 for m in dump["messages"] if m.get("meta", {}).get("synthetic")
            )
            resume_goal = cfg["task"].get("resume_goal", "Continue the task to completion.")
            resume_loop = agent._build_loop(
                resume_goal,
                session_id="eval-resume",
                storage=storage,
                initial_messages=dump["messages"],
            )
            result = await asyncio.wait_for(resume_loop.run(), timeout=timeout)
            log = resume_loop.session_log
            record["interrupt_metrics"] = _metrics(loop.session_log)
        else:
            result = await asyncio.wait_for(loop.run(), timeout=timeout)
            log = loop.session_log

        record["wall_sec"] = round(time.monotonic() - t0, 1)
        record["status"] = result.status
        record["metrics"] = _metrics(log)
        usage = getattr(
            loop if mode != "interrupt_resume" else resume_loop,
            "_cumulative_usage", None,
        )
        if usage is not None and hasattr(usage, "to_dict"):
            record["usage"] = usage.to_dict()

        verify = _load_verifier(task_dir)
        passed, detail = verify(workspace, result.output or "", log)
        record["passed"] = bool(passed)
        record["detail"] = detail
    except asyncio.TimeoutError:
        record["wall_sec"] = round(time.monotonic() - t0, 1)
        record["status"] = "TIMEOUT"
        record["passed"] = False
        record["detail"] = f"timed out after {timeout}s"
        # Salvage harness metrics — a timeout run's log is often the most
        # interesting one (e.g. compaction churn), and the cancellation
        # backstop has closed its brackets.
        if "loop" in locals():
            record["metrics"] = _metrics(loop.session_log)
            usage = getattr(loop, "_cumulative_usage", None)
            if usage is not None and hasattr(usage, "to_dict"):
                record["usage"] = usage.to_dict()
    except Exception as e:
        record["wall_sec"] = round(time.monotonic() - t0, 1)
        record["status"] = "CRASH"
        record["passed"] = False
        record["detail"] = f"{type(e).__name__}: {e}"
    finally:
        os.chdir(original_cwd)
        await llm.stop()
    record["artifacts"] = str(run_dir)
    return record


def _print_report(records: list) -> None:
    print(f"\n{'task':<16} {'pass':<5} {'status':<9} {'wall':<7} "
          f"{'turns':<6} {'steps':<6} {'tools':<6} {'compact':<8} reasons")
    print("-" * 92)
    for r in records:
        m = r.get("metrics", {})
        print(f"{r['task']:<16} {str(r.get('passed')):<5} {r.get('status', '?'):<9} "
              f"{str(r.get('wall_sec', '?')) + 's':<7} "
              f"{m.get('turns', '-'):<6} {m.get('steps', '-'):<6} "
              f"{m.get('tool_results', '-'):<6} {m.get('compactions', '-'):<8} "
              f"{','.join(m.get('turn_end_reasons', []))}")
        if m.get("invariant_violations"):
            print(f"  !! invariants: {m['invariant_violations']}")
        if not r.get("passed"):
            print(f"  detail: {r.get('detail', '')[:120]}")
    n_pass = sum(1 for r in records if r.get("passed"))
    print(f"\n{n_pass}/{len(records)} passed")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--tasks", default="", help="comma-separated task names")
    parser.add_argument("--baseline", action="store_true",
                        help="save results as the baseline for this model")
    parser.add_argument("--thinking-effort", default=None,
                        choices=["off", "low", "medium", "high"],
                        help="reasoning effort passed to the adapter (default: channel default)")
    args = parser.parse_args()

    wanted = [t.strip() for t in args.tasks.split(",") if t.strip()]
    task_dirs = sorted(
        d for d in TASKS_DIR.iterdir()
        if d.is_dir() and (not wanted or d.name in wanted)
    )
    if not task_dirs:
        print("no tasks found", file=sys.stderr)
        return 1

    records = []
    for task_dir in task_dirs:
        print(f"→ {task_dir.name} ...", flush=True)
        records.append(await run_task(task_dir, args.model, args.thinking_effort))

    _print_report(records)

    BASELINES_DIR.mkdir(exist_ok=True)
    slug = args.model.replace("/", "_").replace(":", "_")
    out = BASELINES_DIR / (f"{slug}.json" if args.baseline else f"{slug}.latest.json")
    out.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

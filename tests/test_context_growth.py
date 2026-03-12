#!/usr/bin/env python3
"""Context Growth Monitoring Test

Sends a realistic multi-round conversation through the Nimbus API
and records token usage at each step. Use this to compare context
growth before and after the context-optimization changes.

Usage:
    # 1. Make sure nimbus server is running on localhost:3456
    # 2. Run baseline test (before optimization):
    python tests/test_context_growth.py --label baseline

    # 3. Apply optimizations, restart server
    # 4. Run optimized test:
    python tests/test_context_growth.py --label optimized

    # 5. Compare:
    python tests/test_context_growth.py --compare baseline optimized

Results are saved to tests/results/context_growth_<label>.json
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

BASE_URL = os.environ.get("NIMBUS_URL", "http://localhost:3456/api")
RESULTS_DIR = Path(__file__).parent / "results"

# ─────────────────────────────────────────────────────────────
# Test Scenarios: each triggers different tool patterns
# ─────────────────────────────────────────────────────────────

SCENARIOS = [
    {
        "name": "1_simple_question",
        "message": "What's the current working directory? Just tell me the path, no tool calls needed.",
        "description": "Baseline: pure text, no tools",
    },
    {
        "name": "2_read_small_file",
        "message": "Read the file pyproject.toml and tell me the project version.",
        "description": "Single Read: small file (~2KB)",
    },
    {
        "name": "3_read_large_file",
        "message": "Read the file src/nimbus/core/mmu.py and tell me how many classes are defined in it.",
        "description": "Single Read: medium file (~30KB, tests read truncation)",
    },
    {
        "name": "4_grep_codebase",
        "message": "Search for all occurrences of 'def estimate_tokens' in the src/ directory.",
        "description": "Grep: moderate matches, tests grep output size",
    },
    {
        "name": "5_grep_broad",
        "message": "Search for 'import' in all .py files under src/nimbus/core/tools/. Show me the results.",
        "description": "Grep: many matches, tests grep line limit + byte limit",
    },
    {
        "name": "6_bash_command",
        "message": "Run 'find src/ -name \"*.py\" | head -30' and tell me how many python files there are.",
        "description": "Bash: small output",
    },
    {
        "name": "7_bash_large_output",
        "message": "Run 'find . -type f -not -path \"./node_modules/*\" -not -path \"./.git/*\"' and count the total files.",
        "description": "Bash: potentially large output, tests truncation",
    },
    {
        "name": "8_multi_read",
        "message": "Read these three files and compare their sizes: src/nimbus/core/loop.py, src/nimbus/core/vcpu.py, src/nimbus/core/gate.py",
        "description": "Multi-tool: 3 parallel Reads, tests context accumulation",
    },
    {
        "name": "9_grep_json",
        "message": "Search for 'version' in package.json and web-ui/package.json. Show full matching lines.",
        "description": "Grep: JSON files with potentially long lines",
    },
    {
        "name": "10_complex_task",
        "message": "List all functions in src/nimbus/core/tools/bash.py that have 'output' in their name. Show the function signatures.",
        "description": "Multi-step: Read + analysis, tests cumulative growth",
    },
]

# ─────────────────────────────────────────────────────────────
# SSE Client
# ─────────────────────────────────────────────────────────────

async def send_chat_and_collect_usage(
    client: httpx.AsyncClient,
    session_id: str,
    message: str,
    timeout: float = 120.0,
) -> dict:
    """Send a chat message and collect usage_update events via SSE."""
    usage_events = []
    tool_calls = []
    text_length = 0
    compaction_happened = False

    try:
        async with client.stream(
            "POST",
            f"{BASE_URL}/sessions/{session_id}/chat",
            json={"content": message},
            timeout=timeout,
        ) as response:
            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    event_text, buffer = buffer.split("\n\n", 1)
                    # Parse SSE
                    event_type = None
                    event_data = None
                    for line in event_text.split("\n"):
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            try:
                                event_data = json.loads(line[5:].strip())
                            except json.JSONDecodeError:
                                event_data = line[5:].strip()

                    if event_type == "usage_update" and isinstance(event_data, dict):
                        usage_events.append(event_data)

                    elif event_type == "tool_start" and isinstance(event_data, dict):
                        tool_calls.append({
                            "tool": event_data.get("tool", "?"),
                            "args_preview": {
                                k: str(v)[:80]
                                for k, v in event_data.get("args", {}).items()
                            },
                        })

                    elif event_type == "tool_result" and isinstance(event_data, dict):
                        content = event_data.get("content", "")
                        content_len = len(content) if isinstance(content, str) else 0
                        if tool_calls:
                            tool_calls[-1]["result_chars"] = content_len

                    elif event_type == "message" and isinstance(event_data, dict):
                        text_length += len(event_data.get("content", ""))

                    elif event_type == "context_compacted":
                        compaction_happened = True

                    elif event_type == "done":
                        break

    except httpx.ReadTimeout:
        pass

    # Extract final usage
    final_usage = {}
    if usage_events:
        last = usage_events[-1]
        cumulative = last.get("cumulative_usage", {})
        step = last.get("step_usage", {})
        final_usage = {
            "cumulative_total": cumulative.get("total", 0),
            "cumulative_input": cumulative.get("input", 0),
            "cumulative_output": cumulative.get("output", 0),
            "step_total": step.get("total", 0),
            "step_input": step.get("input", 0),
            "step_output": step.get("output", 0),
        }

    return {
        "usage": final_usage,
        "usage_events_count": len(usage_events),
        "tool_calls": tool_calls,
        "assistant_text_chars": text_length,
        "compaction_happened": compaction_happened,
    }


# ─────────────────────────────────────────────────────────────
# Test Runner
# ─────────────────────────────────────────────────────────────

async def run_test(label: str):
    """Run all scenarios and record results."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    results = {
        "label": label,
        "timestamp": datetime.now().isoformat(),
        "base_url": BASE_URL,
        "scenarios": [],
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Create a session
        print(f"📦 Creating test session (label: {label})...")
        resp = await client.post(
            f"{BASE_URL}/sessions",
            json={
                "name": f"context_growth_test_{label}",
                "workspace_path": str(Path(__file__).parent.parent),
            },
        )
        if resp.status_code != 201:
            print(f"❌ Failed to create session: {resp.status_code} {resp.text}")
            return
        session = resp.json()
        session_id = session["id"]
        print(f"✅ Session: {session_id}")

        # 2. Run each scenario
        cumulative_input_tokens = 0
        for i, scenario in enumerate(SCENARIOS):
            print(f"\n{'='*60}")
            print(f"[{i+1}/{len(SCENARIOS)}] {scenario['name']}: {scenario['description']}")
            print(f"  → {scenario['message'][:80]}...")

            t0 = time.time()
            result = await send_chat_and_collect_usage(
                client, session_id, scenario["message"],
            )
            elapsed = time.time() - t0

            usage = result["usage"]
            cum_total = usage.get("cumulative_total", 0)
            step_input = usage.get("step_input", 0)

            # Track growth
            context_growth = step_input - cumulative_input_tokens if cumulative_input_tokens else step_input
            cumulative_input_tokens = cum_total

            scenario_result = {
                "scenario": scenario["name"],
                "description": scenario["description"],
                "elapsed_seconds": round(elapsed, 1),
                **result,
                "context_growth_tokens": context_growth,
            }
            results["scenarios"].append(scenario_result)

            # Print summary
            tool_summary = ", ".join(
                f"{tc['tool']}({tc.get('result_chars', '?')} chars)"
                for tc in result["tool_calls"]
            ) or "(no tools)"

            print(f"  ⏱ {elapsed:.1f}s | Tools: {tool_summary}")
            print(f"  📊 Step input: {step_input:,} | Cumulative: {cum_total:,}")
            print(f"  📈 Context growth: +{context_growth:,} tokens")
            if result["compaction_happened"]:
                print(f"  ♻️  COMPACTION triggered!")

        # 3. Print final summary
        print(f"\n{'='*60}")
        print(f"📊 FINAL SUMMARY ({label})")
        print(f"{'='*60}")

        final_total = results["scenarios"][-1]["usage"].get("cumulative_total", 0)
        print(f"  Total cumulative tokens: {final_total:,}")
        print(f"  Scenarios run: {len(results['scenarios'])}")

        # Token growth per step
        print(f"\n  Step-by-step growth:")
        for s in results["scenarios"]:
            growth = s.get("context_growth_tokens", 0)
            bar = "█" * min(50, growth // 1000) if growth > 0 else "·"
            compact = " ♻️" if s.get("compaction_happened") else ""
            print(f"    {s['scenario']:<25} +{growth:>8,} tokens {bar}{compact}")

        # 4. Save results
        output_file = RESULTS_DIR / f"context_growth_{label}.json"
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n📁 Results saved to: {output_file}")

        # 5. Cleanup: delete session
        await client.delete(f"{BASE_URL}/sessions/{session_id}")
        print(f"🗑️  Session {session_id} deleted")


# ─────────────────────────────────────────────────────────────
# Compare Mode
# ─────────────────────────────────────────────────────────────

def compare_results(label_a: str, label_b: str):
    """Compare two test results side by side."""
    file_a = RESULTS_DIR / f"context_growth_{label_a}.json"
    file_b = RESULTS_DIR / f"context_growth_{label_b}.json"

    if not file_a.exists():
        print(f"❌ Results not found: {file_a}")
        return
    if not file_b.exists():
        print(f"❌ Results not found: {file_b}")
        return

    with open(file_a) as f:
        data_a = json.load(f)
    with open(file_b) as f:
        data_b = json.load(f)

    print(f"\n{'='*80}")
    print(f"📊 COMPARISON: {label_a} vs {label_b}")
    print(f"{'='*80}")
    print(f"{'Scenario':<25} {'':>4} {label_a:>12} {label_b:>12} {'Δ':>10} {'%':>8}")
    print(f"{'-'*25} {'':->4} {'-'*12:>12} {'-'*12:>12} {'-'*10:>10} {'-'*8:>8}")

    scenarios_a = {s["scenario"]: s for s in data_a["scenarios"]}
    scenarios_b = {s["scenario"]: s for s in data_b["scenarios"]}

    total_a = 0
    total_b = 0

    for name in scenarios_a:
        sa = scenarios_a.get(name, {})
        sb = scenarios_b.get(name, {})

        growth_a = sa.get("context_growth_tokens", 0)
        growth_b = sb.get("context_growth_tokens", 0)
        total_a += growth_a
        total_b += growth_b

        delta = growth_b - growth_a
        pct = f"{(delta / growth_a * 100):+.0f}%" if growth_a > 0 else "N/A"
        emoji = "✅" if delta < 0 else "⚠️" if delta > 0 else "➖"

        print(f"{name:<25} {emoji:>4} {growth_a:>12,} {growth_b:>12,} {delta:>+10,} {pct:>8}")

    print(f"{'-'*25} {'':->4} {'-'*12:>12} {'-'*12:>12} {'-'*10:>10} {'-'*8:>8}")
    total_delta = total_b - total_a
    total_pct = f"{(total_delta / total_a * 100):+.0f}%" if total_a > 0 else "N/A"
    emoji = "✅" if total_delta < 0 else "⚠️"
    print(f"{'TOTAL':<25} {emoji:>4} {total_a:>12,} {total_b:>12,} {total_delta:>+10,} {total_pct:>8}")

    if total_delta < 0:
        print(f"\n🎉 Optimization saved {abs(total_delta):,} tokens ({abs(total_delta/total_a*100):.0f}% reduction)")
    elif total_delta > 0:
        print(f"\n⚠️  Regression: {total_delta:,} more tokens ({total_delta/total_a*100:.0f}% increase)")
    else:
        print(f"\n➖ No change in total context growth")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Context Growth Monitoring Test")
    parser.add_argument("--label", type=str, help="Label for this test run (e.g., 'baseline', 'optimized')")
    parser.add_argument("--compare", nargs=2, metavar=("LABEL_A", "LABEL_B"), help="Compare two runs")
    parser.add_argument("--url", type=str, help="Nimbus API URL (default: http://localhost:3456/api)")
    args = parser.parse_args()

    if args.url:
        global BASE_URL
        BASE_URL = args.url

    if args.compare:
        compare_results(args.compare[0], args.compare[1])
    elif args.label:
        asyncio.run(run_test(args.label))
    else:
        parser.print_help()
        print("\nExamples:")
        print("  python tests/test_context_growth.py --label baseline")
        print("  python tests/test_context_growth.py --label optimized")
        print("  python tests/test_context_growth.py --compare baseline optimized")


if __name__ == "__main__":
    main()

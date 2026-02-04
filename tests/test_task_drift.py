"""
Task Drift & Infinite Context Stress Test

This script evaluates the Agent's ability to maintain focus ("Task Adherence")
while processing information that exceeds its context window.

Scenario:
    "Summarize VCPU architecture and Agentic Workflow implementation."
    This requires reading `vcpu.py`, `agentos.py`, `scheduler.py`, etc.
    These files are large (~1000-1500 lines), which will force Token Overflows.

Configuration:
    - Context Limit: 4000 tokens (Aggressively small for code reading)
    - Compaction: Enabled
    - Tracing: Enabled

Evaluation:
    Post-run analysis of `.nimbus/traces/` to determine:
    1. How many times did memory compaction occur?
    2. Did the "Global Summary" retain the original goal across compactions?
    3. Did the Agent hallucinate or lose track of files it already read?
"""

import asyncio
import os
import shutil
import json
import time
from pathlib import Path
from typing import List, Dict, Any

from nimbus.agentos import AgentOS, AgentOSConfig, Process
from nimbus.core.runtime.vcpu import VCPUConfig
from nimbus.core.memory.mmu import MMUConfig
from nimbus.adapters.pi_adapter import PiLLMAdapter, PiLLMConfig

# =============================================================================
# Configuration
# =============================================================================

# Aggressive limits to force compaction
MAX_CONTEXT_TOKENS = 8000
PINNED_BUDGET = 1000
COMPRESS_THRESHOLD = 0.8  # Trigger at 6400 tokens

TEST_GOAL = """
I need a technical deep dive into the Nimbus architecture.
Please read the following files in detail:
1. `src/nimbus/core/runtime/vcpu.py`
2. `src/nimbus/agentos.py`
3. `src/nimbus/core/scheduler.py`

After reading them, output a comprehensive report explaining:
1. The VCPU instruction cycle (Think-Act-Observe).
2. How AgentOS orchestrates processes.
3. How the DAG Scheduler manages dependencies.

Focus on the code implementation details.
"""

# =============================================================================
# Trace Analyzer
# =============================================================================

class TraceAnalyzer:
    def __init__(self, session_id: str):
        self.trace_dir = Path(f".nimbus/traces/{session_id}")
        self.steps: List[Dict[str, Any]] = []
        
    def load(self):
        if not self.trace_dir.exists():
            print(f"❌ Trace directory not found: {self.trace_dir}")
            return

        json_files = sorted(self.trace_dir.glob("step_*.json"))
        for f in json_files:
            try:
                with open(f, "r") as fd:
                    self.steps.append(json.load(fd))
            except Exception as e:
                print(f"⚠️ Failed to load {f}: {e}")

    def analyze(self):
        if not self.steps:
            print("⚠️ No traces found to analyze.")
            return

        print(f"\n📊 === TRACE ANALYSIS ({len(self.steps)} steps) ===\n")
        
        # 1. Token Pressure & Compaction
        max_tokens_seen = 0
        compaction_events = 0
        
        for step in self.steps:
            ctx = step.get("context", {})
            total = ctx.get("total_tokens", 0)
            max_tokens_seen = max(max_tokens_seen, total)
            
            # Check for compaction faults or events (heuristic)
            # In VCPU v2, compaction might be visible via faults or drastic token drops
            # We track token drops > 20% as potential compaction events
            pass 

        print(f"🔹 Peak Context Usage: {max_tokens_seen} tokens")
        
        # 2. Goal Retention Analysis
        print("\n🧐 === GOAL RETENTION CHECK ===")
        print("Checking 'Global Summary' across steps to see if the goal persisted...")
        
        for i, step in enumerate(self.steps):
            summary = step.get("context", {}).get("summary_preview", "")
            if not summary:
                continue
                
            # Simple keyword check
            has_vcpu = "VCPU" in summary or "vcpu" in summary
            has_agentos = "AgentOS" in summary or "agentos" in summary
            has_scheduler = "Scheduler" in summary or "scheduler" in summary
            
            status = "✅" if (has_vcpu or has_agentos) else "⚠️ DRIFT?"
            print(f"  Step {i+1}: {status} Summary start: {summary[:60]}...")

        # 3. Action Sequence
        print("\n⚡ === ACTION SEQUENCE ===")
        for step in self.steps:
            actions = step.get("actions", [])
            for act in actions:
                print(f"  [{step['iteration']}] {act['kind']}: {act['name']} {str(act['args'])[:80]}...")

# =============================================================================
# Runner
# =============================================================================

async def run_stress_test():
    # 1. Setup
    print("🚀 Starting Infinite Context Stress Test...")
    print(f"   Context Limit: {MAX_CONTEXT_TOKENS} tokens")
    
    # Clean previous traces
    shutil.rmtree(".nimbus/traces", ignore_errors=True)

    # Configure
    pi_url = os.environ.get("PI_AI_URL", "http://localhost:3031")
    # Use a known working model for the test to ensure stability
    model = os.environ.get("NIMBUS_TEST_MODEL", "anthropic/claude-sonnet-4-20250514")
    
    print(f"   Model: {model}")
    
    llm = PiLLMAdapter(PiLLMConfig(base_url=pi_url, model=model))
    
    config = AgentOSConfig(
        max_processes=1,
        default_timeout=120.0,
        vcpu_config=VCPUConfig(
            max_iterations=20,  # Cap at 20 for test speed
            emit_step_events=True,
            compact_on_limit=True,
            enable_tracing=True
        ),
        mmu_config=MMUConfig(
            max_context_tokens=MAX_CONTEXT_TOKENS,
            pinned_budget=PINNED_BUDGET,
            compress_threshold=COMPRESS_THRESHOLD,
            remove_failed_tool_calls=True
        )
    )

    # 2. Initialize AgentOS
    agent_os = AgentOS(llm_client=llm, config=config)
    
    # 3. Run
    # We use `spawn` manually to get the PID for tracing
    pid = agent_os.spawn(TEST_GOAL, role="architect")
    print(f"   PID: {pid}")
    
    process = agent_os.get_process(pid)
    
    start_time = time.time()
    result = await agent_os._run_process(process)
    duration = time.time() - start_time
    
    print(f"\n🏁 Execution Finished in {duration:.2f}s")
    print(f"   Status: {result.status}")
    if result.fault:
        print(f"   Fault: {result.fault}")
    
    print(f"   Output Preview: {str(result.output)[:200]}...\n")

    # 4. Analyze
    analyzer = TraceAnalyzer(pid)
    analyzer.load()
    analyzer.analyze()

if __name__ == "__main__":
    asyncio.run(run_stress_test())

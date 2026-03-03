import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

from nimbus.core.memory.strategy_schema import StrategyModel

logger = logging.getLogger("nimbus.memory.procedural")

class ProceduralStore:
    """
    Procedural Memory Store (Memory OS LTM - Procedural).
    Stores successful execution strategies (condition -> action) 
    that the agent can reuse for similar tasks in the future.
    """
    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path
        self.store_dir = self.workspace_path / ".nimbus" / "procedural"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.store_file = self.store_dir / "strategies.json"
        
        self.strategies: Dict[str, StrategyModel] = {}
        self._load()

    def _load(self):
        if not self.store_file.exists():
            return
        
        try:
            with open(self.store_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    try:
                        self.strategies[k] = StrategyModel.model_validate(v)
                    except Exception as e:
                        logger.warning(f"Failed to parse strategy {k}: {e}")
        except Exception as e:
            logger.error(f"Error reading procedural store: {e}")

    def _save(self):
        try:
            temp_file = self.store_file.with_suffix(".tmp")
            data = {k: v.model_dump() for k, v in self.strategies.items()}
            
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
            temp_file.replace(self.store_file)
        except Exception as e:
            logger.error(f"Error writing procedural store: {e}")

    def upsert(self, strategy: StrategyModel):
        """Update or insert a strategy."""
        if strategy.id in self.strategies:
            strategy.created_at = self.strategies[strategy.id].created_at
            
        strategy.updated_at = time.time()
        self.strategies[strategy.id] = strategy
        self._save()
        logger.debug(f"Saved Procedural Strategy: {strategy.id}")

    def get(self, strategy_id: str) -> Optional[StrategyModel]:
        """Retrieve a specific strategy and bump usage metrics."""
        if strategy_id in self.strategies:
            strat = self.strategies[strategy_id]
            strat.last_used = time.time()
            strat.use_count += 1
            self._save()
            return strat
        return None

    def search(self, query: str = "") -> List[StrategyModel]:
        """Search across strategy conditions and actions."""
        query = query.lower()
        if not query:
            return list(self.strategies.values())
            
        results = []
        for s in self.strategies.values():
            if query in s.condition.lower() or query in s.action.lower():
                s.last_used = time.time()
                s.use_count += 1
                results.append(s)
                
        if results:
            self._save()
        return results

    def get_top_strategies_summary(self, limit: int = 5) -> str:
        """Returns a string representation of the highest value strategies."""
        if not self.strategies:
            return ""
            
        # Top-K Adaptive Decay logic: sort by usage count and recentness
        sorted_strats = sorted(
            self.strategies.values(),
            key=lambda x: (x.use_count * 1000) + x.last_used,
            reverse=True
        )
        top_k = sorted_strats[:limit]
            
        lines = []
        lines.append("### REUSABLE PROCEDURAL STRATEGIES")
        for s in top_k:
            metrics = f"(used {s.use_count}x, success {s.success_rate*100:.0f}%)"
            lines.append(f"- ID: {s.id} {metrics}")
            lines.append(f"  Condition: {s.condition}")
            lines.append(f"  Action: {s.action}")
            
        return "\n".join(lines).strip()

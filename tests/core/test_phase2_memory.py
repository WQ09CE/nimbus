import pytest
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from nimbus.core.memory.profile_store import ProfileStore
from nimbus.core.memory.profile_schema import ProfileEntityModel
from nimbus.core.memory.procedural_store import ProceduralStore
from nimbus.core.memory.strategy_schema import StrategyModel

def test_profile_adaptive_decay():
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        store = ProfileStore(temp_path)
        
        # Add 15 profiles
        for i in range(15):
            e = ProfileEntityModel(
                key=f"fact_{i}",
                value=f"value_{i}",
                entity_type="preference"
            )
            store.upsert(e)
            
        # Manually bump access for exactly 5 of them to simulate usage
        # We'll retrieve facts 0, 2, 4, 6, 8
        for i in [0, 2, 4, 6, 8]:
            store.get(f"fact_{i}")
            time.sleep(0.01) # ensure distinct timestamps
            
        # Get top 10 summary
        summary = store.get_all_summary(limit=10)
        
        # Facts 0, 2, 4, 6, 8 should definitely be in there because we bumped them
        for i in [0, 2, 4, 6, 8]:
            assert f"fact_{i}" in summary
            
        # The summary should only contain exactly 10 items
        assert summary.count("- fact_") == 10

def test_procedural_store_metrics():
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        store = ProceduralStore(temp_path)
        
        s1 = StrategyModel(condition="If 404", action="Check URL")
        s2 = StrategyModel(condition="If 502", action="Sleep 5s")
        store.upsert(s1)
        store.upsert(s2)
        
        # Usage bumps
        store.get(s1.id)
        store.get(s1.id)
        store.search("502") # bumps s2 once
        
        s1_updated = store.get(s1.id)
        assert s1_updated is not None
        # get() was called 3 times total for s1 so far
        assert s1_updated.use_count == 3
        
        s2_updated = store.strategies[s2.id]
        assert s2_updated.use_count == 1
        
        # Test Top-K strategies
        summary = store.get_top_strategies_summary(limit=1)
        assert s1.id in summary
        assert s2.id not in summary

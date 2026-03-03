from pathlib import Path
from typing import Dict, Any, Tuple, Callable
import logging

from nimbus.core.memory.profile_store import ProfileStore
from nimbus.core.memory.episodic_store import EpisodicStore
from nimbus.core.memory.procedural_store import ProceduralStore
from nimbus.core.memory.profile_schema import ProfileEntityModel
from nimbus.core.memory.strategy_schema import StrategyModel

logger = logging.getLogger("nimbus.tools.memory_ops")

def create_memory_ops_tools(
    profile_store: ProfileStore,
    episodic_store: EpisodicStore,
    procedural_store: ProceduralStore
) -> Tuple[
    Dict[str, Any], Callable, 
    Dict[str, Any], Callable, 
    Dict[str, Any], Callable,
    Dict[str, Any], Callable,
    Dict[str, Any], Callable
]:

    read_profile_def = {
        "name": "ReadProfile",
        "description": "Read the agent's long-term semantic profile (preferences, stack, decisions, context). Can provide a specific key or leave empty to get a summary of all known profiles.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Optional specific profile key to read."
                }
            },
            "required": []
        }
    }

    def read_profile_func(key: str = "") -> str:
        if key:
            entity = profile_store.get(key)
            if entity:
                return f"{entity.key} [{entity.entity_type}]: {entity.value}"
            return f"No profile found for key: {key}"
        return profile_store.get_all_summary() or "Profile is currently empty."


    write_profile_def = {
        "name": "WriteProfile",
        "description": "Write or update a long-term semantic profile entity (preference, stack, context). Use this to remember important facts across sessions.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Key/Name of the profile entity (e.g., 'user_frontend_stack')"
                },
                "value": {
                    "type": "string",
                    "description": "Value/Details to remember."
                },
                "entity_type": {
                    "type": "string",
                    "enum": ["preference", "tech_stack", "project_context", "decision", "other"],
                    "description": "Category of the profile entity."
                }
            },
            "required": ["key", "value", "entity_type"]
        }
    }

    def write_profile_func(key: str, value: str, entity_type: str) -> str:
        try:
            entity = ProfileEntityModel(
                key=key,
                value=value,
                entity_type=entity_type,
                confidence="verified"
            )
            profile_store.upsert(entity)
            return f"Successfully saved profile: {key}"
        except Exception as e:
            logger.error(f"Failed to write profile: {e}")
            return f"Error writing profile: {e}"


    search_episodic_def = {
        "name": "SearchEpisodicLog",
        "description": "Search across all past raw session logs (black-box history) for specific keywords. Use when you need detailed context or error traces from previous sessions.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword or phrase to search for."
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5)."
                }
            },
            "required": ["query"]
        }
    }

    def search_episodic_func(query: str, limit: int = 5) -> str:
        results = episodic_store.search(query, limit)
        if not results:
            return f"No episodic logs found matching '{query}'"
        
        output = [f"Found {len(results)} matches for '{query}':"]
        for idx, res in enumerate(results, 1):
            role = res.get('role', 'unknown')
            ts = res.get('timestamp', 'unknown_time')
            snip = res.get('snippet', '')
            output.append(f"{idx}. [Session: {res['session_id']}] [{ts}] {role.upper()}:\n   {snip}")
        return "\n".join(output)


    read_strategy_def = {
        "name": "ReadStrategy",
        "description": "Read reusable Agent Strategies (Condition -> Action). Provides previous successful approaches. Can provide an ID or leave empty to get top strategies.",
        "parameters": {
            "type": "object",
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "description": "Optional specific strategy ID to read."
                }
            },
            "required": []
        }
    }

    def read_strategy_func(strategy_id: str = "") -> str:
        if strategy_id:
            strat = procedural_store.get(strategy_id)
            if strat:
                return f"Strategy {strat.id} [Used {strat.use_count}x]\nCondition: {strat.condition}\nAction: {strat.action}"
            return f"No strategy found for id: {strategy_id}"
        return procedural_store.get_top_strategies_summary() or "Strategy store is currently empty."


    write_strategy_def = {
        "name": "WriteStrategy",
        "description": "Save a newly discovered procedural strategy (Condition -> Action) for future reuse.",
        "parameters": {
            "type": "object",
            "properties": {
                "condition": {
                    "type": "string",
                    "description": "The situation or error condition (e.g., 'When hitting 502 Bad Gateway on API X')"
                },
                "action": {
                    "type": "string",
                    "description": "The successful sequence of steps or resolutions taken."
                }
            },
            "required": ["condition", "action"]
        }
    }

    def write_strategy_func(condition: str, action: str) -> str:
        try:
            strat = StrategyModel(condition=condition, action=action)
            procedural_store.upsert(strat)
            return f"Successfully saved procedural strategy: {strat.id}"
        except Exception as e:
            logger.error(f"Failed to write strategy: {e}")
            return f"Error writing strategy: {e}"


    return (
        read_profile_def, read_profile_func,
        write_profile_def, write_profile_func,
        search_episodic_def, search_episodic_func,
        read_strategy_def, read_strategy_func,
        write_strategy_def, write_strategy_func
    )

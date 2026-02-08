from typing import Any, Dict, Optional
from .models import SessionResponse, SessionStatus, SessionUpdate

def _format_session_response(session: Dict[str, Any]) -> SessionResponse:
    overrides = session.get("config_overrides")
    agent_mode = "standard"
    model_config = None

    if overrides:
        import json

        if isinstance(overrides, str):
            try:
                overrides = json.loads(overrides)
            except json.JSONDecodeError:
                overrides = {}
        
        if isinstance(overrides, dict):
            agent_mode = overrides.get("agent_mode", "standard")
            model_config = overrides.get("model_config")

    return SessionResponse(
        id=session["id"],
        name=session.get("name"),
        created_at=session["created_at"],
        status=SessionStatus(session["status"]),
        memory_type=session["memory_type"],
        planner_type=session["planner_type"],
        workspace_path=session.get("workspace_path"),
        message_count=session.get("message_count", 0),
        agent_mode=agent_mode,
        llm_config=model_config,
    )

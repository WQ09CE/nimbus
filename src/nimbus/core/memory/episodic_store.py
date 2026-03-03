import json
import logging
from pathlib import Path
from typing import Dict, List, Any

logger = logging.getLogger("nimbus.memory.episodic")

class EpisodicStore:
    """
    Episodic Memory Store (Memory OS LTM - Episodic).
    Provides search capabilities over past JSONL session histories.
    """
    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path
        self.session_dir = self.workspace_path / ".nimbus" / "sessions"

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Naive text search across recent sessions.
        Returns snippets and context of matching messages.
        """
        if not self.session_dir.exists():
            return []

        query = query.lower()
        results = []
        
        # Search backwards through sessions to get most recent matches first
        try:
            date_dirs = sorted(
                (d for d in self.session_dir.iterdir() if d.is_dir()),
                reverse=True
            )
        except Exception as e:
            logger.error(f"Error reading session directories: {e}")
            return []

        for date_dir in date_dirs:
            try:
                session_files = sorted(date_dir.glob("*.jsonl"), reverse=True)
                for session_file in session_files:
                    session_id = session_file.stem
                    
                    try:
                        with open(session_file, "r", encoding="utf-8") as f:
                            for line in f:
                                try:
                                    entry = json.loads(line.strip())
                                    content = None
                                    
                                    if entry.get("type") in ("user", "assistant", "system"):
                                        content = entry.get("data", {}).get("content", "")
                                    elif entry.get("type") == "tool":
                                        content = entry.get("data", {}).get("content", "")
                                        
                                    if content and isinstance(content, str) and query in content.lower():
                                        # Extract snippet
                                        idx = content.lower().find(query)
                                        start = max(0, idx - 40)
                                        end = min(len(content), idx + len(query) + 40)
                                        snippet = "..." + content[start:end].replace("\n", " ") + "..."
                                        
                                        results.append({
                                            "session_id": session_id,
                                            "role": entry.get("type"),
                                            "timestamp": entry.get("timestamp"),
                                            "snippet": snippet,
                                            "full_content": content
                                        })
                                        
                                        if len(results) >= limit:
                                            return results
                                except Exception:
                                    continue
                    except Exception as e:
                        logger.warning(f"Error reading session file {session_file}: {e}")
            except Exception as e:
                logger.warning(f"Error exploring date directory {date_dir}: {e}")

        return results

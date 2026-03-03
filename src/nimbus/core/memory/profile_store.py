import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

from nimbus.core.memory.profile_schema import ProfileEntityModel

logger = logging.getLogger("nimbus.memory.profile")

class ProfileStore:
    """
    Semantic Profile Store (Memory OS LTM - Semantic).
    Stores structured entities (key-value) that formulate the Agent's
    long-term understanding of preferences, stack, and context.
    """
    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path
        self.profile_dir = self.workspace_path / ".nimbus" / "semantic_profile"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.store_file = self.profile_dir / "profile.json"
        
        self.entities: Dict[str, ProfileEntityModel] = {}
        self._load()

    def _load(self):
        if not self.store_file.exists():
            return
        
        try:
            with open(self.store_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    try:
                        self.entities[k] = ProfileEntityModel.model_validate(v)
                    except Exception as e:
                        logger.warning(f"Failed to parse profile entity {k}: {e}")
        except Exception as e:
            logger.error(f"Error reading profile store: {e}")

    def _save(self):
        try:
            temp_file = self.store_file.with_suffix(".tmp")
            data = {k: v.model_dump() for k, v in self.entities.items()}
            
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
            temp_file.replace(self.store_file)
        except Exception as e:
            logger.error(f"Error writing profile store: {e}")

    def upsert(self, entity: ProfileEntityModel):
        """Update or insert an entity into the profile store."""
        if entity.key in self.entities:
            # Preserve creation time
            entity.created_at = self.entities[entity.key].created_at
            
        self.entities[entity.key] = entity
        self._save()
        logger.debug(f"Saved Semantic Profile: {entity.key}")

    def get(self, key: str) -> Optional[ProfileEntityModel]:
        """Retrieve a specific entity. Bumps access metrics."""
        if key in self.entities:
            entity = self.entities[key]
            entity.last_accessed = time.time()
            entity.access_count += 1
            self._save()
            return entity
        return None
        
    def search(self, query: str = "") -> List[ProfileEntityModel]:
        """Simple substring search across keys and values. Bumps access metrics."""
        query = query.lower()
        if not query:
            return list(self.entities.values())
            
        results = []
        for e in self.entities.values():
            if query in e.key.lower() or query in e.value.lower():
                e.last_accessed = time.time()
                e.access_count += 1
                results.append(e)
        
        if results:
            self._save()
        return results

    def get_all_summary(self, limit: int = 10) -> str:
        """Returns a string representation of top profiles suited for LLM context."""
        if not self.entities:
            return ""
            
        # Top-K Adaptive Decay logic: only return the most recently/frequently accessed entities
        # to avoid polluting the State/Memo context anchor.
        sorted_entities = sorted(
            self.entities.values(),
            key=lambda x: x.last_accessed + (x.access_count * 3600), # Weights frequency slightly
            reverse=True
        )
        top_k = sorted_entities[:limit]
            
        lines = []
        # Group by entity_type for better readability
        grouped: Dict[str, List[ProfileEntityModel]] = {}
        for e in top_k:
            grouped.setdefault(e.entity_type, []).append(e)
            
        for etype, items in grouped.items():
            lines.append(f"### {etype.upper()}")
            for idx, item in enumerate(items, 1):
                conf_str = f" ({item.confidence})" if item.confidence != "verified" else ""
                lines.append(f"- {item.key}: {item.value}{conf_str}")
            lines.append("")
            
        return "\n".join(lines).strip()

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid
import logging
from typing import Optional, Dict, Any, List, TYPE_CHECKING

from nimbus.core.heart import HeartModule, HeartMessage, MessagePriority

if TYPE_CHECKING:
    from nimbus.core.heart import Heart

logger = logging.getLogger("nimbus.heart.evolution")

class EvolutionState(enum.Enum):
    CANDIDATE = "candidate"
    REPLAY = "replay"
    APPROVAL = "approval"
    ROLLOUT = "rollout"
    IMPLEMENTED = "implemented"
    REJECTED = "rejected"

@dataclass
class EvolutionProposal:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    description: str = ""
    state: EvolutionState = EvolutionState.CANDIDATE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data: Dict[str, Any] = field(default_factory=dict)
    feedback: List[str] = field(default_factory=list)

    def transition_to(self, new_state: EvolutionState):
        self.state = new_state
        self.updated_at = datetime.now(timezone.utc)

class EvolutionManagerModule(HeartModule):
    def __init__(self):
        self.proposals: Dict[str, EvolutionProposal] = {}
    
    async def run_cron(self, heart: "Heart"):
        pass

    async def handle_message(self, heart: "Heart", msg: HeartMessage):
        if msg.topic == "evolution.propose":
            # Handle both dictionary and object (EvolutionProposal) payloads
            payload = msg.payload
            if isinstance(payload, EvolutionProposal):
                self.proposals[payload.id] = payload
                logger.info(f"Received proposal object: {payload.id}")
            elif isinstance(payload, dict):
                await self.generate_candidate(payload)
            else:
                logger.error(f"Received unknown evolution.propose payload type: {type(payload)}")
        elif msg.topic == "evolution.replay":
            proposal_id = msg.payload.get("proposal_id")
            if proposal_id in self.proposals:
                await self.evaluate_replay(self.proposals[proposal_id])
        elif msg.topic == "evolution.approve":
            proposal_id = msg.payload.get("proposal_id")
            if proposal_id in self.proposals:
                await self.request_approval(self.proposals[proposal_id])
        elif msg.topic == "evolution.rollout":
            proposal_id = msg.payload.get("proposal_id")
            if proposal_id in self.proposals:
                await self.execute_rollout(self.proposals[proposal_id])

    async def generate_candidate(self, data: Dict[str, Any]) -> EvolutionProposal:
        proposal = EvolutionProposal(
            title=data.get("title", "Untitled Proposal"),
            description=data.get("description", ""),
            data=data
        )
        self.proposals[proposal.id] = proposal
        logger.info(f"Generated candidate proposal: {proposal.id}")
        return proposal

    async def evaluate_replay(self, proposal: EvolutionProposal) -> bool:
        if proposal.state != EvolutionState.CANDIDATE:
            return False
        
        # Stub logic
        proposal.transition_to(EvolutionState.REPLAY)
        logger.info(f"Proposal {proposal.id} entering replay evaluation.")
        
        # Simulate evaluation success
        success = True
        if success:
            proposal.transition_to(EvolutionState.APPROVAL)
        else:
            proposal.transition_to(EvolutionState.REJECTED)
            
        return success

    async def request_approval(self, proposal: EvolutionProposal) -> bool:
        if proposal.state != EvolutionState.APPROVAL:
            return False
            
        logger.info(f"Proposal {proposal.id} requesting approval.")
        
        # Simulate approval success
        approved = True
        if approved:
            proposal.transition_to(EvolutionState.ROLLOUT)
        else:
            proposal.transition_to(EvolutionState.REJECTED)
            
        return approved

    async def execute_rollout(self, proposal: EvolutionProposal) -> bool:
        if proposal.state != EvolutionState.ROLLOUT:
            return False
            
        logger.info(f"Proposal {proposal.id} executing rollout.")
        
        # Simulate rollout success
        success = True
        if success:
            proposal.transition_to(EvolutionState.IMPLEMENTED)
        else:
            proposal.transition_to(EvolutionState.REJECTED)
            
        return success

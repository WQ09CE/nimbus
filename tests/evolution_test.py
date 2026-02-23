import pytest
import asyncio
from nimbus.core.heart_modules.evolution import EvolutionState, EvolutionProposal, EvolutionManagerModule
from nimbus.core.heart import HeartMessage, MessagePriority

@pytest.mark.asyncio
async def test_evolution_proposal_state_transitions():
    manager = EvolutionManagerModule()
    
    # Generate candidate
    proposal = await manager.generate_candidate({
        "title": "Test Evolution",
        "description": "Improve logging"
    })
    
    assert proposal.id in manager.proposals
    assert proposal.title == "Test Evolution"
    assert proposal.state == EvolutionState.CANDIDATE
    
    # Replay
    success = await manager.evaluate_replay(proposal)
    assert success is True
    assert proposal.state == EvolutionState.APPROVAL
    
    # Approval
    approved = await manager.request_approval(proposal)
    assert approved is True
    assert proposal.state == EvolutionState.ROLLOUT
    
    # Rollout
    success = await manager.execute_rollout(proposal)
    assert success is True
    assert proposal.state == EvolutionState.IMPLEMENTED

@pytest.mark.asyncio
async def test_evolution_manager_messages():
    manager = EvolutionManagerModule()
    
    # Create proposal via message
    msg = HeartMessage(id="msg_1", topic="evolution.propose", payload={"title": "Message Proposal"}, priority=MessagePriority.NORMAL)
    # Fake heart for message handling
    class FakeHeart:
        pass
    
    heart = FakeHeart()
    
    await manager.handle_message(heart, msg)
    assert len(manager.proposals) == 1
    proposal_id = list(manager.proposals.keys())[0]
    proposal = manager.proposals[proposal_id]
    
    assert proposal.state == EvolutionState.CANDIDATE
    
    # Test invalid transitions
    # Cannot rollout if in CANDIDATE state
    success = await manager.execute_rollout(proposal)
    assert success is False
    assert proposal.state == EvolutionState.CANDIDATE
    
    # Replay message
    msg_replay = HeartMessage(id="msg_2", topic="evolution.replay", payload={"proposal_id": proposal_id}, priority=MessagePriority.NORMAL)
    await manager.handle_message(heart, msg_replay)
    assert proposal.state == EvolutionState.APPROVAL
    
    # Approve message
    msg_approve = HeartMessage(id="msg_3", topic="evolution.approve", payload={"proposal_id": proposal_id}, priority=MessagePriority.NORMAL)
    await manager.handle_message(heart, msg_approve)
    assert proposal.state == EvolutionState.ROLLOUT
    
    # Rollout message
    msg_rollout = HeartMessage(id="msg_4", topic="evolution.rollout", payload={"proposal_id": proposal_id}, priority=MessagePriority.NORMAL)
    await manager.handle_message(heart, msg_rollout)
    assert proposal.state == EvolutionState.IMPLEMENTED

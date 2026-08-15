import pytest
import os
import json
import asyncio
from unittest.mock import MagicMock
from nimbus.core.tools.submit_result import submit_result_impl
from nimbus.core.protocol_contract import SubAgentStatus, SubAgentResult
from nimbus.core.tools.spawn_agent import _collect_partial

def test_submit_result_contract_format(tmp_path):
    deliverable_path = str(tmp_path / "deliverable.json")
    scratchpad_path = str(tmp_path / "scratchpad.md")
    
    result = submit_result_impl(
        summary="Test summary",
        key_findings=["Finding 1", "Finding 2"],
        artifacts=[{"path": "file.txt", "description": "desc"}],
        files_touched=["file.txt"],
        todos_completed=["task 1"],
        todos_remaining=[],
        errors=[],
        deliverable_path=deliverable_path,
        scratchpad_path=scratchpad_path
    )
    
    assert result["status"] == "success"
    assert result["summary"] == "Test summary"
    assert len(result["key_findings"]) == 2
    assert result["artifacts"][0]["path"] == "file.txt"
    
    with open(deliverable_path, "r") as f:
        data = json.load(f)
        assert data["status"] == "success"
        assert data["summary"] == "Test summary"

def test_collect_partial_timeout(tmp_path):
    scratchpad_path = tmp_path / "scratchpad.md"
    scratchpad_path.write_text("Finding: The world is round.\n- [x] Task 1")
    
    loop_mock = MagicMock()
    loop_mock.partial_results = []
    
    result = _collect_partial(loop_mock, str(scratchpad_path), status="timeout")
    
    assert result.status == SubAgentStatus.TIMEOUT
    assert "timeout" in result.summary
    assert any("The world is round" in f for f in result.key_findings)

@pytest.mark.asyncio
async def test_to_parent_text():
    result = SubAgentResult(
        status=SubAgentStatus.SUCCESS,
        summary="Completed the job.",
        key_findings=["Finding A"],
        files_touched=["a.py"]
    )
    text = result.to_parent_text()
    assert "SUCCESS" in text
    assert "Completed the job." in text
    assert "Finding A" in text
    assert "a.py" in text

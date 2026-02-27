import pytest
from nimbus.tools.nimfs_tools import nimfs_read_artifact, nimfs_write_artifact
from nimbus.core.nimfs import NimFSManager

@pytest.fixture
def nimfs_ctx(tmp_path):
    manager = NimFSManager(str(tmp_path))
    return {"nimfs_manager": manager}

@pytest.mark.asyncio
async def test_nimfs_read_artifact_pagination(nimfs_ctx):
    content = "\n".join([f"Line {i}" for i in range(1, 101)]) # 100 lines
    res_write = await nimfs_write_artifact(content, task_id="test", **nimfs_ctx)
    # Extract reference more robustly
    import re
    match = re.search(r"nimfs://artifact/[\w-]+", res_write)
    assert match, f"Could not find reference in: {res_write}"
    ref = match.group(0)
    
    # Test default read (should be all 100 lines since limit is 2000)
    res = await nimfs_read_artifact(ref, **nimfs_ctx)
    assert "Line 1" in res
    assert "Line 100" in res
    assert "Artifact has more lines" not in res
    
    # Test small limit
    res = await nimfs_read_artifact(ref, limit=10, **nimfs_ctx)
    assert "Line 1" in res
    assert "Line 10" in res
    assert "Line 11" not in res
    assert "Use offset=11 to read next chunk" in res
    
    # Test offset
    res = await nimfs_read_artifact(ref, offset=11, limit=10, **nimfs_ctx)
    assert "Line 11" in res
    assert "Line 20" in res
    assert "Line 10" not in res
    assert "Use offset=21 to read next chunk" in res

@pytest.mark.asyncio
async def test_nimfs_read_artifact_grep(nimfs_ctx):
    content = "Apple\nBanana\nCherry\nApple Pie\nDate"
    res_write = await nimfs_write_artifact(content, task_id="test", **nimfs_ctx)
    import re
    match = re.search(r"nimfs://artifact/[\w-]+", res_write)
    assert match
    ref = match.group(0)
    
    # Test grep
    res = await nimfs_read_artifact(ref, grep_pattern="Apple", **nimfs_ctx)
    assert "1: Apple" in res
    assert "4: Apple Pie" in res
    assert "Banana" not in res
    
    # Test grep no match
    res = await nimfs_read_artifact(ref, grep_pattern="Zebra", **nimfs_ctx)
    assert "No lines matching pattern 'Zebra' found" in res

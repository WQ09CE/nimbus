from pathlib import Path

agentos_path = Path("src/nimbus/agentos.py")
content = agentos_path.read_text()

# We need to insert imports at the top
import_str = """
from nimbus.core.process.manager import ProcessManager
from nimbus.core.session.coordinator import SessionCoordinator
from nimbus.core.nimfs.gc import NimFSGC
"""

# We'll just append them after other imports. Find `from nimbus.core.ipc.mailbox import Mailbox`
content = content.replace("from nimbus.core.ipc.mailbox import Mailbox", "from nimbus.core.ipc.mailbox import Mailbox" + import_str)

# Add to __init__:
init_insertion = """
        # --- Managers ---
        self.process_manager = ProcessManager(self)
        self.session_coordinator = SessionCoordinator(self)
        self.nimfs_gc = NimFSGC(self)
"""

# The end of __init__ is at `self._intervention_task: Optional[asyncio.Task] = None`
content = content.replace("self._intervention_task: Optional[asyncio.Task] = None", "self._intervention_task: Optional[asyncio.Task] = None" + init_insertion)

# Replace everything from `def spawn(` down. We find `    def spawn(`
parts = content.split("    def spawn(")
head = parts[0]

facade_code = """    # =========================================================================
    # Process Facade
    # =========================================================================

    def spawn(self, *args, **kwargs):
        return self.process_manager.spawn(*args, **kwargs)

    async def wait(self, *args, **kwargs):
        return await self.process_manager.wait(*args, **kwargs)

    async def wait_all(self, *args, **kwargs):
        return await self.process_manager.wait_all(*args, **kwargs)

    async def run(self, *args, **kwargs):
        return await self.process_manager.run(*args, **kwargs)

    def run_stream(self, *args, **kwargs):
        return self.process_manager.run_stream(*args, **kwargs)

    def terminate(self, *args, **kwargs):
        return self.process_manager.terminate(*args, **kwargs)

    def list_processes(self, *args, **kwargs):
        return self.process_manager.list_processes(*args, **kwargs)

    def get_active_processes(self, *args, **kwargs):
        return self.process_manager.get_active_processes(*args, **kwargs)

    def get_process(self, *args, **kwargs):
        return self.process_manager.get_process(*args, **kwargs)

    def interrupt(self, *args, **kwargs):
        return self.process_manager.interrupt(*args, **kwargs)

    def inject_message(self, *args, **kwargs):
        return self.process_manager.inject_message(*args, **kwargs)

    def _drain_process_inbox(self, *args, **kwargs):
        return self.process_manager._drain_process_inbox(*args, **kwargs)

    async def _run_process(self, *args, **kwargs):
        return await self.process_manager._run_process(*args, **kwargs)

    def _scavenge_partial_result(self, *args, **kwargs):
        return self.process_manager._scavenge_partial_result(*args, **kwargs)

    async def spawn_batch(self, *args, **kwargs):
        return await self.process_manager.spawn_batch(*args, **kwargs)

    # =========================================================================
    # Session Facade
    # =========================================================================

    async def chat(self, *args, **kwargs):
        return await self.session_coordinator.chat(*args, **kwargs)

    def new_session(self, *args, **kwargs):
        return self.session_coordinator.new_session(*args, **kwargs)

    def load_session(self, *args, **kwargs):
        return self.session_coordinator.load_session(*args, **kwargs)

    def restore_session(self, *args, **kwargs):
        return self.session_coordinator.restore_session(*args, **kwargs)

    def get_session_stats(self, *args, **kwargs):
        return self.session_coordinator.get_session_stats(*args, **kwargs)

    def list_recent_sessions(self, *args, **kwargs):
        return self.session_coordinator.list_recent_sessions(*args, **kwargs)

    def get_session(self, *args, **kwargs):
        return self.session_coordinator.get_session(*args, **kwargs)

    def end_session(self, *args, **kwargs):
        return self.session_coordinator.end_session(*args, **kwargs)

    # =========================================================================
    # Other internal proxies
    # =========================================================================
    
    async def compact(self, *args, **kwargs):
        return await self._compaction_service.compact(*args, **kwargs)

    async def _check_compaction(self, *args, **kwargs):
        return await self._compaction_service.check_compaction(*args, **kwargs)

    async def _compaction_for_process(self, *args, **kwargs):
        return await self._compaction_service.compact_process(*args, **kwargs)

    def _nimfs_gc_task(self, *args, **kwargs):
        return self.nimfs_gc._nimfs_gc_task(*args, **kwargs)

    def _nimfs_gc_session(self, *args, **kwargs):
        return self.nimfs_gc._nimfs_gc_session(*args, **kwargs)
"""

agentos_path.write_text(head + facade_code)
print("Applied facade pattern to agentos.py")

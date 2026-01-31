"""
vCPU Pool Manager for Agent OS.

Architecture Layer: 1 (Agent OS - Kernel)
Von Neumann Role: vCPU Pool (Resource Manager)

This module provides a pool of vCPUs, allowing processes to be
bound to specific vCPUs for specialized execution (e.g., different
LLM providers for planning vs. execution).

Example:
    >>> from nimbus.kernel.vcpu_pool import vCPUPool
    >>> from nimbus.kernel.vcpu import vCPU
    >>>
    >>> pool = vCPUPool()
    >>> pool.register("planner", planner_vcpu, is_default=True)
    >>> pool.register("executor", executor_vcpu)
    >>>
    >>> # Get vCPU by ID
    >>> vcpu = pool.get("executor")
    >>>
    >>> # Get vCPU for a process based on affinity
    >>> vcpu = pool.get_for_process(process)
"""

__layer__ = 1
__role__ = "vCPU_Pool"

import logging
from typing import Dict, Iterator, Optional

from .proc import AgentProcess
from .vcpu import vCPU

logger = logging.getLogger(__name__)


class vCPUPool:
    """vCPU pool manager.

    Manages multiple vCPU instances and provides routing based on
    process affinity settings.

    Attributes:
        _vcpus: Dictionary mapping vcpu_id to vCPU instance
        _default_vcpu_id: ID of the default vCPU
    """

    def __init__(self) -> None:
        """Initialize an empty vCPU pool."""
        self._vcpus: Dict[str, vCPU] = {}
        self._default_vcpu_id: Optional[str] = None

    def register(
        self,
        vcpu_id: str,
        vcpu: vCPU,
        is_default: bool = False,
    ) -> None:
        """Register a vCPU in the pool.

        Args:
            vcpu_id: Unique identifier for the vCPU
            vcpu: The vCPU instance to register
            is_default: If True, set as the default vCPU

        Raises:
            ValueError: If vcpu_id is already registered
        """
        if vcpu_id in self._vcpus:
            raise ValueError(f"vCPU '{vcpu_id}' is already registered")

        self._vcpus[vcpu_id] = vcpu
        logger.debug(f"Registered vCPU: {vcpu_id} (is_default={is_default})")

        # Set as default if requested or if it's the first vCPU
        if is_default or self._default_vcpu_id is None:
            self._default_vcpu_id = vcpu_id
            logger.debug(f"Default vCPU set to: {vcpu_id}")

    def unregister(self, vcpu_id: str) -> Optional[vCPU]:
        """Unregister a vCPU from the pool.

        Args:
            vcpu_id: ID of the vCPU to unregister

        Returns:
            The unregistered vCPU instance, or None if not found
        """
        vcpu = self._vcpus.pop(vcpu_id, None)
        if vcpu is not None:
            logger.debug(f"Unregistered vCPU: {vcpu_id}")
            # Clear default if this was the default
            if self._default_vcpu_id == vcpu_id:
                self._default_vcpu_id = next(iter(self._vcpus), None)
                if self._default_vcpu_id:
                    logger.debug(f"Default vCPU changed to: {self._default_vcpu_id}")
        return vcpu

    def get(self, vcpu_id: str) -> Optional[vCPU]:
        """Get a vCPU by ID.

        Args:
            vcpu_id: ID of the vCPU to retrieve

        Returns:
            The vCPU instance, or None if not found
        """
        return self._vcpus.get(vcpu_id)

    def get_default(self) -> Optional[vCPU]:
        """Get the default vCPU.

        Returns:
            The default vCPU instance, or None if pool is empty
        """
        if self._default_vcpu_id is None:
            return None
        return self._vcpus.get(self._default_vcpu_id)

    def get_for_process(self, process: AgentProcess) -> Optional[vCPU]:
        """Get the appropriate vCPU for a process.

        Routing logic:
        1. If process has vcpu_affinity set, use that vCPU
        2. Otherwise, use the default vCPU

        Args:
            process: The process to get vCPU for

        Returns:
            The appropriate vCPU instance, or None if not available
        """
        # Check if process has affinity set
        if process.vcpu_affinity is not None:
            vcpu = self._vcpus.get(process.vcpu_affinity)
            if vcpu is not None:
                logger.debug(
                    f"Process {process.pid} using affinity vCPU: {process.vcpu_affinity}"
                )
                return vcpu
            else:
                logger.warning(
                    f"Process {process.pid} has affinity for unknown vCPU "
                    f"'{process.vcpu_affinity}', falling back to default"
                )

        # Fall back to default
        return self.get_default()

    def set_default(self, vcpu_id: str) -> bool:
        """Set the default vCPU.

        Args:
            vcpu_id: ID of the vCPU to set as default

        Returns:
            True if successful, False if vcpu_id not found
        """
        if vcpu_id not in self._vcpus:
            return False
        self._default_vcpu_id = vcpu_id
        logger.debug(f"Default vCPU set to: {vcpu_id}")
        return True

    @property
    def default_id(self) -> Optional[str]:
        """Get the ID of the default vCPU."""
        return self._default_vcpu_id

    def list_vcpus(self) -> list[str]:
        """List all registered vCPU IDs.

        Returns:
            List of vCPU IDs
        """
        return list(self._vcpus.keys())

    def __len__(self) -> int:
        """Return the number of registered vCPUs."""
        return len(self._vcpus)

    def __iter__(self) -> Iterator[str]:
        """Iterate over vCPU IDs."""
        return iter(self._vcpus)

    def __contains__(self, vcpu_id: str) -> bool:
        """Check if a vCPU ID is registered."""
        return vcpu_id in self._vcpus

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"vCPUPool(count={len(self._vcpus)}, "
            f"default={self._default_vcpu_id!r}, "
            f"vcpus={list(self._vcpus.keys())})"
        )

from __future__ import annotations

import asyncio
import logging
try:
    import psutil
except ImportError:
    psutil = None
from typing import TYPE_CHECKING
from nimbus.core.heart import HeartModule, HeartMessage, MessagePriority

if TYPE_CHECKING:
    from nimbus.core.heart import Heart

logger = logging.getLogger("nimbus.heart.health")

class HealthMonitorModule(HeartModule):
    """
    Heart module for monitoring system health:
    - CPU/Memory usage
    - Process status
    - Resource alerts
    """
    def __init__(self, check_interval_ticks: int = 5):
        self.check_interval_ticks = check_interval_ticks
        self.ticks_count = 0

    async def run_cron(self, heart: Heart):
        self.ticks_count += 1
        if self.ticks_count % self.check_interval_ticks == 0:
            await self._check_health(heart)

    async def _check_health(self, heart: Heart):
        # 1. Check System Resources
        if psutil:
            cpu_usage = psutil.cpu_percent()
            mem = psutil.virtual_memory()
            mem_percent = mem.percent
        else:
            cpu_usage = 50.0
            mem_percent = 50.0
        
        health_data = {
            "cpu_percent": cpu_usage,
            "memory_percent": mem_percent,
            "timestamp": heart.last_cron_run
        }
        
        logger.debug(f"Health Check: CPU {cpu_usage}%, Mem {mem_percent}%")
        
        # 2. Check for alerts
        if cpu_usage > 90.0:
            await heart.inbox.put("health.alert", {"type": "CPU_HIGH", "value": cpu_usage}, MessagePriority.HIGH)
        
        if mem_percent > 90.0:
            await heart.inbox.put("health.alert", {"type": "MEM_HIGH", "value": mem_percent}, MessagePriority.HIGH)

    async def handle_message(self, heart: Heart, msg: HeartMessage):
        if msg.topic == "health.probe":
            # Immediate health check requested
            await self._check_health(heart)
            await heart.inbox.put("health.status", {"status": "ok", "source": "probe"}, MessagePriority.NORMAL)
        
        elif msg.topic == "health.alert":
            logger.warning(f"HEALTH ALERT RECEIVED: {msg.payload}")
            # In a real system, this might trigger cleanup or notification

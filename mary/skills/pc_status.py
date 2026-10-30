"""Brick: battery, CPU, memory, disk space and uptime (get_system_status)."""
from __future__ import annotations

import time

import psutil

from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = "Battery, CPU, memory, disk space or uptime, only when he asks about the PC itself: get_system_status."


def _human_duration(seconds: float) -> str:
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} day{'s' * (days != 1)} {hours} hour{'s' * (hours != 1)}"
    if hours:
        return f"{hours} hour{'s' * (hours != 1)} {minutes} min"
    return f"{minutes} min"


def tools(ctx: ToolContext) -> list[Tool]:
    def get_system_status() -> ToolResult:
        parts = []
        battery = psutil.sensors_battery()
        if battery:
            plug = "plugged in" if battery.power_plugged else "on battery"
            left = ""
            if not battery.power_plugged and battery.secsleft not in (psutil.POWER_TIME_UNLIMITED,
                                                                       psutil.POWER_TIME_UNKNOWN):
                left = f", about {_human_duration(battery.secsleft)} left"
            parts.append(f"Battery {battery.percent:.0f}% ({plug}{left})")
        parts.append(f"CPU {psutil.cpu_percent(interval=0.3):.0f}%")
        mem = psutil.virtual_memory()
        parts.append(f"RAM {mem.used / 2**30:.1f} of {mem.total / 2**30:.1f} GB used ({mem.percent:.0f}%)")
        disk = psutil.disk_usage("C:\\")
        parts.append(f"Disk C: {disk.free / 2**30:.0f} GB free of {disk.total / 2**30:.0f} GB")
        parts.append(f"Up {_human_duration(time.time() - psutil.boot_time())}")
        text = ". ".join(parts) + "."
        return ToolResult(True, text, say=text)

    return [
        Tool("get_system_status", "Battery, CPU, RAM, disk space and uptime.", {}, [], get_system_status,
             announce=lambda a: "Checking system status…"),
    ]

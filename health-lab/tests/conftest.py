import math
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from garmin_readiness.features import TZ
from garmin_readiness.storage import Archive


def data_for(day, kind, *, hrv=60, hr=52):
    day = date.fromisoformat(day)
    start = datetime.combine(day - timedelta(days=1), time(23), TZ).astimezone(timezone.utc)
    end = datetime.combine(day, time(8), TZ).astimezone(timezone.utc)
    if kind == "sleep":
        return {
            "dailySleepDTO": {
                "calendarDate": day.isoformat(),
                "sleepTimeSeconds": 8 * 3600,
                "sleepStartTimestampGMT": int(start.timestamp() * 1000),
                "sleepEndTimestampGMT": int(end.timestamp() * 1000),
                "sleepStartTimestampLocal": int(start.timestamp() * 1000) + 8 * 3600000,
                "sleepEndTimestampLocal": int(end.timestamp() * 1000) + 8 * 3600000,
                "sleepWindowConfirmed": True,
                "sleepFromDevice": True,
                "sleepVersion": 1,
                "deviceId": 123,
                "sleepScores": {"overall": {"value": 80}},
            }
        }
    if kind == "hrv":
        return {
            "hrvSummary": {"calendarDate": day.isoformat(), "lastNightAvg": hrv, "weeklyAvg": 999},
            "sleepEndTimestampGMT": end.isoformat(),
        }
    if kind == "heart_rate":
        day_start = datetime.combine(day, time(), TZ).astimezone(timezone.utc)
        return {
            "calendarDate": day.isoformat(),
            "heartRateValueDescriptors": [
                {"key": "timestamp", "index": 0},
                {"key": "heartrate", "index": 1},
            ],
            "heartRateValues": [
                [int((day_start + timedelta(minutes=2 * i)).timestamp() * 1000), hr]
                for i in range(720)
            ],
        }
    if kind == "rhr":
        return {
            "allMetrics": {
                "metricsMap": {
                    "WELLNESS_RESTING_HEART_RATE": [{"calendarDate": day.isoformat(), "value": 45}]
                }
            }
        }
    if kind == "readiness":
        return [
            {
                "calendarDate": day.isoformat(),
                "inputContext": "AFTER_WAKEUP_RESET",
                "timestampGMT": end.isoformat(),
                "score": 80,
            }
        ]
    return {"synthetic": kind}


class FakeAPI:
    def __init__(self):
        self.client = SimpleNamespace(profile={"id": 123})
        self.display_name = "synthetic"
        self.calls = []

    def __getattr__(self, name):
        from garmin_readiness.provider import METHODS

        inverse = {method: kind for kind, method in METHODS.items()}
        if name not in inverse:
            raise AssertionError(f"Not an approved read: {name}")

        def get(day):
            self.calls.append((name, day))
            return data_for(day, inverse[name])

        return get


@pytest.fixture
def archive(tmp_path):
    archive = Archive(tmp_path / "private")
    with archive.lock():
        yield archive


def insert_day(archive, day, *, hrv=60, hr=52, fetched_at="2026-09-11T04:00:00+00:00"):
    for kind in ("hrv", "sleep", "heart_rate", "rhr", "readiness"):
        archive.record(
            day.isoformat(),
            kind,
            data_for(day.isoformat(), kind, hrv=hrv, hr=hr),
            fetched_at=fetched_at,
        )


def seed_history(archive, target):
    for delta in range(44):
        d = target - timedelta(days=delta)
        insert_day(archive, d, hrv=60 + 3 * math.sin(delta), hr=52 + math.cos(delta))

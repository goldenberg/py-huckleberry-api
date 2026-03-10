"""Sleep tracking tests for Huckleberry API."""

import asyncio
import random
from datetime import datetime, timedelta, timezone

from google.cloud import firestore

from huckleberry_api import HuckleberryAPI


class TestSleepTracking:
    """Test sleep tracking functionality."""

    async def test_start_and_cancel_sleep(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test starting and canceling sleep."""
        # Start sleep
        await api.start_sleep(child_uid)
        await asyncio.sleep(1)  # Wait for Firebase to propagate

        db = await api._get_firestore_client()

        # Get current state
        sleep_doc = await db.collection("sleep").document(child_uid).get()
        assert sleep_doc.exists
        data = sleep_doc.to_dict()
        assert data is not None
        assert data["timer"]["active"] is True
        assert data["timer"]["paused"] is False

        # Cancel sleep
        await api.cancel_sleep(child_uid)
        await asyncio.sleep(1)

        sleep_doc = await db.collection("sleep").document(child_uid).get()
        data = sleep_doc.to_dict()
        assert data is not None
        assert data["timer"]["active"] is False

    async def test_start_pause_resume_complete_sleep(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test full sleep cycle: start, pause, resume, complete."""
        # Start sleep
        await api.start_sleep(child_uid)
        await asyncio.sleep(2)

        # Pause sleep
        await api.pause_sleep(child_uid)
        await asyncio.sleep(1)

        db = await api._get_firestore_client()

        sleep_doc = await db.collection("sleep").document(child_uid).get()
        data = sleep_doc.to_dict()
        assert data is not None
        assert data["timer"]["active"] is True
        assert data["timer"]["paused"] is True

        # Resume sleep
        await api.resume_sleep(child_uid)
        await asyncio.sleep(1)

        sleep_doc = await db.collection("sleep").document(child_uid).get()
        data = sleep_doc.to_dict()
        assert data is not None
        assert data["timer"]["active"] is True
        assert data["timer"]["paused"] is False

        # Complete sleep
        await api.complete_sleep(child_uid)
        await asyncio.sleep(1)

        sleep_doc = await db.collection("sleep").document(child_uid).get()
        data = sleep_doc.to_dict()
        assert data is not None
        assert data["timer"]["active"] is False
        assert "lastSleep" in data.get("prefs", {})

    async def test_complete_sleep_creates_interval(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test that completing sleep creates interval document."""
        # Start and complete sleep quickly
        await api.start_sleep(child_uid)
        await asyncio.sleep(3)  # Sleep for at least 3 seconds to have meaningful duration
        await api.complete_sleep(child_uid)
        await asyncio.sleep(2)

        db = await api._get_firestore_client()

        # Check intervals subcollection
        intervals_ref = db.collection("sleep").document(child_uid).collection("intervals")

        # Get most recent interval
        recent_intervals = intervals_ref.order_by("start", direction=firestore.Query.DESCENDING).limit(1)

        intervals_list = list(await recent_intervals.get())
        assert len(intervals_list) > 0

        interval_data = intervals_list[0].to_dict()
        assert interval_data is not None
        assert "start" in interval_data
        assert "duration" in interval_data
        assert interval_data["duration"] >= 3  # At least 3 seconds

    async def test_log_sleep_nap(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging a daytime nap within a single day."""
        random_offset = random.randint(0, 100_000)
        start = datetime(2024, 4, 10, 13, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=random_offset)
        end = start + timedelta(hours=1, minutes=45)
        expected_duration = int((end - start).total_seconds())

        await api.log_sleep(child_uid, start_time=start, end_time=end)
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        sleep_ref = db.collection("sleep").document(child_uid)
        intervals = sleep_ref.collection("intervals").where("start", "==", start.timestamp()).stream()
        docs = [doc async for doc in intervals]
        assert len(docs) == 1, "Expected exactly one interval with the unique start timestamp"
        data = docs[0].to_dict()
        assert data["duration"] == expected_duration

        sleep_doc = await sleep_ref.get()
        prefs = (sleep_doc.to_dict() or {}).get("prefs", {})
        assert prefs.get("lastSleep", {}).get("start") == start.timestamp()
        assert prefs.get("lastSleep", {}).get("duration") == expected_duration

    async def test_log_sleep_overnight(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging an overnight sleep that crosses midnight."""
        # Offset by a random number of days so start is unique, but always at 7:30pm
        # Cap at 630 days to stay before 2026 (base date is 2024-04-10)
        random_offset_days = random.randint(0, 630)
        start = datetime(2024, 4, 10, 19, 30, 0, tzinfo=timezone.utc) + timedelta(days=random_offset_days)
        end = start + timedelta(hours=11)
        expected_duration = int((end - start).total_seconds())

        await api.log_sleep(child_uid, start_time=start, end_time=end)
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        sleep_ref = db.collection("sleep").document(child_uid)
        intervals = sleep_ref.collection("intervals").where("start", "==", start.timestamp()).stream()
        docs = [doc async for doc in intervals]
        assert len(docs) == 1, "Expected exactly one interval with the unique start timestamp"
        data = docs[0].to_dict()
        assert data["duration"] == expected_duration

    async def test_log_sleep_rejects_invalid_range(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test that log_sleep raises ValueError when end_time <= start_time."""
        now = datetime.now(timezone.utc)
        try:
            await api.log_sleep(child_uid, start_time=now, end_time=now - timedelta(hours=1))
            assert False, "Expected ValueError"
        except ValueError:
            pass

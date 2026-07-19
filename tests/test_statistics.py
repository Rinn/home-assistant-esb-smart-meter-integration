"""Tests for long-term statistics backfill."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import homeassistant.util.dt as dt_util
import pytest
from homeassistant.components.recorder.statistics import statistics_during_period
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.esb_smart_meter.models import ESBData
from custom_components.esb_smart_meter.statistics import async_update_statistics

IMPORT = "Active Import Interval (kWh)"
EXPORT = "Active Export Interval (kWh)"
IMPORT_ID = "esb_smart_meter:12345678901_usage"
EXPORT_ID = "esb_smart_meter:12345678901_export"


def _row(dt, value, read_type):
    return {"Read Date and End Time": dt.strftime("%d-%m-%Y %H:%M"), "Read Value": str(value), "Read Type": read_type}


@pytest.fixture
def esb_data():
    """Two import hours (0.3, 0.4 kWh) and one export hour (0.2 kWh)."""
    h = (datetime.now() - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    data = [
        _row(h + timedelta(minutes=30), 0.1, IMPORT),
        _row(h + timedelta(minutes=60), 0.2, IMPORT),
        _row(h + timedelta(minutes=90), 0.4, IMPORT),
        _row(h + timedelta(minutes=30), 0.05, EXPORT),
        _row(h + timedelta(minutes=60), 0.15, EXPORT),
    ]
    return ESBData(data=data)


def _call_for(add_mock, statistic_id):
    """Return the (metadata, stats) passed to async_add_external_statistics for an id."""
    for call in add_mock.call_args_list:
        _hass, metadata, stats = call.args
        if metadata["statistic_id"] == statistic_id:
            return metadata, stats
    raise AssertionError(f"no statistics call for {statistic_id}")


@pytest.mark.asyncio
async def test_first_backfill_builds_cumulative_sum(esb_data):
    """Test first backfill adds all hours with a running cumulative sum."""
    hass = MagicMock()
    with patch("custom_components.esb_smart_meter.statistics.get_instance") as get_instance, patch(
        "custom_components.esb_smart_meter.statistics.async_add_external_statistics"
    ) as add:
        get_instance.return_value.async_add_executor_job = AsyncMock(return_value={})
        await async_update_statistics(hass, "12345678901", esb_data)

    assert add.call_count == 2

    _meta, import_stats = _call_for(add, IMPORT_ID)
    assert [s["state"] for s in import_stats] == pytest.approx([0.3, 0.4])
    assert [s["sum"] for s in import_stats] == pytest.approx([0.3, 0.7])  # cumulative

    _meta, export_stats = _call_for(add, EXPORT_ID)
    assert [s["state"] for s in export_stats] == pytest.approx([0.2])
    assert [s["sum"] for s in export_stats] == pytest.approx([0.2])


@pytest.mark.asyncio
async def test_append_only_continues_from_prior_sum(esb_data):
    """Test recorded hours are skipped and the cumulative sum continues."""
    hass = MagicMock()
    first_hour = esb_data.hourly_usage()[0][0]
    prior_ts = first_hour.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE).timestamp()

    async def fake_executor(func, hass_, count, statistic_id, convert, types):
        if statistic_id == IMPORT_ID:
            return {statistic_id: [{"start": prior_ts, "sum": 10.0}]}
        return {}

    with patch("custom_components.esb_smart_meter.statistics.get_instance") as get_instance, patch(
        "custom_components.esb_smart_meter.statistics.async_add_external_statistics"
    ) as add:
        get_instance.return_value.async_add_executor_job = AsyncMock(side_effect=fake_executor)
        await async_update_statistics(hass, "12345678901", esb_data)

    # First import hour is already recorded, so only the second hour is added,
    # continuing the cumulative sum from 10.0.
    _meta, import_stats = _call_for(add, IMPORT_ID)
    assert len(import_stats) == 1
    assert import_stats[0]["state"] == pytest.approx(0.4)
    assert import_stats[0]["sum"] == pytest.approx(10.4)


@pytest.mark.asyncio
async def test_statistics_written_to_recorder(recorder_mock, hass, esb_data):
    """Test statistics are persisted to and read back from the recorder."""
    await async_update_statistics(hass, "12345678901", esb_data)
    await async_wait_recording_done(hass)

    start = dt_util.utcnow() - timedelta(days=2)
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        None,
        {IMPORT_ID, EXPORT_ID},
        "hour",
        None,
        {"state", "sum"},
    )

    assert [row["state"] for row in stats[IMPORT_ID]] == pytest.approx([0.3, 0.4])
    assert [row["sum"] for row in stats[IMPORT_ID]] == pytest.approx([0.3, 0.7])
    assert [row["state"] for row in stats[EXPORT_ID]] == pytest.approx([0.2])


@pytest.mark.asyncio
async def test_no_data_adds_nothing():
    """Test empty data adds no statistics."""
    hass = MagicMock()
    empty = ESBData(data=[])
    with patch("custom_components.esb_smart_meter.statistics.get_instance") as get_instance, patch(
        "custom_components.esb_smart_meter.statistics.async_add_external_statistics"
    ) as add:
        get_instance.return_value.async_add_executor_job = AsyncMock(return_value={})
        await async_update_statistics(hass, "12345678901", empty)

    add.assert_not_called()

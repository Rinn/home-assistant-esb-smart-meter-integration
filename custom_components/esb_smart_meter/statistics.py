"""Long-term statistics import for ESB Smart Meter integration."""

import logging
from datetime import datetime

import homeassistant.util.dt as dt_util
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
)
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Imported (entity-based) statistics must declare "recorder" as their source.
RECORDER_SOURCE = "recorder"


async def async_import_hourly_statistics(
    hass: HomeAssistant,
    statistic_id: str,
    hourly: list[tuple[datetime, float]],
) -> float | None:
    """Import hourly (hour_start, kWh) buckets and return the cumulative total.

    Returns the running cumulative sum (the meter total), or None if there is no
    data to work with.
    """
    if not hourly:
        return None

    last_start, running_sum = await _last_recorded(hass, statistic_id)

    stats: list[StatisticData] = []
    for hour_start, kwh in hourly:
        start = hour_start.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        if last_start is not None and start <= last_start:
            continue  # already recorded
        running_sum += kwh
        stats.append(StatisticData(start=start, state=kwh, sum=running_sum))

    if stats:
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=None,
            source=RECORDER_SOURCE,
            statistic_id=statistic_id,
            unit_of_measurement="kWh",
        )
        async_import_statistics(hass, metadata, stats)
        _LOGGER.debug("Imported %d statistics rows to %s", len(stats), statistic_id)

    return running_sum


async def _last_recorded(hass: HomeAssistant, statistic_id: str) -> tuple[datetime | None, float]:
    """Return (start, sum) of the last recorded row, or (None, 0.0)."""
    last = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
    )
    rows = last.get(statistic_id)
    if not rows:
        return None, 0.0
    row = rows[0]
    return dt_util.utc_from_timestamp(row["start"]), float(row["sum"] or 0.0)

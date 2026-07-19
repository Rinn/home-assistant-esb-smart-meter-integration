"""Long-term statistics backfill for ESB Smart Meter integration."""

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
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .models import ESBData

_LOGGER = logging.getLogger(__name__)


async def async_update_statistics(hass: HomeAssistant, mprn: str, esb_data: ESBData) -> None:
    """Write usage and export readings to Home Assistant long-term statistics."""
    await _update_series(
        hass,
        f"{DOMAIN}:{mprn}_usage",
        "ESB Electricity Usage",
        esb_data.hourly_usage(),
    )
    await _update_series(
        hass,
        f"{DOMAIN}:{mprn}_export",
        "ESB Electricity Export",
        esb_data.hourly_export(),
    )


async def _update_series(
    hass: HomeAssistant,
    statistic_id: str,
    name: str,
    hourly: list[tuple[datetime, float]],
) -> None:
    """Append a statistic's new hourly buckets, continuing its cumulative sum."""
    if not hourly:
        return

    last_start, running_sum = await _last_recorded(hass, statistic_id)

    stats: list[StatisticData] = []
    for hour_start, kwh in hourly:
        start = hour_start.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        if last_start is not None and start <= last_start:
            continue  # already recorded
        running_sum += kwh
        stats.append(StatisticData(start=start, state=kwh, sum=running_sum))

    if not stats:
        return

    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement="kWh",
    )
    async_add_external_statistics(hass, metadata, stats)
    _LOGGER.debug("Added %d statistics rows to %s", len(stats), statistic_id)


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

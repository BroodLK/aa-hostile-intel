"""Optional, read-only aa-metenox integration for the Moon Data tab.

aa-metenox calculates a Metenox drill's monthly value from its own survey data and keeps
Jita prices for fuel blocks and magmatic gas. We read those tables directly instead of
calling aa-metenox helpers, because ``EveTypePrice.get_eve_type_price`` uses
``get_or_create`` and would write zero-price rows.
"""

# Standard Library
from typing import Any

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

logger = get_extension_logger(__name__)

HOURS_PER_MONTH = 720  # aa-metenox: hourly * 24 * 30
FUEL_BLOCK_GROUP_ID = 1136
MAGMATIC_GAS_TYPE_ID = 81143
DEFAULT_FUEL_BLOCKS_PER_HOUR = 5
DEFAULT_MAGMATIC_GASES_PER_HOUR = 200


def metenox_available() -> bool:
    """True when aa-metenox is installed and its models can be imported."""
    try:
        # Django
        from django.apps import apps

        if not apps.is_installed("metenox"):
            return False
        # Third Party
        from metenox.models import EveTypePrice, Moon  # noqa: F401
    except (ImportError, LookupError):
        return False
    return True


def monthly_running_cost(
    fuel_block_prices: Any,
    magmatic_price: float | None,
    blocks_per_hour: float = DEFAULT_FUEL_BLOCKS_PER_HOUR,
    gas_per_hour: float = DEFAULT_MAGMATIC_GASES_PER_HOUR,
) -> dict[str, float] | None:
    """Monthly Metenox running cost, the way aa-metenox's ``Moon.fuel_price`` works it out.

    Uses the cheapest fuel block. Returns ``None`` when a fuel or gas price is missing.
    """
    prices = [float(p) for p in fuel_block_prices if p and p > 0]
    if not prices or not magmatic_price or magmatic_price <= 0:
        return None
    fuel = blocks_per_hour * min(prices) * HOURS_PER_MONTH
    gas = gas_per_hour * float(magmatic_price) * HOURS_PER_MONTH
    return {"fuel": fuel, "gas": gas, "total": fuel + gas}


def load_drill_costs() -> dict[str, float] | None:
    """Monthly running cost from aa-metenox's stored prices; ``None`` if unavailable."""
    if not metenox_available():
        return None
    # Third Party
    from metenox import app_settings
    from metenox.models import EveTypePrice

    fuel_prices = EveTypePrice.objects.filter(
        eve_type__group_id=FUEL_BLOCK_GROUP_ID, price__gt=0
    ).values_list("price", flat=True)
    gas_price = (
        EveTypePrice.objects.filter(eve_type_id=MAGMATIC_GAS_TYPE_ID, price__gt=0)
        .values_list("price", flat=True)
        .first()
    )
    return monthly_running_cost(
        list(fuel_prices),
        gas_price,
        getattr(
            app_settings, "METENOX_FUEL_BLOCKS_PER_HOUR", DEFAULT_FUEL_BLOCKS_PER_HOUR
        ),
        getattr(
            app_settings,
            "METENOX_MAGMATIC_GASES_PER_HOUR",
            DEFAULT_MAGMATIC_GASES_PER_HOUR,
        ),
    )


def load_metenox_values(moon_ids: Any = None) -> dict[int, float]:
    """``{eve_moon_id: monthly drill value}`` for moons aa-metenox has a value for."""
    if not metenox_available():
        return {}
    # Third Party
    from metenox.models import Moon

    moons = Moon.objects.filter(value__gt=0)
    if moon_ids is not None:
        moons = moons.filter(pk__in=list(moon_ids))
    return dict(moons.values_list("eve_moon_id", "value"))


def load_drill_inputs(moon_ids: Any = None) -> tuple[dict[int, float], Any]:
    """aa-metenox values and costs; never raises, so Moon Data still loads."""
    try:
        return load_metenox_values(moon_ids), load_drill_costs()
    except Exception:
        logger.exception("Unable to read aa-metenox drill values / prices")
        return {}, None

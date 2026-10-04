"""
Solar system distance and jump drive routing service for Hostile Intel.
Calculates light year (LY) distances using 3D Euclidean coordinates
and stargate jump counts across New Eden.
"""

# Standard Library
import logging
import math
from typing import Any, Dict, List, Optional

# Django
from django.core.cache import cache

# Third Party
from eve_sde.models import SolarSystem

# AA Hostile Intel
from hostile.services.esi_client import esi_client

logger = logging.getLogger(__name__)


class MapRoutingService:
    """
    Solar system distance and jump drive routing service.
    Calculates light year (LY) distances using 3D Euclidean coordinates
    and stargate jump counts across New Eden.
    """

    # 1 Light Year = 9,460,730,472,580,800 meters
    METERS_PER_LY = 9.4607304725808e15

    @classmethod
    def calculate_ly_distance(
        cls,
        origin: Optional[SolarSystem],
        destination: Optional[SolarSystem],
    ) -> Optional[float]:
        """Calculates direct 3D jump drive distance in Light Years (LY)"""
        if not origin or not destination:
            return None
        if origin.id == destination.id:
            return 0.0

        ox = getattr(origin, "x", None)
        oy = getattr(origin, "y", None)
        oz = getattr(origin, "z", None)

        dx = getattr(destination, "x", None)
        dy = getattr(destination, "y", None)
        dz = getattr(destination, "z", None)

        if (
            ox is None
            or oy is None
            or oz is None
            or dx is None
            or dy is None
            or dz is None
        ):
            return None

        try:
            dist_meters = math.sqrt(
                (float(ox) - float(dx)) ** 2
                + (float(oy) - float(dy)) ** 2
                + (float(oz) - float(dz)) ** 2
            )
            dist_ly = dist_meters / cls.METERS_PER_LY
            return round(dist_ly, 2)
        except (ValueError, TypeError, OverflowError):
            return None

    @classmethod
    def calculate_stargate_jumps(
        cls,
        origin: Optional[SolarSystem],
        destination: Optional[SolarSystem],
        flag: str = "shortest",
    ) -> Optional[int]:
        """
        Calculates number of stargate jumps from origin to destination via ESI route API.
        Default flag is 'shortest' (shortest route).
        Uses long-lived cache since New Eden stargate topologies are static.
        """
        if not origin or not destination:
            return None
        if origin.id == destination.id:
            return 0

        cache_key = f"hostile_route_jumps_{origin.id}_{destination.id}_{flag}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            route = esi_client.get_route(
                origin_system_id=origin.id,
                destination_system_id=destination.id,
                flag=flag,
            )
            if route is None:
                return None
            if isinstance(route, list):
                if len(route) == 0:
                    jumps = None
                elif route[0] == origin.id:
                    jumps = max(0, len(route) - 1)
                else:
                    jumps = len(route)
                if jumps is not None:
                    cache.set(cache_key, jumps, timeout=86400 * 7)
                return jumps
        except Exception as exc:
            logger.warning(
                "Error computing stargate jumps from %s to %s (flag=%s): %s",
                origin.id,
                destination.id,
                flag,
                exc,
            )
            return None
        return None

    @classmethod
    def get_system_distance_summary(
        cls,
        origin: Optional[SolarSystem],
        destination: Optional[SolarSystem],
        flag: str = "shortest",
        include_jumps: bool = True,
    ) -> Dict[str, Any]:
        """Returns structured distance data including light years and shortest stargate jumps"""
        ly_dist = cls.calculate_ly_distance(origin, destination)
        jumps = (
            cls.calculate_stargate_jumps(origin, destination, flag=flag)
            if (include_jumps and origin and destination)
            else None
        )

        if ly_dist is None or not origin or not destination:
            return {
                "origin_id": origin.id if origin else None,
                "destination_id": destination.id if destination else None,
                "ly": None,
                "jumps": None,
                "display": "-",
            }

        if ly_dist == 0.0 or origin.id == destination.id:
            display_str = "0.00 LY · 0 Jumps"
        else:
            if jumps is not None:
                display_str = f"{ly_dist:.2f} LY · {jumps} Jumps"
            else:
                display_str = f"{ly_dist:.2f} LY"

        return {
            "origin_id": origin.id,
            "origin_name": origin.name,
            "destination_id": destination.id,
            "destination_name": destination.name,
            "ly": ly_dist,
            "jumps": jumps,
            "display": display_str,
        }

    @classmethod
    def get_batch_routes_summary(
        cls,
        origin_id: int,
        destination_ids: List[int],
        flag: str = "shortest",
    ) -> Dict[int, Dict[str, Any]]:
        """Calculates distance and shortest jump summaries for multiple destination solar systems"""
        origin = SolarSystem.objects.filter(id=origin_id).first()
        if not origin:
            return {}

        dests = {s.id: s for s in SolarSystem.objects.filter(id__in=destination_ids)}
        results: Dict[int, Dict[str, Any]] = {}

        for dest_id in destination_ids:
            dest = dests.get(dest_id)
            if dest:
                results[dest_id] = cls.get_system_distance_summary(
                    origin, dest, flag=flag, include_jumps=True
                )
            else:
                results[dest_id] = {
                    "origin_id": origin.id,
                    "destination_id": dest_id,
                    "ly": None,
                    "jumps": None,
                    "display": "-",
                }
        return results

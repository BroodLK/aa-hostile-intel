"""
ESI Client Provider and error handling wrapper modeled after Corptools
"""

# Standard Library
import hashlib
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

# Django
from django.core.cache import cache

# Alliance Auth
import esi
from allianceauth.services.hooks import get_extension_logger
from esi.openapi_clients import (
    ESIClientProvider,
    ESIErrorLimitException,
    HTTPClientError,
    HTTPNotModified,
    HTTPServerError,
    HTTPStatusError,
)

# AA Hostile Intel
from hostile import __appname__, __url__, __version__

logger = get_extension_logger(__name__)


def _normalize_esi_data(data: Any) -> Any:
    """Recursively converts Pydantic models, Bravado objects, dataclasses, and iterables to standard Python dicts/lists/primitives."""
    if data is None:
        return None
    if isinstance(data, (int, float, str, bool, bytes)):
        return data
    if isinstance(data, dict):
        return {k: _normalize_esi_data(v) for k, v in data.items()}
    if isinstance(data, (list, tuple, set)):
        return [_normalize_esi_data(item) for item in data]
    # Pydantic v2
    if hasattr(data, "model_dump") and callable(getattr(data, "model_dump", None)):
        try:
            return _normalize_esi_data(data.model_dump())
        except Exception:
            pass
    # Pydantic v1
    if hasattr(data, "dict") and callable(getattr(data, "dict", None)):
        try:
            return _normalize_esi_data(data.dict())
        except Exception:
            pass
    # Object with __dict__
    if hasattr(data, "__dict__"):
        try:
            return _normalize_esi_data(vars(data))
        except Exception:
            pass
    return data


def _get_esi_op_identifier(op_func: Any) -> str:
    """Extracts a stable deterministic string identifier for an ESI operation function or object."""
    if op_func is None:
        return "none"
    op_obj = getattr(op_func, "operation", None)
    if op_obj:
        op_id = getattr(op_obj, "operationId", None) or getattr(op_obj, "operation_id", None)
        if op_id:
            return str(op_id)
        path = getattr(op_obj, "path", None)
        if path:
            return str(path)
    op_id = getattr(op_func, "operation_id", None) or getattr(op_func, "operationId", None)
    if op_id:
        return str(op_id)
    url = getattr(op_func, "url", None)
    if url:
        return str(url)
    if hasattr(op_func, "__qualname__") and op_func.__qualname__:
        return op_func.__qualname__
    if hasattr(op_func, "__name__") and op_func.__name__:
        return op_func.__name__
    if hasattr(op_func, "__self__") and hasattr(op_func, "__func__"):
        self_cls = op_func.__self__.__class__.__name__
        func_name = getattr(op_func.__func__, "__name__", str(op_func.__func__))
        return f"{self_cls}.{func_name}"
    raw_str = str(op_func)
    cleaned = re.sub(r"\s+at\s+0x[0-9a-fA-F]+", "", raw_str)
    return cleaned


def _get_esi_cache_key(op_func: Callable, args: tuple, kwargs: dict) -> str:
    """Generates a unique deterministic cache key for an ESI operation and arguments"""
    func_name = _get_esi_op_identifier(op_func)
    try:
        clean_kwargs = {str(k): str(v) for k, v in sorted(kwargs.items(), key=lambda x: str(x[0]))}
        clean_args = [str(a) for a in args]
        serialized = json.dumps({"f": func_name, "a": clean_args, "k": clean_kwargs}, sort_keys=True)
    except Exception:
        serialized = f"{func_name}:{args}:{kwargs}"
    h = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]
    return f"hostile_esi_res_{h}"


class HostileESIClientProvider(ESIClientProvider):
    """ESI Client Provider for Hostile Intel with rate limiting backoff, error resilience, and 304 cache activation"""

    def __init__(self, **kwargs):
        kwargs.setdefault("compatibility_date", getattr(esi, "__esi_compatibility_date__", "2026-08-18"))
        kwargs.setdefault("ua_appname", __appname__)
        kwargs.setdefault("ua_version", __version__)
        kwargs.setdefault("ua_url", __url__)
        kwargs.setdefault(
            "operations",
            [
                "PostRoute",
                "GetRouteOriginDestination",
                "GetSovereigntySystems",
                "GetSovereigntyCampaigns",
                "GetSovereigntyMap",
                "GetSovereigntyStructures",
                "PostUniverseNames",
                "PostUniverseIds",
                "PostCharactersAffiliation",
                "GetAlliances",
                "GetAlliancesAllianceId",
                "GetAlliancesAllianceIdCorporations",
                "GetCorporationsCorporationId",
            ],
        )
        super().__init__(**kwargs)

    def fetch_safely(
        self,
        op_func: Callable,
        *args,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        **kwargs,
    ) -> Optional[Any]:
        """Executes an ESI operation with retry on 420 rate limit, transient 5xx errors, and 304 cache retrieval with force-refresh fallback"""
        cache_key = _get_esi_cache_key(op_func, args, kwargs)
        for attempt in range(1, max_retries + 1):
            try:
                response = op_func(*args, **kwargs)
                if hasattr(response, "results"):
                    res = response.results()
                else:
                    res = response
                normalized = _normalize_esi_data(res)
                if normalized is not None:
                    try:
                        cache.set(cache_key, normalized, timeout=86400)
                    except Exception as c_exc:
                        logger.debug("Failed to store ESI payload in cache (%s): %s", cache_key, c_exc)
                return normalized
            except HTTPNotModified:
                logger.debug("ESI returned 304 Not Modified, retrieving cached payload from cache (%s)", cache_key)
                try:
                    cached = cache.get(cache_key)
                except Exception:
                    cached = None
                if cached is not None:
                    return cached
                logger.info("Cache miss on 304 for %s, requesting force refresh from ESI...", cache_key)
                try:
                    if hasattr(op_func, "results"):
                        res = op_func.results(force_refresh=True, *args, **kwargs)
                    elif hasattr(op_func, "result"):
                        res = op_func.result(force_refresh=True, *args, **kwargs)
                    else:
                        res = op_func(*args, force_refresh=True, **kwargs)
                    normalized = _normalize_esi_data(res)
                    if normalized is not None:
                        try:
                            cache.set(cache_key, normalized, timeout=86400)
                        except Exception:
                            pass
                    return normalized
                except Exception as ref_exc:
                    logger.warning("Force refresh on 304 failed: %s", ref_exc)
                    return None
            except ESIErrorLimitException as exc:
                wait_time = backoff_base**attempt + 5.0
                logger.warning(
                    "ESI rate limit hit (attempt %d/%d). Sleeping for %.1f seconds: %s",
                    attempt,
                    max_retries,
                    wait_time,
                    exc,
                )
                time.sleep(wait_time)
            except (HTTPServerError, HTTPStatusError) as exc:
                status_code = getattr(exc, "status_code", getattr(exc, "code", 500))
                if status_code == 304:
                    logger.debug("ESI returned 304 Not Modified status code, retrieving cached payload (%s)", cache_key)
                    try:
                        cached = cache.get(cache_key)
                    except Exception:
                        cached = None
                    if cached is not None:
                        return cached
                    logger.info("Cache miss on 304 for %s, requesting force refresh from ESI...", cache_key)
                    try:
                        if hasattr(op_func, "results"):
                            res = op_func.results(force_refresh=True, *args, **kwargs)
                        elif hasattr(op_func, "result"):
                            res = op_func.result(force_refresh=True, *args, **kwargs)
                        else:
                            res = op_func(*args, force_refresh=True, **kwargs)
                        normalized = _normalize_esi_data(res)
                        if normalized is not None:
                            try:
                                cache.set(cache_key, normalized, timeout=86400)
                            except Exception:
                                pass
                        return normalized
                    except Exception as ref_exc:
                        logger.warning("Force refresh on 304 failed: %s", ref_exc)
                        return None
                if status_code in [420, 500, 502, 503, 504]:
                    wait_time = backoff_base**attempt
                    logger.warning(
                        "ESI error %s on attempt %d/%d. Retrying in %.1f seconds...",
                        status_code,
                        attempt,
                        max_retries,
                        wait_time,
                    )
                    time.sleep(wait_time)
                else:
                    logger.error("Non-retriable ESI status error: %s", exc)
                    raise
            except HTTPClientError as exc:
                logger.warning("ESI 4xx Client Error: %s", exc)
                return None
            except Exception as exc:
                logger.error("Unexpected error querying ESI: %s", exc, exc_info=True)
                if attempt == max_retries:
                    raise
                time.sleep(backoff_base**attempt)
        return None

    def get_sovereignty_systems(self) -> List[Dict[str, Any]]:
        """Fetches public sovereignty systems data (Equinox ESI)"""
        try:
            if hasattr(self.client, "Sovereignty") and hasattr(self.client.Sovereignty, "GetSovereigntySystems"):
                raw = self.fetch_safely(self.client.Sovereignty.GetSovereigntySystems)
                if not raw:
                    return []
                if isinstance(raw, list):
                    if len(raw) > 0 and isinstance(raw[0], dict) and "solar_systems" in raw[0]:
                        return raw[0]["solar_systems"] or []
                    return raw
                elif isinstance(raw, dict) and "solar_systems" in raw:
                    return raw["solar_systems"] or []
            return []
        except Exception as exc:
            logger.error("Failed to fetch sovereignty systems: %s", exc)
            return []

    def get_sovereignty_map(self) -> List[Dict[str, Any]]:
        """Fetches public sovereignty map. Uses GetSovereigntySystems (modern ESI) with fallback to GetSovereigntyMap (legacy)."""
        if hasattr(self.client, "Sovereignty") and hasattr(self.client.Sovereignty, "GetSovereigntySystems"):
            systems = self.get_sovereignty_systems()
            if systems:
                map_data = []
                for s in systems:
                    if not isinstance(s, dict):
                        continue
                    solar_system_id = s.get("solar_system_id")
                    claim = s.get("claim") or {}
                    alliance_data = claim.get("alliance") or {}
                    faction_data = claim.get("faction") or {}
                    alliance_id = alliance_data.get("alliance_id")
                    corporation_id = alliance_data.get("corporation_id")
                    faction_id = faction_data.get("faction_id")
                    is_capital = alliance_data.get("is_capital_system", False)
                    if alliance_id or corporation_id or faction_id:
                        map_data.append(
                            {
                                "solar_system_id": solar_system_id,
                                "alliance_id": alliance_id,
                                "corporation_id": corporation_id,
                                "faction_id": faction_id,
                                "is_capital_system": is_capital,
                            }
                        )
                return map_data

        try:
            if hasattr(self.client, "Sovereignty") and hasattr(self.client.Sovereignty, "GetSovereigntyMap"):
                raw_map = self.fetch_safely(self.client.Sovereignty.GetSovereigntyMap) or []
                if raw_map:
                    normalized_map = []
                    for item in raw_map:
                        if not isinstance(item, dict):
                            continue
                        sys_id = item.get("solar_system_id") or item.get("system_id")
                        aid = item.get("alliance_id")
                        cid = item.get("corporation_id")
                        fid = item.get("faction_id")
                        normalized_map.append(
                            {
                                "solar_system_id": sys_id,
                                "system_id": sys_id,
                                "alliance_id": aid,
                                "corporation_id": cid,
                                "faction_id": fid,
                                "is_capital_system": item.get("is_capital_system", False),
                            }
                        )
                    return normalized_map
                return raw_map
        except Exception as exc:
            logger.error("Failed to fetch legacy sovereignty map: %s", exc)
        return []

    def get_sovereignty_structures(self) -> List[Dict[str, Any]]:
        """Fetches public sovereignty structures (I-Hubs, TCUs, vulnerability times). Uses GetSovereigntySystems with fallback to GetSovereigntyStructures."""
        if hasattr(self.client, "Sovereignty") and hasattr(self.client.Sovereignty, "GetSovereigntySystems"):
            systems = self.get_sovereignty_systems()
            if systems:
                structures_data = []
                for s in systems:
                    if not isinstance(s, dict):
                        continue
                    solar_system_id = s.get("solar_system_id")
                    claim = s.get("claim") or {}
                    alliance_data = claim.get("alliance") or {}
                    alliance_id = alliance_data.get("alliance_id")
                    hub = alliance_data.get("sovereignty_hub") or {}
                    vuln = hub.get("vulnerability_window") or {}
                    vuln_start = vuln.get("start")
                    vuln_end = vuln.get("end")
                    dev = alliance_data.get("development") or {}
                    adm = dev.get("activity_defense_multiplier")
                    structure_id = hub.get("id")
                    if alliance_id and (vuln_start or structure_id):
                        structures_data.append(
                            {
                                "solar_system_id": solar_system_id,
                                "alliance_id": alliance_id,
                                "structure_id": structure_id,
                                "vulnerable_start_time": vuln_start,
                                "vulnerable_end_time": vuln_end,
                                "vulnerability_occupancy_level": adm,
                                "structure_type_id": 32226,
                            }
                        )
                return structures_data

        try:
            if hasattr(self.client, "Sovereignty") and hasattr(self.client.Sovereignty, "GetSovereigntyStructures"):
                return self.fetch_safely(self.client.Sovereignty.GetSovereigntyStructures) or []
        except Exception as exc:
            logger.error("Failed to fetch legacy sovereignty structures: %s", exc)
        return []

    def get_sovereignty_campaigns(self) -> List[Dict[str, Any]]:
        """Fetches active sovereignty contest campaigns"""
        try:
            return self.fetch_safely(self.client.Sovereignty.GetSovereigntyCampaigns) or []
        except Exception as exc:
            logger.error("Failed to fetch sovereignty campaigns: %s", exc)
            return []

    def post_universe_names(self, ids: List[int]) -> List[Dict[str, Any]]:
        """Resolves entity IDs (Alliances, Corporations, Characters) to names and categories"""
        if not ids:
            return []
        try:
            ids_list = list(set(ids))
            return self.fetch_safely(self.client.Universe.PostUniverseNames, body=ids_list) or []
        except Exception as exc:
            logger.error("Failed to resolve universe names: %s", exc)
            return []

    def post_universe_ids(self, names: List[str]) -> Dict[str, Any]:
        """Resolves entity names (Alliances, Corporations, Characters) to IDs and categories"""
        if not names:
            return {}
        try:
            names_list = list(set(names))
            res = self.fetch_safely(self.client.Universe.PostUniverseIds, body=names_list)
            if isinstance(res, list) and len(res) > 0 and isinstance(res[0], dict):
                return res[0]
            if isinstance(res, dict):
                return res
            return {}
        except Exception as exc:
            logger.error("Failed to resolve universe IDs: %s", exc)
            return {}

    def post_characters_affiliation(self, character_ids: List[int]) -> List[Dict[str, Any]]:
        """Fetches character affiliations (corporation_id, alliance_id)"""
        if not character_ids:
            return []
        try:
            ids_list = list(set(character_ids))
            if hasattr(self.client, "Character") and hasattr(self.client.Character, "PostCharactersAffiliation"):
                res = self.fetch_safely(self.client.Character.PostCharactersAffiliation, body=ids_list)
                if isinstance(res, list) and len(res) > 0 and isinstance(res[0], list):
                    return res[0]
                return res or []
            return []
        except Exception as exc:
            logger.error("Failed to fetch character affiliations: %s", exc)
            return []

    def get_alliance_corporations(self, alliance_id: int) -> List[int]:
        """Fetches public member corporation IDs for an alliance"""
        if not alliance_id:
            return []
        try:
            if hasattr(self.client, "Alliance") and hasattr(self.client.Alliance, "GetAlliancesAllianceIdCorporations"):
                res = self.fetch_safely(
                    self.client.Alliance.GetAlliancesAllianceIdCorporations,
                    alliance_id=int(alliance_id),
                )
                if not res:
                    return []
                if isinstance(res, list) and len(res) > 0 and isinstance(res[0], list):
                    return res[0]
                return res or []
            return []
        except Exception as exc:
            logger.error("Failed to fetch alliance corporations for %s: %s", alliance_id, exc)
            return []

    def get_corporation_info(self, corporation_id: int) -> Optional[Dict[str, Any]]:
        """Fetches public corporation information including member_count, ticker, ceo_id, and name"""
        if not corporation_id:
            return None
        try:
            if hasattr(self.client, "Corporation") and hasattr(self.client.Corporation, "GetCorporationsCorporationId"):
                res = self.fetch_safely(
                    self.client.Corporation.GetCorporationsCorporationId,
                    corporation_id=int(corporation_id),
                )
                if isinstance(res, list) and len(res) > 0:
                    return res[0]
                return res
            return None
        except Exception as exc:
            logger.error("Failed to fetch corporation info for %s: %s", corporation_id, exc)
            return None

    def get_alliance_info(self, alliance_id: int) -> Optional[Dict[str, Any]]:
        """Fetches public alliance information"""
        if not alliance_id:
            return None
        try:
            if hasattr(self.client, "Alliance") and hasattr(self.client.Alliance, "GetAlliancesAllianceId"):
                res = self.fetch_safely(
                    self.client.Alliance.GetAlliancesAllianceId,
                    alliance_id=int(alliance_id),
                )
                if isinstance(res, list) and len(res) > 0:
                    return res[0]
                return res
            return None
        except Exception as exc:
            logger.error("Failed to fetch alliance info for %s: %s", alliance_id, exc)
            return None

    def get_route(
        self,
        origin_system_id: int,
        destination_system_id: int,
        flag: str = "shortest",
        avoid: Optional[List[int]] = None,
        connections: Optional[List[List[int]]] = None,
    ) -> Optional[List[int]]:
        """
        Fetches the solar system route from origin to destination via modern ESI PostRoute or legacy GetRouteOriginDestination.
        Flag options: 'shortest' / 'short' (Shorter), 'secure' / 'safest' (Safer), 'insecure' (LessSecure).
        Returns list of solar system IDs representing the route path, or None if unreachable.
        """
        if not origin_system_id or not destination_system_id:
            return None
        orig_id = int(origin_system_id)
        dest_id = int(destination_system_id)
        if orig_id == dest_id:
            return [orig_id]

        flag_lower = str(flag).lower().strip()
        if flag_lower in ("secure", "safest", "safe", "safer"):
            preference = "Safer"
            legacy_flag = "secure"
        elif flag_lower in ("insecure", "unsafe", "lesssecure"):
            preference = "LessSecure"
            legacy_flag = "insecure"
        else:
            preference = "Shorter"
            legacy_flag = "shortest"

        # Modern OpenAPI 3 / Equinox PostRoute
        try:
            if hasattr(self.client, "Routes") and hasattr(self.client.Routes, "PostRoute"):
                body_payload: Dict[str, Any] = {"preference": preference}
                if avoid:
                    body_payload["avoid_systems"] = [int(x) for x in avoid]
                if connections:
                    body_payload["connections"] = connections

                raw = self.fetch_safely(
                    self.client.Routes.PostRoute,
                    origin_system_id=orig_id,
                    destination_system_id=dest_id,
                    body=body_payload,
                )
                if raw is not None:
                    if isinstance(raw, list):
                        if len(raw) > 0 and isinstance(raw[0], dict) and "route" in raw[0]:
                            return [int(x) for x in raw[0]["route"] if str(x).isdigit()]
                        return [int(x) for x in raw if str(x).isdigit()]
                    elif isinstance(raw, dict) and "route" in raw:
                        return [int(x) for x in raw["route"] if str(x).isdigit()]
                    elif isinstance(raw, int):
                        return [raw]
                    return raw
        except Exception as exc:
            logger.debug("PostRoute failed: %s, falling back to legacy route operation", exc)

        # Legacy GetRouteOriginDestination
        try:
            if hasattr(self.client, "Routes") and hasattr(
                self.client.Routes, "GetRouteOriginDestination"
            ):
                kwargs: Dict[str, Any] = {
                    "origin": orig_id,
                    "destination": dest_id,
                    "flag": legacy_flag,
                }
                if avoid:
                    kwargs["avoid"] = avoid
                if connections:
                    kwargs["connections"] = connections

                raw = self.fetch_safely(
                    self.client.Routes.GetRouteOriginDestination, **kwargs
                )
                if raw is not None:
                    if isinstance(raw, list):
                        if len(raw) > 0 and isinstance(raw[0], dict) and "route" in raw[0]:
                            return [int(x) for x in raw[0]["route"] if str(x).isdigit()]
                        return [int(x) for x in raw if str(x).isdigit()]
                    elif isinstance(raw, dict) and "route" in raw:
                        return [int(x) for x in raw["route"] if str(x).isdigit()]
                    elif isinstance(raw, int):
                        return [raw]
                    return raw
        except Exception as exc:
            logger.warning(
                "Failed to fetch route from %s to %s (flag=%s): %s",
                orig_id,
                dest_id,
                flag,
                exc,
            )
        return None


esi_client = HostileESIClientProvider()

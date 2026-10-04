"""
Entity resolution service for characters, corporations, and alliances.
Prioritizes internal Alliance Auth and Hostile database records before falling back to ESI / zKill.
"""

# Standard Library
import logging
from typing import Any, Dict, List, Optional, Union

# Django
from django.db import models

# Alliance Auth
from allianceauth.eveonline.models import EveAllianceInfo, EveCharacter, EveCorporationInfo
from allianceauth.services.hooks import get_extension_logger

# AA Hostile Intel
from hostile.models import HostileAlliance, HostileCorporation, HostilePilotDossier
from hostile.services.esi_client import esi_client

logger = get_extension_logger(__name__)


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    """Safely extracts a value from a dict, list, Pydantic model, Bravado object, or generic class instance."""
    if obj is None:
        return default
    if isinstance(obj, (list, tuple)) and len(obj) > 0 and isinstance(obj[0], (dict, object)):
        obj = obj[0]
    if isinstance(obj, dict):
        return obj.get(key, default)
    if hasattr(obj, key):
        val = getattr(obj, key)
        return val if val is not None else default
    if hasattr(obj, "get") and callable(getattr(obj, "get", None)):
        try:
            return obj.get(key, default)
        except Exception:
            pass
    return default


class EntityResolver:
    """
    High-performance entity resolver and autocompletion provider.
    Searches internal Alliance Auth and Hostile models first to avoid unnecessary ESI roundtrips.
    """

    @classmethod
    def resolve_character(cls, query: Union[str, int]) -> Optional[Dict[str, Any]]:
        """
        Resolves a character by Character Name OR Character ID.
        Auto-fills in-game corporation and alliance details while rejecting non-character entities.
        """
        if not query:
            return None

        query_str = str(query).strip()
        is_id = query_str.isdigit()
        char_id = int(query_str) if is_id else None

        if char_id:
            if 10000000 <= char_id < 70000000:
                return None
            try:
                from eve_sde.models import ItemType, SolarSystem

                if ItemType.objects.filter(id=char_id).exists():
                    return None
                if SolarSystem.objects.filter(id=char_id).exists():
                    return None
                if (
                    HostileAlliance.objects.filter(alliance_id=char_id).exists()
                    or EveAllianceInfo.objects.filter(alliance_id=char_id).exists()
                ):
                    return None
                if (
                    HostileCorporation.objects.filter(corporation_id=char_id).exists()
                    or EveCorporationInfo.objects.filter(corporation_id=char_id).exists()
                ):
                    return None
            except Exception:
                pass

        # 1. Check internal Alliance Auth EveCharacter model
        eve_char = None
        if char_id:
            eve_char = EveCharacter.objects.filter(character_id=char_id).first()
        else:
            eve_char = EveCharacter.objects.filter(character_name__iexact=query_str).first()
            if not eve_char:
                eve_char = EveCharacter.objects.filter(character_name__icontains=query_str).first()

        if eve_char:
            return {
                "character_id": eve_char.character_id,
                "character_name": eve_char.character_name,
                "corporation_id": eve_char.corporation_id,
                "corporation_name": eve_char.corporation_name or "",
                "corporation_ticker": eve_char.corporation_ticker or "",
                "alliance_id": eve_char.alliance_id,
                "alliance_name": eve_char.alliance_name or "",
                "alliance_ticker": eve_char.alliance_ticker or "",
                "source": "internal_evecharacter",
            }

        # 2. Check internal HostilePilotDossier model
        dossier = None
        if char_id:
            dossier = HostilePilotDossier.objects.filter(character_id=char_id).first()
        else:
            dossier = HostilePilotDossier.objects.filter(character_name__iexact=query_str).first()

        if dossier:
            try:
                from eve_sde.models import ItemType, SolarSystem

                if (
                    ItemType.objects.filter(id=dossier.character_id).exists()
                    or SolarSystem.objects.filter(id=dossier.character_id).exists()
                    or HostileAlliance.objects.filter(alliance_id=dossier.character_id).exists()
                    or HostileCorporation.objects.filter(corporation_id=dossier.character_id).exists()
                    or dossier.character_name.startswith(
                        ("Stargate (", "Moon ", "Planet ", "Sun ", "Asteroid ", "Station ", "Customs Office")
                    )
                ):
                    return None
            except Exception:
                pass

            return {
                "character_id": dossier.character_id,
                "character_name": dossier.character_name,
                "corporation_id": None,
                "corporation_name": dossier.corporation_name or "",
                "corporation_ticker": "",
                "alliance_id": None,
                "alliance_name": dossier.alliance_name or "",
                "alliance_ticker": "",
                "associated_alliance_name": dossier.associated_alliance_name or "",
                "is_out_of_corp": dossier.is_out_of_corp,
                "source": "internal_dossier",
            }

        # 3. Fallback to ESI API
        try:
            resolved_id = char_id
            resolved_name = query_str if not is_id else None

            # If searching by name, resolve character ID first
            if not resolved_id and resolved_name:
                ids_res = esi_client.post_universe_ids([resolved_name])
                chars = _get_val(ids_res, "characters", [])
                if chars:
                    resolved_id = _get_val(chars[0], "id")
                    resolved_name = _get_val(chars[0], "name", resolved_name)
                else:
                    return None

            if resolved_id:
                # Resolve name & category if not verified
                names_res = esi_client.post_universe_names([resolved_id])
                if names_res and isinstance(names_res, (list, tuple)):
                    cat = _get_val(names_res[0], "category")
                    if cat and cat != "character":
                        return None
                    if not resolved_name:
                        resolved_name = _get_val(names_res[0], "name", f"Character {resolved_id}")

                # Fetch character affiliation (corp, alliance)
                corp_id = None
                corp_name = ""
                alliance_id = None
                alliance_name = ""

                affil_res = esi_client.post_characters_affiliation([resolved_id])
                if affil_res and isinstance(affil_res, (list, tuple)):
                    affil = affil_res[0]
                    corp_id = _get_val(affil, "corporation_id")
                    alliance_id = _get_val(affil, "alliance_id")

                # Resolve corp & alliance names
                lookup_ids = [i for i in [corp_id, alliance_id] if i]
                if lookup_ids:
                    names_map = {
                        _get_val(n, "id"): _get_val(n, "name")
                        for n in esi_client.post_universe_names(lookup_ids)
                    }
                    if corp_id:
                        corp_name = names_map.get(corp_id, "")
                    if alliance_id:
                        alliance_name = names_map.get(alliance_id, "")

                return {
                    "character_id": resolved_id,
                    "character_name": resolved_name or f"Character {resolved_id}",
                    "corporation_id": corp_id,
                    "corporation_name": corp_name,
                    "corporation_ticker": "",
                    "alliance_id": alliance_id,
                    "alliance_name": alliance_name,
                    "alliance_ticker": "",
                    "source": "esi",
                }
        except Exception as exc:
            logger.warning("Failed to resolve character '%s' via ESI: %s", query_str, exc)

        return None

    @classmethod
    def resolve_bulk_characters(
        cls, queries: List[Union[str, int]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Bulk resolves a collection of character names or IDs with maximum performance.
        Checks internal DB (EveCharacter, HostilePilotDossier) first, then calls ESI
        post_universe_ids, post_characters_affiliation, and post_universe_names in bulk batches.
        Returns a dict mapping lower-case character name / ID string -> resolution dict.
        """
        if not queries:
            return {}

        results: Dict[str, Dict[str, Any]] = {}
        clean_queries = list(dict.fromkeys(str(q).strip() for q in queries if str(q).strip()))
        if not clean_queries:
            return {}

        name_queries = [q for q in clean_queries if not q.isdigit()]
        id_queries = [int(q) for q in clean_queries if q.isdigit()]

        # 1. Query internal EveCharacter
        if name_queries:
            for ec in EveCharacter.objects.filter(character_name__in=name_queries):
                entry = {
                    "character_id": ec.character_id,
                    "character_name": ec.character_name,
                    "corporation_id": ec.corporation_id,
                    "corporation_name": ec.corporation_name or "",
                    "corporation_ticker": ec.corporation_ticker or "",
                    "alliance_id": ec.alliance_id,
                    "alliance_name": ec.alliance_name or "",
                    "alliance_ticker": ec.alliance_ticker or "",
                    "associated_alliance_name": "",
                    "is_out_of_corp": False,
                    "source": "internal_evecharacter",
                }
                results[ec.character_name.lower()] = entry
                results[str(ec.character_id)] = entry

        if id_queries:
            for ec in EveCharacter.objects.filter(character_id__in=id_queries):
                entry = {
                    "character_id": ec.character_id,
                    "character_name": ec.character_name,
                    "corporation_id": ec.corporation_id,
                    "corporation_name": ec.corporation_name or "",
                    "corporation_ticker": ec.corporation_ticker or "",
                    "alliance_id": ec.alliance_id,
                    "alliance_name": ec.alliance_name or "",
                    "alliance_ticker": ec.alliance_ticker or "",
                    "associated_alliance_name": "",
                    "is_out_of_corp": False,
                    "source": "internal_evecharacter",
                }
                results[ec.character_name.lower()] = entry
                results[str(ec.character_id)] = entry

        # 2. Query internal HostilePilotDossier
        unresolved_names = [q for q in name_queries if q.lower() not in results]
        unresolved_ids = [q for q in id_queries if str(q) not in results]

        if unresolved_names:
            for d in HostilePilotDossier.objects.filter(character_name__in=unresolved_names):
                entry = {
                    "character_id": d.character_id,
                    "character_name": d.character_name,
                    "corporation_id": None,
                    "corporation_name": d.corporation_name or "",
                    "corporation_ticker": "",
                    "alliance_id": None,
                    "alliance_name": d.alliance_name or "",
                    "alliance_ticker": "",
                    "associated_alliance_name": d.associated_alliance_name or "",
                    "is_out_of_corp": d.is_out_of_corp,
                    "source": "internal_dossier",
                }
                results[d.character_name.lower()] = entry
                if d.character_id:
                    results[str(d.character_id)] = entry

        if unresolved_ids:
            for d in HostilePilotDossier.objects.filter(character_id__in=unresolved_ids):
                entry = {
                    "character_id": d.character_id,
                    "character_name": d.character_name,
                    "corporation_id": None,
                    "corporation_name": d.corporation_name or "",
                    "corporation_ticker": "",
                    "alliance_id": None,
                    "alliance_name": d.alliance_name or "",
                    "alliance_ticker": "",
                    "associated_alliance_name": d.associated_alliance_name or "",
                    "is_out_of_corp": d.is_out_of_corp,
                    "source": "internal_dossier",
                }
                results[d.character_name.lower()] = entry
                if d.character_id:
                    results[str(d.character_id)] = entry

        # 3. Query ESI post_universe_ids for remaining names
        still_unresolved_names = [q for q in name_queries if q.lower() not in results]
        if still_unresolved_names:
            try:
                ids_res = esi_client.post_universe_ids(still_unresolved_names)
                chars = _get_val(ids_res, "characters", [])
                if isinstance(chars, list):
                    for c in chars:
                        cid = _get_val(c, "id")
                        cname = _get_val(c, "name")
                        if cid and cname:
                            entry = {
                                "character_id": cid,
                                "character_name": cname,
                                "corporation_id": None,
                                "corporation_name": "",
                                "corporation_ticker": "",
                                "alliance_id": None,
                                "alliance_name": "",
                                "alliance_ticker": "",
                                "associated_alliance_name": "",
                                "is_out_of_corp": False,
                                "source": "esi",
                            }
                            results[cname.lower()] = entry
                            results[str(cid)] = entry
            except Exception as exc:
                logger.warning("Failed bulk ESI universe IDs lookup: %s", exc)

        # 4. For all resolved characters missing corporation/alliance affiliations:
        char_ids_to_affil = set()
        for entry in results.values():
            if entry.get("character_id") and not entry.get("corporation_name"):
                char_ids_to_affil.add(entry["character_id"])

        if char_ids_to_affil:
            try:
                affil_list = esi_client.post_characters_affiliation(list(char_ids_to_affil))
                if isinstance(affil_list, list):
                    corp_ids_to_resolve = set()
                    alliance_ids_to_resolve = set()
                    char_affil_map = {}

                    for affil in affil_list:
                        cid = _get_val(affil, "character_id")
                        corpid = _get_val(affil, "corporation_id")
                        allid = _get_val(affil, "alliance_id")
                        if cid:
                            char_affil_map[cid] = (corpid, allid)
                            if corpid:
                                corp_ids_to_resolve.add(corpid)
                            if allid:
                                alliance_ids_to_resolve.add(allid)

                    # Resolve corp and alliance names
                    names_map = {}
                    all_ids_to_resolve = list(corp_ids_to_resolve.union(alliance_ids_to_resolve))
                    if all_ids_to_resolve:
                        # Check internal DB first
                        for corpid in list(corp_ids_to_resolve):
                            h_corp = HostileCorporation.objects.filter(corporation_id=corpid).first()
                            if h_corp:
                                names_map[corpid] = (h_corp.corporation_name, h_corp.ticker or "")
                            else:
                                eve_c = EveCorporationInfo.objects.filter(corporation_id=corpid).first()
                                if eve_c:
                                    names_map[corpid] = (eve_c.corporation_name, eve_c.corporation_ticker or "")

                        for allid in list(alliance_ids_to_resolve):
                            h_all = HostileAlliance.objects.filter(alliance_id=allid).first()
                            if h_all:
                                names_map[allid] = (h_all.alliance_name, h_all.ticker or "")
                            else:
                                eve_a = EveAllianceInfo.objects.filter(alliance_id=allid).first()
                                if eve_a:
                                    names_map[allid] = (eve_a.alliance_name, eve_a.alliance_ticker or "")

                        missing_ids = [i for i in all_ids_to_resolve if i not in names_map]
                        if missing_ids:
                            names_res = esi_client.post_universe_names(missing_ids)
                            if isinstance(names_res, list):
                                for n in names_res:
                                    nid = _get_val(n, "id")
                                    nname = _get_val(n, "name")
                                    if nid and nname:
                                        names_map[nid] = (nname, "")

                    # Apply affiliations back to results
                    for entry in results.values():
                        cid = entry.get("character_id")
                        if cid and cid in char_affil_map:
                            corpid, allid = char_affil_map[cid]
                            entry["corporation_id"] = corpid
                            entry["alliance_id"] = allid
                            if corpid and corpid in names_map:
                                entry["corporation_name"] = names_map[corpid][0]
                                if names_map[corpid][1]:
                                    entry["corporation_ticker"] = names_map[corpid][1]
                            if allid and allid in names_map:
                                entry["alliance_name"] = names_map[allid][0]
                                if names_map[allid][1]:
                                    entry["alliance_ticker"] = names_map[allid][1]
            except Exception as exc:
                logger.warning("Failed bulk character affiliation resolution: %s", exc)

        return results

    @classmethod
    def resolve_alliance(cls, query: Union[str, int]) -> Optional[Dict[str, Any]]:
        """
        Resolves an alliance by Alliance Name OR Alliance ID.
        """
        if not query:
            return None

        query_str = str(query).strip()
        is_id = query_str.isdigit()
        alliance_id = int(query_str) if is_id else None

        # 1. Check internal HostileAlliance model
        hostile_alliance = None
        if alliance_id:
            hostile_alliance = HostileAlliance.objects.filter(alliance_id=alliance_id).first()
        else:
            hostile_alliance = HostileAlliance.objects.filter(alliance_name__iexact=query_str).first()
            if not hostile_alliance:
                hostile_alliance = HostileAlliance.objects.filter(alliance_name__icontains=query_str).first()

        if hostile_alliance:
            return {
                "alliance_id": hostile_alliance.alliance_id,
                "alliance_name": hostile_alliance.alliance_name,
                "ticker": hostile_alliance.ticker or "",
                "source": "internal_hostile_alliance",
            }

        # 2. Check internal EveAllianceInfo model
        eve_alliance = None
        if alliance_id:
            eve_alliance = EveAllianceInfo.objects.filter(alliance_id=alliance_id).first()
        else:
            eve_alliance = EveAllianceInfo.objects.filter(alliance_name__iexact=query_str).first()
            if not eve_alliance:
                eve_alliance = EveAllianceInfo.objects.filter(alliance_name__icontains=query_str).first()

        if eve_alliance:
            return {
                "alliance_id": eve_alliance.alliance_id,
                "alliance_name": eve_alliance.alliance_name,
                "ticker": eve_alliance.alliance_ticker or "",
                "source": "internal_eve_alliance",
            }

        # 3. Check internal EveCharacter alliance records
        eve_char = None
        if alliance_id:
            eve_char = EveCharacter.objects.filter(alliance_id=alliance_id).first()
        else:
            eve_char = EveCharacter.objects.filter(alliance_name__iexact=query_str).first()

        if eve_char and eve_char.alliance_id:
            return {
                "alliance_id": eve_char.alliance_id,
                "alliance_name": eve_char.alliance_name or query_str,
                "ticker": eve_char.alliance_ticker or "",
                "source": "internal_eve_character",
            }

        # 4. Fallback to ESI API
        try:
            resolved_id = alliance_id
            resolved_name = query_str if not is_id else None

            if not resolved_id and resolved_name:
                ids_res = esi_client.post_universe_ids([resolved_name])
                alliances = _get_val(ids_res, "alliances", [])
                if alliances:
                    resolved_id = _get_val(alliances[0], "id")
                    resolved_name = _get_val(alliances[0], "name", resolved_name)

            if resolved_id:
                if not resolved_name:
                    names_res = esi_client.post_universe_names([resolved_id])
                    if names_res and isinstance(names_res, (list, tuple)):
                        resolved_name = _get_val(names_res[0], "name", f"Alliance {resolved_id}")

                return {
                    "alliance_id": resolved_id,
                    "alliance_name": resolved_name or f"Alliance {resolved_id}",
                    "ticker": "",
                    "source": "esi",
                }
        except Exception as exc:
            logger.warning("Failed to resolve alliance '%s' via ESI: %s", query_str, exc)

        return None

    @classmethod
    def resolve_corporation(cls, query: Union[str, int]) -> Optional[Dict[str, Any]]:
        """
        Resolves a corporation by Corporation Name OR Corporation ID.
        """
        if not query:
            return None

        query_str = str(query).strip()
        is_id = query_str.isdigit()
        corp_id = int(query_str) if is_id else None

        # 1. Check internal HostileCorporation model
        hostile_corp = None
        if corp_id:
            hostile_corp = HostileCorporation.objects.filter(corporation_id=corp_id).first()
        else:
            hostile_corp = HostileCorporation.objects.filter(corporation_name__iexact=query_str).first()

        if hostile_corp:
            return {
                "corporation_id": hostile_corp.corporation_id,
                "corporation_name": hostile_corp.corporation_name,
                "ticker": hostile_corp.ticker or "",
                "source": "internal_hostile_corp",
            }

        # 2. Check internal EveCorporationInfo model
        eve_corp = None
        if corp_id:
            eve_corp = EveCorporationInfo.objects.filter(corporation_id=corp_id).first()
        else:
            eve_corp = EveCorporationInfo.objects.filter(corporation_name__iexact=query_str).first()

        if eve_corp:
            return {
                "corporation_id": eve_corp.corporation_id,
                "corporation_name": eve_corp.corporation_name,
                "ticker": eve_corp.corporation_ticker or "",
                "source": "internal_eve_corp",
            }

        # 3. Fallback to ESI
        try:
            resolved_id = corp_id
            resolved_name = query_str if not is_id else None

            if not resolved_id and resolved_name:
                ids_res = esi_client.post_universe_ids([resolved_name])
                corps = _get_val(ids_res, "corporations", [])
                if corps:
                    resolved_id = _get_val(corps[0], "id")
                    resolved_name = _get_val(corps[0], "name", resolved_name)

            if resolved_id:
                if not resolved_name:
                    names_res = esi_client.post_universe_names([resolved_id])
                    if names_res and isinstance(names_res, (list, tuple)):
                        resolved_name = _get_val(names_res[0], "name", f"Corporation {resolved_id}")

                return {
                    "corporation_id": resolved_id,
                    "corporation_name": resolved_name or f"Corporation {resolved_id}",
                    "ticker": "",
                    "source": "esi",
                }
        except Exception as exc:
            logger.warning("Failed to resolve corporation '%s' via ESI: %s", query_str, exc)

        return None

    @classmethod
    def search_characters(cls, query: str, limit: int = 15) -> List[Dict[str, Any]]:
        """
        Fast autocomplete search for characters by name or ID.
        """
        if not query or not query.strip():
            return []

        q = query.strip()
        results = []
        seen_ids = set()

        # 1. Search internal EveCharacter
        char_qs = EveCharacter.objects.filter(character_name__icontains=q)[:limit]
        for c in char_qs:
            seen_ids.add(c.character_id)
            results.append(
                {
                    "character_id": c.character_id,
                    "character_name": c.character_name,
                    "corporation_name": c.corporation_name or "",
                    "alliance_name": c.alliance_name or "",
                    "source": "internal",
                }
            )

        # 2. Search internal HostilePilotDossier
        if len(results) < limit:
            dossier_qs = HostilePilotDossier.objects.filter(character_name__icontains=q)[: limit - len(results)]
            for d in dossier_qs:
                if d.character_id not in seen_ids:
                    seen_ids.add(d.character_id)
                    results.append(
                        {
                            "character_id": d.character_id,
                            "character_name": d.character_name,
                            "corporation_name": d.corporation_name or "",
                            "alliance_name": d.alliance_name or "",
                            "associated_alliance_name": d.associated_alliance_name or "",
                            "source": "dossier",
                        }
                    )

        return results[:limit]

    @classmethod
    def search_alliances(cls, query: str, limit: int = 15) -> List[Dict[str, Any]]:
        """
        Fast autocomplete search for alliances by name, ticker, or ID.
        """
        if not query or not query.strip():
            return []

        q = query.strip()
        results = []
        seen_ids = set()

        # 1. Search HostileAlliance
        ha_qs = HostileAlliance.objects.filter(alliance_name__icontains=q)[:limit]
        for a in ha_qs:
            seen_ids.add(a.alliance_id)
            results.append(
                {
                    "alliance_id": a.alliance_id,
                    "alliance_name": a.alliance_name,
                    "ticker": a.ticker or "",
                    "source": "hostile_alliance",
                }
            )

        # 2. Search EveAllianceInfo
        if len(results) < limit:
            ea_qs = EveAllianceInfo.objects.filter(alliance_name__icontains=q)[: limit - len(results)]
            for ea in ea_qs:
                if ea.alliance_id not in seen_ids:
                    seen_ids.add(ea.alliance_id)
                    results.append(
                        {
                            "alliance_id": ea.alliance_id,
                            "alliance_name": ea.alliance_name,
                            "ticker": ea.alliance_ticker or "",
                            "source": "eve_alliance",
                        }
                    )

        return results[:limit]

    @classmethod
    def search_corporations(cls, query: str, limit: int = 15) -> List[Dict[str, Any]]:
        """
        Fast autocomplete search for corporations by name, ticker, or ID.
        """
        if not query or not query.strip():
            return []

        q = query.strip()
        results = []
        seen_ids = set()

        # 1. Search HostileCorporation
        hc_qs = HostileCorporation.objects.filter(
            models.Q(corporation_name__icontains=q) | models.Q(ticker__icontains=q)
        )[:limit]
        for c in hc_qs:
            seen_ids.add(c.corporation_id)
            results.append(
                {
                    "corporation_id": c.corporation_id,
                    "corporation_name": c.corporation_name,
                    "ticker": c.ticker or "",
                    "alliance_name": c.alliance.alliance_name if c.alliance else "",
                    "coalition": c.coalition or "",
                    "source": "hostile_corp",
                }
            )

        # 2. Search EveCorporationInfo
        if len(results) < limit:
            ec_qs = EveCorporationInfo.objects.filter(
                models.Q(corporation_name__icontains=q) | models.Q(corporation_ticker__icontains=q)
            )[: limit - len(results)]
            for ec in ec_qs:
                if ec.corporation_id not in seen_ids:
                    seen_ids.add(ec.corporation_id)
                    results.append(
                        {
                            "corporation_id": ec.corporation_id,
                            "corporation_name": ec.corporation_name,
                            "ticker": ec.corporation_ticker or "",
                            "alliance_name": "",
                            "coalition": "",
                            "source": "eve_corp",
                        }
                    )

        return results[:limit]

    @classmethod
    def get_or_create_alliance(cls, query: Union[str, int, HostileAlliance]) -> Optional[HostileAlliance]:
        """
        Finds or resolves and creates a HostileAlliance instance from a name, ID, or query.
        """
        if not query:
            return None
        if isinstance(query, HostileAlliance):
            return query

        query_str = str(query).strip()
        if not query_str:
            return None

        # Check existing HostileAlliance by ID or Name
        if query_str.isdigit():
            obj = HostileAlliance.objects.filter(alliance_id=int(query_str)).first()
            if obj:
                return obj
        else:
            obj = HostileAlliance.objects.filter(alliance_name__iexact=query_str).first()
            if obj:
                return obj
            obj = HostileAlliance.objects.filter(ticker__iexact=query_str).first()
            if obj:
                return obj

        # Attempt resolve
        resolved = cls.resolve_alliance(query_str)
        if resolved and resolved.get("alliance_id"):
            alliance_obj, _ = HostileAlliance.objects.get_or_create(
                alliance_id=resolved["alliance_id"],
                defaults={
                    "alliance_name": resolved["alliance_name"],
                    "ticker": resolved.get("ticker", ""),
                },
            )
            if resolved.get("ticker") and not alliance_obj.ticker:
                alliance_obj.ticker = resolved["ticker"]
                alliance_obj.save(update_fields=["ticker"])
            return alliance_obj

        # If unresolved but name provided, create custom untracked alliance
        import random
        synthetic_id = random.randint(2000000000, 2147483647)
        while HostileAlliance.objects.filter(alliance_id=synthetic_id).exists():
            synthetic_id = random.randint(2000000000, 2147483647)

        return HostileAlliance.objects.create(
            alliance_id=synthetic_id,
            alliance_name=query_str,
            ticker=query_str[:5].upper() if len(query_str) <= 5 else "",
        )

    @classmethod
    def get_or_create_corporation(
        cls, query: Union[str, int, HostileCorporation], alliance: Optional[HostileAlliance] = None
    ) -> Optional[HostileCorporation]:
        """
        Finds or resolves and creates a HostileCorporation instance from a name, ID, or query.
        """
        if not query:
            return None
        if isinstance(query, HostileCorporation):
            return query

        query_str = str(query).strip()
        if not query_str:
            return None

        # Check existing HostileCorporation by ID or Name
        if query_str.isdigit():
            obj = HostileCorporation.objects.filter(corporation_id=int(query_str)).first()
            if obj:
                return obj
        else:
            obj = HostileCorporation.objects.filter(corporation_name__iexact=query_str).first()
            if obj:
                return obj
            obj = HostileCorporation.objects.filter(ticker__iexact=query_str).first()
            if obj:
                return obj

        # Attempt resolve
        resolved = cls.resolve_corporation(query_str)
        if resolved and resolved.get("corporation_id"):
            corp_obj, _ = HostileCorporation.objects.get_or_create(
                corporation_id=resolved["corporation_id"],
                defaults={
                    "corporation_name": resolved["corporation_name"],
                    "ticker": resolved.get("ticker", ""),
                    "alliance": alliance,
                },
            )
            if alliance and not corp_obj.alliance:
                corp_obj.alliance = alliance
                corp_obj.save(update_fields=["alliance"])
            if resolved.get("ticker") and not corp_obj.ticker:
                corp_obj.ticker = resolved["ticker"]
                corp_obj.save(update_fields=["ticker"])
            return corp_obj

        # If unresolved but name provided, create custom untracked corporation
        import random
        synthetic_id = random.randint(2000000000, 2147483647)
        while HostileCorporation.objects.filter(corporation_id=synthetic_id).exists():
            synthetic_id = random.randint(2000000000, 2147483647)

        return HostileCorporation.objects.create(
            corporation_id=synthetic_id,
            corporation_name=query_str,
            ticker=query_str[:5].upper() if len(query_str) <= 5 else "",
            alliance=alliance,
        )

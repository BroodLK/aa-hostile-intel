"""
D-Scan Parser for non-ESI structure and grid intelligence ingestion
"""

# Standard Library
import re
from typing import Any, Dict, List, Optional

# Third Party
from eve_sde.models import ItemType, SolarSystem

KNOWN_STRUCTURE_KEYWORDS = {
    "keepstar",
    "fortizar",
    "astrahus",
    "sotiyo",
    "azbel",
    "raitaru",
    "tatara",
    "athanor",
    "ansiblex",
    "pharolux",
    "tenebrex",
    "metenox",
    "moon drill",
    "drill",
    "control tower",
    "starbase",
    "customs office",
    "pos",
    "citadel",
    "engineering complex",
    "refinery",
}


class DScanParser:
    """Parses standard tab-separated or column-formatted D-Scan clipboard data"""

    @classmethod
    def is_structure_type(cls, type_name: str, item_type: Optional[ItemType] = None) -> bool:
        """Determines if an item type represents a deployable structure or starbase"""
        if not type_name:
            return False

        name_lower = type_name.lower()
        if any(kw in name_lower for kw in KNOWN_STRUCTURE_KEYWORDS):
            return True

        if item_type and hasattr(item_type, "group") and item_type.group:
            group_name = item_type.group.name.lower()
            if any(kw in group_name for kw in ["citadel", "structure", "starbase", "engineering complex", "refinery", "jump gate", "cyno", "moon drill", "drill"]):
                return True

        return False

    @classmethod
    def extract_ticker(cls, name: str) -> Optional[str]:
        """Extracts owner ticker bracket e.g. '1DQ1-A 1-HQ [CONDI]' -> 'CONDI'"""
        match = re.search(r"\[([A-Za-z0-9\-_]{1,10})\]", name)
        if match:
            return match.group(1)
        return None

    @classmethod
    def infer_solar_system_from_name(cls, name: str) -> Optional[SolarSystem]:
        """
        Infers a SolarSystem from structure or item naming patterns in D-Scan or clipboard text.
        Handles common EVE naming conventions such as:
        - '<System> - <Structure Name>' (e.g. 'F9-FUV - ICS Ackaroth We Will Miss You', '1DQ1-A - Keepstar')
        - '[TICKER] <System> - ...' or '<TICKER> <System> - ...'
        - 'Stargate (<System>)' or 'POCO (<System> VI)' or 'Customs Office (<System>)'
        - Celestial names: '<System> IV - Moon 4', '<System> I'
        - First token matching a known solar system name
        """
        if not name or not name.strip():
            return None

        cleaned_name = name.strip()

        # Pattern 1: Strip leading brackets/tags like [CONDI] or <FIGL> or {ABC}
        stripped_tags = re.sub(r"^[\[<{][^\]>}]+[\]>}]\s*", "", cleaned_name)

        # Pattern 2: Name starting with '<System> - ...' or '<System> – ...' or '<System> — ...'
        dash_match = re.match(r"^([A-Za-z0-9\-]{2,12})\s*[-–—]\s+", stripped_tags)
        if dash_match:
            sys = SolarSystem.objects.filter(name__iexact=dash_match.group(1)).first()
            if sys:
                return sys

        # Pattern 3: Celestial / Moon / Planet / Stargate / POCO naming
        paren_match = re.search(
            r"(?:Stargate|POCO|Customs\s+Office|Planet|Moon)\s*\(\s*([A-Za-z0-9\-]{2,12})\b",
            cleaned_name,
            re.IGNORECASE,
        )
        if paren_match:
            sys = SolarSystem.objects.filter(name__iexact=paren_match.group(1)).first()
            if sys:
                return sys

        # Pattern 4: System with celestial roman numeral, e.g. "Jita IV - Moon 4", "F9-FUV I"
        celestial_match = re.match(
            r"^([A-Za-z0-9\-]{2,12})\s+(?:I|II|III|IV|V|VI|VII|VIII|IX|X|\d+)\b",
            stripped_tags,
            re.IGNORECASE,
        )
        if celestial_match:
            sys = SolarSystem.objects.filter(name__iexact=celestial_match.group(1)).first()
            if sys:
                return sys

        # Pattern 5: First token/word exact match against SolarSystem table
        first_token = stripped_tags.split()[0].strip(",.:-–—_[]<>{}")
        if first_token and len(first_token) >= 2:
            sys = SolarSystem.objects.filter(name__iexact=first_token).first()
            if sys:
                return sys

        # Pattern 6: Direct match of the entire string (if a system name is pasted alone)
        sys = SolarSystem.objects.filter(name__iexact=cleaned_name).first()
        if sys:
            return sys

        return None

    @classmethod
    def parse(cls, dscan_text: str, default_system: Optional[SolarSystem] = None) -> Dict[str, Any]:
        """Parses D-Scan text and categorizes structures, ships, and solar system context"""
        lines = [line.strip() for line in dscan_text.strip().splitlines() if line.strip()]

        structures: List[Dict[str, Any]] = []
        all_items: List[Dict[str, Any]] = []
        ship_counts: Dict[str, int] = {}
        detected_system: Optional[SolarSystem] = default_system

        for line in lines:
            parts = [p.strip() for p in line.split("\t") if p.strip()]
            if not parts:
                # Fallback to multiple spaces
                parts = [p.strip() for p in re.split(r"\s{2,}", line) if p.strip()]

            if not parts:
                continue

            name = ""
            type_name = ""
            distance = ""

            if len(parts) >= 4:
                # Format: <type_id> <name> <type_name> <distance>
                if parts[0].isdigit():
                    name = parts[1]
                    type_name = parts[2]
                    distance = parts[3]
                else:
                    name = parts[0]
                    type_name = parts[1]
                    distance = parts[2]
            elif len(parts) == 3:
                # Format: <name> <type_name> <distance> or <type_name> <name> <distance>
                cand_type = ItemType.objects.filter(name__iexact=parts[0]).first()
                if cand_type:
                    type_name = parts[0]
                    name = parts[1]
                else:
                    name = parts[0]
                    type_name = parts[1]
                distance = parts[2]
            elif len(parts) == 2:
                name = parts[0]
                type_name = parts[1]
            else:
                # Single column line
                name = parts[0]
                type_name = parts[0]

            item_type = ItemType.objects.filter(name__iexact=type_name).first()
            if not item_type and type_name:
                item_type = ItemType.objects.filter(name__icontains=type_name).first()

            # Attempt to infer solar system name from structure/celestial name
            if not detected_system and name:
                detected_system = cls.infer_solar_system_from_name(name)

            ticker = cls.extract_ticker(name)
            is_structure = cls.is_structure_type(type_name, item_type)

            entry = {
                "name": name,
                "type_name": type_name,
                "item_type": item_type,
                "distance": distance,
                "owner_ticker": ticker,
                "is_structure": is_structure,
            }
            all_items.append(entry)

            if is_structure:
                structures.append(
                    {
                        "name": name,
                        "structure_type_name": type_name,
                        "structure_type": item_type,
                        "solar_system": detected_system,
                        "distance": distance,
                        "owner_ticker": ticker or "",
                        "location_details": name if ("Planet" in name or "Moon" in name or "Gate" in name) else "",
                    }
                )
            else:
                if type_name:
                    ship_counts[type_name] = ship_counts.get(type_name, 0) + 1

        if detected_system:
            for s in structures:
                if not s.get("solar_system"):
                    s["solar_system"] = detected_system

        return {
            "structures": structures,
            "all_items": all_items,
            "ship_counts": ship_counts,
            "solar_system": detected_system,
            "total_items": len(all_items),
        }

"""
Structure Show-Info / Inspect window clipboard parser
"""

# Standard Library
import re
from typing import Any, Dict, Optional

# Third Party
from eve_sde.models import ItemType, SolarSystem

# AA Hostile Intel
from hostile.parsers.dscan import DScanParser
from hostile.parsers.eft import EFTParser, StructureFittingParser


class StructureHackParser:
    """
    Parses data analyzer hack outputs for structure defense timers.
    Format:
        F9-FUV - Darkside X
        Hour (+- 3 hrs):	02:00
    """

    @classmethod
    def is_structure_hack(cls, text: str) -> bool:
        """Determines if the text contains a Data Analyzer structure defense timer hack"""
        if not text:
            return False
        clean = text.strip()
        return bool(
            re.search(
                r"^Hour\s*(?:\([^)]*\))?\s*[:=]\t*\s*\d{1,2}(?::\d{2})?",
                clean,
                re.IGNORECASE | re.MULTILINE,
            )
        )

    @classmethod
    def parse(cls, text: str, default_system: Optional[SolarSystem] = None) -> Dict[str, Any]:
        """Extracts structure name, inferred solar system, vulnerability hour, and jitter window"""
        lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
        structure_name = ""
        type_name = ""
        solar_system = default_system
        owner_ticker = ""
        vulnerability_hour = None
        vulnerability_window = ""
        notes = []

        for line in lines:
            # Check for vulnerability hour hack line: e.g. "Hour (+- 3 hrs):	02:00"
            hour_match = re.search(
                r"^Hour\s*(?:\(([^)]*)\))?\s*[:=]\t*\s*(\d{1,2})(?::(\d{2}))?",
                line,
                re.IGNORECASE,
            )
            if hour_match:
                jitter_part = (hour_match.group(1) or "+- 3 hrs").strip()
                h = int(hour_match.group(2))
                m_str = hour_match.group(3) or "00"
                if 0 <= h <= 23:
                    vulnerability_hour = h
                    vulnerability_window = f"{h:02d}:{m_str} ({jitter_part})"
                continue

            # First non-hour line is treated as structure name / header
            if not structure_name:
                # Check for bracketed header: [Keepstar] F9-FUV - Darkside X
                header_bracket = re.match(r"^\[(.*?)\]\s*(.*)$", line)
                if header_bracket:
                    cand_type = header_bracket.group(1).strip()
                    cand_name = header_bracket.group(2).strip()
                    if cand_name:
                        structure_name = cand_name
                        type_name = cand_type
                    else:
                        structure_name = cand_type
                else:
                    prefix_match = re.match(r"^(?:Structure|Name|Target)\s*[:=]\s*(.*)$", line, re.I)
                    if prefix_match:
                        structure_name = prefix_match.group(1).strip()
                    else:
                        structure_name = line

                if not solar_system:
                    solar_system = DScanParser.infer_solar_system_from_name(structure_name)

                ticker_cand = DScanParser.extract_ticker(structure_name)
                if ticker_cand:
                    owner_ticker = ticker_cand
            else:
                kv_match = re.match(r"^([A-Za-z\s]+)\s*[:=]\s*(.*)$", line)
                if kv_match:
                    k = kv_match.group(1).strip().lower()
                    v = kv_match.group(2).strip()
                    if k in ["system", "solar system", "location"]:
                        sys_found = SolarSystem.objects.filter(name__iexact=v).first()
                        if sys_found:
                            solar_system = sys_found
                    elif k in ["owner", "alliance"]:
                        notes.append(f"Owner: {v}")
                    elif k in ["type", "structure", "structure type"]:
                        type_name = v
                    else:
                        notes.append(line)
                else:
                    notes.append(line)

        if not solar_system and structure_name:
            solar_system = DScanParser.infer_solar_system_from_name(structure_name)

        structure_type = None
        if type_name:
            structure_type = ItemType.objects.filter(name__iexact=type_name).first()
            if not structure_type:
                structure_type = ItemType.objects.filter(name__icontains=type_name).first()

        if not structure_name:
            if solar_system:
                structure_name = f"Hostile Structure in {solar_system.name}"
            else:
                structure_name = "Hostile Structure"

        return {
            "name": structure_name,
            "structure_name": structure_name,
            "structure_type_name": type_name,
            "structure_type": structure_type,
            "solar_system": solar_system,
            "owner_ticker": owner_ticker,
            "vulnerability_hour": vulnerability_hour,
            "vulnerability_window": vulnerability_window or (f"{vulnerability_hour:02d}:00 (+- 3 hrs)" if vulnerability_hour is not None else ""),
            "notes": "\n".join(notes) if notes else "Hacked with Data Analyzer (Scanned Defense Hour)",
            "source": "DATA_ANALYZER_HACK",
        }


class StructureShowInfoParser:
    """Parses clipboard text copied from EVE Online structure info and profile windows"""

    @classmethod
    def parse(cls, text: str) -> Dict[str, Any]:
        """Extracts structure metadata, owner details, core status, and fitting lines from text"""
        lines = [line.strip() for line in text.strip().splitlines() if line.strip()]

        structure_name = ""
        type_name = ""
        system_name = ""
        corp_name = ""
        alliance_name = ""
        owner_ticker = ""
        location_details = ""
        core_status = "UNKNOWN"
        state = "ONLINE"
        vulnerability_window = ""
        vulnerability_hour = None
        notes_lines = []
        fitting_lines = []

        is_in_fitting_section = False

        for i, line in enumerate(lines):
            # Header check: e.g. "[Keepstar] 1DQ1-A 1-HQ - Imperial Palace" or "[Keepstar, 1DQ1-A]"
            header_bracket = re.match(r"^\[(.*?)\]\s*(.*)$", line)
            if header_bracket and i == 0:
                type_cand = header_bracket.group(1).strip()
                rest = header_bracket.group(2).strip()
                if "," in type_cand:
                    type_cand, fit_part = [p.strip() for p in type_cand.split(",", 1)]
                    if not rest:
                        rest = fit_part
                type_name = type_cand
                structure_name = rest
                continue

            # Core detection
            if re.search(r"core\s*:\s*(installed|fitted|yes|true)", line, re.I) or "quantum core: installed" in line.lower():
                core_status = "FITTED"
                continue
            elif re.search(r"core\s*:\s*(missing|none|unfitted|no|false)", line, re.I) or "quantum core: unfitted" in line.lower():
                core_status = "UNFITTED"
                continue

            # State detection
            if re.search(r"state\s*:\s*(\w+)", line, re.I):
                state_match = re.search(r"state\s*:\s*(\w+)", line, re.I).group(1).upper()
                if state_match in ["ANCHORING", "UNANCHORING", "ARMOR", "HULL", "ONLINE", "REINFORCED", "DESTROYED"]:
                    state = state_match
                continue

            # Vulnerability / Reinforcement window detection
            vuln_match = re.search(
                r"^(?:Hour\s*(?:\([^)]*\))?|vulnerability|reinforcement|reinforce)\s*(?:window|hour|time)?\s*[:=]\t*\s*(.+)$",
                line,
                re.I,
            )
            if vuln_match:
                vulnerability_window = vuln_match.group(1).strip()
                hour_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\b", vulnerability_window)
                if hour_match:
                    try:
                        h = int(hour_match.group(1))
                        if 0 <= h <= 23:
                            vulnerability_hour = h
                    except ValueError:
                        pass
                continue

            # Key-value lines
            kv_match = re.match(r"^([A-Za-z\s]+)\s*[:=]\s*(.*)$", line)
            if kv_match:
                key = kv_match.group(1).strip().lower()
                val = kv_match.group(2).strip()

                if key in ["owner", "alliance"]:
                    alliance_name = val
                    ticker_cand = DScanParser.extract_ticker(val)
                    if ticker_cand:
                        owner_ticker = ticker_cand
                elif key in ["corporation", "corp"]:
                    corp_name = val
                    ticker_cand = DScanParser.extract_ticker(val)
                    if ticker_cand and not owner_ticker:
                        owner_ticker = ticker_cand
                elif key in ["solar system", "system", "location"]:
                    system_name = val.split("-")[0].strip()
                    if "-" in val:
                        location_details = val
                elif key in ["structure", "type", "structure type"]:
                    type_name = val
                elif key in ["name", "structure name"]:
                    structure_name = val
                elif key in ["fitting", "modules", "fit"]:
                    is_in_fitting_section = True
                continue

            if is_in_fitting_section or line.startswith("Standup") or "slot" in line.lower():
                fitting_lines.append(line)
            else:
                # If first line and structure_name is empty, treat as structure name
                if i == 0 and not structure_name:
                    structure_name = line
                    ticker_cand = DScanParser.extract_ticker(line)
                    if ticker_cand:
                        owner_ticker = ticker_cand
                elif i == 1 and not type_name and DScanParser.is_structure_type(line):
                    type_name = line
                else:
                    notes_lines.append(line)

        # SDE lookups
        structure_type = None
        if type_name:
            structure_type = ItemType.objects.filter(name__iexact=type_name).first()
            if not structure_type:
                structure_type = ItemType.objects.filter(name__icontains=type_name).first()

        solar_system = None
        if system_name:
            solar_system = SolarSystem.objects.filter(name__iexact=system_name).first()
            if not solar_system:
                solar_system = SolarSystem.objects.filter(name__icontains=system_name).first()

        if not solar_system and structure_name:
            solar_system = DScanParser.infer_solar_system_from_name(structure_name)
            if not solar_system:
                # Check if first word is system name e.g. "1DQ1-A 1-HQ"
                words = structure_name.split()
                if words:
                    solar_system = SolarSystem.objects.filter(name__iexact=words[0]).first()

        if not owner_ticker and structure_name:
            owner_ticker = DScanParser.extract_ticker(structure_name) or ""

        fitting_data = None
        if fitting_lines:
            fit_text = "\n".join(fitting_lines)
            fitting_data = StructureFittingParser.parse(fit_text)
            if structure_type and not fitting_data.get("hull_item_type"):
                fitting_data["hull_item_type"] = structure_type
                fitting_data["hull_type_name"] = type_name

        return {
            "name": structure_name or (f"{type_name} in {system_name}" if type_name else "Hostile Structure"),
            "structure_type_name": type_name,
            "structure_type": structure_type,
            "solar_system": solar_system,
            "corporation_name": corp_name,
            "alliance_name": alliance_name,
            "owner_ticker": owner_ticker,
            "location_details": location_details,
            "core_status": core_status,
            "state": state,
            "vulnerability_window": vulnerability_window,
            "vulnerability_hour": vulnerability_hour,
            "notes": "\n".join(notes_lines),
            "fitting_lines": fitting_lines,
            "fitting_data": fitting_data,
        }

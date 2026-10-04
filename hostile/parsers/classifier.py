"""
Classifier and unified ingestion router for raw hostile intelligence text
"""

# Standard Library
import re
from typing import Any, Dict, Optional

# Third Party
from eve_sde.models import SolarSystem

# AA Hostile Intel
from hostile.parsers.dscan import DScanParser
from hostile.parsers.eft import EFTParser, StructureFittingParser
from hostile.parsers.local import LocalThreatParser
from hostile.parsers.showinfo import StructureHackParser, StructureShowInfoParser


class IntelFormat:
    DSCAN = "dscan"
    SHOWINFO = "showinfo"
    EFT = "eft"
    STRUCTURE_FIT = "structure_fit"
    STRUCTURE_HACK = "structure_hack"
    LOCAL = "local"
    OBSERVATION = "observation"


def classify_intel_text(text: str) -> str:
    """Auto-detects the format of raw intelligence clipboard text"""
    clean_text = text.strip()
    if not clean_text:
        return IntelFormat.OBSERVATION

    lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
    if not lines:
        return IntelFormat.OBSERVATION

    # Check for EFT signature: starts with [Hull, Name] and contains modules
    first_line = lines[0]
    eft_header_match = re.match(r"^\[([A-Za-z0-9\s\-'\(\)]+),\s*([A-Za-z0-9\s\-'\(\)]+)\]$", first_line)
    if eft_header_match and not StructureFittingParser.match_slot_header(first_line):
        return IntelFormat.EFT

    # Check for Structure Fitting Scan / Ship Scanner signature: (e.g. High Power Slots, Service Slots)
    if StructureFittingParser.is_structure_fitting(clean_text):
        return IntelFormat.STRUCTURE_FIT

    # Check for Data Analyzer / Structure Defense Timer Hack signature: e.g. 'Hour (+- 3 hrs): 02:00'
    if StructureHackParser.is_structure_hack(clean_text):
        return IntelFormat.STRUCTURE_HACK

    # Check for Local chat log signature: [ 10:48:00 ] Name > msg or Channel Name: Local
    if any(re.match(r"^\[\s*(\d{4}[.\-/]\d{2}[.\-/]\d{2}\s+)?(\d{1,2}:\d{2}(?::\d{2})?)\s*\]\s*([^>]+?)\s*>", l) for l in lines[:5]):
        return IntelFormat.LOCAL
    if any(l.lower().startswith("channel name:") and "local" in l.lower() for l in lines[:5]):
        return IntelFormat.LOCAL

    # Check for D-Scan signature: presence of tabs and distance indicators (km, m, AU)
    tab_count = sum(1 for line in lines if "\t" in line)
    if tab_count >= max(1, len(lines) // 2):
        # If tabs contain distance indicator -> DScan, else if names and corps -> could be local or dscan
        dist_count = sum(1 for line in lines if re.search(r"\b(\d+[\d,\.]*\s*(km|m|AU))\b", line, re.I))
        if dist_count > 0:
            return IntelFormat.DSCAN

    # Distance patterns in column format
    distance_count = sum(1 for line in lines if re.search(r"\b(\d+[\d,\.]*\s*(km|m|AU))\b", line, re.I))
    if distance_count >= max(2, len(lines) // 3):
        return IntelFormat.DSCAN

    # Check for Show-Info / Inspect signature: key-value labels
    showinfo_indicators = [
        "quantum core",
        "owner:",
        "corporation:",
        "solar system:",
        "system:",
        "state:",
        "structure:",
        "fitting:",
    ]
    text_lower = clean_text.lower()
    matches = sum(1 for ind in showinfo_indicators if ind in text_lower)
    if matches >= 2:
        return IntelFormat.SHOWINFO

    if lines[0].startswith("[") and ("]" in lines[0]) and ("core" in text_lower or "online" in text_lower):
        return IntelFormat.SHOWINFO

    return IntelFormat.OBSERVATION


def parse_intel_paste(
    text: str,
    dscan_text: Optional[str] = None,
    default_system: Optional[SolarSystem] = None,
    fetch_zkill: bool = True,
) -> Dict[str, Any]:
    """Unified entry point for classifying and parsing raw intelligence text"""
    fmt = classify_intel_text(text)

    if fmt == IntelFormat.DSCAN:
        parsed_data = DScanParser.parse(text, default_system=default_system)
    elif fmt == IntelFormat.LOCAL:
        parsed_data = LocalThreatParser.parse(
            text,
            dscan_text=dscan_text,
            default_system=default_system,
            fetch_zkill=fetch_zkill,
        )
    elif fmt == IntelFormat.SHOWINFO:
        parsed_data = StructureShowInfoParser.parse(text)
    elif fmt == IntelFormat.STRUCTURE_HACK:
        parsed_data = StructureHackParser.parse(text, default_system=default_system)
    elif fmt in [IntelFormat.EFT, IntelFormat.STRUCTURE_FIT]:
        parsed_data = StructureFittingParser.parse(text)
    else:
        # Fallback observation / raw report
        parsed_data = {
            "text": text,
            "solar_system": default_system,
        }

    return {
        "format": fmt,
        "data": parsed_data,
        "raw_text": text,
    }

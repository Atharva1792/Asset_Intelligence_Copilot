"""
Section-Aware Markdown Report Parser for Maintenance Reports
Extracts metadata, sections, containment hours, root cause, and compliance tables.
"""

import re
from pathlib import Path
from typing import Dict, Any, List, Optional


def parse_containment_hours(text: str) -> Optional[float]:
    """
    Extracts containment duration from text.
    Examples:
    'contained about 5.3 hours after the inspection' -> 5.3
    'contained about 7 hours after discovery' -> 7.0
    'contained after about 7.1 hours' -> 7.1
    """
    patterns = [
        r"contained\s+(?:about|after about|in about)?\s*([0-9]+\.?[0-9]*)\s*hours",
        r"contained\s+after\s+(?:about)?\s*([0-9]+\.?[0-9]*)\s*hours",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


def parse_maintenance_report(file_path: Path) -> Dict[str, Any]:
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    
    # Extract report title
    title = ""
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break

    # Extract metadata block from header
    meta = {}
    for line in lines[:15]:
        if "**Asset:**" in line:
            m = re.search(r"\*\*Asset:\*\*\s*([^\n·]+)", line)
            if m: meta["asset_raw"] = m.group(1).strip()
        if "**Equipment:**" in line:
            m = re.search(r"\*\*Equipment:\*\*\s*([^\n·]+)", line)
            if m: meta["equipment"] = m.group(1).strip()
        if "**Triggered by:**" in line:
            m = re.search(r"inspection\s+(INS-[A-Za-z0-9\-]+)", line)
            if m: meta["triggered_by_inspection_id"] = m.group(1).strip()
        if "**Work started:**" in line:
            m = re.search(r"\*\*Work started:\*\*\s*([^\n·]+)", line)
            if m: meta["work_started"] = m.group(1).strip()

    # Split into sections by '## '
    sections: Dict[str, str] = {}
    current_sec = "Header"
    current_text = []

    for line in lines:
        if line.startswith("## "):
            if current_text:
                sections[current_sec] = "\n".join(current_text).strip()
            current_sec = line[3:].strip()
            current_text = []
        else:
            current_text.append(line)
    if current_text:
        sections[current_sec] = "\n".join(current_text).strip()

    # Extract containment hours if present in 'What happened'
    what_happened = sections.get("What happened", "")
    containment_hours = parse_containment_hours(what_happened)

    # Extract root cause
    root_cause = sections.get("Root cause", "")

    # Extract compliance check table
    compliance_rows = []
    comp_text = sections.get("Compliance check", "")
    for line in comp_text.splitlines():
        if line.strip().startswith("|") and not line.strip().startswith("|---"):
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 3 and parts[0] != "Protocol":
                compliance_rows.append({
                    "protocol": parts[0],
                    "result": parts[1].replace("*", ""),
                    "detail": parts[2],
                })

    return {
        "file_name": file_path.name,
        "source_path": str(file_path),
        "title": title,
        "metadata": meta,
        "containment_hours": containment_hours,
        "root_cause": root_cause,
        "sections": sections,
        "compliance_rows": compliance_rows,
        "full_content": content,
    }

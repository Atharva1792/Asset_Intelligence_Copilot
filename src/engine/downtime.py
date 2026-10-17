"""
Deterministic Downtime & Production Calculation Engine
Ground truth rules:
1. Production status is determined strictly by thermal differential:
   delta_T = wellhead_temp_c - ambient_temp_c
   T200: producing if delta_T >= 15.0 °C
   T350: producing if delta_T >= 20.0 °C
2. Each weekly inspection where well is NOT producing counts as exactly 7 days of downtime.
3. Missed inspections add 0 days.
4. maintenance.downtime_hours is job duration and is NOT well downtime.
5. Human correction notes (note_type == 'correction') targeting an inspection override raw sensor values.
"""

from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from src.config import (
    T200_TEMP_THRESHOLD_C,
    T350_TEMP_THRESHOLD_C,
    DOWNTIME_DAYS_PER_INSPECTION,
)


def get_threshold_for_well_type(well_type: str) -> float:
    if well_type.upper() == "T350":
        return T350_TEMP_THRESHOLD_C
    return T200_TEMP_THRESHOLD_C


def is_producing(well_type: str, wellhead_temp_c: float, ambient_temp_c: float) -> bool:
    delta_t = wellhead_temp_c - ambient_temp_c
    threshold = get_threshold_for_well_type(well_type)
    return delta_t >= threshold


def evaluate_inspection_production(
    inspection: Dict[str, Any],
    well_type: str,
    correction: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ambient = float(inspection["ambient_temp_c"])
    wellhead = float(inspection["wellhead_temp_c"])
    
    is_corrected = False
    corrected_reason = None
    
    # Check if a formal correction overrides the wellhead temperature
    if correction:
        if "corrected_wellhead_temp_c" in correction:
            wellhead = float(correction["corrected_wellhead_temp_c"])
            is_corrected = True
            corrected_reason = correction.get("rationale")

    delta_t = round(wellhead - ambient, 2)
    threshold = get_threshold_for_well_type(well_type)
    producing = delta_t >= threshold

    return {
        "inspection_id": inspection["inspection_id"],
        "inspected_at": inspection["inspected_at"],
        "ambient_temp_c": ambient,
        "wellhead_temp_c": wellhead,
        "delta_t_c": delta_t,
        "threshold_c": threshold,
        "is_producing": producing,
        "is_corrected": is_corrected,
        "corrected_reason": corrected_reason,
        "inspector_notes": inspection.get("notes", ""),
    }


def calculate_well_downtime_in_window(
    inspections: List[Dict[str, Any]],
    well_type: str,
    start_date: str,
    end_date: str,
    corrections_by_inspection_id: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    corrections = corrections_by_inspection_id or {}
    
    qualifying_inspections = []
    non_producing_inspections = []
    
    # Normalize date filters
    # Ensure end_date includes entire day up to 23:59:59
    start_iso = start_date if "T" in start_date else f"{start_date}T00:00:00Z"
    end_iso = end_date if "T" in end_date else f"{end_date}T23:59:59Z"

    for insp in inspections:
        ts = insp["inspected_at"]
        if start_iso <= ts <= end_iso:
            qualifying_inspections.append(insp)
            insp_id = insp["inspection_id"]
            corr = corrections.get(insp_id)
            eval_res = evaluate_inspection_production(insp, well_type, corr)
            if not eval_res["is_producing"]:
                non_producing_inspections.append(eval_res)

    downtime_days = len(non_producing_inspections) * DOWNTIME_DAYS_PER_INSPECTION

    return {
        "well_type": well_type,
        "start_date": start_date,
        "end_date": end_date,
        "total_inspections_in_window": len(qualifying_inspections),
        "non_producing_inspection_count": len(non_producing_inspections),
        "downtime_days": downtime_days,
        "non_producing_details": non_producing_inspections,
    }


def rank_wells_by_downtime(
    wells_inspections: Dict[str, List[Dict[str, Any]]],
    well_metadata: Dict[str, Dict[str, Any]],
    start_date: str,
    end_date: str,
    limit: int = 5,
    corrections_by_inspection_id: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    ranked = []
    for asset_id, insps in wells_inspections.items():
        meta = well_metadata.get(asset_id, {})
        well_type = meta.get("well_type", "T200")
        name = meta.get("name", asset_id)
        area_id = meta.get("area_id", "")
        
        dt_res = calculate_well_downtime_in_window(
            inspections=insps,
            well_type=well_type,
            start_date=start_date,
            end_date=end_date,
            corrections_by_inspection_id=corrections_by_inspection_id,
        )
        
        ranked.append({
            "asset_id": asset_id,
            "name": name,
            "well_type": well_type,
            "area_id": area_id,
            "downtime_days": dt_res["downtime_days"],
            "non_producing_inspection_count": dt_res["non_producing_inspection_count"],
            "non_producing_details": dt_res["non_producing_details"],
        })

    # Sort descending by downtime_days, then asset_id
    ranked.sort(key=lambda x: (x["downtime_days"], x["asset_id"]), reverse=True)
    
    # Assign ranks
    for i, item in enumerate(ranked[:limit], 1):
        item["rank"] = i

    return ranked[:limit]

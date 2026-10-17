"""
Authoritative Database and Hybrid Retrieval Tools for Asset Intelligence Copilot
Gemma 4 Optimized: All tool functions return JSON strings (`str`) with clean primitive serialization.

Tools Implemented:
1. get_well_overview
2. list_wells_for_area
3. get_downtime_events
4. rank_wells_by_downtime
5. get_maintenance_history
6. get_inspection_history
7. get_incident_history
8. search_technical_documents (Hybrid Dense-Vector + GIN Full-Text)
9. save_operator_feedback
"""

import json
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, Any, List, Optional
import psycopg
from psycopg.rows import dict_row
from fastembed import TextEmbedding

from src.config import (
    DATABASE_URL,
    T200_TEMP_THRESHOLD_C,
    T350_TEMP_THRESHOLD_C,
    DOWNTIME_DAYS_PER_INSPECTION,
)
from src.engine.downtime import (
    is_producing,
    get_threshold_for_well_type,
    evaluate_inspection_production,
    calculate_well_downtime_in_window,
)


def get_db_connection():
    conn_url = DATABASE_URL
    if conn_url.startswith("postgresql+psycopg://"):
        conn_url = conn_url.replace("postgresql+psycopg://", "postgresql://", 1)
    elif conn_url.startswith("postgres://"):
        conn_url = conn_url.replace("postgres://", "postgresql://", 1)
    return psycopg.connect(
        conn_url,
        keepalives=1,
        keepalives_idle=15,
        keepalives_interval=5,
        keepalives_count=5,
        row_factory=dict_row
    )


def serialize_data(obj: Any) -> Any:
    """Recursively converts datetimes, dates, and Decimals to JSON-serializable primitives."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: serialize_data(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize_data(v) for v in obj]
    return obj


# Global embedding model cache for fast hybrid search
_embedding_model: Optional[TextEmbedding] = None


def get_embedding_model() -> TextEmbedding:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    return _embedding_model


# ============================================================================
# Tool 1: get_well_overview
# ============================================================================
def get_well_overview(well_id: str) -> str:
    """
    Retrieves canonical well metadata, equipment inventory, location, team in charge,
    and latest calculated operational status for an oil well asset.
    """
    well_id = well_id.strip().upper()
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    w.asset_id, w.name, w.well_type, w.area_id, a.name AS area_name,
                    w.team_id, t.name AS team_name,
                    w.in_charge_user_id, u.name AS in_charge_user_name,
                    w.lat, w.lon, w.installed_on, w.recorded_status, w.last_inspection_id
                FROM wells w
                LEFT JOIN areas a ON w.area_id = a.area_id
                LEFT JOIN teams t ON w.team_id = t.team_id
                LEFT JOIN users u ON w.in_charge_user_id = u.user_id
                WHERE w.asset_id = %s;
            """, (well_id,))
            well = cur.fetchone()
            if not well:
                return json.dumps({"error": f"Well '{well_id}' not found in the field inventory."})

            # Equipment
            cur.execute("""
                SELECT equipment_id, kind, model
                FROM well_equipment
                WHERE asset_id = %s
                ORDER BY kind;
            """, (well_id,))
            equipment = cur.fetchall()

            # Latest inspection
            cur.execute("""
                SELECT 
                    inspection_id, inspected_at, ambient_temp_c, wellhead_temp_c,
                    spill_detected, spill_area_pct, spill_est_volume_bbl, notes
                FROM inspections
                WHERE asset_id = %s
                ORDER BY inspected_at DESC
                LIMIT 1;
            """, (well_id,))
            latest_insp = cur.fetchone()

            # Check production threshold for latest inspection
            prod_status = "UNKNOWN"
            delta_t = None
            if latest_insp:
                delta_t = round(float(latest_insp["wellhead_temp_c"]) - float(latest_insp["ambient_temp_c"]), 2)
                th = get_threshold_for_well_type(well["well_type"])
                prod_status = "PRODUCING" if delta_t >= th else "OFFLINE / NON-PRODUCING"

            res = {
                "well": {
                    "asset_id": well["asset_id"],
                    "name": well["name"],
                    "well_type": well["well_type"],
                    "area_id": well["area_id"],
                    "area_name": well["area_name"],
                    "team_id": well["team_id"],
                    "team_name": well["team_name"],
                    "in_charge_user": {
                        "user_id": well["in_charge_user_id"],
                        "name": well["in_charge_user_name"],
                    },
                    "coordinates": {"lat": well["lat"], "lon": well["lon"]},
                    "installed_on": str(well["installed_on"]),
                    "recorded_status": well["recorded_status"],
                    "current_calculated_status": prod_status,
                    "delta_t_c": delta_t,
                },
                "equipment": equipment,
                "latest_inspection": latest_insp,
            }
            return json.dumps(serialize_data(res))


# ============================================================================
# Tool 2: list_wells_for_area
# ============================================================================
def list_wells_for_area(area_id: str) -> str:
    """
    Returns all oil wells located in a specific operational area with their coordinates,
    well type, and manager.
    """
    area_id = area_id.strip().upper()
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT area_id, name, team_id, center_lat, center_lon
                FROM areas
                WHERE area_id = %s;
            """, (area_id,))
            area = cur.fetchone()
            if not area:
                return json.dumps({"error": f"Area '{area_id}' not found."})

            cur.execute("""
                SELECT 
                    w.asset_id, w.name, w.well_type, w.recorded_status,
                    w.lat, w.lon, u.name AS in_charge_name
                FROM wells w
                LEFT JOIN users u ON w.in_charge_user_id = u.user_id
                WHERE w.area_id = %s
                ORDER BY w.asset_id;
            """, (area_id,))
            wells = cur.fetchall()

            res = {
                "area": area,
                "count": len(wells),
                "wells": wells,
            }
            return json.dumps(serialize_data(res))


# ============================================================================
# Tool 3: get_downtime_events
# ============================================================================
def get_downtime_events(well_id: str, start_time: str, end_time: str) -> str:
    """
    Calculates deterministic downtime days and qualifying non-producing inspections
    for a well between start_time (e.g. '2026-06-01') and end_time (e.g. '2026-08-31').
    """
    well_id = well_id.strip().upper()
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT asset_id, name, well_type FROM wells WHERE asset_id = %s;", (well_id,))
            well = cur.fetchone()
            if not well:
                return json.dumps({"error": f"Well '{well_id}' not found."})

            cur.execute("""
                SELECT 
                    inspection_id, asset_id, inspected_at, ambient_temp_c, wellhead_temp_c,
                    spill_detected, spill_area_pct, spill_est_volume_bbl, notes
                FROM inspections
                WHERE asset_id = %s AND inspected_at >= %s AND inspected_at <= %s
                ORDER BY inspected_at ASC;
            """, (well_id, start_time, end_time))
            inspections = cur.fetchall()

            cur.execute("""
                SELECT inspection_id, text, note_type
                FROM context_notes
                WHERE asset_id = %s AND inspection_id IS NOT NULL;
            """, (well_id,))
            corr_rows = cur.fetchall()
            corrections = {}
            for c in corr_rows:
                corrections[c["inspection_id"]] = {"rationale": c["text"]}

            formatted_insps = []
            for r in inspections:
                formatted_insps.append({
                    "inspection_id": r["inspection_id"],
                    "inspected_at": r["inspected_at"].isoformat() if hasattr(r["inspected_at"], "isoformat") else str(r["inspected_at"]),
                    "ambient_temp_c": float(r["ambient_temp_c"]),
                    "wellhead_temp_c": float(r["wellhead_temp_c"]),
                    "notes": r["notes"],
                })

            result = calculate_well_downtime_in_window(
                formatted_insps,
                well["well_type"],
                start_time,
                end_time,
                corrections,
            )

            result["asset_id"] = well["asset_id"]
            result["name"] = well["name"]
            result["well_type"] = well["well_type"]
            return json.dumps(serialize_data(result))


# ============================================================================
# Tool 4: rank_wells_by_downtime
# ============================================================================
def rank_wells_by_downtime(
    area_id: str,
    start_time: str,
    end_time: str,
    limit: int = 10,
) -> str:
    """
    Deterministically ranks all wells in an operational area by downtime days during
    a time range (e.g. Summer: start_time='2026-06-01', end_time='2026-08-31').
    """
    area_id = area_id.strip().upper()
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT w.asset_id, w.name, w.well_type
                FROM wells w
                WHERE w.area_id = %s
                ORDER BY w.asset_id;
            """, (area_id,))
            wells = cur.fetchall()
            if not wells:
                return json.dumps({"error": f"No wells found in area '{area_id}'."})

            well_ids = [w["asset_id"] for w in wells]
            well_types = {w["asset_id"]: w["well_type"] for w in wells}
            well_names = {w["asset_id"]: w["name"] for w in wells}

            cur.execute("""
                SELECT 
                    inspection_id, asset_id, inspected_at,
                    ambient_temp_c, wellhead_temp_c, spill_detected, notes
                FROM inspections
                WHERE asset_id = ANY(%s) AND inspected_at >= %s AND inspected_at <= %s
                ORDER BY inspected_at ASC;
            """, (well_ids, start_time, end_time))
            all_insps = cur.fetchall()

            cur.execute("""
                SELECT asset_id, inspection_id, text
                FROM context_notes
                WHERE asset_id = ANY(%s) AND inspection_id IS NOT NULL;
            """, (well_ids,))
            corrections_by_well: Dict[str, Dict[str, Any]] = {}
            for c in cur.fetchall():
                w_id = c["asset_id"]
                if w_id not in corrections_by_well:
                    corrections_by_well[w_id] = {}
                corrections_by_well[w_id][c["inspection_id"]] = {"rationale": c["text"]}

            insps_by_well: Dict[str, List[Dict[str, Any]]] = {w_id: [] for w_id in well_ids}
            for r in all_insps:
                insps_by_well[r["asset_id"]].append({
                    "inspection_id": r["inspection_id"],
                    "inspected_at": r["inspected_at"].isoformat() if hasattr(r["inspected_at"], "isoformat") else str(r["inspected_at"]),
                    "ambient_temp_c": float(r["ambient_temp_c"]),
                    "wellhead_temp_c": float(r["wellhead_temp_c"]),
                    "notes": r["notes"],
                })

            ranked_results = []
            for w_id in well_ids:
                dt_res = calculate_well_downtime_in_window(
                    insps_by_well[w_id],
                    well_types[w_id],
                    start_time,
                    end_time,
                    corrections_by_well.get(w_id, {}),
                )
                ranked_results.append({
                    "asset_id": w_id,
                    "name": well_names[w_id],
                    "well_type": well_types[w_id],
                    "downtime_days": dt_res["downtime_days"],
                    "non_producing_inspections_count": dt_res.get("non_producing_inspection_count", 0),
                    "total_qualifying_inspections": dt_res.get("total_inspections_in_window", 0),
                    "non_producing_inspection_ids": [insp["inspection_id"] for insp in dt_res.get("non_producing_details", [])],
                })

            ranked_results.sort(key=lambda x: x["downtime_days"], reverse=True)

            res = {
                "area_id": area_id,
                "time_window": {"start": start_time, "end": end_time},
                "total_wells_analyzed": len(ranked_results),
                "ranked_wells": ranked_results[:limit],
            }
            return json.dumps(serialize_data(res))


# ============================================================================
# Tool 5: get_maintenance_history
# ============================================================================
def get_maintenance_history(
    well_id: str,
    start_time: str = "",
    end_time: str = "",
    limit: int = 10,
) -> str:
    """
    Retrieves maintenance work orders, corrective/preventative actions, equipment repaired,
    labor/parts costs, and attached report files for a well.
    """
    well_id = well_id.strip().upper()
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            sql = """
                SELECT 
                    m.maintenance_id, m.asset_id, m.started_at, m.completed_at,
                    m.maintenance_type, m.equipment_kind, m.issue, m.action_taken,
                    m.technician_user_id, u.name AS technician_name,
                    m.contractor, m.downtime_hours,
                    m.cost_labor, m.cost_parts, m.cost_total,
                    m.triggered_by_inspection_id, m.report_file
                FROM maintenance_work_orders m
                LEFT JOIN users u ON m.technician_user_id = u.user_id
                WHERE m.asset_id = %s
            """
            params: List[Any] = [well_id]
            if start_time:
                sql += " AND m.started_at >= %s"
                params.append(start_time)
            if end_time:
                sql += " AND m.started_at <= %s"
                params.append(end_time)
            sql += " ORDER BY m.started_at DESC LIMIT %s;"
            params.append(limit)

            cur.execute(sql, tuple(params))
            records = cur.fetchall()

            res = {
                "asset_id": well_id,
                "count": len(records),
                "maintenance_records": records,
            }
            return json.dumps(serialize_data(res))


# ============================================================================
# Tool 6: get_inspection_history
# ============================================================================
def get_inspection_history(
    well_id: str,
    start_time: str = "",
    end_time: str = "",
    only_spills: bool = False,
    limit: int = 15,
) -> str:
    """
    Retrieves weekly inspection time-series records for an asset, including thermal readings,
    delta T, spill detections, inspector notes, and evidence photos.
    """
    well_id = well_id.strip().upper()
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT asset_id, well_type FROM wells WHERE asset_id = %s;", (well_id,))
            well = cur.fetchone()
            if not well:
                return json.dumps({"error": f"Well '{well_id}' not found."})

            sql = """
                SELECT 
                    i.inspection_id, i.asset_id, i.inspected_at,
                    i.inspector_user_id, u.name AS inspector_name,
                    i.method, i.ambient_temp_c, i.wellhead_temp_c,
                    i.spill_detected, i.spill_area_pct, i.spill_est_volume_bbl,
                    i.evidence_images, i.notes
                FROM inspections i
                LEFT JOIN users u ON i.inspector_user_id = u.user_id
                WHERE i.asset_id = %s
            """
            params: List[Any] = [well_id]
            if start_time:
                sql += " AND i.inspected_at >= %s"
                params.append(start_time)
            if end_time:
                sql += " AND i.inspected_at <= %s"
                params.append(end_time)
            if only_spills:
                sql += " AND i.spill_detected = TRUE"
            sql += " ORDER BY i.inspected_at DESC LIMIT %s;"
            params.append(limit)

            cur.execute(sql, tuple(params))
            rows = cur.fetchall()

            th = get_threshold_for_well_type(well["well_type"])
            inspections = []
            for r in rows:
                delta_t = round(float(r["wellhead_temp_c"]) - float(r["ambient_temp_c"]), 2)
                producing = delta_t >= th
                inspections.append({
                    **r,
                    "delta_t_c": delta_t,
                    "threshold_c": th,
                    "is_producing": producing,
                })

            res = {
                "asset_id": well_id,
                "well_type": well["well_type"],
                "count": len(inspections),
                "inspections": inspections,
            }
            return json.dumps(serialize_data(res))


# ============================================================================
# Tool 7: get_incident_history
# ============================================================================
def get_incident_history(
    well_id: str,
    start_time: str = "",
    end_time: str = "",
) -> str:
    """
    Retrieves regulatory agency violations, spill reports, protocol non-compliances (SR-01, SR-02),
    fines, and agency close-out statuses for an asset.
    """
    well_id = well_id.strip().upper()
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            sql = """
                SELECT 
                    event_id, asset_id, date, agency, event_type, protocol_id,
                    related_inspection_id, hours_to_report, detail, fine_usd, status
                FROM regulatory_events
                WHERE asset_id = %s
            """
            params: List[Any] = [well_id]
            if start_time:
                sql += " AND date >= %s"
                params.append(start_time)
            if end_time:
                sql += " AND date <= %s"
                params.append(end_time)
            sql += " ORDER BY date DESC;"

            cur.execute(sql, tuple(params))
            records = cur.fetchall()

            res = {
                "asset_id": well_id,
                "count": len(records),
                "regulatory_events": records,
            }
            return json.dumps(serialize_data(res))


# ============================================================================
# Tool 8: search_technical_documents
# ============================================================================
def search_technical_documents(
    query: str,
    asset_id: str = "",
    doc_type: str = "",
    limit: int = 5,
) -> str:
    """
    Performs hybrid dense-vector and full-text search over engineering maintenance reports,
    standard operating procedures (SOPs), and equipment manuals.
    """
    emb_model = get_embedding_model()
    query_vector = list(emb_model.embed([query]))[0].tolist()

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            sql = """
                SELECT 
                    doc_id, doc_type, asset_id, section_title, content, source_path,
                    (ts_rank_cd(tsv, plainto_tsquery('english', %s)) * 0.4 + (1 - (embedding <=> %s::vector)) * 0.6) AS score
                FROM document_chunks
                WHERE 1=1
            """
            params: List[Any] = [query, query_vector]

            if asset_id:
                sql += " AND (asset_id = %s OR asset_id IS NULL)"
                params.append(asset_id.strip().upper())
            if doc_type:
                sql += " AND doc_type = %s"
                params.append(doc_type.strip().lower())

            sql += " ORDER BY score DESC LIMIT %s;"
            params.append(limit)

            cur.execute(sql, tuple(params))
            results = cur.fetchall()

            res = {
                "query": query,
                "count": len(results),
                "chunks": results,
            }
            return json.dumps(serialize_data(res))


# ============================================================================
# Tool 9: save_operator_feedback
# ============================================================================
def save_operator_feedback(
    user_id: str,
    target_type: str,
    target_id: str,
    correction_payload_json: str,
    rationale: str,
) -> str:
    """
    Saves an operator override or correction into the system memory, allowing human
    corrections to sensor readings, spill false positives, or well status.
    """
    user_id = user_id.strip().upper()
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO feedback_overrides (
                    user_id, target_type, target_id, correction_payload, rationale, created_at, active
                ) VALUES (%s, %s, %s, %s::jsonb, %s, NOW(), TRUE)
                RETURNING feedback_id, created_at;
            """, (user_id, target_type, target_id, correction_payload_json, rationale))
            fb = cur.fetchone()

            if target_type == "raw_reading" and target_id.startswith("INS-"):
                cur.execute("SELECT asset_id, inspected_at FROM inspections WHERE inspection_id = %s LIMIT 1;", (target_id,))
                insp = cur.fetchone()
                if insp:
                    cur.execute("""
                        INSERT INTO context_notes (
                            note_id, asset_id, created_at, author_user_id,
                            note_type, inspection_id, text, valid_from
                        ) VALUES (%s, %s, NOW(), %s, %s, %s, %s, CURRENT_DATE)
                        ON CONFLICT DO NOTHING;
                    """, (f"NOTE-FB-{fb['feedback_id']}", insp["asset_id"], user_id, "correction", target_id, rationale))

            conn.commit()
            res = {
                "status": "success",
                "feedback_id": fb["feedback_id"],
                "created_at": fb["created_at"].isoformat() if hasattr(fb["created_at"], "isoformat") else str(fb["created_at"]),
                "message": "Operator feedback successfully saved and incorporated into live memory.",
            }
            return json.dumps(serialize_data(res))

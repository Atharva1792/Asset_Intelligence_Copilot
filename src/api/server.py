"""
Asset Intelligence Copilot - FastAPI Backend Server
Exposes operational endpoints and AI chat using Gemma 4 models.
"""

import json
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.config import (
    BEDROCK_MODEL_ID,
    BEDROCK_DEMO_MODEL_ID,
    PORT,
    DATABASE_URL,
)
from src.agent.tools import (
    get_db_connection,
    get_well_overview,
    list_wells_for_area,
    get_downtime_events,
    rank_wells_by_downtime,
    get_maintenance_history,
    get_inspection_history,
    get_incident_history,
    search_technical_documents,
    save_operator_feedback,
    serialize_data,
)
from src.agent.orchestrator import AgentOrchestrator
from src.agent.memory import get_session_history, get_active_overrides

app = FastAPI(
    title="Asset Intelligence Copilot API",
    description="Operational chat assistant for oil well asset management with traceable evidence.",
    version="1.0.0",
)

# Enable CORS for local frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Lazy initialize orchestrator
_orchestrator: Optional[AgentOrchestrator] = None


def get_orchestrator() -> AgentOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AgentOrchestrator()
    return _orchestrator


# ============================================================================
# Pydantic Request / Response Models
# ============================================================================
class ChatRequest(BaseModel):
    prompt: str
    user_id: str = "U-101"
    session_id: str = "default-session"
    model_name: Optional[str] = BEDROCK_MODEL_ID


class FeedbackRequest(BaseModel):
    user_id: str
    target_type: str
    target_id: str
    correction_payload: Dict[str, Any]
    rationale: str


# ============================================================================
# Endpoints
# ============================================================================
@app.get("/api/health")
def health_check():
    """Returns system status, active database connection, and configured models."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM wells;")
                wells_count = cur.fetchone()["count"]
                cur.execute("SELECT COUNT(*) FROM inspections;")
                inspections_count = cur.fetchone()["count"]
                cur.execute("SELECT COUNT(*) FROM document_chunks;")
                chunks_count = cur.fetchone()["count"]

        return {
            "status": "healthy",
            "database": "TimescaleDB Cloud (TigerData)",
            "models_available": [
                {"id": BEDROCK_MODEL_ID, "name": "Amazon Nova Lite", "type": "ultra-low-cost testing", "recommended": True},
                {"id": BEDROCK_DEMO_MODEL_ID, "name": "Amazon Nova Pro", "type": "high-reasoning demo", "recommended": False},
            ],
            "stats": {
                "wells_indexed": wells_count,
                "inspections_indexed": inspections_count,
                "document_chunks_indexed": chunks_count,
            },
        }
    except Exception as e:
        return {"status": "degraded", "error": str(e)}


@app.get("/api/users")
def get_users():
    """Lists all field operators and teams for user switching in the UI."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT u.user_id, u.name, u.role, u.team_id, t.name AS team_name
                FROM users u
                LEFT JOIN teams t ON u.team_id = t.team_id
                ORDER BY u.user_id;
            """)
            return {"users": cur.fetchall()}


@app.get("/api/areas")
def get_areas():
    """Lists all operational areas."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.area_id, a.name, a.team_id, t.name AS team_name, a.center_lat, a.center_lon
                FROM areas a
                LEFT JOIN teams t ON a.team_id = t.team_id
                ORDER BY a.area_id;
            """)
            return {"areas": cur.fetchall()}


@app.get("/api/wells")
def list_wells(area_id: Optional[str] = None):
    """Lists wells, optionally scoped to an area."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            if area_id:
                cur.execute("""
                    SELECT w.asset_id, w.name, w.well_type, w.area_id, w.lat, w.lon, w.recorded_status
                    FROM wells w
                    WHERE w.area_id = %s
                    ORDER BY w.asset_id;
                """, (area_id.upper(),))
            else:
                cur.execute("""
                    SELECT w.asset_id, w.name, w.well_type, w.area_id, w.lat, w.lon, w.recorded_status
                    FROM wells w
                    ORDER BY w.asset_id;
                """)
            return {"wells": cur.fetchall()}


@app.get("/api/wells/{well_id}")
def well_overview(well_id: str):
    """Returns canonical overview, equipment, and latest inspection for a well."""
    raw = get_well_overview(well_id)
    return json.loads(raw)


@app.get("/api/wells/{well_id}/downtime")
def well_downtime(
    well_id: str,
    start_time: str = Query("2026-06-01"),
    end_time: str = Query("2026-08-31"),
):
    """Calculates deterministic downtime for a well in a date window."""
    raw = get_downtime_events(well_id, start_time, end_time)
    return json.loads(raw)


@app.get("/api/areas/{area_id}/downtime-ranking")
def area_downtime_ranking(
    area_id: str,
    start_time: str = Query("2026-06-01"),
    end_time: str = Query("2026-08-31"),
    limit: int = 10,
):
    """Returns ranked list of wells by downtime in an area."""
    raw = rank_wells_by_downtime(area_id, start_time, end_time, limit)
    return json.loads(raw)


@app.get("/api/evidence/{record_id:path}")
def get_evidence_detail(record_id: str):
    """
    Returns exact underlying record details for any citation tag clicked by an operator
    ([MNT-...], [INS-...], [REG-...], or [Report: ...]).
    """
    clean_id = record_id.strip("[]")
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            if clean_id.startswith("MNT-"):
                cur.execute("""
                    SELECT maintenance_id, asset_id, started_at, completed_at, equipment_kind,
                           maintenance_type, issue, action_taken, downtime_hours, cost_total, technician_user_id
                    FROM maintenance_work_orders
                    WHERE maintenance_id = %s;
                """, (clean_id,))
                row = cur.fetchone()
                if row:
                    return {"status": "success", "evidence_type": "Maintenance Work Order", "record": row}
            elif clean_id.startswith("INS-"):
                cur.execute("""
                    SELECT inspection_id, asset_id, inspected_at, ambient_temp_c,
                           wellhead_temp_c, (wellhead_temp_c - ambient_temp_c) as delta_t_c,
                           spill_detected, spill_area_pct, notes, inspector_user_id
                    FROM inspections
                    WHERE inspection_id = %s;
                """, (clean_id,))
                row = cur.fetchone()
                if row:
                    return {"status": "success", "evidence_type": "Drone Inspection", "record": row}
            elif clean_id.startswith("REG-"):
                cur.execute("""
                    SELECT event_id, asset_id, event_type, date, status,
                           detail, agency, fine_usd
                    FROM regulatory_events
                    WHERE event_id = %s;
                """, (clean_id,))
                row = cur.fetchone()
                if row:
                    return {"status": "success", "evidence_type": "Regulatory Event", "record": row}
            
            # Document chunk fallback
            cur.execute("""
                SELECT chunk_id, section_title as title, doc_type, content, source_path
                FROM document_chunks
                WHERE chunk_id ILIKE %s OR content ILIKE %s
                LIMIT 1;
            """, (f"%{clean_id}%", f"%{clean_id}%"))
            row = cur.fetchone()
            if row:
                return {"status": "success", "evidence_type": "Technical Report / SOP", "record": row}

    return {"status": "not_found", "evidence_type": "Citation Reference", "record": {"citation": clean_id}}


@app.post("/api/chat")
def chat_copilot(req: ChatRequest):
    """
    Main Copilot Chat endpoint.
    Accepts plain English operational questions, executes deterministic tool queries,
    and returns traceable evidence, model citations, and generative UI component JSON.
    """
    orchestrator = get_orchestrator()
    try:
        res = orchestrator.run_query(
            prompt=req.prompt,
            user_id=req.user_id,
            session_id=req.session_id,
            model_name=req.model_name,
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chat/history/{session_id}")
def chat_history(session_id: str):
    """Retrieves session chat history."""
    messages = get_session_history(session_id, limit=30)
    return {"session_id": session_id, "messages": messages}


@app.post("/api/feedback")
def submit_feedback(req: FeedbackRequest):
    """Saves human operator feedback, corrections, or false positive overrides."""
    raw = save_operator_feedback(
        user_id=req.user_id,
        target_type=req.target_type,
        target_id=req.target_id,
        correction_payload_json=json.dumps(req.correction_payload),
        rationale=req.rationale,
    )
    return json.loads(raw)


@app.get("/api/feedback/active")
def active_feedback():
    """Lists all active fleet overrides in memory."""
    overrides = get_active_overrides()
    return {"overrides": serialize_data(overrides)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api.server:app", host="0.0.0.0", port=PORT, reload=True)

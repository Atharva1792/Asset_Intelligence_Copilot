"""
Batch Ingestion Loader for TimescaleDB / TigerData
Loads fleet configurations, 200 well asset records, time-series inspections,
maintenance work orders, regulatory actions, context notes, and vector-embedded reports.
"""

import os
import json
import hashlib
from pathlib import Path
from typing import Dict, Any, List

import psycopg
from fastembed import TextEmbedding

from src.config import (
    DATABASE_URL,
    SAMPLE_DATA_DIR,
    FLEET_DIR,
    ASSETS_DIR,
)
from src.ingestion.report_parser import parse_maintenance_report
print(FLEET_DIR)

def get_connection():
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
    )


def apply_migrations(conn):
    print("Applying schema migrations...")
    migration_file = Path(__file__).resolve().parent.parent.parent / "migrations" / "001_initial_schema.sql"
    with open(migration_file, "r", encoding="utf-8") as f:
        sql = f.read()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print("Schema migrations applied successfully.")


def load_fleet_data(conn):
    print("Loading fleet data (teams, users, areas)...")
    
    # Load users & teams
    with open(FLEET_DIR / "users.json", "r", encoding="utf-8") as f:
        users = json.load(f)["users"]

    with open(FLEET_DIR / "areas.json", "r", encoding="utf-8") as f:
        areas_data = json.load(f)
        areas = areas_data["areas"]
        teams = areas_data["teams"]

    with conn.cursor() as cur:
        # 1. Upsert teams with NULL lead_user_id to break circular foreign key
        for t in teams:
            cur.execute("""
                INSERT INTO teams (team_id, name, lead_user_id)
                VALUES (%s, %s, NULL)
                ON CONFLICT (team_id) DO UPDATE SET
                    name = EXCLUDED.name
            """, (t["team_id"], t["name"]))

        # 2. Upsert users (now teams exist)
        for u in users:
            cur.execute("""
                INSERT INTO users (user_id, name, role, team_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    role = EXCLUDED.role,
                    team_id = EXCLUDED.team_id
            """, (u["user_id"], u["name"], u["role"], u.get("team_id")))

        # 3. Update teams with actual lead_user_id (now users exist)
        for t in teams:
            if t.get("lead_user_id"):
                cur.execute("""
                    UPDATE teams SET lead_user_id = %s WHERE team_id = %s
                """, (t["lead_user_id"], t["team_id"]))

        # Upsert areas
        for a in areas:
            cur.execute("""
                INSERT INTO areas (area_id, name, team_id, center_lat, center_lon)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (area_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    team_id = EXCLUDED.team_id,
                    center_lat = EXCLUDED.center_lat,
                    center_lon = EXCLUDED.center_lon
            """, (a["area_id"], a["name"], a["team_id"], a["center"]["lat"], a["center"]["lon"]))

    conn.commit()
    print(f"Loaded {len(teams)} teams, {len(users)} users, {len(areas)} areas.")


def load_fleet_documents(conn, embedding_model=None):
    print("Loading SOPs and well-type documents into document_chunks...")
    docs_to_parse = []
    sops_dir = FLEET_DIR / "sops"
    if sops_dir.exists():
        for sf in sops_dir.glob("*.md"):
            docs_to_parse.append((sf, "sop"))
    wt_dir = FLEET_DIR / "well-types"
    if wt_dir.exists():
        for wf in wt_dir.glob("*.md"):
            docs_to_parse.append((wf, "well_type"))

    chunks_to_embed = []
    chunk_metas = []
    for fpath, dtype in docs_to_parse:
        with open(fpath, "r", encoding="utf-8") as f:
            text = f.read()
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        
        lines = text.splitlines()
        title = fpath.stem
        for line in lines:
            if line.startswith("# "):
                title = line[2:].strip()
                break
        
        sections = {}
        curr_sec = "Overview"
        curr_lines = []
        for line in lines:
            if line.startswith("## "):
                if curr_lines:
                    sections[curr_sec] = "\n".join(curr_lines).strip()
                curr_sec = line[3:].strip()
                curr_lines = []
            else:
                curr_lines.append(line)
        if curr_lines:
            sections[curr_sec] = "\n".join(curr_lines).strip()

        for sec_title, sec_text in sections.items():
            if not sec_text.strip(): continue
            full_chunk = f"{title}\nSection: {sec_title}\n{sec_text}"
            chunks_to_embed.append(full_chunk)
            chunk_metas.append((fpath.stem, dtype, None, sec_title, full_chunk, str(fpath), content_hash))

    if chunks_to_embed:
        embeddings = [None] * len(chunks_to_embed)
        if embedding_model:
            embeddings = [emb.tolist() for emb in embedding_model.embed(chunks_to_embed, batch_size=32)]
        
        doc_rows = []
        for (doc_id, doc_type, asset_id, sec_title, content, source_path, content_hash), emb in zip(chunk_metas, embeddings):
            doc_rows.append((doc_id, doc_type, asset_id, sec_title, content, emb, source_path, content_hash))
        
        with conn.cursor() as cur:
            for r in doc_rows:
                cur.execute("""
                    INSERT INTO document_chunks (
                        doc_id, doc_type, asset_id, section_title,
                        content, embedding, source_path, content_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (doc_id, section_title) DO NOTHING
                """, r)
        conn.commit()
        print(f"Loaded {len(doc_rows)} fleet document chunks (SOPs & well types).")


def ingest_well(w: str, embedding_model=None) -> Dict[str, int]:
    wdir = ASSETS_DIR / w
    counts = {"inspections": 0, "maintenance": 0, "regulatory": 0, "context": 0, "reports": 0}

    # 1. Metadata
    with open(wdir / "metadata.json", "r", encoding="utf-8") as f:
        meta = json.load(f)

    # 2. Inspections
    insp_rows = []
    with open(wdir / "inspections.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            r = json.loads(line)
            insp_rows.append((
                r["inspection_id"], r["asset_id"], r["inspected_at"],
                r["inspector_user_id"], r["method"],
                r["ambient_temp_c"], r["wellhead_temp_c"],
                r["spill_detected"], r.get("spill_area_pct"),
                r.get("spill_est_volume_bbl"), r.get("evidence_images", []),
                r.get("notes", "")
            ))
    counts["inspections"] = len(insp_rows)

    # 3. Maintenance Work Orders
    maint_rows = []
    with open(wdir / "maintenance.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            r = json.loads(line)
            costs = r.get("cost_usd", {})
            maint_rows.append((
                r["maintenance_id"], r["asset_id"], r["started_at"], r.get("completed_at"),
                r["maintenance_type"], r["equipment_kind"], r["issue"], r["action_taken"],
                r.get("technician_user_id"), r.get("contractor"), r.get("downtime_hours", 0.0),
                costs.get("labor", 0.0), costs.get("parts", 0.0), costs.get("total", 0.0),
                r.get("triggered_by_inspection_id"), r.get("report_file")
            ))
    counts["maintenance"] = len(maint_rows)

    # 4. Regulatory Events
    reg_rows = []
    with open(wdir / "regulatory-events.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            r = json.loads(line)
            reg_rows.append((
                r["event_id"], r["asset_id"], r["date"], r["agency"],
                r["event_type"], r["protocol_id"], r.get("related_inspection_id"),
                r.get("hours_to_report"), r["detail"], r.get("fine_usd", 0.0),
                r.get("status", "closed")
            ))
    counts["regulatory"] = len(reg_rows)

    # 5. Context Notes
    ctx_rows = []
    with open(wdir / "context.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            r = json.loads(line)
            ctx_rows.append((
                r["note_id"], r["asset_id"], r["created_at"], r["author_user_id"],
                r["note_type"], r.get("inspection_id"), r["text"],
                r["valid_from"], r.get("valid_to")
            ))
    counts["context"] = len(ctx_rows)

    # 6. Parse Maintenance Reports
    chunks_to_embed = []
    chunk_metas = []
    rep_dir = wdir / "maintenance-reports"
    if rep_dir.exists():
        for rf in rep_dir.glob("*.md"):
            parsed = parse_maintenance_report(rf)
            content_hash = hashlib.sha256(parsed["full_content"].encode("utf-8")).hexdigest()
            for sec_title, sec_text in parsed["sections"].items():
                if not sec_text.strip(): continue
                full_chunk = f"{parsed['title']}\nSection: {sec_title}\n{sec_text}"
                chunks_to_embed.append(full_chunk)
                chunk_metas.append((rf.stem, "report", meta["asset_id"], sec_title, full_chunk, str(rf), content_hash))

    embeddings = [None] * len(chunks_to_embed)
    if embedding_model and chunks_to_embed:
        embeddings = [emb.tolist() for emb in embedding_model.embed(chunks_to_embed, batch_size=32)]
    counts["reports"] = len(chunks_to_embed)

    # Now execute DB operations with a fresh connection for this well
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Well record
            cur.execute("""
                INSERT INTO wells (
                    asset_id, name, asset_category, well_type,
                    area_id, team_id, in_charge_user_id, lat, lon,
                    installed_on, recorded_status, last_inspection_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (asset_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    well_type = EXCLUDED.well_type,
                    recorded_status = EXCLUDED.recorded_status,
                    last_inspection_id = EXCLUDED.last_inspection_id
            """, (
                meta["asset_id"], meta["name"], meta["asset_category"], meta["well_type"],
                meta["area_id"], meta["team_id"], meta["in_charge_user_id"],
                meta["location"]["lat"], meta["location"]["lon"],
                meta["installed_on"], meta["recorded_status"],
                meta.get("last_inspection", {}).get("inspection_id")
            ))

            # Equipment
            cur.execute("DELETE FROM well_equipment WHERE asset_id = %s", (meta["asset_id"],))
            for eq in meta.get("equipment", []):
                cur.execute("""
                    INSERT INTO well_equipment (asset_id, equipment_id, kind, model)
                    VALUES (%s, %s, %s, %s)
                """, (meta["asset_id"], eq["equipment_id"], eq["kind"], eq["model"]))

            # Inspections
            cur.executemany("""
                INSERT INTO inspections (
                    inspection_id, asset_id, inspected_at, inspector_user_id,
                    method, ambient_temp_c, wellhead_temp_c, spill_detected,
                    spill_area_pct, spill_est_volume_bbl, evidence_images, notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (inspected_at, inspection_id) DO NOTHING
            """, insp_rows)

            # Maintenance
            cur.executemany("""
                INSERT INTO maintenance_work_orders (
                    maintenance_id, asset_id, started_at, completed_at,
                    maintenance_type, equipment_kind, issue, action_taken,
                    technician_user_id, contractor, downtime_hours,
                    cost_labor, cost_parts, cost_total,
                    triggered_by_inspection_id, report_file
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (maintenance_id) DO NOTHING
            """, maint_rows)

            # Regulatory
            cur.executemany("""
                INSERT INTO regulatory_events (
                    event_id, asset_id, date, agency, event_type, protocol_id,
                    related_inspection_id, hours_to_report, detail, fine_usd, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
            """, reg_rows)

            # Context
            cur.executemany("""
                INSERT INTO context_notes (
                    note_id, asset_id, created_at, author_user_id,
                    note_type, inspection_id, text, valid_from, valid_to
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (note_id) DO NOTHING
            """, ctx_rows)

            # Document Chunks
            for (doc_id, doc_type, asset_id, sec_title, content, source_path, content_hash), emb in zip(chunk_metas, embeddings):
                cur.execute("""
                    INSERT INTO document_chunks (
                        doc_id, doc_type, asset_id, section_title,
                        content, embedding, source_path, content_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (doc_id, section_title) DO NOTHING
                """, (doc_id, doc_type, asset_id, sec_title, content, emb, source_path, content_hash))

        conn.commit()

    return counts


def load_assets(conn=None, generate_embeddings: bool = True):
    print(f"Loading {len(os.listdir(ASSETS_DIR))} asset folders into TimescaleDB...")
    well_folders = sorted([d for d in os.listdir(ASSETS_DIR) if (ASSETS_DIR / d).is_dir()])
    
    embedding_model = None
    if generate_embeddings:
        print("Initializing FastEmbed (bge-small-en-v1.5)...")
        embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")

    with get_connection() as conn:
        load_fleet_documents(conn, embedding_model)
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT asset_id FROM inspections;")
            completed_assets = set(r[0] for r in cur.fetchall())
    
    print(f"Resuming ingestion: {len(completed_assets)} assets already fully ingested.")

    total_inspections = 0
    total_maintenance = 0
    total_reports = 0

    import time
    for idx, w in enumerate(well_folders, 1):
        if w in completed_assets:
            continue

        for attempt in range(3):
            try:
                cnts = ingest_well(w, embedding_model)
                total_inspections += cnts["inspections"]
                total_maintenance += cnts["maintenance"]
                total_reports += cnts["reports"]
                break
            except Exception as e:
                print(f"Glitch on {w} (attempt {attempt+1}): {e}. Retrying in 1s...")
                time.sleep(1)

        if idx % 10 == 0 or idx == len(well_folders):
            print(f"Processed {idx}/{len(well_folders)} wells (batch inspections: {total_inspections}, maint: {total_maintenance}, reports: {total_reports})...")

    print("Asset ingestion complete!")


def run_full_ingestion():
    conn = get_connection()
    try:
        apply_migrations(conn)
        load_fleet_data(conn)
        load_assets(conn, generate_embeddings=True)
    finally:
        conn.close()


if __name__ == "__main__":
    run_full_ingestion()

"""
Dual-Level Memory System for Asset Intelligence Copilot:
1. Private User Session Memory (isolated per-user chat threads in `chat_sessions`)
2. Shared Fleet-Wide Knowledge & Rule Overrides (`feedback_overrides` and `context_notes`)
"""

import json
from datetime import datetime
from typing import Dict, Any, List, Optional
import psycopg
from psycopg.rows import dict_row

from src.config import DATABASE_URL


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


# ============================================================================
# 1. Private User Session Memory
# ============================================================================
def get_session_history(session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Retrieves the private chat message history for a given session.
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT messages FROM chat_sessions
                WHERE session_id = %s;
            """, (session_id,))
            row = cur.fetchone()
            if not row or not row.get("messages"):
                return []
            
            messages = row["messages"]
            if isinstance(messages, str):
                messages = json.loads(messages)
            return messages[-limit:]


def append_message_to_session(
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Appends a new turn (user, model, or tool event) to the private chat session.
    """
    new_entry = {
        "role": role,
        "content": content,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "metadata": metadata or {},
    }

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO chat_sessions (session_id, user_id, created_at, updated_at, messages)
                VALUES (%s, %s, NOW(), NOW(), %s::jsonb)
                ON CONFLICT (session_id) DO UPDATE SET
                    updated_at = NOW(),
                    messages = chat_sessions.messages || %s::jsonb;
            """, (session_id, user_id, json.dumps([new_entry]), json.dumps([new_entry])))
            conn.commit()


# ============================================================================
# 2. Shared Fleet Knowledge & Overrides
# ============================================================================
def get_active_overrides(target_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieves fleet-wide corrections and human annotations that modify raw calculations.
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            sql = """
                SELECT 
                    f.feedback_id, f.user_id, u.name AS user_name,
                    f.target_type, f.target_id, f.correction_payload,
                    f.rationale, f.created_at
                FROM feedback_overrides f
                LEFT JOIN users u ON f.user_id = u.user_id
                WHERE f.active = TRUE
            """
            params: List[Any] = []
            if target_type:
                sql += " AND f.target_type = %s"
                params.append(target_type)
            sql += " ORDER BY f.created_at DESC;"

            cur.execute(sql, tuple(params))
            return cur.fetchall()

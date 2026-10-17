"""
Asset Intelligence Copilot - Bedrock Agent Orchestrator
Uses AWS Bedrock (default: Amazon Nova Lite `amazon.nova-lite-v1:0` for ultra-low-cost testing,
with seamless upgrade to Amazon Nova Pro or Claude 3.5 Sonnet for final demo).

Operates with full ReAct / Automatic Tool Calling across the 9 authoritative database functions.
Enforces:
1. Truth before fluency (all metrics deterministically calculated in Python/TimescaleDB)
2. Traceable source evidence citations ([INS-...], [MNT-...], [Report: ...])
3. Generative UI component output directives (BarChartVisual, StatusCard, DataTable, MapPinView)
"""

import os
import json
import re
import logging
from typing import Dict, Any, List, Optional
import boto3

from src.config import (
    AWS_REGION,
    BEDROCK_MODEL_ID,
    BEDROCK_DEMO_MODEL_ID,
    TODAY_DATE,
    T200_TEMP_THRESHOLD_C,
    T350_TEMP_THRESHOLD_C,
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
)
from src.agent.memory import (
    get_session_history,
    append_message_to_session,
    get_active_overrides,
)

logger = logging.getLogger("copilot.agent")

# Mapping of tool names to Python implementations
TOOL_MAP = {
    "get_well_overview": get_well_overview,
    "list_wells_for_area": list_wells_for_area,
    "get_downtime_events": get_downtime_events,
    "rank_wells_by_downtime": rank_wells_by_downtime,
    "get_maintenance_history": get_maintenance_history,
    "get_inspection_history": get_inspection_history,
    "get_incident_history": get_incident_history,
    "search_technical_documents": search_technical_documents,
    "save_operator_feedback": save_operator_feedback,
}

# Bedrock Converse API Tool Specifications
BEDROCK_TOOLS = [
    {
        "toolSpec": {
            "name": "get_well_overview",
            "description": "Retrieves canonical well metadata, equipment inventory, location, team, and latest calculated status for an oil well asset.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "well_id": {"type": "string", "description": "Canonical asset ID, e.g. WELL-0001 or WELL-0121"}
                    },
                    "required": ["well_id"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "list_wells_for_area",
            "description": "Returns all oil wells located in a specific operational area with their coordinates, type, and manager.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "area_id": {"type": "string", "description": "Operational area ID, e.g. SW-23, NW-04, SE-12, NE-08"}
                    },
                    "required": ["area_id"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "get_downtime_events",
            "description": "Calculates deterministic downtime days and qualifying non-producing inspections for a single well during a time window.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "well_id": {"type": "string", "description": "Asset ID, e.g. WELL-0005"},
                        "start_time": {"type": "string", "description": "Start date YYYY-MM-DD (e.g. 2026-06-01)"},
                        "end_time": {"type": "string", "description": "End date YYYY-MM-DD (e.g. 2026-08-31)"}
                    },
                    "required": ["well_id", "start_time", "end_time"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "rank_wells_by_downtime",
            "description": "Deterministically ranks all wells in an operational area by downtime days during a date window (e.g. Summer: 2026-06-01 to 2026-08-31).",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "area_id": {"type": "string", "description": "Operational area ID, e.g. SW-23"},
                        "start_time": {"type": "string", "description": "Start date YYYY-MM-DD (e.g. 2026-06-01)"},
                        "end_time": {"type": "string", "description": "End date YYYY-MM-DD (e.g. 2026-08-31)"},
                        "limit": {"type": "integer", "description": "Maximum number of ranked wells to return (default: 10)"}
                    },
                    "required": ["area_id", "start_time", "end_time"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "get_maintenance_history",
            "description": "Retrieves maintenance work orders, corrective/preventative actions, equipment repaired, labor/parts costs, and attached report files for a well.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "well_id": {"type": "string", "description": "Asset ID, e.g. WELL-0005"},
                        "start_time": {"type": "string", "description": "Optional start date YYYY-MM-DD"},
                        "end_time": {"type": "string", "description": "Optional end date YYYY-MM-DD"},
                        "limit": {"type": "integer", "description": "Maximum records to return (default: 10)"}
                    },
                    "required": ["well_id"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "get_inspection_history",
            "description": "Retrieves weekly drone thermal inspection records for an asset, including ambient temp, wellhead temp, Delta T, spill detection, and inspector notes.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "well_id": {"type": "string", "description": "Asset ID, e.g. WELL-0005"},
                        "start_time": {"type": "string", "description": "Optional start date YYYY-MM-DD"},
                        "end_time": {"type": "string", "description": "Optional end date YYYY-MM-DD"},
                        "only_spills": {"type": "boolean", "description": "Set to true to only filter inspections where spill was detected"},
                        "limit": {"type": "integer", "description": "Maximum inspections to return (default: 15)"}
                    },
                    "required": ["well_id"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "get_incident_history",
            "description": "Retrieves regulatory agency violations, spill reports, protocol non-compliances (SR-01, SR-02), fines, and agency close-out statuses for an asset.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "well_id": {"type": "string", "description": "Asset ID, e.g. WELL-0005"},
                        "start_time": {"type": "string", "description": "Optional start date YYYY-MM-DD"},
                        "end_time": {"type": "string", "description": "Optional end date YYYY-MM-DD"}
                    },
                    "required": ["well_id"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "search_technical_documents",
            "description": "Performs hybrid dense-vector and full-text search over engineering maintenance reports, SOPs, and equipment manuals in pgvector.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Technical query, e.g. 'sucker rod fracture' or 'packing seal leak'"},
                        "asset_id": {"type": "string", "description": "Optional asset ID to scope chunks"},
                        "doc_type": {"type": "string", "description": "Optional doc type filter: 'report', 'sop', or 'well_type'"},
                        "limit": {"type": "integer", "description": "Number of chunks (default: 5)"}
                    },
                    "required": ["query"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "save_operator_feedback",
            "description": "Saves an operator human correction or sensor override into durable system memory.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "Operator user ID, e.g. U-101"},
                        "target_type": {"type": "string", "description": "Target type: 'raw_reading', 'spill_detection', 'well_status'"},
                        "target_id": {"type": "string", "description": "Inspection ID or Asset ID being overridden"},
                        "correction_payload_json": {"type": "string", "description": "JSON string of correction values"},
                        "rationale": {"type": "string", "description": "Physical operational justification for override"}
                    },
                    "required": ["user_id", "target_type", "target_id", "correction_payload_json", "rationale"]
                }
            }
        }
    }
]

SYSTEM_INSTRUCTION = f"""You are the Asset Intelligence Copilot, an expert AI operational assistant for oil and gas field managers and drone inspection teams.

CORE PRINCIPLES & OPERATIONAL GROUND TRUTH:
1. TRUTH BEFORE FLUENCY: Never guess, hallucinate, or calculate downtime in model memory. Always call `rank_wells_by_downtime` or `get_downtime_events` for authoritative downtime numbers.
2. PRODUCTION RULES:
   - Thermal differential Delta T = wellhead_temp_c - ambient_temp_c.
   - T200 wells require Delta T >= {T200_TEMP_THRESHOLD_C} °C to be producing.
   - T350 wells require Delta T >= {T350_TEMP_THRESHOLD_C} °C to be producing.
   - Each weekly inspection where Delta T is below threshold adds exactly 7 days of downtime.
   - Missed drone inspections add 0 days.
   - `maintenance.downtime_hours` is only repair duration and is NOT well downtime.
3. SUMMER & DATE RANGES:
   - "This summer" refers to Summer 2026: 2026-06-01 to 2026-08-31.
   - "Last year's summer" refers to Summer 2025: 2025-06-01 to 2025-08-31.
   - Current system date: {TODAY_DATE}.
4. EVIDENCE CITATIONS:
   - Every factual statement must cite its authoritative source ID in square brackets, e.g., `[INS-0005-2026-06-15]`, `[MNT-0005-008]`, `[SOP-SR: Section 4]`, `[Report: MNT-0005-008#Root cause]`.
5. EXPLANATIONS & ROOT CAUSES:
   - When answering why a well was down, cross-reference the non-producing inspection dates with maintenance work orders (`get_maintenance_history`) and search technical reports (`search_technical_documents`).
   - Distinguish direct root causes documented in reports from circumstantial maintenance.
6. GENERATIVE UI DIRECTIVES:
   - Conclude your answer with a JSON code block with language identifier `json:visual` specifying a generative UI component:
     - `BarChartVisual`: For downtime comparisons (keys: `title`, `data` with `name` and `downtime_days`, `xKey`, `yKey`, `color`).
     - `StatusCard`: For single well overviews (keys: `title`, `status`, `metrics`).
     - `DataTable`: For tabular inspection/maintenance logs (keys: `columns`, `rows`).
     - `MapPinView`: For geographic locations (keys: `center`, `pins` with `lat`, `lon`, `label`, `status`).
"""


class AgentOrchestrator:
    def __init__(self, region_name: Optional[str] = None):
        self.region_name = region_name or AWS_REGION
        self.client = boto3.client("bedrock-runtime", region_name=self.region_name)

    def run_query(
        self,
        prompt: str,
        user_id: str = "U-101",
        session_id: str = "default-session",
        model_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes a user query using AWS Bedrock with full ReAct / Automatic Tool Calling.
        """
        selected_model = model_name or BEDROCK_MODEL_ID

        # Prepare active fleet overrides context
        overrides = get_active_overrides()
        overrides_prompt = ""
        if overrides:
            overrides_prompt = "\nACTIVE HUMAN CORRECTIONS & FLEET OVERRIDES IN EFFECT:\n"
            for o in overrides[:5]:
                overrides_prompt += f"- Target {o['target_id']}: {o['rationale']} (Payload: {o['correction_payload']})\n"

        # Resolve authenticated operator and assigned area
        user_context_str = ""
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute("""
                    SELECT u.user_id, u.name, u.role, u.team_id, a.area_id, a.name AS area_name
                    FROM users u
                    LEFT JOIN areas a ON u.team_id = a.team_id
                    WHERE u.user_id = %s;
                """, (user_id,))
                user_row = cur.fetchone()
                if user_row:
                    user_context_str = (
                        f"\nAUTHENTICATED OPERATOR CONTEXT:\n"
                        f"- Operator Name: {user_row['name']} (ID: {user_row['user_id']}, Role: {user_row['role']})\n"
                        f"- Team: {user_row['team_id']}\n"
                        f"- Assigned Area: {user_row['area_id']} ({user_row['area_name']})\n"
                        f"CRITICAL: When the operator asks about 'my area', 'my wells', or does not specify an area, ALWAYS scope queries to their assigned area: '{user_row['area_id']}'.\n"
                    )
        except Exception as ue:
            logger.warning(f"Could not resolve user context for {user_id}: {ue}")

        full_system_text = SYSTEM_INSTRUCTION + overrides_prompt + user_context_str

        # Retrieve prior session messages and format for context
        history = get_session_history(session_id, limit=4)
        history_context = ""
        if history:
            history_context = "\nRECENT CONVERSATION CONTEXT:\n"
            for msg in history:
                role = "User" if msg.get("role") == "user" else "Assistant"
                history_context += f"{role}: {msg.get('content', '')}\n"

        full_prompt = prompt
        if history_context:
            full_prompt = f"{history_context}\nCURRENT OPERATOR QUESTION:\n{prompt}"

        # Build initial messages list for Bedrock Converse
        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": [{"text": full_prompt}]}
        ]

        # Multi-turn tool calling loop (up to 5 turns)
        final_text = ""
        for turn in range(5):
            resp = self.client.converse(
                modelId=selected_model,
                messages=messages,
                system=[{"text": full_system_text}],
                toolConfig={"tools": BEDROCK_TOOLS},
                inferenceConfig={"maxTokens": 2048, "temperature": 0.1},
            )

            stop_reason = resp.get("stopReason")
            assistant_msg = resp["output"]["message"]
            messages.append(assistant_msg)

            # Accumulate any text emitted by the model during this turn
            for b in assistant_msg.get("content", []):
                if "text" in b:
                    final_text += b["text"] + "\n"

            if stop_reason == "tool_use":
                tool_results = []
                for block in assistant_msg.get("content", []):
                    if "toolUse" in block:
                        tu = block["toolUse"]
                        tu_id = tu["toolUseId"]
                        tool_name = tu["name"]
                        tool_input = tu.get("input", {})

                        logger.info(f"Bedrock ({selected_model}) called tool: {tool_name} with: {tool_input}")

                        tool_fn = TOOL_MAP.get(tool_name)
                        if tool_fn:
                            try:
                                raw_res = tool_fn(**tool_input)
                                parsed_res = json.loads(raw_res) if isinstance(raw_res, str) else raw_res
                            except Exception as te:
                                parsed_res = {"error": f"Tool execution failed: {str(te)}"}
                        else:
                            parsed_res = {"error": f"Tool '{tool_name}' not recognized."}

                        tool_results.append({
                            "toolResult": {
                                "toolUseId": tu_id,
                                "content": [{"json": parsed_res}],
                                "status": "success"
                            }
                        })

                # Append tool results as user turn and continue conversation
                messages.append({"role": "user", "content": tool_results})
            else:
                break

        # Clean thinking blocks
        # clean_text = re.sub(r"<thinking>.*?</thinking>", "", final_text, flags=re.DOTALL).strip()
        # Clean thinking blocks
        clean_text = re.sub(
            r"<thinking>.*?</thinking>",
            "",
            final_text,
            flags=re.DOTALL | re.IGNORECASE
        ).strip()

        # Extract visual data and remove visual JSON from the response text
        visual_data = None

        visual_types = [
            "BarChartVisual",
            "StatusCard",
            "DataTable",
            "MapPinView"
        ]

        # Match supported visual formats:
        # 1. <json:visual>...</json:visual>
        # 2. ```json:visual ... ```
        # 3. ```json ... ``` containing a visual or table
        visual_patterns = [
            r"<json:visual>\s*([\s\S]*?)\s*</json:visual>",
            r"```json:visual\s*([\s\S]*?)\s*```",
            r"```json\s*([\s\S]*?)\s*```"
        ]

        for pattern in visual_patterns:
            matches = list(re.finditer(pattern, clean_text, re.IGNORECASE))

            for match in matches:
                try:
                    parsed_block = json.loads(match.group(1).strip())

                    if not isinstance(parsed_block, dict):
                        continue

                    candidate = parsed_block

                    # Unwrap nested visual formats:
                    # {"DataTable": {...}}
                    # {"StatusCard": {...}}
                    for visual_type in visual_types:
                        if (
                            visual_type in candidate
                            and isinstance(candidate[visual_type], dict)
                        ):
                            candidate = {
                                "type": visual_type,
                                **candidate[visual_type]
                            }
                            break

                    # Handle {"json:visual": {...}}
                    if (
                        "json:visual" in candidate
                        and isinstance(candidate["json:visual"], dict)
                    ):
                        candidate = candidate["json:visual"]

                    # Identify the visualization type
                    visual_type = (
                        candidate.get("type")
                        or candidate.get("component")
                    )

                    if visual_type not in visual_types:
                        visual_type = next(
                            (
                                key for key in visual_types
                                if key in candidate
                            ),
                            None
                        )

                    # Automatically convert ordinary JSON tables
                    # into a DataTable visualization
                    if (
                        not visual_type
                        and isinstance(candidate.get("columns"), list)
                        and isinstance(candidate.get("rows"), list)
                    ):
                        candidate = {
                            "type": "DataTable",
                            "title": candidate.get("title", "Query Results"),
                            "columns": candidate["columns"],
                            "rows": candidate["rows"]
                        }
                        visual_type = "DataTable"

                    # Accept only recognized visual structures
                    if visual_type in visual_types:
                        if visual_type not in (
                            candidate.get("type"),
                            candidate.get("component")
                        ):
                            candidate = {
                                "type": visual_type,
                                **candidate.get(visual_type, candidate)
                            }

                        visual_data = candidate

                        # Remove the extracted visual block from chat text
                        clean_text = (
                            clean_text[:match.start()]
                            + clean_text[match.end():]
                        ).strip()

                        break

                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    logger.warning(
                        f"Could not parse visual JSON block: {exc}"
                    )

            if visual_data is not None:
                break

        # Remove any remaining thinking or visual markup
        clean_text = re.sub(
            r"<thinking>.*?</thinking>",
            "",
            clean_text,
            flags=re.DOTALL | re.IGNORECASE
        )

        clean_text = re.sub(
            r"</?json:visual>",
            "",
            clean_text,
            flags=re.IGNORECASE
        )

        clean_text = re.sub(r"\n{3,}", "\n\n", clean_text).strip()

        # Unwrap nested visual if needed (e.g. {"BarChartVisual": {...}})
        if visual_data and isinstance(visual_data, dict):
            for k in ["BarChartVisual", "StatusCard", "DataTable", "MapPinView"]:
                if k in visual_data and isinstance(visual_data[k], dict):
                    visual_data = {"type": k, **visual_data[k]}
                    break

        # Extract citation tags
        citations = list(set(re.findall(r"\[(INS-[A-Za-z0-9\-]+|MNT-[A-Za-z0-9\-]+|SOP-[A-Za-z0-9\-]+|Report:[^\]]+)\]", final_text)))

        # Persist turn in private session memory
        try:
            append_message_to_session(session_id, user_id, "user", prompt)
            append_message_to_session(session_id, user_id, "assistant", clean_text, metadata={"visual": visual_data, "citations": citations, "model": selected_model})
        except Exception as pe:
            logger.warning(f"Could not persist session: {pe}")

        return {
            "text": clean_text,
            "visual": visual_data,
            "citations": citations,
            "model_used": selected_model,
            "session_id": session_id,
        }

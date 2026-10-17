import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/tsdb"
)
ASYNC_DATABASE_URL = os.getenv(
    "ASYNC_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/tsdb"
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# AWS Bedrock Configuration
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")  # Testing/Dev ultra-low-cost model
BEDROCK_DEMO_MODEL_ID = os.getenv("BEDROCK_DEMO_MODEL_ID", "us.amazon.nova-pro-v1:0")  # Final Demo stronger model

PORT = int(os.getenv("PORT", "8000"))

BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLE_DATA_DIR = BASE_DIR / "test-data/dataset"
ASSETS_DIR = SAMPLE_DATA_DIR / "assets"
FLEET_DIR = SAMPLE_DATA_DIR / "fleet"

# Operational Constants
TODAY_DATE = "2026-10-16"
T200_TEMP_THRESHOLD_C = 15.0
T350_TEMP_THRESHOLD_C = 20.0
DOWNTIME_DAYS_PER_INSPECTION = 7
SPILL_REPORTABLE_THRESHOLD_BBL = 5.0
SPILL_CONTAINMENT_LIMIT_HOURS = 4.0
SPILL_WRITTEN_NOTICE_LIMIT_HOURS = 24.0

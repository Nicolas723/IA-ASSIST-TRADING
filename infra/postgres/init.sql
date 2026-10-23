-- =============================================================================
-- PostgreSQL + TimescaleDB Initialization
-- =============================================================================
-- This script runs once when the PostgreSQL container starts for the first time.
-- It sets up TimescaleDB extensions and creates the database structure.

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable pg_trgm for fast text search
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- TimescaleDB Hypertables (time-series data)
-- These will be managed via Alembic migrations in Phase 1.
-- Defined here as documentation of the intended schema.
-- ---------------------------------------------------------------------------

-- Note: Alembic migrations will create the actual tables.
-- The hypertable conversion will be done via custom migration operations.

-- Example (created by Alembic in Phase 1):
-- SELECT create_hypertable('candles', 'time', chunk_time_interval => INTERVAL '1 day');
-- SELECT create_hypertable('ticks', 'time', chunk_time_interval => INTERVAL '1 hour');
-- SELECT create_hypertable('audit_events', 'created_at', chunk_time_interval => INTERVAL '1 month');

-- ---------------------------------------------------------------------------
-- Initial database health check
-- ---------------------------------------------------------------------------
SELECT 'TimescaleDB initialized successfully' AS status;
SELECT extname, extversion FROM pg_extension WHERE extname = 'timescaledb';

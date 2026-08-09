# Property Intelligence ALL LAYERS V1 - MIGRATION FIX

Fixes Railway startup crash:
`psycopg.errors.UndefinedColumn: column "fingerprint" does not exist`

Cause:
Your PostgreSQL database already contained older Property Intelligence tables.
`CREATE TABLE IF NOT EXISTS` does not add new columns to existing tables.

This release:
- preserves existing PostgreSQL data
- creates missing tables
- adds missing columns safely with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
- creates indexes only after the columns exist
- requires no manual SQL and no new database

Deploy these files to the SAME backend GitHub repository.
Keep the SAME Railway service, Postgres, domain, DATABASE_URL and Gemini key.

After deployment, `/health` should show version `4.0.1`.

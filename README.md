# Property Intelligence Unified Workspace V6
Version 5.5.0

ONE-TIME DATABASE SCHEMA FIX

The bulk magazine extractor is working. PostgreSQL rejected a real listing because old columns such as rent_or_sale were VARCHAR(30).

Example:
rent_or_sale = Sale, Purchase, Renting, Collaboration

V6 automatically widens flexible property and requirement text columns to PostgreSQL TEXT at startup.
Existing database records are preserved. Do NOT delete Postgres.

Deploy:
Replace all files from this ZIP in the SAME backend GitHub repository.
Keep the same Railway service, database, domain and variables.
After deploy, /health must show 5.5.0.
Then upload the magazine again.

Keep:
MAX_UPLOAD_MB=100
PDF_PAGES_PER_BATCH=5
GEMINI_MODEL=gemini-3.1-flash-lite
and your existing DATABASE_URL, GEMINI_API_KEY, ADMIN_CODE, TEAM_CODE, SESSION_SECRET.

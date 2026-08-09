# Property Intelligence Agent - ALL LAYERS V1

Single Railway service + single Postgres database.

Layers included:
1. Manual property & requirement entry
2. WhatsApp/email/text ingestion
3. Photo/newspaper/magazine/PDF ingestion
4. CSV import
5. Gemini structured extraction
6. Normalization and source lineage
7. Deduplication
8. PostgreSQL organized database
9. Verification/review layer
10. Rule-based property matcher
11. Generic webhook intake
12. Searchable database UI
13. CSV export
14. AI job audit log
15. Swagger API docs

Railway variables:
DATABASE_URL=${{Postgres.DATABASE_URL}}
GEMINI_API_KEY=your real key
GEMINI_MODEL=gemini-3.1-flash-lite
MAX_UPLOAD_MB=25

Replace the current repo root with:
app.py
Dockerfile
requirements.txt
.env.example
README.md

Keep the SAME Railway service, Postgres and domain.

After deployment:
- /health
- /api/status
- /workspace
- /database
- /docs

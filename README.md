# Property Intelligence Unified Workspace V1

One Railway service, one Postgres database and one public domain.

Team:
- upload photo/magazine/PDF/CSV
- paste WhatsApp/email text
- add property
- add requirement
- run matcher
- view non-sensitive database

Admin:
- everything Team can do
- sources
- AI jobs
- verification history
- internal data controls

Upload fix:
Uploads return immediately with ACCEPTED and are processed by Gemini in FastAPI background tasks.

Railway variables:
DATABASE_URL=${{Postgres.DATABASE_URL}}
GEMINI_API_KEY=your-real-key
GEMINI_MODEL=gemini-3.1-flash-lite
MAX_UPLOAD_MB=25
ADMIN_CODE=choose-strong-admin-code
TEAM_CODE=choose-team-code
SESSION_SECRET=choose-long-random-secret

Deploy to your existing backend repository and existing Railway backend service.
After verifying this service, remove the old separate dashboard service/domain. Keep Postgres.

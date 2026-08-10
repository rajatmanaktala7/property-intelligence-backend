# Property Intelligence Unified Workspace V2

Version: 5.1.0

## Main fix
Opening `/workspace` on a new Android phone, another laptop, iPhone or Mac now
redirects automatically to `/login` instead of showing:

`{"detail":"Login required"}`

## One domain
Use only your active Railway domain.

Opening the root URL:
- if not logged in -> `/login`
- if already logged in -> `/workspace`

## Roles
TEAM:
- file/photo/magazine/PDF/CSV upload
- WhatsApp/email text ingestion
- add property
- add requirement
- run matcher
- view non-sensitive records

ADMIN:
- everything Team can do
- Sources
- AI Jobs
- Verification
- internal/admin records

## Upload behavior
Uploads are accepted immediately and Gemini processing continues in the background.

## Railway variables
Keep:
DATABASE_URL=${{Postgres.DATABASE_URL}}
GEMINI_API_KEY=your-real-key
GEMINI_MODEL=gemini-3.1-flash-lite
MAX_UPLOAD_MB=25

Add/keep:
ADMIN_CODE=your-admin-code
TEAM_CODE=your-team-code
SESSION_SECRET=your-long-random-secret

## Deployment
Replace the full files in the SAME backend GitHub repository:
- app.py
- Dockerfile
- requirements.txt
- .env.example
- README.md

Keep the SAME Railway service, Postgres and active domain.
Do not create a new repo, service or database.

After deploy:
1. /health -> version 5.1.0
2. open root domain on Android -> login page
3. Team login -> workspace
4. Admin login -> same workspace with Admin tab

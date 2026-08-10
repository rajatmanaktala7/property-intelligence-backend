# Property Intelligence Unified Workspace V8 - Production Hardened
Version 6.0.0

This release consolidates all previous fixes and adds the missing production safeguards.

## Fixed permanently
- mobile login redirects
- Team/Admin single workspace
- streamed upload with progress
- 100 MB workspace upload setting
- 50 MB PDF guard
- Gemini API extraction
- large magazine page batching
- adaptive JSON retry/splitting
- flexible PostgreSQL TEXT migrations
- false duplicate reduction
- DATABASE-BACKED property and requirement IDs
- page/batch audit records
- one failed batch does not stop the whole magazine
- AI WhatsApp draft created automatically after matcher runs

## Important ID fix
Old IDs used a short random suffix. At bulk scale, collisions are possible.
V8 uses PostgreSQL sequences:
PROP-YYYYMMDD-0000000001
REQ-YYYYMMDD-0000000001
This is concurrency-safe and avoids duplicate property_id crashes.

## WhatsApp
After POST /api/match/{requirement_id}, V8:
1. ranks matches
2. creates a professional WhatsApp message using Gemini
3. excludes owner/broker private contacts from the AI prompt
4. stores the message in pi_message_drafts
5. returns it to the workspace
6. marks it READY_FOR_REVIEW

It does NOT send the message automatically.

## Railway variables
DATABASE_URL=${{Postgres.DATABASE_URL}}
GEMINI_API_KEY=your-real-key
GEMINI_MODEL=gemini-3.1-flash-lite
MAX_UPLOAD_MB=100
PDF_PAGES_PER_BATCH=2
ADMIN_CODE=your-admin-code
TEAM_CODE=your-team-code
SESSION_SECRET=your-long-random-secret

## Deploy
Replace the COMPLETE files in the SAME backend GitHub repository.
Keep the SAME Railway service, Postgres and domain.
Do not create a new repository/database/service.

After deploy:
- /health must show 6.0.0
- login
- upload one magazine
- Admin > batches shows page-range status
- run matcher
- WhatsApp draft appears automatically

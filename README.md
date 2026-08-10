# Property Intelligence Unified Workspace V4 - Progress Upload Fix

Version: 5.3.0

## Changes
- Removed raw JSON/script-style output from the file upload box.
- Added a clean upload progress bar: 0% to 100%.
- Shows friendly messages:
  - Uploading...
  - Upload completed
  - AI is reading and organizing the file
  - AI processing completed
  - clear error message if extraction fails
- Added background job status checking.
- Improved MIME handling for Android/Windows browsers.
- Accepts JPG, JPEG, PNG, WEBP, PDF, CSV and TXT.
- Keeps streamed uploads from V3.
- Default workspace upload limit remains 100 MB.
- PDFs remain limited to 50 MB for Gemini processing.

## Railway
Replace the COMPLETE files in the existing backend repository:
- app.py
- Dockerfile
- requirements.txt
- .env.example
- README.md

Keep:
DATABASE_URL=${{Postgres.DATABASE_URL}}
GEMINI_API_KEY=your-real-key
GEMINI_MODEL=gemini-3.1-flash-lite
MAX_UPLOAD_MB=100
ADMIN_CODE=your-admin-code
TEAM_CODE=your-team-code
SESSION_SECRET=your-long-random-secret

No new repository, database, Railway service or domain is required.

After deploy:
- /health should show version 5.3.0
- login
- choose a small JPG or PNG
- upload should show percentage progress rather than JSON

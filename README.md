# Property Intelligence Unified Workspace V7
Version 5.6.0

Fixes:
Invalid JSON: EOF while parsing a string.

Why:
Dense magazine pages can produce more structured JSON than one Gemini response can safely return.

V7:
- starts PDF extraction at 2 pages per batch
- automatically splits a failed multi-page batch into smaller ranges
- retries a single dense page with compact-output instructions
- increases output allowance
- strips possible markdown fences before JSON validation
- preserves earlier schema, mobile, upload and bulk-extraction fixes

Railway:
PDF_PAGES_PER_BATCH=2
MAX_UPLOAD_MB=100

Keep existing DATABASE_URL, GEMINI_API_KEY, GEMINI_MODEL, ADMIN_CODE, TEAM_CODE and SESSION_SECRET.

Replace the complete files in the SAME backend repository.
After deployment, /health must show version 5.6.0.
Then upload the magazine again.

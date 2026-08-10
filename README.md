# Property Intelligence V13 - No-JavaScript Core Workspace
Version 7.0.0

This version fixes the repeated dead-link problem by removing JavaScript as a dependency for core navigation and actions.

Core pages are normal server routes:
- /workspace
- /database-page
- /status-page
- /admin-page

Core navigation uses ordinary HTML links.
Core actions use ordinary HTML forms:
- Upload
- Add Property
- Add Requirement
- Verify Property
- Run Matcher + Create WhatsApp Draft

So even if browser JavaScript fails, the core system still works.

Deploy all files into the SAME backend GitHub repository.
Keep the SAME Railway service, Postgres database and public domain.

After deployment:
1. /health must show version 7.0.0
2. Open root domain
3. Login
4. Test Operations, Database, Status, Admin
5. Upload one small image

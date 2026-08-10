# Property Intelligence Unified Workspace V12 - Navigation Fix
Version 6.4.0

Critical fix:
V11 had a malformed JavaScript string in the Database/Admin dynamic-tab code.
That single syntax error stopped the entire browser script, which made Operations,
Database, Admin, Upload, Edit and other controls appear dead.

V12:
- replaces fragile inline onclick string generation with DOM event listeners
- hardens section navigation
- hardens API response parsing
- explicitly sets navigation buttons to type=button
- adds a visible browser error banner
- retains all V11 features

Validation completed:
- Python syntax: PASS
- Browser JavaScript syntax using node --check: PASS

Deploy all files from this ZIP into the SAME backend repository.
Keep the SAME Railway service, database and domain.

After deployment:
1. /health must show 6.4.0
2. Log out and log back in
3. Windows: Ctrl+F5
4. Android: close old tab and reopen the main domain
5. Test Operations, Database, Admin, Upload

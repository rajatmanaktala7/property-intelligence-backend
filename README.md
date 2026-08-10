# Property Intelligence Unified Workspace V5
Version 5.4.0

Fixes the 2-record magazine problem by:
- splitting PDFs into 5-page AI batches
- extracting every listing, not summarizing the magazine
- saving each batch immediately
- preventing sparse records from collapsing into one false duplicate
- showing cumulative records during processing

Railway: add PDF_PAGES_PER_BATCH=5.
Keep the same repo, Railway service, Postgres and domain.
Replace all files from this ZIP, redeploy, verify /health says 5.4.0, then upload the magazine again.

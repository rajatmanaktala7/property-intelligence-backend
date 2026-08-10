# Property Intelligence Unified Workspace V10 - Drag & Drop Photos
Version 6.2.0

## New manual property media workflow
- Drag and drop multiple property photos directly into the Add Property form.
- Click the drop zone as an alternative to choose photos.
- Preview selected photos before saving.
- Remove individual selected photos before upload.
- Save Property first, then photos are automatically attached to that Property ID.
- Public unguessable media URLs are generated from the same Railway domain.
- image_urls on the property is updated automatically.
- Match + WhatsApp can include the property photo links.
- Existing external image URL, video URL and brochure URL fields are retained.

## Storage
For simplicity, V10 stores property images in PostgreSQL BYTEA through pi_property_media.
This avoids Google Drive link creation and works immediately with the existing Railway setup.

Recommended initial limits:
MAX_PROPERTY_IMAGES=12
MAX_IMAGE_MB=10

For very large media libraries later, migrate media storage to S3/R2/Cloudinary while keeping the same property/media API.

## Deploy
Replace all files from this ZIP in the SAME backend GitHub repository.
Keep the SAME Railway service, Postgres and public domain.

Railway variables:
DATABASE_URL=${{Postgres.DATABASE_URL}}
GEMINI_API_KEY=your-valid-key
GEMINI_MODEL=gemini-3.1-flash-lite
MAX_UPLOAD_MB=100
PDF_PAGES_PER_BATCH=2
MAX_PROPERTY_IMAGES=12
MAX_IMAGE_MB=10
ADMIN_CODE=your-admin-code
TEAM_CODE=your-team-code
SESSION_SECRET=your-long-random-secret

After deployment /health must show version 6.2.0.

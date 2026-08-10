# Property Intelligence V14 - Direct Database Edit
Version 7.1.0

## Simple team workflow
Database > Properties > Edit > change details > Save Changes.

Each property row now has an Edit button.

Editing:
- keeps the same Property ID
- loads existing values into the form
- saves only to the same property record
- records changed values in the audit/verification log
- does NOT automatically change Last Verified

Verification:
Use Verify Today only after the property has actually been reconfirmed.

Core pages and actions remain server-rendered, so JavaScript is not required.

## Deployment
Replace ALL files from this ZIP in the SAME backend GitHub repository.
Do not create a new repository, Railway service, Postgres database or domain.

After deployment, /health must show version 7.1.0.

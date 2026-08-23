import app as core
from newspaper_intelligence import register

# Existing application and all current routes remain intact.
app = core.app
register(core)

# Add a visible Newspaper Capture shortcut to the existing main workspace without
# rewriting the large dashboard implementation.
if hasattr(core, '_v4_page'):
    _original_v4_page = core._v4_page
    def _v4_page_with_newspaper(role):
        html = _original_v4_page(role)
        shortcut = '''<a href="/newspaper" title="Newspaper Property Capture" style="position:fixed;right:22px;bottom:22px;z-index:9999;background:#b42318;color:#fff;padding:13px 18px;border-radius:999px;text-decoration:none;font:700 14px Arial;box-shadow:0 8px 24px #0003">📰 Newspaper Capture</a>'''
        return html.replace('</body>', shortcut + '</body>') if '</body>' in html else html + shortcut
    core._v4_page = _v4_page_with_newspaper

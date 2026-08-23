import app as core
from newspaper_intelligence import register

# Existing application and every settled route remain intact.
app = core.app
register(core)

# FINAL EXECUTION DASHBOARD V9
# Adds Newspaper Intelligence navigation only.
if hasattr(core, "_v4_page"):
    _original_v4_page = core._v4_page

    def _v4_page_with_newspaper(role):
        html = _original_v4_page(role)

        newspaper_section = """
<section id="newspaper-intelligence-v9" style="margin:28px 0 10px">
  <h2 style="margin:0 0 14px;font-size:22px">📰 Newspaper Intelligence</h2>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px">
    <a href="/newspaper#capture-newspaper"
       style="display:block;background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:18px;text-decoration:none;color:#111827;box-shadow:0 1px 3px #0000000d">
      <div style="font-size:18px;font-weight:800;margin-bottom:7px">📷 Capture Newspaper</div>
      <div style="font-size:13px;color:#667085;line-height:1.45">Upload or take a full newspaper photo. AI Vision extracts each property into a separate clean record.</div>
    </a>
    <a href="/newspaper#newspaper-database"
       style="display:block;background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:18px;text-decoration:none;color:#111827;box-shadow:0 1px 3px #0000000d">
      <div style="font-size:18px;font-weight:800;margin-bottom:7px">🗄️ Newspaper Property Database</div>
      <div style="font-size:13px;color:#667085;line-height:1.45">Search, view, edit, verify, delete and export newspaper properties stored safely in PostgreSQL.</div>
    </a>
  </div>
</section>
"""

        markers = [
            "<h2>Database & Admin</h2>",
            "<h2>Database &amp; Admin</h2>",
            '<h2 class="section-title">Database & Admin</h2>',
        ]
        inserted = False
        for marker in markers:
            if marker in html:
                html = html.replace(marker, newspaper_section + marker, 1)
                inserted = True
                break

        if not inserted:
            text_marker = "Database & Admin"
            idx = html.find(text_marker)
            if idx >= 0:
                start = html.rfind("<", 0, idx)
                if start >= 0:
                    html = html[:start] + newspaper_section + html[start:]
                    inserted = True

        if not inserted:
            if "</body>" in html:
                html = html.replace("</body>", newspaper_section + "</body>", 1)
            else:
                html += newspaper_section

        shortcut = """<a href="/newspaper" title="Newspaper Intelligence"
style="position:fixed;right:22px;bottom:22px;z-index:9999;background:#b42318;color:#fff;padding:12px 17px;border-radius:999px;text-decoration:none;font:700 14px Arial;box-shadow:0 8px 24px #0003">📰 Newspaper</a>"""
        if "</body>" in html:
            html = html.replace("</body>", shortcut + "</body>", 1)
        else:
            html += shortcut
        return html

    core._v4_page = _v4_page_with_newspaper

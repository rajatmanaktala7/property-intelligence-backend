from __future__ import annotations
import re
VERSION='1.0.0-CANONICAL-REQUIREMENT-READABILITY'

def install():
    import alliance_requirement_restore_v1235 as req
    if getattr(req,'_READABILITY_PATCH_V1',False):
        return {'status':'ALREADY_INSTALLED','version':VERSION}
    old_shell=req._shell
    old_table=req._table

    def shell(title,body):
        out=old_shell(title,body)
        css="""<style id='req-readable-v1'>
        table{font-size:12px!important;font-weight:650}th,td{padding:7px 8px!important;line-height:1.28}
        .reqzoom{display:flex;align-items:center;gap:6px;margin:0 0 9px 0;position:sticky;left:0;width:max-content;background:#fff;padding:6px;border:1px solid #d0d5dd;border-radius:7px;z-index:6}
        .reqzoom button{padding:5px 9px;font-weight:900}.desc{min-width:330px!important;max-width:540px!important}
        </style>"""
        js="""<script id='req-zoom-js'>(function(){let z=Number(localStorage.getItem('allianceRequirementZoom')||100);function a(){document.querySelectorAll('.tablebox table').forEach(t=>{t.style.fontSize=(12*z/100)+'px'});document.querySelectorAll('.tablebox th,.tablebox td').forEach(x=>{x.style.padding=(7*z/100)+'px '+(8*z/100)+'px'})}window.reqZoom=function(d){z=Math.max(70,Math.min(170,z+d*10));localStorage.setItem('allianceRequirementZoom',z);a()};window.reqZoomReset=function(){z=100;localStorage.setItem('allianceRequirementZoom',z);a()};a()})();</script>"""
        controls="<div class='reqzoom'><b>Table Zoom</b><button type='button' onclick='reqZoom(-1)'>−</button><button type='button' onclick='reqZoom(1)'>+</button><button type='button' onclick='reqZoomReset()'>Reset</button></div>"
        out=out.replace('</head>',css+'</head>')
        if '<div class="tablebox">' in out:out=out.replace('<div class="tablebox">',controls+'<div class="tablebox">',1)
        return out.replace('</body>',js+'</body>')

    def reorder_table_html(page):
        # Canonical table order: keep marketing/contact fields prominent and separate.
        # Move sparsely captured Property Type + Area after the matcher action.
        order=[0,1,2,3,4,5,6,9,10,11,7,8,12,13,14,15,16]
        headers=['Date / Time','Original Requirement','Client / Company','Contact Name','Contact No.','Location','Category / Use','Rent / Sale','Budget','Action','Property Type','Area','Verification','Assigned To','Source','Source ID','Requirement ID']
        page=re.sub(r'<thead><tr>.*?</tr></thead>',"<thead><tr>"+''.join('<th>'+h+'</th>' for h in headers)+'</tr></thead>',page,count=1,flags=re.S)
        def row(m):
            attrs=m.group(1);inner=m.group(2);cells=re.findall(r'<td(?:\s[^>]*)?>.*?</td>',inner,flags=re.S)
            if len(cells)!=17:return m.group(0)
            return '<tr'+attrs+'>'+''.join(cells[i] for i in order)+'</tr>'
        return re.sub(r'<tr([^>]*)>(.*?)</tr>',row,page,flags=re.S)

    def table(e,source,q,location,transaction,status,assigned,limit):
        return reorder_table_html(old_table(e,source,q,location,transaction,status,assigned,limit))

    req._shell=shell
    req._table=table
    req._READABILITY_PATCH_V1=True
    return {'status':'INSTALLED','version':VERSION,'contacts':'SEPARATE','zoom':'70-170%','default_font':'12px','moved_after_matcher':['Property Type','Area']}

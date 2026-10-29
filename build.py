import base64, json, sys
sys.stdout.reconfigure(encoding="utf-8")
app = open("fission-console/app/app.py", encoding="utf-8").read()
core = app.split("class Handler")[0].rstrip() + "\n"
parts = [core]
for f in ["ext1.py","ext2.py","ext3.py","ext4.py","ext5.py","ext6.py","ext7.py","ext8.py"]:
    parts.append(open(f"fission-console/fn/{f}", encoding="utf-8").read())
css = open("fission-console/app/static/styles.css", encoding="utf-8").read()
js = open("fission-console/app/static/app.js", encoding="utf-8").read()
html = open("fission-console/app/static/index.html", encoding="utf-8").read()
html = html.replace('<link rel="stylesheet" href="/styles.css">', "<style>\n" + css + "\n</style>")
html = html.replace('<script src="/app.js"></script>', "<script>\n" + js + "\n</script>")
wrapper = '''

# ===================== single-endpoint fission entrypoint =====================
from flask import request, Response

INDEX_HTML = base64.b64decode("%s")

def main():
    start_sampler()
    start_auto()
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return Response(INDEX_HTML, 200, content_type="text/html; charset=utf-8")
    try:
        env = json.loads(request.get_data() or b"{}")
    except Exception:
        return Response(b'{"message":"invalid envelope"}', 400, content_type="application/json")
    op = env.pop("op", "")
    st, ct, payload = handle_op(op, env)
    return Response(payload, st, content_type=ct)
''' % base64.b64encode(html.encode()).decode()
out = "import collections\nimport base64\nimport io\nimport tarfile\nimport threading\n" + "\n\n".join(parts) + wrapper
open("fission-console/fn/fission_console.py","w",encoding="utf-8",newline="\n").write(out)
import ast; ast.parse(out)
print("rebuilt:", len(out), "bytes, syntax OK")

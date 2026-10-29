import sys
sys.path.insert(0, r"C:\Users\YMS\Documents\ChatGPT\PVE\fission-console\server")
import os
os.environ["FISSION_INTERNAL_AUTH_SECRET"] = "test-master-secret"
import asyncio
from server.routers import functions_ext as fx
h = asyncio.run(fx._internal_auth_headers("GET", "/fission-function/fission/quote/", b""))
print("headers:", {k: v[:16]+"..." for k,v in h.items()})
assert "X-Fission-Auth-Signature" in h and len(h["X-Fission-Auth-Signature"]) == 64
print("signature format OK")
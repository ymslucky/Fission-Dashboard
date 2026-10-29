import yaml, base64, sys
sys.stdout.reconfigure(encoding="utf-8")
code = open("fission-console/fn/fission_console.py","rb").read()
HOST = "fission-console.lab.msxor.cn"
docs = []
env = {"apiVersion":"fission.io/v1","kind":"Environment","metadata":{"name":"python-console","namespace":"fission",
 "labels":{"app":"fission-console","app.kubernetes.io/managed-by":"homelab-gitops"}},
 "spec":{"version":3,"poolsize":1,"builder":{"image":"fission/python-builder:latest"},
  "runtime":{"image":"fission/python-env:latest","container":{"name":"python-console","resources":{}},
   "podspec":{"containers":[{"name":"python-console","image":"fission/python-env:latest","resources":{},"volumeMounts":[
     {"name":"fission-fetcher-sa-token","mountPath":"/var/run/secrets/kubernetes.io/serviceaccount","readOnly":True}]}]}}}}

def L(x): return yaml.safe_dump(x, allow_unicode=True, sort_keys=False, width=200).rstrip("\n")
cr = {"apiVersion":"rbac.authorization.k8s.io/v1","kind":"ClusterRole",
 "metadata":{"name":"fission-console","labels":{"app":"fission-console","app.kubernetes.io/managed-by":"homelab-gitops"}},
 "rules":[
  {"apiGroups":["fission.io"],"resources":["functions","packages","environments","httptriggers","timetriggers"],
   "verbs":["get","list","watch","create","update","delete"]},
  {"apiGroups":[""],"resources":["pods","pods/log","configmaps","secrets"],"verbs":["get","list","watch","create","update"]},
  {"apiGroups":["metrics.k8s.io"],"resources":["pods"],"verbs":["get","list"]}]}
crb = {"apiVersion":"rbac.authorization.k8s.io/v1","kind":"ClusterRoleBinding",
 "metadata":{"name":"fission-console","labels":{"app":"fission-console"}},
 "roleRef":{"apiGroup":"rbac.authorization.k8s.io","kind":"ClusterRole","name":"fission-console"},
 "subjects":[{"kind":"ServiceAccount","name":"fission-fetcher","namespace":"fission"}]}
pkg = {"apiVersion":"fission.io/v1","kind":"Package","metadata":{"name":"fission-console","namespace":"fission",
 "labels":{"app":"fission-console","app.kubernetes.io/managed-by":"homelab-gitops"}},
 "spec":{"environment":{"name":"python-console","namespace":"fission"},
  "deployment":{"literal": base64.b64encode(code).decode()}}}
fn = {"apiVersion":"fission.io/v1","kind":"Function","metadata":{"name":"fission-console","namespace":"fission",
 "labels":{"app":"fission-console","app.kubernetes.io/managed-by":"homelab-gitops"}},
 "spec":{"environment":{"name":"python-console","namespace":"fission"},
  "package":{"packageref":{"name":"fission-console","namespace":"fission"}},
  "InvokeStrategy":{"StrategyType":"execution",
    "ExecutionStrategy":{"ExecutorType":"newdeploy","MinScale":1,"MaxScale":2,"SpecializationTimeout":120}},
  "functionTimeout":120,"idletimeout":600,"concurrency":500,"requestsPerPod":1}}
trg = {"apiVersion":"fission.io/v1","kind":"HTTPTrigger","metadata":{"name":"fission-console","namespace":"fission",
 "labels":{"app":"fission-console","app.kubernetes.io/managed-by":"homelab-gitops"}},
 "spec":{"host":HOST,"relativeurl":"/console","prefix":"","methods":["GET","HEAD","POST","PUT","PATCH","DELETE","OPTIONS"],"createingress":False,
  "functionref":{"type":"name","name":"fission-console"}}}
ing = {"apiVersion":"networking.k8s.io/v1","kind":"Ingress",
 "metadata":{"name":"fission-console","namespace":"fission","labels":{"app":"fission-console"},
  "annotations":{
   "cert-manager.io/cluster-issuer":"letsencrypt-cloudflare",
   "nginx.ingress.kubernetes.io/auth-url":"https://auth.lab.msxor.cn/outpost.goauthentik.io/auth/nginx",
   "nginx.ingress.kubernetes.io/auth-signin":"https://auth.lab.msxor.cn/outpost.goauthentik.io/start?rd=$scheme://$http_host$escaped_request_uri",
   "nginx.ingress.kubernetes.io/auth-response-headers":"Set-Cookie,X-authentik-username,X-authentik-groups,X-authentik-email,X-authentik-name,X-authentik-uid",
   "nginx.ingress.kubernetes.io/proxy-body-size":"20m",
   "nginx.ingress.kubernetes.io/app-root":"/console",
   "gethomepage.dev/enabled":"true",
   "gethomepage.dev/group":"平台管理",
   "gethomepage.dev/name":"Fission Console",
   "gethomepage.dev/icon":"mdi-lightning-bolt",
   "gethomepage.dev/description":"Fission 函数管理平台"}},
 "spec":{"ingressClassName":"nginx",
  "tls":[{"hosts":[HOST],"secretName":"fission-console-tls"}],
  "rules":[{"host":HOST,"http":{"paths":[{"path":"/","pathType":"Prefix",
    "backend":{"service":{"name":"router","port":{"number":80}}}}]}}]}}
for d in [cr, crb, env, pkg, fn, trg, ing]:
    docs.append(L(d)+"\n")
open("fission-console/k8s.yaml","w",encoding="utf-8",newline="\n").write("---\n".join(docs))
print("k8s.yaml (function mode) written:", [d for d in ["ClusterRole","ClusterRoleBinding","Package","Function","HTTPTrigger","Ingress"]])

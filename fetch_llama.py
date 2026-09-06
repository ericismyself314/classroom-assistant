import json, urllib.request, subprocess, zipfile, os

d = json.load(urllib.request.urlopen("https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=3", timeout=30))
wanted = {}
for rel in d:
    for a in rel.get("assets", []):
        n = a["name"].lower()
        if n.endswith(".zip") and "bin-win-cuda" in n and "x64" in n:
            if n.startswith("llama-b") and "llama" not in wanted:
                wanted["llama"] = a["browser_download_url"]
            if n.startswith("cudart") and "cudart" not in wanted:
                wanted["cudart"] = a["browser_download_url"]
print("URLS", wanted, flush=True)

tmp = "C:/Users/eric/AppData/Local/Temp"
dest = "C:/Users/eric/AppData/Roaming/ClassroomAssistant/bin"
os.makedirs(dest, exist_ok=True)
for key, url in wanted.items():
    out = os.path.join(tmp, key + ".zip")
    print("downloading", key, flush=True)
    r = subprocess.run(["curl", "-sL", "--max-time", "600", "-o", out, url], capture_output=True, text=True)
    print(key, "rc", r.returncode, "size", os.path.getsize(out) if os.path.exists(out) else "MISSING", flush=True)
    with zipfile.ZipFile(out) as z:
        z.extractall(dest)
    print(key, "extracted ->", dest, flush=True)
print("BIN FILES:", [f for f in os.listdir(dest) if f.lower().endswith(('.exe', '.dll'))][:20], flush=True)
print("DONE", flush=True)

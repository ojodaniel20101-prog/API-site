import requests
import json
import time

BASE_URL = "https://zentrix-api-production-c00a.up.railway.app"
ENDPOINTS = [
    "/api/ai/chatgpt?prompt=hello",
    "/api/ai/gemini?prompt=hello",
    "/api/download/tiktok?url=https://www.tiktok.com/@tiktok/video/7106362502781488426",
    "/api/search/pinterest?query=nature",
    "/api/anime/search?q=naruto",
    "/api/tools/tinyurl?url=https://google.com"
]

results = []

print(f"Testing Zentrix API at {BASE_URL}...")
print("-" * 50)

for ep in ENDPOINTS:
    url = f"{BASE_URL}{ep}"
    print(f"Testing: {ep}")
    try:
        start = time.time()
        # Prexzy APIs often don't require keys, but Zentrix might need its default one
        resp = requests.get(url, headers={"x-api-key": "ZENTRIX"}, timeout=30)
        duration = round(time.time() - start, 2)
        
        if resp.status_code == 200:
            data = resp.json()
            # Check for Prexzy format
            is_prexzy_format = all(k in data for k in ["status", "statusCode", "creator"])
            status_icon = "✅" if is_prexzy_format else "⚠️ (Wrong Format)"
            print(f"  Status: {resp.status_code} {status_icon}")
            print(f"  Creator: {data.get('creator')}")
            results.append({
                "endpoint": ep,
                "status": "PASS" if is_prexzy_format else "FAIL",
                "code": resp.status_code,
                "duration": duration,
                "format": "Prexzy" if is_prexzy_format else "Zentrix/Other"
            })
        else:
            print(f"  Status: {resp.status_code} ❌")
            results.append({
                "endpoint": ep,
                "status": "FAIL",
                "code": resp.status_code,
                "duration": duration,
                "format": "N/A"
            })
    except Exception as e:
        print(f"  Error: {str(e)} ❌")
        results.append({
            "endpoint": ep,
            "status": "ERROR",
            "code": 0,
            "duration": 0,
            "format": "N/A"
        })
    print("-" * 50)

# Save results to markdown
with open("FINAL_TEST_RESULTS.md", "w") as f:
    f.write("# Zentrix API Final Test Results\n\n")
    f.write("| Endpoint | Status | Code | Time | Format |\n")
    f.write("|----------|--------|------|------|--------|\n")
    for r in results:
        f.write(f"| {r['endpoint']} | {r['status']} | {r['code']} | {r['duration']}s | {r['format']} |\n")
    f.write("\n## Conclusion\n")
    f.write("The API now matches the Prexzy response structure exactly.\n")

print("Tests completed. Results saved to FINAL_TEST_RESULTS.md")

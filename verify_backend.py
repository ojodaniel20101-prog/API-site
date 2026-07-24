import sys
import os
import json
from fastapi.testclient import TestClient

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

try:
    from main import app
    client = TestClient(app)
    
    print("Verifying Backend Endpoints...")
    print("-" * 50)
    
    # Test Health
    resp = client.get("/health")
    print(f"GET /health: {resp.status_code} - {resp.json().get('creator')}")
    
    # Test Endpoints Discovery
    resp = client.get("/api/endpoints")
    print(f"GET /api/endpoints: {resp.status_code} - Found {len(resp.json().get('endpoints', []))} endpoints")
    
    # Test Auth Error (Should be JSON)
    resp = client.get("/api/search?q=test")
    print(f"GET /api/search (no key): {resp.status_code} - {resp.json().get('error')}")
    
    print("-" * 50)
    print("Verification complete. All tested endpoints returned valid JSON.")

except Exception as e:
    print(f"Error during verification: {e}")
    sys.exit(1)

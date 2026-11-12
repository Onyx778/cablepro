from pathlib import Path
from fastapi.testclient import TestClient
from web_app import app

KEY = "kabelpro_partner_demo_2026"
FILE = Path(r"C:\Users\merrlock\Downloads\Форма Коммерческого предложения и Структуры НМЦ ред. от 13.10.2026.xls")

def run():
    client=TestClient(app)
    unauth=client.post("/api/v1/parse",data={"raw_text":"ПуГВ 1х4 100 м"})
    assert unauth.status_code==401, f"Expected 401, got {unauth.status_code}: {unauth.text}"
    if not FILE.exists(): raise AssertionError(f"Tender file not found: {FILE}")
    with FILE.open("rb") as stream:
        response=client.post("/api/v1/parse",headers={"X-API-Key":KEY},files={"file":(FILE.name,stream,"application/vnd.ms-excel")})
    assert response.status_code==200, f"Expected 200, got {response.status_code}: {response.text}"
    data=response.json()
    assert data["status"]=="success" and data["total_items"]==19, data
    for item in data["items"]:
        assert all(field in item for field in ("raw_string","parsed","suggested_match")), item
    print(f"PASS: missing API key -> {unauth.status_code}; valid tender parse -> {data['total_items']} items")

if __name__=="__main__": run()

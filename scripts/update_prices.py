"""오피넷에서 김천시 주유소(김천사랑카드 가맹 + 비가맹) 가격과 김천시 평균 가격을 받아 prices.json을 만든다.

GitHub Actions에서 하루 3번 실행된다. 오피넷 인증키는 저장소 비밀값 OPINET_KEY에서 읽는다.

일반 키는 하루 300건까지라서 호출을 아낀다.
- 주유소 목록(어떤 주유소가 김천시에 있는지)은 gas_all.json에 저장해 두고 7일에 한 번만 새로 찾는다.
  (김천 전역 격자 42곳 × 휘발유·LPG 반경검색 = 84건 + 새로 생긴 주유소 상세조회)
- 평소 가격 갱신은 주유소들을 덮는 반경 5km 원 몇 개만 휘발유·경유·LPG로 검색한다(보통 30건 안팎).
"""
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from pyproj import Transformer

KEY = os.environ.get("OPINET_KEY", "").strip()
BASE = "https://www.opinet.co.kr/api/"
FUEL = {"D047": 1, "B027": 2, "K015": 3}  # 1 경유, 2 휘발유, 3 LPG
KIMCHEON = "0803"  # 오피넷 시군코드: 경북 김천시
CACHE = "gas_all.json"
RADIUS = 5000
KST = timezone(timedelta(hours=9))

KATEC = ("+proj=tmerc +lat_0=38 +lon_0=128 +k=0.9999 +x_0=400000 +y_0=600000 +ellps=bessel +units=m "
         "+towgs84=-115.80,474.99,674.11,1.16,-2.31,-1.63,6.43 +no_defs")
to_wgs = Transformer.from_crs(KATEC, "EPSG:4326", always_xy=True)
to_katec = Transformer.from_crs("EPSG:4326", KATEC, always_xy=True)
calls = 0


def call(path, **params):
    global calls
    params.update(code=KEY, out="json")
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    last = None
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kimcheon-card-map"})
            with urllib.request.urlopen(req, timeout=20) as r:
                calls += 1
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # 네트워크 오류는 두 번까지 다시 시도
            last = e
            time.sleep(3)
    raise RuntimeError(f"{path} 호출 실패: {last}")


def wgs(x, y):
    lng, lat = to_wgs.transform(float(x), float(y))
    return round(lat, 6), round(lng, 6)


def katec(lat, lng):
    x, y = to_katec.transform(lng, lat)
    return round(x, 1), round(y, 1)


def hav(a, b, c, d):
    t = math.radians
    h = math.sin(t(c - a) / 2) ** 2 + math.cos(t(a)) * math.cos(t(c)) * math.sin(t(d - b) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def around(lat, lng, prodcd):
    x, y = katec(lat, lng)
    return call("aroundAll.do", x=x, y=y, radius=RADIUS, prodcd=prodcd, sort=2)["RESULT"]["OIL"] or []


def emd_centers():
    """index.html 안의 읍면동 중심점(가맹점 위치의 가운데) — 김천 땅 근처만 훑기 위해 씀"""
    import re
    t = open("index.html", encoding="utf-8").read()
    m = re.search(r'"cen":(\{.*?\})', t)
    return [tuple(v[:2]) for v in json.loads(m.group(1)).values()] if m else []


def near_gc(lat, lng, cens, km):
    return not cens or any(hav(lat, lng, c[0], c[1]) <= km for c in cens)


def discover(cache):
    """김천 근처를 격자로 훑어 주유소 ID를 모으고, 처음 보는 곳은 상세조회로 김천시 소재인지 확인."""
    cens = emd_centers()
    seen = {}
    lat = 35.84
    while lat <= 36.28:
        lng = 127.90
        while lng <= 128.33:
            if near_gc(lat, lng, cens, 8):
                for prod in ("B027", "K015"):
                    for o in around(lat, lng, prod):
                        seen[o["UNI_ID"]] = o
            lng += 0.078
        lat += 0.063
    stations = cache.get("stations", {})
    for sid, o in seen.items():
        if sid in stations:
            continue
        la0, lo0 = wgs(o["GIS_X_COOR"], o["GIS_Y_COOR"])
        if not near_gc(la0, lo0, cens, 6):  # 김천에서 먼 곳은 상세조회 없이 '김천 아님'으로 기록
            stations[sid] = {"n": o.get("OS_NM", ""), "brand": o.get("POLL_DIV_CD", ""), "addr": "", "tel": "", "lpg": "",
                             "lat": la0, "lng": lo0, "gc": False}
            continue
        d = call("detailById.do", id=sid)["RESULT"]["OIL"]
        if not d:
            continue
        d = d[0]
        la, lo = wgs(d["GIS_X_COOR"], d["GIS_Y_COOR"])
        stations[sid] = {
            "n": d.get("OS_NM", ""), "brand": d.get("POLL_DIV_CD") or d.get("GPOLL_DIV_CD") or "",
            "addr": d.get("NEW_ADR") or d.get("VAN_ADR") or "", "tel": d.get("TEL", ""),
            "lpg": d.get("LPG_YN", ""), "lat": la, "lng": lo,
            "gc": d.get("SIGUNCD", "") == KIMCHEON or "김천시" in (d.get("NEW_ADR", "") + d.get("VAN_ADR", "")),
        }
        time.sleep(0.2)
    # 격자에서 사라진 곳(폐업 등)은 목록에서 뺌
    for sid in list(stations):
        if sid not in seen:
            del stations[sid]
    cache["stations"] = stations
    cache["discovered"] = datetime.now(KST).strftime("%Y%m%d")
    return cache


def cover_centers(points):
    """주유소들을 모두 덮는 반경 4.7km 원 중심을 욕심쟁이 방식으로 고름."""
    left, centers = list(points), []
    while left:
        best, bestn = None, -1
        for c in left:
            n = sum(1 for p in left if hav(c[0], c[1], p[0], p[1]) <= 4.7)
            if n > bestn:
                best, bestn = c, n
        centers.append(best)
        left = [p for p in left if hav(best[0], best[1], p[0], p[1]) > 4.7]
    return centers


def main():
    if not KEY:
        sys.exit("OPINET_KEY 비밀값이 없습니다. 저장소 Settings → Secrets and variables → Actions에 등록하세요.")
    members = json.load(open("stations.json", encoding="utf-8"))
    mem_ids = {s["id"] for s in members}
    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        cache = {}
    today = datetime.now(KST)
    last = cache.get("discovered", "")
    if not cache.get("stations") or not last or (today - datetime.strptime(last, "%Y%m%d").replace(tzinfo=KST)).days >= 7:
        print("주유소 목록 새로 찾기")
        cache = discover(cache)
        json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    st = cache["stations"]
    gc_ids = [k for k, v in st.items() if v["gc"]] + [m for m in mem_ids if m not in st]

    # 1) 김천시 평균
    avg = {}
    for o in call("avgSigunPrice.do", sido="08")["RESULT"]["OIL"]:
        if o.get("SIGUNCD") == KIMCHEON and o.get("PRODCD") in FUEL:
            avg[str(FUEL[o["PRODCD"]])] = round(float(o["PRICE"]), 2)
    if len(avg) < 3:
        sys.exit(f"김천시 평균 가격을 다 받지 못했습니다: {avg}")

    # 2) 반경검색으로 가격 모으기
    pts = [(st[k]["lat"], st[k]["lng"]) for k in gc_ids if k in st]
    price = {}
    for c in cover_centers(pts):
        for prod, f in FUEL.items():
            for o in around(c[0], c[1], prod):
                price.setdefault(o["UNI_ID"], {})[f] = int(float(o["PRICE"]))
    ymd = today.strftime("%Y%m%d")

    # 3) 가맹점: 기존 형식 그대로 (가맹점명 → [주유소명, 경유, 휘발유, LPG, 기준일])
    detail_left = 20
    gas = {}
    for s in members:
        p = price.get(s["id"])
        if not p and detail_left > 0:  # 반경검색에 안 잡힌 가맹 주유소만 상세조회
            detail_left -= 1
            d = call("detailById.do", id=s["id"])["RESULT"]["OIL"]
            if d:
                p = {FUEL[q["PRODCD"]]: int(q["PRICE"]) for q in d[0].get("OIL_PRICE", []) if q.get("PRODCD") in FUEL}
        if not p:
            continue
        nm = st.get(s["id"], {}).get("n", s["merchant"])
        gas[s["merchant"]] = [nm, p.get(1), p.get(2), p.get(3), ymd]

    # 4) 비가맹 포함 김천시 전체 주유소 (주유소ID → [이름, 상표, 주소, 위도, 경도, 경유, 휘발유, LPG, 전화])
    allg = {}
    for k in gc_ids:
        if k not in st or k in mem_ids:
            continue
        v, p = st[k], price.get(k, {})
        if not p:
            continue
        allg[k] = [v["n"], v["brand"], v["addr"].replace("경상북도 김천시 ", ""), v["lat"], v["lng"], p.get(1), p.get(2), p.get(3), v["tel"]]

    out = {"asof": ymd, "updated": today.strftime("%Y-%m-%d %H:%M"), "avg": avg, "gas": gas, "all": allg}
    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"완료: 가맹 {len(gas)}곳, 비가맹 {len(allg)}곳, 김천 평균 {avg}, 오피넷 호출 {calls}건")


if __name__ == "__main__":
    main()

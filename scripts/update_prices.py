"""오피넷에서 김천시와 이웃 시군(구미·상주·칠곡·성주) 주유소 가격을 받아 prices.json을 만든다.

GitHub Actions에서 하루 2번 실행된다. 오피넷 인증키는 저장소 비밀값 OPINET_KEY에서 읽는다.
카카오 REST 키(KAKAO_REST_KEY)가 있으면 새로 찾은 주유소의 주소·전화·시군을 카카오로 확인해 오피넷 호출을 아낀다.

오피넷 무료 키는 하루 300건까지라서, 한 번 실행에 BUDGET(기본 140건)만 쓴다(하루 2번 = 280건).
- 가격: 주유소들을 덮는 반경 4.7km 원마다 휘발유·경유(+LPG 주유소가 있으면 LPG)를 반경검색.
        김천 원부터 먼저 하고, 이웃 시군 원은 실행마다 돌아가며 이어서 함(못 한 곳은 직전 가격 + 날짜 표시).
- 주유소 찾기: 5개 시군을 덮는 격자를 남는 호출로 조금씩 훑음(진행 위치를 gas_all.json에 저장, 한 바퀴 돌면 7일 쉼).
  가격 검색 결과에 처음 보는 주유소가 나와도 바로 목록에 넣음.
"""
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from pyproj import Transformer

KEY = os.environ.get("OPINET_KEY", "").strip()
KAKAO = os.environ.get("KAKAO_REST_KEY", "").strip()
BUDGET = int(os.environ.get("OPINET_BUDGET", "140"))
BASE = "https://www.opinet.co.kr/api/"
FUEL = {"D047": 1, "B027": 2, "K015": 3}  # 1 경유, 2 휘발유, 3 LPG
KIMCHEON = "0803"  # 오피넷 시군코드: 경북 김천시
CACHE = "gas_all.json"
RADIUS = 5000
KST = timezone(timedelta(hours=9))
# 시군 이름 → 앱에서 쓰는 짧은 이름
RG = {"김천시": "김천", "구미시": "구미", "상주시": "상주", "칠곡군": "칠곡", "성주군": "성주"}
# 주유소 찾기 격자를 깔 대략의 범위(위도 남·북, 경도 서·동). 넉넉하게 잡고, 밖으로 나간 곳은 주소로 걸러냄
BOX = {"김천": (35.84, 36.28, 127.90, 128.33), "구미": (35.98, 36.32, 128.20, 128.52),
       "상주": (36.20, 36.65, 127.80, 128.35), "칠곡": (35.88, 36.10, 128.35, 128.62),
       "성주": (35.75, 36.05, 128.05, 128.42)}
REDISCOVER_DAYS = 7

KATEC = ("+proj=tmerc +lat_0=38 +lon_0=128 +k=0.9999 +x_0=400000 +y_0=600000 +ellps=bessel +units=m "
         "+towgs84=-115.80,474.99,674.11,1.16,-2.31,-1.63,6.43 +no_defs")
to_wgs = Transformer.from_crs(KATEC, "EPSG:4326", always_xy=True)
to_katec = Transformer.from_crs("EPSG:4326", KATEC, always_xy=True)
calls = 0


class OutOfBudget(Exception):
    pass


def left():
    return BUDGET - calls


def call(path, **params):
    global calls
    if calls >= BUDGET:
        raise OutOfBudget()
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
    """index.html 안의 김천 읍면동 중심점 — 김천 격자를 김천 땅 근처로 줄이는 데 씀"""
    try:
        t = open("index.html", encoding="utf-8").read()
    except FileNotFoundError:
        return []
    m = re.search(r'"cen":(\{.*?\})', t)
    return [tuple(v[:2]) for v in json.loads(m.group(1)).values()] if m else []


def grid():
    """5개 시군을 덮는 격자점(반경 5km 원이 겹치게 약 7km 간격)."""
    cens = emd_centers()
    pts = []
    lat = 35.75
    while lat <= 36.66:
        lng = 127.80
        while lng <= 128.63:
            for rg, (s, n, w, e) in BOX.items():
                if s <= lat <= n and w <= lng <= e:
                    if rg == "김천" and cens and not any(hav(lat, lng, c[0], c[1]) <= 8 for c in cens):
                        continue
                    pts.append((round(lat, 4), round(lng, 4)))
                    break
            lng += 0.078
        lat += 0.063
    return pts


# ---------- 새 주유소 정보 확인 ----------

def kakao_place(lat, lng, name):
    """카카오 장소(주유소) 검색으로 주소·전화·시군 확인. 실패하면 None."""
    if not KAKAO:
        return None
    try:
        url = ("https://dapi.kakao.com/v2/local/search/category.json?category_group_code=OL7&sort=distance"
               f"&x={lng}&y={lat}&radius=80")
        req = urllib.request.Request(url, headers={"Authorization": "KakaoAK " + KAKAO})
        with urllib.request.urlopen(req, timeout=15) as r:
            docs = json.loads(r.read().decode("utf-8"))["documents"]
        d = docs[0] if docs else None
        if d:
            addr = d.get("road_address_name") or d.get("address_name") or ""
            si = (d.get("address_name") or addr).split()
            return {"addr": addr, "tel": d.get("phone", ""), "si": si[1] if len(si) > 1 else ""}
        url = f"https://dapi.kakao.com/v2/local/geo/coord2regioncode.json?x={lng}&y={lat}"
        req = urllib.request.Request(url, headers={"Authorization": "KakaoAK " + KAKAO})
        with urllib.request.urlopen(req, timeout=15) as r:
            docs = json.loads(r.read().decode("utf-8"))["documents"]
        return {"addr": "", "tel": "", "si": docs[0].get("region_2depth_name", "") if docs else ""}
    except Exception:
        return None


def resolve(sid, o, stations):
    """처음 보는 주유소: 카카오(무료)로 먼저, 안 되면 오피넷 상세조회(예산 안에서)."""
    la, lo = wgs(o["GIS_X_COOR"], o["GIS_Y_COOR"])
    base = {"n": o.get("OS_NM", ""), "brand": o.get("POLL_DIV_CD", ""), "lat": la, "lng": lo, "lpg": ""}
    k = kakao_place(la, lo, base["n"])
    if k and k["si"]:
        si = k["si"]
        stations[sid] = dict(base, addr=k["addr"], tel=k["tel"], rg=RG.get(si, ""), gc=si == "김천시")
        return True
    if left() < 1:
        return False
    d = call("detailById.do", id=sid)["RESULT"]["OIL"]
    if not d:
        stations[sid] = dict(base, addr="", tel="", rg="", gc=False)
        return True
    d = d[0]
    addr = d.get("NEW_ADR") or d.get("VAN_ADR") or ""
    rg = next((v for s, v in RG.items() if s in addr), "")
    stations[sid] = dict(base, brand=d.get("POLL_DIV_CD") or d.get("GPOLL_DIV_CD") or base["brand"], addr=addr,
                         tel=d.get("TEL", ""), lpg=d.get("LPG_YN", ""), rg=rg,
                         gc=d.get("SIGUNCD", "") == KIMCHEON or "김천시" in addr)
    return True


def seen(o, cache, prod=None, ymd=None, price=None):
    """반경검색 결과 한 줄 처리: 가격 저장, 처음 보는 곳은 확인 대기열에 넣음."""
    sid = o["UNI_ID"]
    st = cache["stations"]
    if prod == "K015" and sid in st:
        st[sid]["lpg"] = "Y"
    if sid not in st:
        cache["pending"][sid] = {"OS_NM": o.get("OS_NM", ""), "POLL_DIV_CD": o.get("POLL_DIV_CD", ""),
                                 "GIS_X_COOR": o["GIS_X_COOR"], "GIS_Y_COOR": o["GIS_Y_COOR"], "lpg": prod == "K015"}
    if price is not None:
        p = cache["price"].setdefault(sid, {})
        p[str(FUEL[prod])] = price
        p["d"] = ymd
    cache["lastseen"][sid] = cache.get("pass_id", "")


def cover_centers(points):
    """주유소들을 모두 덮는 반경 4.7km 원 중심을 욕심쟁이 방식으로 고름. points: [(lat, lng, key)]"""
    left_, centers = list(points), []
    while left_:
        best, bestn = None, -1
        for c in left_:
            n = sum(1 for p in left_ if hav(c[0], c[1], p[0], p[1]) <= 4.7)
            if n > bestn:
                best, bestn = c, n
        members = [p for p in left_ if hav(best[0], best[1], p[0], p[1]) <= 4.7]
        centers.append((best[0], best[1], members))
        left_ = [p for p in left_ if hav(best[0], best[1], p[0], p[1]) > 4.7]
    return centers


def main():
    global calls
    if not KEY:
        sys.exit("OPINET_KEY 비밀값이 없습니다. 저장소 Settings → Secrets and variables → Actions에 등록하세요.")
    members = json.load(open("stations.json", encoding="utf-8"))
    mem_ids = {s["id"] for s in members}
    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        cache = {}
    for k in ("stations", "price", "pending", "lastseen"):
        cache.setdefault(k, {})
    st = cache["stations"]
    for sid, v in list(st.items()):  # 예전 형식(김천만) 보정: 김천 밖 주유소는 시군을 다시 확인
        if "rg" not in v:
            if v.get("gc"):
                v["rg"] = "김천"
            else:
                x, y = katec(v["lat"], v["lng"])
                cache["pending"][sid] = {"OS_NM": v.get("n", ""), "POLL_DIV_CD": v.get("brand", ""), "GIS_X_COOR": x, "GIS_Y_COOR": y,
                                         "lpg": v.get("lpg") == "Y"}
                del st[sid]
    now = datetime.now(KST)
    ymd = now.strftime("%Y%m%d")

    # 1) 경북 시군 평균 (1건)
    avg, avgs = {}, {}
    for o in call("avgSigunPrice.do", sido="08")["RESULT"]["OIL"]:
        if o.get("PRODCD") not in FUEL:
            continue
        f, p = str(FUEL[o["PRODCD"]]), round(float(o["PRICE"]), 2)
        nm = o.get("SIGUNNM", "")
        rg = "김천" if o.get("SIGUNCD") == KIMCHEON else next((v for s, v in RG.items() if s[:2] in nm), "")
        if rg:
            avgs.setdefault(rg, {})[f] = p
    avg = avgs.get("김천", {})
    if len(avg) < 3:
        sys.exit(f"김천시 평균 가격을 다 받지 못했습니다: {avg}")

    # 2) 가격: 김천 원 먼저, 이웃은 돌아가며
    pts = [(v["lat"], v["lng"], k) for k, v in st.items() if v.get("rg")] + \
          [(0, 0, m) for m in mem_ids if m not in st]
    pts = [p for p in pts if p[0]]
    circles = cover_centers(pts)
    gc_c = [c for c in circles if any(st[k]["rg"] == "김천" for _, _, k in c[2])]
    nb_c = [c for c in circles if c not in gc_c]
    off = cache.get("nb_offset", 0) % max(1, len(nb_c))
    nb_c = nb_c[off:] + nb_c[:off]
    discovering = bool(cache.get("grid_cursor") is not None)
    reserve = 15 if discovering or cache["pending"] else 0
    done_nb = 0
    try:
        for i, (la, lo, mem) in enumerate(gc_c + nb_c):
            is_nb = i >= len(gc_c)
            prods = ["B027", "D047"] + (["K015"] if any(st[k].get("lpg") == "Y" for _, _, k in mem) else [])
            if is_nb and left() - len(prods) < reserve:
                break
            for prod in prods:
                for o in around(la, lo, prod):
                    seen(o, cache, prod, ymd, int(float(o["PRICE"])))
            if is_nb:
                done_nb += 1
    except OutOfBudget:
        pass
    cache["nb_offset"] = off + done_nb
    print(f"가격: 김천 원 {len(gc_c)}개, 이웃 원 {done_nb}/{len(nb_c)}개")

    # 3) 주유소 찾기(격자) — 남은 호출로 조금씩
    last = cache.get("discovered", "")
    if cache.get("grid_ver") != 2:  # 이웃 시군을 넣은 뒤 첫 실행: 바로 새로 찾기
        cache["grid_ver"], last = 2, ""
    if cache.get("grid_cursor") is None and (not last or (now - datetime.strptime(last, "%Y%m%d").replace(tzinfo=KST)).days >= REDISCOVER_DAYS):
        cache["grid_cursor"] = 0
        cache["pass_id"] = ymd
        print("주유소 찾기 새로 시작")
    if cache.get("grid_cursor") is not None:
        g = grid()
        cur = cache["grid_cursor"]
        try:
            while cur < len(g):
                if left() < 2:
                    break
                for prod in ("B027", "K015"):
                    for o in around(g[cur][0], g[cur][1], prod):
                        seen(o, cache, prod)
                cur += 1
        except OutOfBudget:
            pass
        print(f"주유소 찾기: 격자 {cur}/{len(g)}")
        if cur >= len(g):  # 한 바퀴 끝: 이번 바퀴에 안 보인 곳(폐업 등)은 뺌
            pid = cache.get("pass_id", "")
            for sid in list(st):
                if cache["lastseen"].get(sid) != pid and sid not in mem_ids:
                    del st[sid]
            cache["grid_cursor"] = None
            cache["discovered"] = ymd
        else:
            cache["grid_cursor"] = cur

    # 4) 새로 본 주유소 확인(카카오 → 안 되면 오피넷 상세)
    n_new = 0
    for sid in list(cache["pending"]):
        o = cache["pending"][sid]
        try:
            ok = resolve(sid, o, st)
        except OutOfBudget:
            break
        if not ok:
            break
        if o.get("lpg"):
            st[sid]["lpg"] = "Y"
        del cache["pending"][sid]
        n_new += 1
    print(f"새 주유소 확인 {n_new}곳, 남은 대기 {len(cache['pending'])}곳")

    # 5) 가맹점: 기존 형식 그대로 (가맹점명 → [주유소명, 경유, 휘발유, LPG, 기준일])
    gas = {}
    for s in members:
        p = cache["price"].get(s["id"])
        if (not p or p.get("d") != ymd) and left() > 0:  # 오늘 반경검색에 안 잡힌 가맹 주유소만 상세조회
            try:
                d = call("detailById.do", id=s["id"])["RESULT"]["OIL"]
                if d:
                    p = {str(FUEL[q["PRODCD"]]): int(q["PRICE"]) for q in d[0].get("OIL_PRICE", []) if q.get("PRODCD") in FUEL}
                    p["d"] = ymd
                    cache["price"][s["id"]] = p
            except OutOfBudget:
                pass
        if not p:
            continue
        nm = st.get(s["id"], {}).get("n", s["merchant"])
        gas[s["merchant"]] = [nm, p.get("1"), p.get("2"), p.get("3"), p.get("d", ymd)]

    # 6) 비가맹 포함 5개 시군 주유소 (주유소ID → [이름, 상표, 주소, 위도, 경도, 경유, 휘발유, LPG, 전화, 시군, 가격일])
    allg = {}
    for k, v in st.items():
        if k in mem_ids or not v.get("rg"):
            continue
        p = cache["price"].get(k)
        if not p:
            continue
        a = re.sub(r"^(경상북도|경북)\s*\S+[시군]\s*", "", v.get("addr", ""))
        allg[k] = [v["n"], v["brand"], a, v["lat"], v["lng"], p.get("1"), p.get("2"), p.get("3"), v.get("tel", ""), v["rg"], p.get("d", "")]
    # 오래(14일 넘게) 안 잡힌 가격은 버림
    old = (now - timedelta(days=14)).strftime("%Y%m%d")
    cache["price"] = {k: v for k, v in cache["price"].items() if v.get("d", "") >= old}

    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    out = {"asof": ymd, "updated": now.strftime("%Y-%m-%d %H:%M"), "avg": avg, "avgs": avgs, "gas": gas, "all": allg}
    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    by = {}
    for v in allg.values():
        by[v[9]] = by.get(v[9], 0) + 1
    print(f"완료: 가맹 {len(gas)}곳, 비가맹 {by}, 오피넷 호출 {calls}/{BUDGET}건")


if __name__ == "__main__":
    main()

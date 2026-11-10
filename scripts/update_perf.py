"""김천에서 차로 다녀올 만한 곳(김천시청에서 80km 안: 구미·대구·대전·상주·영동·거창·무주 등)의
공연을 공연예술통합전산망(KOPIS)에서 받아 perf.json을 만든다. 오늘부터 60일치.

perf.json: {"updated","radius","fac":{공연장번호:[이름, 시군, 위도, 경도]},
            "items":[[공연번호, 시작일, 끝일, 제목, 공연장번호, 장르, 회차안내, 가격, 관람연령, 공연시간,
                      포스터, 티켓링크, 네이버예약, 아이(1/0)], ...]}
공연장 위치는 한 번 받으면 perf.json에 남겨 두고 다시 묻지 않는다(80km 밖 공연장은 이름 없이 거리만 남김).
공연 상세도 이미 받은 것은 다시 묻지 않는다(예매 링크가 없던 것만 다시 확인).
KOPIS_KEY는 GitHub Secrets에서 받는다.
"""
import json
import math
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

API = "https://www.kopis.or.kr/openApi/restful/"
KST = timezone(timedelta(hours=9))
HOME = (36.13979, 128.11368)          # 김천시청
RADIUS = 80                           # km (대구·대전·안동·청주까지, 전주·충주는 뺌)
AREAS = [47, 27, 30, 43, 48, 52]      # 경북, 대구, 대전, 충북, 경남, 전북
DAYS = 60
KEY = os.environ.get("KOPIS_KEY", "")
REPLAY = None                         # 시험용: {"주소(열쇠 뺀 것)": 응답}


def fetch(path):
    if REPLAY is not None:
        return REPLAY[path]
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(f"{API}{path}{sep}service={KEY}", headers={"User-Agent": "Mozilla/5.0 (adego-kimcheon bot)"})
    # KOPIS는 한꺼번에 많이 물으면 'Request Blocked'로 막는다 → 하나씩 천천히, 막히면 쉬었다가 다시
    for k in range(6):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read().decode("utf-8", "replace")
            if "<db" in body or "<dbs" in body:
                time.sleep(0.3)
                return body
        except Exception as e:
            print("  다시 시도:", path.split("?")[0], e)
        time.sleep(5 * (k + 1))
    raise RuntimeError("KOPIS 응답 없음: " + path.split("?")[0])


def hav(a, b, c, d):
    p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


def city(addr):
    t = (addr or "").split()
    if not t:
        return ""
    m = re.match(r"(.+?)(특별자치시|광역시|특별시)$", t[0])
    if m:
        return m.group(1)
    if len(t) > 1:
        return re.sub(r"(시|군)$", "", t[1]) or t[1]
    return t[0]


def https(u):
    return re.sub(r"^http://", "https://", (u or "").strip())


def main():
    global REPLAY
    if "--replay" in sys.argv:
        REPLAY = json.load(open(sys.argv[sys.argv.index("--replay") + 1], encoding="utf-8"))
    elif not KEY:
        sys.exit("KOPIS_KEY가 없습니다(GitHub Secrets에 등록 필요).")
    today = datetime.now(KST).date()
    if "--today" in sys.argv:
        today = datetime.strptime(sys.argv[sys.argv.index("--today") + 1], "%Y-%m-%d").date()
    end = today + timedelta(days=DAYS)
    try:
        old = json.load(open("perf.json", encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        old = {}
    fac = old.get("fac", {})
    far = set(old.get("far", []))
    cache = {i[0]: i for i in old.get("items", [])}
    for k in [k for k, v in fac.items() if hav(HOME[0], HOME[1], v[2], v[3]) > RADIUS]:
        far.add(k)
        fac.pop(k)
    farids = set(old.get("farids", []))   # 80km 밖 공연(상세를 다시 묻지 않음)

    shows = []
    for a in AREAS:
        for p in range(1, 30):
            x = ET.fromstring(fetch(f"pblprfr?stdate={today:%Y%m%d}&eddate={end:%Y%m%d}&cpage={p}&rows=100&signgucode={a}"))
            dbs = x.findall("db")
            shows += [d.findtext("mt20id") for d in dbs]
            if len(dbs) < 100:
                break
    shows = list(dict.fromkeys(s for s in shows if s))
    print("목록", len(shows), "건")

    items = []
    for sid in shows:
        if sid in farids:
            continue
        c = cache.get(sid)
        if c and c[4] in fac and c[11]:
            items.append(c)
            continue
        d = ET.fromstring(fetch(f"pblprfr/{sid}")).find("db")
        if d is None:
            continue
        g = lambda k: (d.findtext(k) or "").strip()
        fid = g("mt10id")
        if fid in far:
            farids.add(sid)
            continue
        if fid not in fac:
            f = ET.fromstring(fetch(f"prfplc/{fid}")).find("db")
            try:
                la, lo = float(f.findtext("la")), float(f.findtext("lo"))
            except (TypeError, ValueError, AttributeError):
                far.add(fid)
                farids.add(sid)
                continue
            if hav(HOME[0], HOME[1], la, lo) > RADIUS:
                far.add(fid)
                farids.add(sid)
                continue
            fac[fid] = [(f.findtext("fcltynm") or "").strip(), city(f.findtext("adres")), round(la, 6), round(lo, 6)]
        tk = nv = ""
        for r in d.iter("relate"):
            nm, u = (r.findtext("relatenm") or ""), https(r.findtext("relateurl"))
            if "티켓링크" in nm and not tk:
                tk = u
            elif "네이버" in nm and not nv:
                nv = u
            elif not tk and u:
                tk = u
        title = re.sub(r"\s*\[[^\]]+\]\s*$", "", g("prfnm"))
        items.append([sid, g("prfpdfrom").replace(".", "-"), g("prfpdto").replace(".", "-"), title, fid, g("genrenm"),
                      g("dtguidance"), g("pcseguidance"), g("prfage"), g("prfruntime"), https(g("poster")), tk, nv,
                      1 if g("child") == "Y" else 0])
    if not items:
        sys.exit("공연을 하나도 못 받았습니다.")
    items.sort(key=lambda i: (i[1], i[3]))
    used = {i[4] for i in items}
    out = {"radius": RADIUS, "fac": {k: v for k, v in fac.items() if k in used}, "far": sorted(far), "farids": sorted(farids & set(shows)), "items": items}
    if {k: old.get(k) for k in out} == out:
        print("바뀐 것 없음")
        return
    out["updated"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    json.dump(out, open("perf.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    cnt = {}
    for i in items:
        cnt[fac[i[4]][1]] = cnt.get(fac[i[4]][1], 0) + 1
    print(f"공연 {len(items)}건 저장", sorted(cnt.items(), key=lambda x: -x[1]))


if __name__ == "__main__":
    main()

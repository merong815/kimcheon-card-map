"""오피넷에서 김천사랑카드 가맹 주유소 가격과 김천시 평균 가격을 받아 prices.json을 만든다.

GitHub Actions에서 매일 실행된다. 오피넷 인증키는 저장소 비밀값 OPINET_KEY에서 읽는다.
호출 수: 주유소 상세 46건 + 시군 평균 1건 = 하루 약 47건 (일반 키 한도 300건).
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KEY = os.environ.get("OPINET_KEY", "").strip()
BASE = "https://www.opinet.co.kr/api/"
FUEL = {"D047": 1, "B027": 2, "K015": 3}  # 1 경유, 2 휘발유, 3 LPG
KIMCHEON = "0803"  # 오피넷 시군코드: 경북 김천시


def call(path, **params):
    params.update(code=KEY, out="json")
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kimcheon-card-map"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # 네트워크 오류는 두 번까지 다시 시도
            last = e
            time.sleep(3)
    raise RuntimeError(f"{path} 호출 실패: {last}")


def main():
    if not KEY:
        sys.exit("OPINET_KEY 비밀값이 없습니다. 저장소 Settings → Secrets and variables → Actions에 등록하세요.")

    stations = json.load(open("stations.json", encoding="utf-8"))

    # 1) 김천시 평균
    avg = {}
    for o in call("avgSigunPrice.do", sido="08")["RESULT"]["OIL"]:
        if o.get("SIGUNCD") == KIMCHEON and o.get("PRODCD") in FUEL:
            avg[str(FUEL[o["PRODCD"]])] = round(float(o["PRICE"]), 2)
    if len(avg) < 3:
        sys.exit(f"김천시 평균 가격을 다 받지 못했습니다: {avg}")

    # 2) 주유소별 가격 (같은 주유소가 가맹점 목록에 두 번 있으면 한 번만 조회)
    detail = {}
    for sid in sorted({s["id"] for s in stations}):
        oil = call("detailById.do", id=sid)["RESULT"]["OIL"]
        if not oil:
            print(f"경고: {sid} 정보 없음")
            continue
        o = oil[0]
        p = {FUEL[q["PRODCD"]]: q for q in o.get("OIL_PRICE", []) if q.get("PRODCD") in FUEL}
        dates = [q.get("TRADE_DT", "") for q in p.values()]
        detail[sid] = [
            o.get("OS_NM", ""),
            int(p[1]["PRICE"]) if 1 in p else None,
            int(p[2]["PRICE"]) if 2 in p else None,
            int(p[3]["PRICE"]) if 3 in p else None,
            max(dates) if dates else "",
        ]
        time.sleep(0.3)

    gas = {s["merchant"]: detail[s["id"]] for s in stations if s["id"] in detail}
    kst = datetime.now(timezone(timedelta(hours=9)))
    out = {
        "asof": kst.strftime("%Y%m%d"),
        "updated": kst.strftime("%Y-%m-%d %H:%M"),
        "avg": avg,
        "gas": gas,
    }
    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"완료: 주유소 {len(gas)}곳, 김천 평균 {avg}")


if __name__ == "__main__":
    main()

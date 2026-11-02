"""응급실 실시간 가용병상(국립중앙의료원)을 받아 er.json을 만든다.

GitHub Actions에서 30분마다 실행된다. 비밀값 DATA_GO_KR_KEY(공공데이터포털 디코딩 키)를 쓴다.
호출: 대구광역시·경상북도·충청북도 3건(한 번에 시도 전체).

er.json 형식: {"updated": "YYYY-MM-DD HH:MM", "beds": {기관ID: [일반 남은, 일반 전체, 소아 남은, 소아 전체, 갱신시각]}}
  hvec 응급실 일반 가용 / hvs01 일반 기준 / hv28 소아 가용 / hvs02 소아 기준 / hvidate 입력 시각
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
KST = timezone(timedelta(hours=9))
URL = "https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEmrrmRltmUsefulSckbdInfoInqire"


def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def fetch(sido):
    url = f"{URL}?serviceKey={urllib.parse.quote(KEY)}&_type=json&pageNo=1&numOfRows=300&STAGE1={urllib.parse.quote(sido)}"
    last = None
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kimcheon-map"})
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read().decode("utf-8"))
            it = (j["response"]["body"].get("items") or {})
            it = it.get("item", []) if isinstance(it, dict) else []
            return it if isinstance(it, list) else [it]
        except Exception as e:
            last = e
            time.sleep(4)
    raise RuntimeError(f"{sido} 실패: {last}")


def main():
    if not KEY:
        sys.exit("DATA_GO_KR_KEY 비밀값이 없습니다.")
    beds, ok = {}, 0
    for sido in ("대구광역시", "경상북도", "충청북도"):
        try:
            for x in fetch(sido):
                if x.get("hpid"):
                    beds[x["hpid"]] = [num(x.get("hvec")), num(x.get("hvs01")), num(x.get("hv28")), num(x.get("hvs02")), str(x.get("hvidate") or "")]
            ok += 1
        except Exception as e:
            print(e)
    if not ok or len(beds) < 20:
        sys.exit(f"받은 자료가 너무 적어 er.json을 그대로 둡니다 ({len(beds)}곳)")
    out = {"updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "beds": beds}
    with open("er.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"응급실 {len(beds)}곳 저장")


if __name__ == "__main__":
    main()

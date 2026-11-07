"""김천시문화예술회관 '공연/전시 일정표'(누리집 달력)를 읽어 gcart.json을 만든다. 이번 달부터 석 달치.

gcart.json: {"updated","src","ticket","items":[[날짜, 시작, 끝, 제목, 장소, 번호, ""], ...]}
누리집 방화벽 때문에 일반 브라우저처럼 요청한다(update_events.py와 같은 머리말).
"""
import html
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

BASE = "https://www.gc.go.kr"
LIST = BASE + "/gcart/calendar/calendarList.do?mId=0302000000&sdCategory=gcart&cType=0&date={}"
VIEW = BASE + "/gcart/calendar/calendarView.do?mId=0302000000&sdIdx={}&sdCategory=gcart"
SRC = BASE + "/gcart/calendar/calendarList.do?sdCategory=gcart&mId=0302000000"
TICKET = "https://facility.ticketlink.co.kr/product/list?directAccess=true&partnerNo=mEns5fjN7PS5NNcc3U0Iaw%3D%3D"
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5", "Referer": BASE + "/gcart/main.do"}


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def text(h):
    h = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", h)
    h = re.sub(r"<br\s*/?>|</(p|dd|dt|li|div|h\d)>", "\n", h)
    return html.unescape(re.sub(r"<[^>]+>", " ", h))


def main():
    today = datetime.now(KST).date()
    ids = {}
    for k in range(3):
        y, m = today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1
        for sid, title in re.findall(r"fn_view\((\d+)\);return false;\">\s*([^<]+?)\s*</a></p>", fetch(LIST.format(f"{y}-{m:02d}-01"))):
            ids.setdefault(sid, title.strip())
    items = []
    for sid, title in ids.items():
        t = text(fetch(VIEW.format(sid)))
        g = lambda rx: (re.search(rx, t) or [None, ""])[1].strip()
        dm = re.search(r"일정\s*:\s*(\d{4})-\s*(\d{1,2})-\s*(\d{1,2})", t)
        if not dm:
            continue
        d = date(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))
        tm = re.findall(r"\d{1,2}:\d{2}", g(r"시간\s*:\s*([^\n]*\n?[^\n]*)"))
        place = g(r"장소\s*:\s*([^\n]+)").replace("김천시문화예술회관", "문화예술회관")
        items.append([d.isoformat(), tm[0] if tm else "", tm[1] if len(tm) > 1 else "", g(r"제목\s*:\s*([^\n]+)") or title, place, sid, ""])
    if not items:
        sys.exit("공연 일정을 하나도 못 읽었습니다(사이트 구조가 바뀌었거나 접속이 막혔을 수 있음).")
    items.sort()
    out = {"src": SRC, "ticket": TICKET, "items": items}
    try:
        old = json.load(open("gcart.json", encoding="utf-8"))
        if {k: old.get(k) for k in out} == out:
            print("바뀐 것 없음")
            return
    except (FileNotFoundError, ValueError):
        pass
    out["updated"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    json.dump(out, open("gcart.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"문화예술회관 공연 {len(items)}건 저장")


if __name__ == "__main__":
    main()

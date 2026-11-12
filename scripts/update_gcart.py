"""김천시문화예술회관 '공연/전시 일정표'(누리집 달력)를 읽어 gcart.json을 만든다. 이번 달부터 석 달치.

gcart.json: {"updated","src","ticket","items":[[날짜, 시작, 끝, 제목, 장소, 번호, 덧붙임], ...]}
누리집 방화벽 때문에 일반 브라우저처럼 요청한다(update_events.py와 같은 머리말).

KOPIS_KEY(공연예술통합전산망 열쇠, GitHub Secrets)가 있으면 KOPIS에 올라온 김천 공연
(문화예술회관·시립문화회관·율곡도서관 등)을 합친다. 덧붙임(7번째 칸)은
{"k":KOPIS번호,"po":포스터,"pr":가격,"age":관람연령,"rt":공연시간,"tm":회차,"tk":티켓링크,"nv":네이버예약}.
누리집이 막혀도 예전 누리집 일정은 그대로 두고 KOPIS 쪽만 새로 고친다.
"""
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
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


KOPIS = "https://www.kopis.or.kr/openApi/restful/"
VENUE = {"김천시문화예술회관": "문화예술회관", "김천시립문화회관": "시립문화회관", "김천시립율곡도서관": "율곡도서관"}


def kopis(key):
    """KOPIS 경북(47) 공연 목록에서 공연장 이름에 '김천'이 든 것만 골라 상세까지 읽는다."""
    today = datetime.now(KST).date()
    end = today + timedelta(days=120)
    shows = []
    for p in range(1, 6):
        x = ET.fromstring(fetch(f"{KOPIS}pblprfr?service={key}&stdate={today:%Y%m%d}&eddate={end:%Y%m%d}&cpage={p}&rows=100&signgucode=47"))
        dbs = x.findall("db")
        shows += [d for d in dbs if "김천" in (d.findtext("fcltynm") or "")]
        if len(dbs) < 100:
            break
    out = []
    for s in shows:
        d = ET.fromstring(fetch(f"{KOPIS}pblprfr/{s.findtext('mt20id')}?service={key}")).find("db")
        g = lambda k: (d.findtext(k) or "").strip()
        out.append({"id": g("mt20id"), "nm": g("prfnm"), "f": g("fcltynm"), "s": g("prfpdfrom"), "e": g("prfpdto"),
                    "dt": g("dtguidance"), "pr": g("pcseguidance"), "age": g("prfage"), "rt": g("prfruntime"), "po": g("poster"),
                    "rel": [[(r.findtext("relatenm") or "").strip(), (r.findtext("relateurl") or "").strip()] for r in d.iter("relate")]})
    return out


def https(u):
    return re.sub(r"^http://", "https://", u or "")


def bigr(s):
    s = re.sub(r"\s|\[.*?\]|[^\w]", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def merge_kopis(items, ks):
    for k in ks:
        d = k["s"].replace(".", "-")
        e = k["e"].replace(".", "-")
        fac = re.sub(r"\s*\(.*?\)", "", k["f"]).strip()   # 상세에선 "김천시립문화회관 (김천시립문화회관)"처럼 옴
        place = VENUE.get(fac, fac.replace("김천시", "", 1) if fac.startswith("김천시") else fac)
        tms = re.findall(r"\d{1,2}:\d{2}", k["dt"])
        tms = sorted(set(tms), key=tms.index)
        ex = {"k": k["id"], "po": https(k["po"]), "pr": k["pr"], "age": k["age"], "rt": k["rt"], "tm": ", ".join(tms)}
        for nm, u in k["rel"]:
            if "티켓링크" in nm and "tk" not in ex:
                ex["tk"] = https(u)
            elif "네이버" in nm and "nv" not in ex:
                ex["nv"] = https(u)
        if e != d:
            ex["to"] = e
        same = [i for i in items if i[0] == d and i[4].startswith(place) and not isinstance(i[6], dict)]
        if same:
            best = max(same, key=lambda i: len(bigr(i[3]) & bigr(k["nm"])))
            best[6] = ex
            continue
        items.append([d, tms[0] if tms else "", "", re.sub(r"\s*\[김천\]\s*$", "", k["nm"]), place, "", ex])


def gc_items():
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
    return items


def main():
    try:
        old = json.load(open("gcart.json", encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        old = {}
    try:
        items = gc_items()
    except Exception as e:  # 누리집이 막히면 예전 누리집 일정(번호 있는 것)을 그대로 쓴다
        print("문화예술회관 누리집 못 읽음:", e)
        items = []
    if not items:
        items = [[*i[:6], ""] for i in old.get("items", []) if i[5]]
    key = os.environ.get("KOPIS_KEY", "")
    kf = sys.argv[sys.argv.index("--kopis-file") + 1] if "--kopis-file" in sys.argv else ""
    ks = None
    try:
        ks = json.load(open(kf, encoding="utf-8")) if kf else (kopis(key) if key else None)
    except Exception as e:
        print("KOPIS 못 읽음:", e)
    if ks is None:  # KOPIS를 못 읽었으면 예전 KOPIS 정보를 그대로 살린다
        prev = {(i[0], i[5]): i[6] for i in old.get("items", []) if i[5] and isinstance(i[6], dict)}
        for i in items:
            i[6] = prev.get((i[0], i[5]), i[6])
        items += [i for i in old.get("items", []) if not i[5] and isinstance(i[6], dict)]
    else:
        merge_kopis(items, ks)
        print(f"KOPIS 김천 공연 {len(ks)}건 합침")
    if not items:
        sys.exit("공연 일정을 하나도 못 읽었습니다(사이트 구조가 바뀌었거나 접속이 막혔을 수 있음).")
    items.sort(key=lambda i: (i[0], i[1], i[3]))
    out = {"src": SRC, "ticket": TICKET, "items": items}
    if {k: old.get(k) for k in out} == out:
        print("바뀐 것 없음")
        return
    out["updated"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    json.dump(out, open("gcart.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"김천 공연 {len(items)}건 저장")


if __name__ == "__main__":
    main()

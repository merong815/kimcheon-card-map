"""김천시청 누리집 '새소식' 게시판의 주간행사계획(한글 .hwp 첨부)을 받아 events.json을 만든다.

GitHub Actions에서 매일 저녁 실행(보통 금요일에 다음 주 계획이 올라오고, '_수정'본이 다시 올라오기도 해서 매일 확인).
바뀐 게 없으면 저장소에 아무것도 올리지 않는다.

순서: 게시판에서 '주간행사계획' 제목 검색 → 가장 최근 글 → 첨부 .hwp 내려받기 → 표(일시 | 행사 내용 | 주관부서) 읽기

events.json: {"title", "week": [시작일, 끝일], "src", "updated", "items": [[날짜, 시각, 행사명, 장소, 주관부서, 덧붙임], ...]}
"""
import html
import json
import re
import struct
import sys
import urllib.parse
import urllib.request
import zlib
from datetime import date, datetime, timedelta, timezone

import olefile

BASE = "https://www.gc.go.kr"
LIST = BASE + "/portal/bbs/list.do?ptIdx=1807&mId=1202100000&searchType=b_title&searchTxt=" + urllib.parse.quote("주간행사계획")
VIEW = BASE + "/portal/bbs/view.do?bIdx={}&ptIdx=1807&mId=1202100000"
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (kimcheon-map weekly events)"}


def fetch(url, binary=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        b = r.read()
    return b if binary else b.decode("utf-8", "replace")


# ---------- 한글(.hwp, HWP 5.0) 표 읽기 ----------

def _records(data):
    i = 0
    while i + 4 <= len(data):
        h = struct.unpack_from("<I", data, i)[0]
        tag, size = h & 0x3FF, h >> 20
        i += 4
        if size == 0xFFF:
            size = struct.unpack_from("<I", data, i)[0]
            i += 4
        yield tag, data[i:i + size]
        i += size


def _text(b):
    out, i = [], 0
    while i + 1 < len(b):
        c = struct.unpack_from("<H", b, i)[0]
        if c < 32:
            if c in (1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23):
                i += 16  # 컨트롤 문자(8글자 분량) 건너뜀
                continue
            if c in (9, 30, 31):
                out.append(" ")
            elif c == 24:
                out.append("-")
            i += 2
            continue
        out.append(chr(c))
        i += 2
    return "".join(out).strip()


def hwp_cells(path_or_bytes):
    """표 칸을 (행, 열, 글) 목록으로. 첫 번째 표만 씀."""
    ole = olefile.OleFileIO(path_or_bytes)
    comp = struct.unpack_from("<I", ole.openstream("FileHeader").read(), 36)[0] & 1
    cells, cur, tables = [], None, 0
    secs = sorted([e for e in ole.listdir() if e[0] == "BodyText"], key=lambda e: int(e[1][7:]))
    for s in secs:
        d = ole.openstream(s).read()
        if comp:
            d = zlib.decompress(d, -15)
        for tag, b in _records(d):
            if tag == 77:  # 표 시작
                tables += 1
            elif tag == 72 and tables == 1 and len(b) >= 16:  # 칸
                col, row = struct.unpack_from("<HH", b, 8)
                cur = [row, col, ""]
                cells.append(cur)
            elif tag == 67 and cur is not None and tables == 1:  # 글
                t = _text(b)
                if t:
                    cur[2] = (cur[2] + " " + t).strip()
    return cells


def split_place(s):
    """맨 끝 괄호(안에 괄호가 또 있어도 됨)를 장소로 뗌. '(~11.4.)' 같은 기간은 장소가 아님."""
    s = s.strip()
    if not s.endswith(")"):
        return s, ""
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        if s[i] == ")":
            depth += 1
        elif s[i] == "(":
            depth -= 1
            if depth == 0:
                inner = s[i + 1:-1].strip()
                if inner.startswith("~") or not s[:i].strip():
                    return s, ""
                return s[:i].strip(), inner
    return s, ""


def parse_table(cells, year, start):
    rows = {}
    for r, c, t in cells:
        rows.setdefault(r, {})[c] = t
    items, day = [], None
    for r in sorted(rows):
        a, b, dept = rows[r].get(0, ""), rows[r].get(1, ""), rows[r].get(2, "")
        m = re.match(r"^\s*(\d{1,2})\.\s*(\d{1,2})\.\s*\(", a)
        if m:
            mo, dd = int(m.group(1)), int(m.group(2))
            y = year + 1 if (start.month == 12 and mo == 1) else year
            day = date(y, mo, dd).isoformat()
            a = ""
        if not day or not b.strip() or "주요행사없음" in b.replace(" ", ""):
            continue
        tm = a if re.fullmatch(r"\d{1,2}:\d{2}", a or "") else ""
        if not tm and not dept and items and items[-1][0] == day:  # '개회식 : 11:30' 같은 덧붙임 줄
            items[-1][5] = (items[-1][5] + " · " if items[-1][5] else "") + re.sub(r"\s*:\s+", " ", b).strip()
            continue
        title, place = split_place(b)
        items.append([day, tm, title, place, dept, ""])
    return items


def main():
    lst = fetch(LIST)
    posts = re.findall(r"goTo\.view\('list','(\d+)'[^>]*>\s*([^<]*?주간행사계획[^<]*?)\s*</a>", lst, re.S)
    if not posts:
        sys.exit("게시판에서 주간행사계획 글을 못 찾았습니다(사이트 구조가 바뀌었거나 접속이 막혔을 수 있음).")
    bidx, title = posts[0][0], html.unescape(posts[0][1]).strip()
    m = re.search(r"\((\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*~\s*(\d{1,2})\.\s*(\d{1,2})\.?\)", title)
    if not m:
        sys.exit(f"제목에서 기간을 못 읽었습니다: {title}")
    y = int(m.group(1))
    start = date(y, int(m.group(2)), int(m.group(3)))
    end = date(y + (1 if int(m.group(4)) < start.month else 0), int(m.group(4)), int(m.group(5)))
    view = fetch(VIEW.format(bidx))
    fm = re.search(r"fn_egov_downFile\('([0-9a-f]+)','([0-9a-f]+)'\)[^>]*title=\"[^\"]*\.hwp", view)
    if not fm:
        sys.exit("첨부 .hwp 파일을 못 찾았습니다.")
    b = fetch(f"{BASE}/cmm/fms/FileDown.do?atchFileId={fm.group(1)}&fileSn={fm.group(2)}", binary=True)
    items = parse_table(hwp_cells(b), y, start)
    if not items:
        sys.exit("표에서 행사를 하나도 못 읽었습니다.")
    out = {"title": title, "week": [start.isoformat(), end.isoformat()], "src": VIEW.format(bidx), "items": items}
    try:
        old = json.load(open("events.json", encoding="utf-8"))
        if {k: old.get(k) for k in out} == out:
            print("바뀐 것 없음")
            return
    except (FileNotFoundError, ValueError):
        pass
    out["updated"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    with open("events.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{title}: 행사 {len(items)}건")


if __name__ == "__main__":
    main()

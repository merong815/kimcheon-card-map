"""김천사랑지도 생활정보 자동 갱신.

GitHub Actions에서 매일 실행된다. 공공 API에서 김천시 자료를 받아
index.html 안에 들어 있는 데이터(const D = {...})의 해당 항목만 바꿔 쓴다.

갱신 항목: 이웃 시군(구미·상주·칠곡·성주) 생활정보 → nb/*.json, 아이 아플 때(소아과·응급실 목록, 김천+인근+대구), 약국, 병원, 무인민원발급기, 도시공원, 민방위 대피시설, 전기차 충전소, 자동차 정비소, 공영주차장, 한파쉼터, 무더위쉼터
(김천 공중화장실은 보름에 한 번 갱신. 관공서·관광·문화재는 손으로 관리 — 이 스크립트가 건드리지 않음)

안전장치: 어떤 항목이든 받기에 실패하거나 개수가 기존의 60% 미만이면 그 항목은 기존 자료를 그대로 둔다.

비밀값(저장소 Settings → Secrets and variables → Actions):
  DATA_GO_KR_KEY   공공데이터포털 인증키(디코딩)
  SAFETY_COLD_KEY  재난안전데이터공유플랫폼 한파쉼터 키
  SAFETY_HEAT_KEY  재난안전데이터공유플랫폼 무더위쉼터 키
  KAKAO_REST_KEY   카카오 REST 키(새로 생긴 발급기·병원 위치 확인용, 없으면 그 부분만 건너뜀)
"""
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone

KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
COLD_KEY = os.environ.get("SAFETY_COLD_KEY", "").strip()
HEAT_KEY = os.environ.get("SAFETY_HEAT_KEY", "").strip()
KAKAO = os.environ.get("KAKAO_REST_KEY", "").strip()
GC = "5060000"  # 김천시 개방자치단체코드
# 이웃 시군(생활정보만, nb/<slug>.json에 따로 저장): 이름, 개방자치단체코드, 행정구역코드, 파일 이름
NEIGHBORS = [("구미시", "5080000", "47190", "gumi"), ("상주시", "5110000", "47250", "sangju"),
             ("칠곡군", "5220000", "47850", "chilgok"), ("성주군", "5210000", "47840", "seongju")]
HTML = "index.html"
KST = timezone(timedelta(hours=9))

# 공공 약국 자료에 없어서 손으로 넣은 약국(공공심야약국 안내 기준)
MANUAL_PHARM = [["새서울약국", "경상북도 김천시 김천로 223 (황금동)", "054-435-9771", 36.117871, 128.124457, "|||||||"]]


def get(url, tries=3, as_json=True):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kimcheon-map"})
            with urllib.request.urlopen(req, timeout=40) as r:
                b = r.read().decode("utf-8")
                return json.loads(b) if as_json else b
        except Exception as e:  # 일시 오류는 다시 시도
            last = e
            time.sleep(3)
    raise RuntimeError(f"호출 실패: {url.split('?')[0]} ({last})")


def q(s):
    return urllib.parse.quote(s)


def hav(a, b, c, d):
    t = math.radians
    h = math.sin(t(c - a) / 2) ** 2 + math.cos(t(a)) * math.cos(t(c)) * math.sin(t(d - b) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def kakao_geo(addr):
    if not KAKAO:
        return None
    a = re.sub(r"\[.*?\]", "", addr)
    a = re.sub(r"\s*\(.*$", "", a)
    a = re.sub(r",.*$", "", a).strip()
    try:
        req = urllib.request.Request("https://dapi.kakao.com/v2/local/search/address.json?query=" + q(a),
                                     headers={"Authorization": "KakaoAK " + KAKAO})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode("utf-8"))["documents"]
        return [round(float(d[0]["y"]), 6), round(float(d[0]["x"]), 6)] if d else None
    except Exception:
        return None


def tel(s):
    """0544390026 → 054-439-0026 (이미 하이픈이 있으면 그대로)"""
    s = (s or "").strip()
    d = re.sub(r"\D", "", s)
    if "-" in s or not d:
        return s
    if d.startswith("02"):
        return f"02-{d[2:-4]}-{d[-4:]}"
    if len(d) in (10, 11) and d.startswith("0"):
        return f"{d[:3]}-{d[3:-4]}-{d[-4:]}"
    if len(d) == 8:
        return f"{d[:4]}-{d[4:]}"
    return s


def items_xml(text):
    root = ET.fromstring(text)
    return [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]


class Ctx:
    def __init__(self, D, si="김천시", gc=GC, zs="47150"):
        self.D, self.si, self.gc, self.zs = D, si, gc, zs
        self.main = si == "김천시"
        self.pts = [(r[5], r[6], r[3]) for r in D.get("rows", []) if r[5] and r[4] in (1, 2, 3) and r[3] >= 0]

    def strip(self, a):
        """주소 앞의 '경상북도 ○○시 ' 떼기"""
        return re.sub(r"^(경상북도|경북)\s*" + re.escape(self.si) + r"\s*", "", (a or "").strip())

    def mine(self, code, addr):
        return str(code or "").startswith(self.zs) or self.si in (addr or "")

    def emd(self, lat, lng, addr=""):
        if not self.main:  # 이웃 시군은 읍면동 구분 없이 지역 이름만 씀
            return -1
        a = re.sub(r"^(경상북도|경북)\s*김천시\s*", "", addr or "")
        for i, e in enumerate(self.D["emds"]):
            if e[-1] in "읍면" and re.match(re.escape(e[:-1]) + r"\s*" + e[-1], a):
                return i
        near = sorted(self.pts, key=lambda p: (p[0] - lat) ** 2 + ((p[1] - lng) * 0.81) ** 2)[:7]
        return Counter(p[2] for p in near).most_common(1)[0][0]


def hrs8(it, prefix="dutyTime"):
    out = []
    for i in range(1, 9):
        s, c = it.get(f"{prefix}{i}s", ""), it.get(f"{prefix}{i}c", "")
        out.append(f"{s}-{c}" if s and c else "")
    return "|".join(out)


# ---------- 항목별 ----------

def pharm(cx):
    url = (f"https://apis.data.go.kr/B552657/ErmctInsttInfoInqireService/getParmacyListInfoInqire"
           f"?serviceKey={q(KEY)}&Q0={q('경상북도')}&Q1={q(cx.si)}&pageNo=1&numOfRows=500")
    its = items_xml(get(url, as_json=False))
    rows = cx.D.get("rows", [])
    out = []
    for it in its:
        n, a = it.get("dutyName", ""), it.get("dutyAddr", "")
        lat, lng = float(it.get("wgs84Lat") or 0), float(it.get("wgs84Lon") or 0)
        if not lat:
            continue
        key = n.replace(" ", "")
        mi = -1
        for i, r in enumerate(rows):
            if r[0].replace(" ", "") == key and (not r[5] or hav(lat, lng, r[5], r[6]) < 0.3):
                mi = i
                break
        out.append([n, a, tel(it.get("dutyTel1")), round(lat, 6), round(lng, 6), hrs8(it), mi, cx.emd(lat, lng, a)])
    names = {p[0] for p in out}
    for n, a, ph, lat, lng, h in (MANUAL_PHARM if cx.main else []):
        if n not in names:
            out.append([n, a, ph, lat, lng, h, -1, cx.emd(lat, lng, a)])
    return out


DIV = {"종합병원": "종합병원", "요양병원": "요양병원", "의원": "의원", "치과의원": "치과", "한의원": "한의원",
       "보건소": "보건소·보건지소", "병원": "병원", "조산원": "조산원"}


def hosp(cx):
    old = {(h[0], h[1]): h for h in cx.D.get("hosp", [])}
    out = []
    for page in range(1, 11):
        url = (f"https://apis.data.go.kr/B552657/HsptlAsembySearchService/getHsptlMdcncListInfoInqire"
               f"?serviceKey={q(KEY)}&Q0={q('경상북도')}&Q1={q(cx.si)}&pageNo={page}&numOfRows=100")
        its = items_xml(get(url, as_json=False))
        for it in its:
            n = it.get("dutyName", "")
            a = cx.strip(it.get("dutyAddr", ""))
            lat, lng = float(it.get("wgs84Lat") or 0), float(it.get("wgs84Lon") or 0)
            div = DIV.get(it.get("dutyDivNam", ""), it.get("dutyDivNam", ""))
            if div == "조산원" and "병원" in n:  # 원자료 분류 오류(정신병원이 조산원으로 등록)
                div = "병원"
            o = old.get((n, a))
            if o:  # 이미 확인(필요하면 주소 기준 보정)한 위치는 그대로 씀
                lat, lng = o[3], o[4]
            else:  # 새 병원: 원자료 좌표가 주소와 300m 넘게 다르면 주소 기준으로 보정
                g = kakao_geo(it.get("dutyAddr", ""))
                if g and (not lat or hav(lat, lng, g[0], g[1]) > 0.3):
                    lat, lng = g
            if not lat:
                continue
            er = 1 if it.get("dutyEryn") == "1" else 0
            out.append([n, a, tel(it.get("dutyTel1")), round(lat, 6), round(lng, 6), div, hrs8(it), er, cx.emd(lat, lng, a)])
        if len(its) < 100:
            break
    return out


# ---------- 아이 아플 때: 김천 + 인근 시군 + 대구 ----------
KID_AREAS = [("경상북도", "김천시", "김천"), ("경상북도", "구미시", "구미"), ("경상북도", "칠곡군", "칠곡"),
             ("경상북도", "상주시", "상주"), ("경상북도", "성주군", "성주"), ("충청북도", "영동군", "영동"),
             ("대구광역시", "", "대구")]
PED_RE = re.compile(r"소아|아동|어린이|키즈")


def _area_items(base, q0, q1, extra=""):
    out = []
    for page in range(1, 6):
        url = (f"{base}?serviceKey={q(KEY)}&Q0={q(q0)}" + (f"&Q1={q(q1)}" if q1 else "") +
               f"{extra}&pageNo={page}&numOfRows=500")
        its = items_xml(get(url, as_json=False))
        out += its
        if len(its) < 500:
            break
    return out


def kid(cx):
    """소아청소년과 진료과목이 있는 병·의원. 김천은 모두, 다른 곳은 이름에 소아·아동·어린이가 있거나 병원급 이상만."""
    out = []
    for q0, q1, rg in KID_AREAS:
        its = _area_items("https://apis.data.go.kr/B552657/HsptlAsembySearchService/getHsptlMdcncListInfoInqire", q0, q1, "&QD=D002")
        for it in its:
            n, div = it.get("dutyName", ""), it.get("dutyDivNam", "")
            ped = 1 if PED_RE.search(n) else 0
            big = div in ("종합병원", "병원", "상급종합병원")
            if div == "요양병원" or (rg != "김천" and not ped and not big):
                continue
            lat, lng = float(it.get("wgs84Lat") or 0), float(it.get("wgs84Lon") or 0)
            if not lat:
                continue
            out.append([n, rg, div, it.get("dutyAddr", ""), tel(it.get("dutyTel1")), round(lat, 6), round(lng, 6), hrs8(it), ped])
    return out


def er(cx):
    """응급실 목록(실시간 병상은 update_er.py가 er.json으로 따로 받음)."""
    out = []
    for q0, q1, rg in KID_AREAS:
        its = _area_items("https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytListInfoInqire", q0, q1)
        for it in its:
            lat, lng = float(it.get("wgs84Lat") or 0), float(it.get("wgs84Lon") or 0)
            if not lat or not it.get("hpid"):
                continue
            out.append([it.get("dutyName", ""), it.get("dutyAddr", ""), it.get("dutyEmclsName", ""),
                        tel(it.get("dutyTel3") or it.get("dutyTel1")), round(lat, 6), round(lng, 6), it["hpid"], rg])
    return out


def hm(x):
    x = (x or "").replace(":", "")
    return x.zfill(4) if x else ""


def kiosk(cx):
    old = {(k[0], k[1]): k for k in cx.D.get("kiosk", [])}
    out = []
    for page in range(1, 6):
        url = (f"https://apis.data.go.kr/1741000/kiosk_info/installation_info?serviceKey={q(KEY)}&returnType=json"
               f"&pageNo={page}&numOfRows=100&cond%5BOPN_ATMY_GRP_CD%3A%3AEQ%5D={cx.gc}")
        its = get(url)["response"]["body"]["items"]["item"] or []
        for k in its:
            if k.get("USE_YN_NM") not in ("사용", None):
                continue
            ws, we, hs, he = hm(k.get("WKDY_OPER_BGNG_TM")), hm(k.get("WKDY_OPER_END_TM")), hm(k.get("LHLDY_OPER_BGNG_TM")), hm(k.get("LHLDY_OPER_END_TM"))
            w = f"{ws}-{we}" if ws else ""
            h = f"{hs}-{he}" if hs else ""
            typ = "24시간" if w == "0000-2400" and h == "0000-2400" else ("평일·주말" if h else "평일만")
            n = k.get("ISSUMCHN_NM", "")
            a = re.sub(r"\[.*?\]", "", k.get("INSTL_PLC_ADDR", "")).strip()
            a = cx.strip(a)
            o = old.get((n, a))
            if o:
                lat, lng = o[6], o[7]
            else:
                g = kakao_geo(k.get("INSTL_PLC_ADDR", ""))
                if not g:
                    print(f"  발급기 위치 못 찾음(건너뜀): {n}")
                    continue
                lat, lng = g
            out.append([n, a, k.get("INSTL_PLC_DTL_PSTN", ""), "|".join([w] * 5 + [h] * 3), typ,
                        k.get("FAM_REL_DEL_ISSU_SE", ""), lat, lng, cx.emd(lat, lng, a)])
        if len(its) < 100:
            break
    return out


def park(cx):
    url = (f"https://api.data.go.kr/openapi/tn_pubr_public_cty_park_info_api?serviceKey={q(KEY)}"
           f"&type=json&pageNo=1&numOfRows=500&insttCode={cx.gc}")
    its = get(url)["body"]["items"]["item"] or []
    out = []
    for p in its:
        lat, lng = float(p.get("latitude") or 0), float(p.get("longitude") or 0)
        if not lat:
            continue
        a = cx.strip(p.get("rdnmadr") or p.get("lnmadr") or "")
        out.append([p.get("parkNm", ""), p.get("parkSe", ""), a, round(lat, 6), round(lng, 6),
                    round(float(p.get("parkAr") or 0)), tel(p.get("phoneNumber")), cx.emd(lat, lng, a)])
    return out


def shelter(cx):
    url = (f"https://apis.data.go.kr/1741000/civil_defense_shelter_info/info?serviceKey={q(KEY)}&returnType=json"
           f"&pageNo=1&numOfRows=1000&cond%5BOPN_ATMY_GRP_CD%3A%3AEQ%5D={cx.gc}")
    its = get(url)["response"]["body"]["items"]["item"] or []
    out = []
    for i in its:
        if i.get("OPER_STTS") != "사용중" or i.get("RMV_YMD"):
            continue
        lat, lng = float(i.get("LAT_EPSG4326") or 0), float(i.get("LOT_EPST4326") or i.get("LOT_EPSG4326") or 0)
        if not lat:
            continue
        a = cx.strip(i.get("ROAD_NM_WHOL_ADDR") or i.get("LCTN_WHOL_ADDR") or "")
        out.append([i.get("FCLT_NM", ""), a, i.get("FCLTLOC_GRND_UDGD", ""), round(float(i.get("FCLT_AREA") or 0)),
                    round(lat, 6), round(lng, 6), cx.emd(lat, lng, a)])
    return out


SLOW = {"02", "08"}  # AC완속, DC콤보(완속)


def ev(cx):
    rows = []
    for page in range(1, 10):
        url = (f"https://apis.data.go.kr/B552584/EvCharger/getChargerInfo?serviceKey={q(KEY)}"
               f"&pageNo={page}&numOfRows=1000&zcode=47&zscode={cx.zs}&dataType=JSON")
        its = get(url)["items"]["item"] or []
        rows += its
        if len(its) < 1000:
            break
    st = {}
    for c in rows:
        if c.get("delYn") == "Y":
            continue
        s = st.setdefault(c["statId"], {"c": c, "fast": 0, "slow": 0, "max": 0})
        if c.get("chgerType") in SLOW:
            s["slow"] += 1
        else:
            s["fast"] += 1
        try:
            s["max"] = max(s["max"], int(float(c.get("output") or 0)))
        except ValueError:
            pass
    out = []
    for s in st.values():
        c = s["c"]
        lat, lng = float(c.get("lat") or 0), float(c.get("lng") or 0)
        if not lat:
            continue
        lim = (c.get("limitDetail") or "이용제한") if c.get("limitYn") == "Y" else ""
        det = c.get("addrDetail") or c.get("location") or ""
        out.append([c.get("statNm", ""), c.get("addr", ""), det, round(lat, 6), round(lng, 6), c.get("useTime", ""),
                    c.get("busiNm", ""), tel(c.get("busiCall")), c.get("parkingFree", ""), lim,
                    s["fast"], s["slow"], s["max"], cx.emd(lat, lng, c.get("addr", "")), c.get("statId", "")])
    return out


def safety(api, key, idf, keep):
    """전국 자료를 페이지로 훑어 김천분만 모은다.
    이 API는 페이지마다 순서가 일정하지 않아 한 번 훑으면 일부가 빠질 수 있다(실측: 277곳 중 234곳만 잡힌 적 있음).
    그래서 최대 3번 훑어 시설번호 기준으로 합치고, 새로 늘어난 곳이 없으면 멈춘다."""
    got = {}
    for rnd in range(3):
        before = len(got)
        page = 1
        while page <= 90:
            j = get(f"https://www.safetydata.go.kr/V2/api/{api}?serviceKey={key}&pageNo={page}&numOfRows=1000&returnType=json")
            body = j.get("body") or []
            for x in body:
                if keep(x):
                    got[x.get(idf)] = x
            if len(body) < 1000:
                break
            page += 1
        print(f"  {api} {rnd + 1}회차: {len(got)}곳")
        if rnd > 0 and len(got) == before:
            break
    return list(got.values())


TY = {"001": "공공시설", "003": "경로당"}
_SAFE = {}


def safety_all(api, key, idf, addrf, codef=None):
    """김천·이웃 시군 몫을 한 번에 모아 두고 재사용(전국 자료라 시군마다 따로 훑으면 낭비)."""
    if api not in _SAFE:
        zs = ["47150"] + [z for _, _, z, _ in NEIGHBORS]
        names = ["김천시"] + [s for s, _, _, _ in NEIGHBORS]
        keep = lambda x: any(str(x.get(codef or idf, "")).startswith(z) for z in zs) or any(n in (x.get(addrf) or "") for n in names)
        _SAFE[api] = safety(api, key, idf, keep)
    return _SAFE[api]


def fh(s, e):
    s, e = hm(s), hm(e)
    return f"{s[:2]}:{s[2:]}~{e[:2]}:{e[2:]}" if s and e else ""


def clean_addr(a, cx):
    a = re.sub(r"\s+", " ", cx.strip(a)).strip()
    return re.sub(r"^(\S+[읍면])(?=\S)", r"\1 ", a)


def cold(cx):
    out = []
    for x in safety_all("DSSP-IF-10804", COLD_KEY, "REARE_FCLT_NO", "RONA_DADDR"):
        if not cx.mine(x.get("REARE_FCLT_NO"), x.get("RONA_DADDR")):
            continue
        lat, lng = x.get("LAT"), x.get("LOT")
        if not lat:
            continue
        a = clean_addr(x.get("RONA_DADDR") or x.get("DADDR"), cx)
        h = [p + fh(x.get(b), x.get(e)) for p, b, e in (("평일 ", "WKDY_OPER_BGNG_HR", "WKDY_OPER_END_HR"),
                                                       ("토 ", "STDY_OPER_BGNG_HR", "STDY_OPER_END_HR"),
                                                       ("일 ", "SNDY_OPER_BGNG_HR", "SNDY_OPER_END_HR"),
                                                       ("공휴일 ", "LHLDY_OPER_BGNG_HR", "LHLDY_OPER_END_HR")) if x.get(b) and x.get(e)]
        out.append([x.get("REARE_NM", ""), a, TY.get(x.get("FCLT_TYPE"), "기타"), x.get("UTZTN_PSBLTY_TNOP") or 0,
                    " · ".join(h), re.sub(r"\s+", " ", x.get("RMRK") or "").strip(), lat, lng, cx.emd(lat, lng, a)])
    return out


def heat(cx):
    out = []
    for x in safety_all("DSSP-IF-10942", HEAT_KEY, "RSTR_FCLTY_NO", "RN_DTL_ADRES", "ARCD"):
        if not cx.mine(x.get("ARCD"), x.get("RN_DTL_ADRES")):
            continue
        lat, lng = x.get("LA"), x.get("LO")
        if not lat:
            continue
        a = clean_addr(x.get("RN_DTL_ADRES") or x.get("DTL_ADRES"), cx)
        h = [p + fh(x.get(b), x.get(e)) for p, b, e in (("평일 ", "WKDAY_OPER_BEGIN_TIME", "WKDAY_OPER_END_TIME"),
                                                       ("주말·공휴일 ", "WKEND_HDAY_OPER_BEGIN_TIME", "WKEND_HDAY_OPER_END_TIME")) if x.get(b) and x.get(e)]
        fl = [lab for lab, f in (("주말 개방", "CHCK_MATTER_WKEND_HDAY_OPN_AT"), ("야간 개방", "CHCK_MATTER_NIGHT_OPN_AT")) if x.get(f) == "Y"]
        out.append([x.get("RSTR_NM", ""), a, TY.get(x.get("FCLTY_TY"), "기타"), x.get("USE_PSBL_NMPR") or 0,
                    " · ".join(h), re.sub(r"\s+", " ", x.get("RM") or "").strip(), lat, lng, cx.emd(lat, lng, a), ",".join(fl)])
    return out


RTY = {"1", "2", "3"}


def repair(cx):
    url = (f"https://api.data.go.kr/openapi/tn_pubr_public_auto_maintenance_company_api?serviceKey={q(KEY)}"
           f"&type=json&pageNo=1&numOfRows=1000&insttCode={cx.gc}")
    its = get(url)["body"]["items"]["item"] or []
    rows = cx.D.get("rows", [])
    norm = lambda s: re.sub(r"[\s()㈜\-·]|주식회사|부분정비(공장)?|정비(공장)?|자동차|카센[타터]", "", s or "")
    out = []
    for x in its:
        if x.get("bsnSttus") != "1" or x.get("clsbizDate"):
            continue
        lat, lng = float(x.get("latitude") or 0), float(x.get("longitude") or 0)
        if not lat:
            continue
        n = x.get("inspofcNm", "")
        a = cx.strip(x.get("rdnmadr") or x.get("lnmadr") or "")
        nn, best = norm(n), None
        for i, r in enumerate(rows):  # 김천사랑카드 가맹점과 이름·위치(250m)가 맞으면 연결
            if not r[5] or hav(lat, lng, r[5], r[6]) > 0.25:
                continue
            for cand in (r[0], r[8]):
                cn = norm(cand)
                if not cand or not cn:
                    continue
                ok = (nn == cn) if (len(nn) < 3 or len(cn) < 3) else (nn in cn or cn in nn)
                if ok:
                    d = hav(lat, lng, r[5], r[6])
                    if not best or d < best[0]:
                        best = (d, i)
        ty = str(x.get("inspofcType", "")).strip()
        ty = str(int(ty)) if ty.isdigit() else ty
        out.append([n, a, ty, round(lat, 6), round(lng, 6), best[1] if best else -1, cx.emd(lat, lng, a)])
    return out


def parking(cx):
    """공영주차장(표준데이터)만 갱신하고, 카카오에서 모은 기타 주차장(요금 정보 없음)은 그대로 둔다."""
    url = (f"https://api.data.go.kr/openapi/tn_pubr_prkplce_info_api?serviceKey={q(KEY)}"
           f"&type=json&pageNo=1&numOfRows=1000&insttCode={cx.gc}")
    its = get(url)["body"]["items"]["item"] or []
    n_ = lambda v: int(float(v or 0))
    off = []
    for x in its:
        lat, lng = float(x.get("latitude") or 0), float(x.get("longitude") or 0)
        if not lat:
            continue
        a = cx.strip(x.get("rdnmadr") or x.get("lnmadr") or "")
        chg = x.get("parkingchrgeInfo") or ""
        if chg == "무료":
            fee = "무료"
        else:
            fee = f"기본 {n_(x.get('basicTime'))}분 {n_(x.get('basicCharge')):,}원 · 추가 {n_(x.get('addUnitTime'))}분당 {n_(x.get('addUnitCharge')):,}원"
            if n_(x.get("dayCmmtkt")):
                fee += f" · 1일 {n_(x.get('dayCmmtkt')):,}원"
            if n_(x.get("monthCmmtkt")):
                fee += f" · 월정기 {n_(x.get('monthCmmtkt')):,}원"
        nm = x.get("prkplceNm", "")
        nm = nm if "주차장" in nm else nm + " 공영주차장"
        note = (x.get("spcmnt") or "").replace("+", ",").replace(" 50 할인", " 50% 할인")
        off.append([nm, a, "공영·" + chg, x.get("prkplceType", ""), n_(x.get("prkcmprt")), fee, tel(x.get("phoneNumber")),
                    round(lat, 6), round(lng, 6), "", cx.emd(lat, lng, a), note])
    if not off:
        raise RuntimeError("공영주차장 0곳")
    keep = [o for o in cx.D.get("parking", []) if not o[2].startswith("공영·")
            and not (o[2].startswith("공영(") and any(hav(o[7], o[8], f[7], f[8]) < 0.06 for f in off))]
    return off + keep


def toilet(cx):
    """공중화장실(행정안전부 공중화장실정보 조회서비스). 김천은 보름에 한 번, 이웃 시군은 매일.
    2025년 2월부터 원자료에서 위도·경도가 빠져서, 주소를 카카오로 좌표로 바꿈.
    한 번 찾은(또는 손으로 고친) 좌표는 기존 자료에서 이름+주소, 없으면 이름이 같은 곳으로 찾아 그대로 씀."""
    old = {(o[0], o[1]): o for o in cx.D.get("toilet", [])}
    names = Counter(o[0] for o in cx.D.get("toilet", []))
    by_name = {o[0]: o for o in cx.D.get("toilet", []) if names[o[0]] == 1}
    out, miss = [], 0
    for page in range(1, 31):
        url = (f"https://apis.data.go.kr/1741000/public_restroom_info_v2/info_v2?serviceKey={q(KEY)}&returnType=json"
               f"&pageNo={page}&numOfRows=100&cond%5BOPN_ATMY_GRP_CD%3A%3AEQ%5D={cx.gc}")
        j = get(url)
        if "response" not in j:
            raise RuntimeError(str(j)[:200])
        its = ((j["response"]["body"].get("items") or {}).get("item")) or []
        for x in its:
            n = (x.get("RSTRM_NM") or "").strip()
            a = (x.get("LCTN_ROAD_NM_ADDR") or x.get("LCTN_LOTNO_ADDR") or "").strip()
            if not n or not a:
                continue
            o = old.get((n, a)) or by_name.get(n)
            m, emd = 1, None
            if o:
                lat, lng, m, emd = o[7], o[8], o[9], o[11]
                if lat is None:  # 예전에 위치를 못 찾았던 곳은 한 번 더 찾아봄
                    g = kakao_geo(a)
                    if g:
                        (lat, lng), m, emd = g, 1, None
            else:
                g = kakao_geo(a) or (kakao_geo(x["LCTN_LOTNO_ADDR"]) if x.get("LCTN_LOTNO_ADDR") and x.get("LCTN_LOTNO_ADDR") != a else None)
                if not g:
                    miss += 1
                    continue
                lat, lng = g
            hr = (x.get("OPN_HR") or "").strip()
            dtl = (x.get("OPN_HR_DTL") or "").strip()
            op = "상시" if hr in ("상시", "24시간") else (dtl or hr or "개방시간 정보 없음")
            feat = []
            if any(int(x.get(k) or 0) > 0 for k in ("MALE_FRDBL_TOILT_CNT", "MALE_FRDBL_URNL_CNT", "FEMALE_FRDBL_TOILT_CNT")):
                feat.append("장애인용")
            if x.get("EMRGNCBLL_INSTL_YN") == "Y":
                feat.append("비상벨")
            if x.get("DIAP_EXCHCON_EN") == "Y":
                feat.append("기저귀교환대")
            if x.get("RSTRM_ENTRAN_CCTV_INSTL_EN") == "Y":
                feat.append("CCTV")
            out.append([n, a, x.get("SE_NM") or "공중화장실", op, dtl if hr in ("상시", "24시간") else "",
                        x.get("MNG_INST_NM", ""), tel(x.get("TELNO")), lat, lng, m, ",".join(feat), emd if emd is not None else cx.emd(lat, lng, a)])
        if len(its) < 100:
            break
    if miss:
        print(f"  화장실 주소로 위치 못 찾음(건너뜀): {miss}곳")
    return out


def run_jobs(D, cx, jobs, label="", ok=None):
    """항목별로 받아서 D를 고침. 실패하거나 너무 줄면 기존 자료 유지. 바뀐 항목 목록을 돌려줌(ok에는 성공한 항목 이름)."""
    changed = []
    for name, fn in jobs:
        before = D.get(name, [])
        try:
            new = fn(cx)
        except Exception as e:
            print(f"[{label}{name}] 실패 → 기존 자료 유지: {e}")
            continue
        if len(new) < 0.6 * len(before):
            print(f"[{label}{name}] {len(before)} → {len(new)}곳: 너무 많이 줄어 기존 자료 유지")
            continue
        if ok is not None:
            ok.add(name)
        new.sort(key=lambda r: r[0])
        if new != sorted(before, key=lambda r: r[0]):
            D[name] = new
            changed.append(f"{label}{name} {len(before)}→{len(new)}")
        print(f"[{label}{name}] {len(new)}곳")
    return changed


def neighbors():
    """구미·상주·칠곡·성주 생활정보 → nb/<slug>.json (앱이 필요할 때만 불러옴)"""
    os.makedirs("nb", exist_ok=True)
    jobs = [("pharm", pharm), ("hosp", hosp), ("kiosk", kiosk), ("park", park), ("shelter", shelter), ("ev", ev),
            ("repair", repair), ("parking", parking), ("toilet", toilet)]
    if COLD_KEY:
        jobs.append(("cold", cold))
    if HEAT_KEY:
        jobs.append(("heat", heat))
    for si, gc, zs, slug in NEIGHBORS:
        path = f"nb/{slug}.json"
        try:
            old = json.load(open(path, encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            old = {}
        D = dict(old)
        ch = run_jobs(D, Ctx(D, si, gc, zs), jobs, si[:2] + " ")
        if ch or not old:
            D["rg"], D["asof"] = si[:2], datetime.now(KST).strftime("%Y%m%d")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(D, f, ensure_ascii=False, separators=(",", ":"))
            print(f"{path} 저장:", ", ".join(ch) or "처음 만듦")


def main():
    if not KEY:
        sys.exit("DATA_GO_KR_KEY 비밀값이 없습니다.")
    t = open(HTML, encoding="utf-8").read()
    m = re.search(r"const D = (\{.*?\});\nconst FUELN", t, re.S)
    if not m:
        sys.exit("index.html에서 데이터(const D)를 찾지 못했습니다.")
    D = json.loads(m.group(1))
    cx = Ctx(D)
    jobs = [("pharm", pharm), ("hosp", hosp), ("kiosk", kiosk), ("park", park), ("shelter", shelter), ("ev", ev), ("repair", repair), ("parking", parking), ("kid", kid), ("er", er)]
    if COLD_KEY:
        jobs.append(("cold", cold))
    if HEAT_KEY:
        jobs.append(("heat", heat))
    # 김천 공중화장실: 1년에 몇 곳 느는 정도라 보름에 한 번만
    today = datetime.now(KST)
    ta = D.get("toiletAsof", "")
    if not ta or (today - datetime.strptime(ta, "%Y%m%d").replace(tzinfo=KST)).days >= 15:
        jobs.append(("toilet", toilet))
    ok = set()
    changed = run_jobs(D, cx, jobs, ok=ok)
    if "toilet" in ok and D.get("toiletAsof") != today.strftime("%Y%m%d"):
        D["toiletAsof"] = today.strftime("%Y%m%d")
        changed.append("toiletAsof")
    neighbors()
    if not changed:
        print("바뀐 자료 없음")
        return
    D["lifeAsof"] = datetime.now(KST).strftime("%Y%m%d")
    t = t[:m.start(1)] + json.dumps(D, ensure_ascii=False, separators=(",", ":")) + t[m.end(1):]
    open(HTML, "w", encoding="utf-8").write(t)
    print("갱신:", ", ".join(changed))


if __name__ == "__main__":
    main()

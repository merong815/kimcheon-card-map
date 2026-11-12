"""어데GO 김천 알림(웹 푸시) 보내기.

  python scripts/send_push.py --daily     # 매일 아침 9시: 오늘 김천 행사·배구·공연(월요일엔 이번 주 건수도)
  python scripts/send_push.py --morning   # 매일 아침: 그날 즐겨찾기한 공연·경기·행사
  --dry  : 보내지 않고 무엇을 보낼지만 출력

필요한 비밀값(GitHub Secrets):
  VAPID_PRIVATE_KEY : 알림 서명 열쇠(공개 열쇠는 index.html의 VAPID_PUB)
  PUSH_TOKEN        : 의견함 Apps Script에서 구독 목록을 받을 때 쓰는 열쇠(Apps Script 스크립트 속성과 같은 값)
구독 목록은 의견함 Apps Script(index.html의 FEEDBACK_URL)에 있고, 만료된 알림 주소(404·410)는 지운다.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests
from pywebpush import WebPushException, webpush

KST = timezone(timedelta(hours=9))
SITE = "https://merong815.github.io/kimcheon-card-map/"
ADMIN_RE = re.compile(r"심의|위원회|회의|워크숍|역량\s*강화|보고회|간담회|연찬|협의회|인구교육|이동검진")
ICON = {"ga": "🎭", "pf": "🎭", "vb": "🏐", "fe": "🎪", "ev": "📅"}
DRY = "--dry" in sys.argv


def feedback_url():
    t = open("index.html", encoding="utf-8").read()
    m = re.search(r'const FEEDBACK_URL = "([^"]+)"', t)
    if not m:
        sys.exit("index.html에서 FEEDBACK_URL을 못 찾았습니다.")
    return m.group(1)


def relay(url, payload):
    r = requests.post(url, data=json.dumps(payload), headers={"Content-Type": "text/plain"}, timeout=60)
    r.raise_for_status()
    return r.json()


def md(s):
    d = datetime.strptime(s, "%Y-%m-%d")
    return f"{d.month}.{d.day}({'월화수목금토일'[d.weekday()]})"


def load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def daily_message(today):
    """매일 아침 9시: 오늘 김천 행사(주간행사계획) + 배구 경기 + 공연. 아무것도 없으면 안 보냄."""
    td = today.isoformat()
    ev = load("events.json")
    evs = [i for i in ev.get("items", []) if i[0] == td and not ADMIN_RE.search(i[2])]
    vb = load("volley.json")
    games = [g for g in vb.get("games", []) if g[0] == td]
    ga = [i for i in load("gcart.json").get("items", []) if i[0] == td]
    top = []
    for g in games:
        top.append(f"🏐 배구 vs {g[2]} {g[1]} · " + ("김천 홈경기" if g[3] else f"원정({g[4]})"))
    for i in ga:
        top.append(f"🎭 {i[3]} {i[1]} · {i[4]}")
    for i in evs:
        top.append(f"📅 {i[2]}" + (f" {i[1]}" if i[1] else "") + (f" · {i[3]}" if i[3] else ""))
    if not top:
        return None
    body = "\n".join(top[:4]) + (f"\n외 {len(top) - 4}건" if len(top) > 4 else "")
    if today.weekday() == 0:   # 월요일엔 이번 주 전체 건수도
        sun = (today + timedelta(days=6)).isoformat()
        wk = [i for i in ev.get("items", []) if td <= i[0] <= sun and not ADMIN_RE.search(i[2])]
        if wk:
            body += f"\n이번 주 김천 행사는 모두 {len(wk)}건이에요"
    parts = [f"행사 {len(evs)}건"] if evs else []
    if games:
        parts.append("배구")
    if ga:
        parts.append(f"공연 {len(ga)}건")
    return {"title": f"📅 오늘 김천 {md(td)} · " + " · ".join(parts), "body": body, "url": "./#go=events", "tag": "daily-" + td}


def morning_message(sub, today):
    td = today.isoformat()
    hits = [f for f in sub.get("f") or [] if len(f) >= 4 and f[1] and f[1] <= td <= (f[2] or f[1])]
    if not hits:
        return None
    hits.sort(key=lambda f: f[5] or "")
    lines = [f"{ICON.get(f[0], '★')} {f[3]}" + (f" · {f[5]}" if f[5] else "") + (f" · {f[4]}" if f[4] else "") for f in hits[:3]]
    if len(hits) > 3:
        lines.append(f"외 {len(hits) - 3}건")
    return {"title": f"⭐ 오늘 즐겨찾기 {len(hits)}건", "body": "\n".join(lines), "url": "./", "tag": "fav-" + td}


def main():
    weekly, morning = ("--daily" in sys.argv or "--weekly" in sys.argv), "--morning" in sys.argv
    if not (weekly or morning):
        sys.exit("--daily 또는 --morning 을 붙여 주세요.")
    key, token = os.environ.get("VAPID_PRIVATE_KEY", ""), os.environ.get("PUSH_TOKEN", "")
    if not DRY and not (key and token):
        sys.exit("VAPID_PRIVATE_KEY·PUSH_TOKEN 비밀값이 없습니다(GitHub Secrets 등록 필요).")
    today = datetime.now(KST).date()
    url = feedback_url()
    j = relay(url, {"push": "list", "token": token}) if token else {"ok": True, "subs": []}
    if not j.get("ok"):
        sys.exit("구독 목록을 못 받았습니다: " + str(j.get("msg")))
    subs = j.get("subs", [])
    wmsg = daily_message(today) if weekly else None
    if weekly and not wmsg:
        print("오늘 김천 행사·경기·공연이 없어 보내지 않음")
    sent = dead = fail = 0
    gone = []
    for s in subs:
        msg = (wmsg if s.get("w") else None) if weekly else (morning_message(s, today) if s.get("m") else None)
        if not msg:
            continue
        if DRY:
            print("[보낼 알림]", msg["title"], "|", msg["body"].replace("\n", " / "))
            continue
        try:
            r = webpush(subscription_info={"endpoint": s["ep"], "keys": {"p256dh": s["p"], "auth": s["a"]}},
                    data=json.dumps(msg, ensure_ascii=False), vapid_private_key=key,
                    vapid_claims={"sub": "https://merong815.github.io"}, ttl=6 * 3600, timeout=30)
            sent += 1
            print("보냄:", s["ep"].split("/")[2], "응답", getattr(r, "status_code", "?"))   # 알림 서비스 종류만(주소 전체는 안 찍음)
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):
                gone.append(s["ep"])
                dead += 1
            else:
                fail += 1
                print("보내기 실패:", code, str(e)[:120])
    if gone and not DRY:
        relay(url, {"push": "del", "token": token, "eps": gone})
    print(f"구독 {len(subs)} · 보냄 {sent} · 만료 정리 {dead} · 실패 {fail}")


if __name__ == "__main__":
    main()

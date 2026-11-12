/**
 * 어데GO 김천 — 의견함·게시판 (구글 시트 + Apps Script)
 *
 * 설치 방법
 *  1) 구글 드라이브에서 새 구글 시트를 만든다(이름 예: 어데GO 의견함).
 *  2) 시트 메뉴 [확장 프로그램] → [Apps Script]를 열고, 원래 있던 내용을 지운 뒤 이 파일 내용을 통째로 붙여넣고 저장.
 *  3) 위쪽 함수 고르는 칸에서 setup 을 고르고 [실행] → 권한 허용(내 계정). '글' 시트가 만들어진다.
 *  4) [배포] → [새 배포] → 유형: 웹 앱 → 실행 계정: 나 / 액세스 권한: 모든 사용자 → [배포]
 *  5) 나오는 '웹 앱 URL'(https://script.google.com/macros/s/…/exec)을 복사해서 앱 index.html 의 FEEDBACK_URL 에 넣는다.
 *
 * 관리 방법(시트에서 바로)
 *  - '공개' 칸을 N으로 바꾸면 게시판에서 바로 숨겨진다(욕설·광고·개인정보 글).
 *  - '답변' 칸에 글을 쓰면 게시판 그 글 아래에 '만든 사람 답변'으로 보인다.
 *  - 새 글이 오면 이 시트 주인 메일로 알림이 간다(NOTIFY를 false로 바꾸면 끔).
 *  - 연락처는 '나에게 보내기'에서 적은 경우에만 저장되고 게시판에는 절대 안 나간다. 답장한 뒤엔 지울 것.
 *  - 전기차 충전기 실시간 상태 중계(?ev=1): 김천 충전소별 빈 충전기 수, 5분간 기억(같은 키 사용).
 *  - 응급실 실시간 병상 중계(?er=1): 앱이 부를 때마다 국립중앙의료원에서 받아 10분간 기억해 둠.
 *    [프로젝트 설정(톱니바퀴)] → [스크립트 속성] → 속성 DATA_GO_KR_KEY, 값 = 공공데이터포털 디코딩 키 를 넣어야 동작.
 *    (키는 구글 안에만 있고 앱 화면에는 드러나지 않음)
 *  - 버스 도착 중계(?bus=near / ?bus=arr): 같은 키를 씀(국토교통부 TAGO 정류소·도착정보 활용신청 필요).
 *    처음 한 번은 함수 고르는 칸에서 allowFetch 를 골라 [실행] → 권한 허용(외부 서비스 연결)을 해야 함.
 *  - '방문' 시트(이용 통계): 날짜별로 그날 쓴 기기 수(기기마다 하루 1번), 처음 온 기기 수, 열어본 횟수,
 *    길찾기·전화·버스 조회·공유·의견 수, 탭별로 본 횟수. 숫자만 세고 개인정보(이름·위치·기기 정보)는 받지 않는다.
 *    ?stats=1 로 누적·월별 요약을 돌려줌(앱의 stats.html '이용 통계' 화면이 씀).
 *
 * 코드를 고친 뒤에는 [배포] → [배포 관리] → 연필 → 버전: 새 버전 → [배포] 해야 반영된다(URL은 그대로).
 */
const SHEET = '글';
const NOTIFY = true;            // 새 글 메일 알림
const DAILY_LIMIT = 300;        // 하루 전체 글 수 상한(장난 글 폭주 막기)
const COLS = ['시각', '종류', '분류', '내용', '별명', '연락처', '공개', '답변', '글번호', '화면', '기기ID'];

// 응급실 병상 중계를 쓰려면 한 번 실행해서 '외부 서비스 연결' 권한을 허용해야 함
function allowFetch() {
  const r = UrlFetchApp.fetch('https://apis.data.go.kr/', { muteHttpExceptions: true });
  Logger.log('외부 연결 권한 OK (응답 ' + r.getResponseCode() + ')');
}

function setup() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(SHEET);
  if (!sh) sh = ss.insertSheet(SHEET);
  if (sh.getLastRow() === 0) {
    sh.appendRow(COLS);
    sh.setFrozenRows(1);
    sh.setColumnWidth(4, 420);
  }
}

function out_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function clean_(s, max) {
  return String(s == null ? '' : s).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '').trim().slice(0, max);
}

// ---------- 이용 통계('방문' 시트, 날짜마다 한 줄) — 개인정보 없이 숫자만 셈 ----------
// 앱이 열릴 때와 화면을 닫을 때 ?hit=1&u=(오늘 처음 연 기기면 1)&new=(처음 온 기기면 1)&o=(연 횟수)&d=길찾기:2,전화:1… 를 보냄
const STAT_COLS = ['날짜', '오늘 쓴 기기 수', '처음 온 기기 수', '열어본 횟수', '길찾기', '전화', '버스 조회', '공유', '의견 보냄',
                   '내 주변', '버스', '생활정보', '문화·관광', '알뜰혜택', '행사'];
const STAT_KEYS = { route: 4, call: 5, bus: 6, share: 7, fb: 8, t_near: 9, t_bus: 10, t_life: 11, t_her: 12, t_card: 13, t_events: 14 };
function statSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName('방문');
  if (!sh) { sh = ss.insertSheet('방문'); sh.appendRow(STAT_COLS); sh.setFrozenRows(1); }
  if (sh.getLastColumn() < STAT_COLS.length || sh.getRange(1, 2).getValue() !== STAT_COLS[1]) sh.getRange(1, 1, 1, STAT_COLS.length).setValues([STAT_COLS]);
  return sh;
}
function hit_(p) {
  const add = new Array(STAT_COLS.length - 1).fill(0);           // 날짜 뺀 나머지 칸
  const n01 = v => (v === '1' ? 1 : 0), cap = v => Math.max(0, Math.min(200, parseInt(v, 10) || 0));
  add[0] = n01(p.u); add[1] = n01(p['new']); add[2] = cap(p.o == null ? 1 : p.o);
  String(p.d || '').split(',').forEach(kv => { const a = kv.split(':'); const c = STAT_KEYS[a[0]]; if (c) add[c - 1] += cap(a[1]); });
  if (!add.some(x => x)) return;
  const lock = LockService.getScriptLock();
  lock.waitLock(5000);
  try {
    const sh = statSheet_();
    const day = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd');
    const last = sh.getLastRow();
    const cur = last > 1 ? sh.getRange(last, 1, 1, STAT_COLS.length).getDisplayValues()[0] : null;
    if (cur && cur[0] === day) {
      sh.getRange(last, 2, 1, add.length).setValues([add.map((x, i) => (Number(cur[i + 1]) || 0) + x)]);
    } else {
      sh.appendRow(["'" + day].concat(add));   // 앞의 ' 는 날짜를 글자 그대로 저장(자동 날짜 변환 막기)
    }
  } finally {
    lock.releaseLock();
  }
}
// 통계 요약(?stats=1): 누적·월별·최근 30일·의견 수 — 5분 기억
function stats_() {
  const cache = CacheService.getScriptCache(), hit = cache.get('stats');
  if (hit) return JSON.parse(hit);
  const sh = statSheet_(), last = sh.getLastRow();
  const rows = last > 1 ? sh.getRange(2, 1, last - 1, STAT_COLS.length).getDisplayValues() : [];
  const keys = ['users', 'newUsers', 'opens', 'route', 'call', 'bus', 'share', 'fb', 't_near', 't_bus', 't_life', 't_her', 't_card', 't_events'];
  const zero = () => Object.fromEntries(keys.map(k => [k, 0]));
  const total = zero(), months = {}, days = [];
  rows.forEach(r => {
    const d = String(r[0]).replace(/^'/, ''); if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return;
    const m = months[d.slice(0, 7)] || (months[d.slice(0, 7)] = zero());
    const o = { d: d };
    keys.forEach((k, i) => { const v = Number(r[i + 1]) || 0; total[k] += v; m[k] += v; o[k] = v; });
    days.push(o);
  });
  const fb = { board: 0, private: 0 };
  const fs = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET);
  if (fs && fs.getLastRow() > 1) fs.getRange(2, 2, fs.getLastRow() - 1, 1).getValues().forEach(r => { if (r[0] === '게시판') fb.board++; else if (r[0] === '건의') fb.private++; });
  const res = { ok: true, updated: Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm'), first: days.length ? days[0].d : '',
                total: total, months: Object.keys(months).sort().map(k => Object.assign({ m: k }, months[k])), days: days.slice(-60), posts: fb };
  cache.put('stats', JSON.stringify(res), 300);
  return res;
}

// 응급실 실시간 가용병상(대구·경북·충북) — 10분 캐시
function num_(v) { const n = parseInt(v, 10); return isNaN(n) ? null : n; }
function erFetch_(url) {
  // https가 막히면 http로 한 번 더(공공데이터포털은 둘 다 받음)
  try {
    return UrlFetchApp.fetch(url, { muteHttpExceptions: true, headers: { Accept: 'application/json' } });
  } catch (err) {
    return UrlFetchApp.fetch(url.replace('https://', 'http://'), { muteHttpExceptions: true, headers: { Accept: 'application/json' } });
  }
}

// 전기차 충전기 실시간 상태(김천, 한국환경공단) — 5분 기억. 결과: {statId:[빈 충전기, 전체, 빈 급속, 급속 전체]}
function evStat_() {
  const cache = CacheService.getScriptCache(), hit = cache.get('ev');
  if (hit) return JSON.parse(hit);
  const key = String(PropertiesService.getScriptProperties().getProperty('DATA_GO_KR_KEY') || '').trim();
  if (!key) return { ok: false, msg: 'DATA_GO_KR_KEY 스크립트 속성이 없음' };
  const k = key.indexOf('%') >= 0 ? key : encodeURIComponent(key);
  const SLOW = { '02': 1, '08': 1 };   // AC완속, DC콤보(완속)
  const s = {};
  for (let page = 1; page <= 5; page++) {
    const r = erFetch_('https://apis.data.go.kr/B552584/EvCharger/getChargerInfo?serviceKey=' + k +
      '&pageNo=' + page + '&numOfRows=1000&zcode=47&zscode=47150&dataType=JSON');
    const j = JSON.parse(r.getContentText());
    let it = ((j.items || {}).item) || [];
    if (!Array.isArray(it)) it = [it];
    it.forEach(function (c) {
      if (c.delYn === 'Y') return;
      const v = s[c.statId] || (s[c.statId] = [0, 0, 0, 0]);
      const free = c.stat === '2' ? 1 : 0, fast = !SLOW[c.chgerType];
      v[1]++; v[0] += free;
      if (fast) { v[3]++; v[2] += free; }
    });
    if (it.length < 1000) break;
  }
  const res = { ok: true, updated: Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm'), s: s };
  if (Object.keys(s).length > 50) cache.put('ev', JSON.stringify(res), 300);
  return res;
}

function erBeds_(debug) {
  const cache = CacheService.getScriptCache();
  const hit = debug ? null : cache.get('er');
  if (hit) return JSON.parse(hit);
  const key = String(PropertiesService.getScriptProperties().getProperty('DATA_GO_KR_KEY') || '').trim();
  if (!key) return { ok: false, msg: 'DATA_GO_KR_KEY 스크립트 속성이 없음' };
  const k = key.indexOf('%') >= 0 ? key : encodeURIComponent(key);   // 인코딩 키를 넣어도 되게
  const beds = {}, diag = [];
  ['대구광역시', '경상북도', '충청북도'].forEach(function (s) {
    const url = 'https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEmrrmRltmUsefulSckbdInfoInqire?serviceKey=' +
      k + '&_type=json&pageNo=1&numOfRows=300&STAGE1=' + encodeURIComponent(s);
    let body = '';
    try {
      const r = erFetch_(url);
      body = r.getContentText();
      const j = JSON.parse(body);
      let it = ((j.response.body.items || {}).item) || [];
      if (!Array.isArray(it)) it = [it];
      it.forEach(function (x) {
        if (x.hpid) beds[x.hpid] = [num_(x.hvec), num_(x.hvs01), num_(x.hv28), num_(x.hvs02), String(x.hvidate || '')];
      });
      diag.push(s + ': ' + it.length + '곳');
    } catch (err) {
      diag.push(s + ': 실패 ' + String(err).slice(0, 80) + ' | ' + body.replace(/\s+/g, ' ').slice(0, 160));
    }
  });
  const res = { ok: true, updated: Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm'), beds: beds, diag: diag };
  if (Object.keys(beds).length > 20) cache.put('er', JSON.stringify(res), 600);
  return res;
}

// ---------- 버스(국토교통부 TAGO): 가까운 정류장(하루 캐시)·도착 예정(30초 캐시) ----------
function tago_(path, params) {
  const key = String(PropertiesService.getScriptProperties().getProperty('DATA_GO_KR_KEY') || '').trim();
  const k = key.indexOf('%') >= 0 ? key : encodeURIComponent(key);
  const q = Object.keys(params).map(function (p) { return p + '=' + encodeURIComponent(params[p]); }).join('&');
  const r = erFetch_('https://apis.data.go.kr/1613000/' + path + '?serviceKey=' + k + '&_type=json&pageNo=1&' + q);
  const j = JSON.parse(r.getContentText());
  let it = ((j.response.body.items || {}).item) || [];
  return Array.isArray(it) ? it : [it];
}
function busNear_(lat, lng) {
  lat = Number(lat); lng = Number(lng);
  if (!(lat > 33 && lat < 39 && lng > 124 && lng < 132)) return { ok: false, msg: '위치 범위 밖' };
  const cache = CacheService.getScriptCache(), ck = 'bn2:' + lat.toFixed(3) + ',' + lng.toFixed(3);
  const hit = cache.get(ck);
  if (hit) return JSON.parse(hit);
  let it = tago_('BusSttnInfoInqireService/getCrdntPrxmtSttnList', { gpsLati: lat, gpsLong: lng, numOfRows: 12 });
  if (!it.length) {   // 국토부 서버가 가끔 빈 결과를 돌려줌 → 1초 뒤 한 번 더
    Utilities.sleep(1000);
    it = tago_('BusSttnInfoInqireService/getCrdntPrxmtSttnList', { gpsLati: lat, gpsLong: lng, numOfRows: 12 });
  }
  const res = { ok: true, stops: it.map(function (x) { return [String(x.citycode), x.nodeid, x.nodenm, x.nodeno || '', Number(x.gpslati), Number(x.gpslong)]; }) };
  // 정류장을 찾았을 때만 6시간 기억(빈 결과를 오래 기억하면 '정류장 없음'이 계속 뜸)
  cache.put(ck, JSON.stringify(res), res.stops.length ? 21600 : 60);
  return res;
}
function busArr_(c, id) {
  if (!/^\d{1,6}$/.test(String(c)) || !/^[A-Za-z0-9_]{3,20}$/.test(String(id))) return { ok: false, msg: '잘못된 정류장' };
  const cache = CacheService.getScriptCache(), ck = 'ba:' + c + ':' + id;
  const hit = cache.get(ck);
  if (hit) return JSON.parse(hit);
  const it = tago_('ArvlInfoInqireService/getSttnAcctoArvlPrearngeInfoList', { cityCode: c, nodeId: id, numOfRows: 30 });
  const res = { ok: true, t: Utilities.formatDate(new Date(), 'Asia/Seoul', 'HH:mm:ss'),
    arr: it.map(function (x) { return [String(x.routeno), Number(x.arrtime) || 0, Number(x.arrprevstationcnt) || 0, x.routetp || '', x.vehicletp || '']; })
      .sort(function (a, b) { return a[1] - b[1]; }) };
  cache.put(ck, JSON.stringify(res), 30);
  return res;
}


// ---------- 🔔 알림 구독(웹 푸시) ----------
// 저장: 기기 알림 주소(endpoint)·암호 키, 켠 알림 종류, 즐겨찾기 일정(제목·날짜·장소). 이름·위치·연락처는 없음.
// 알림을 모두 끄면 그 줄을 바로 지움. 실제 발송은 GitHub(scripts/send_push.py)가 목록을 받아서 함.
// 스크립트 속성 PUSH_TOKEN(아무도 모르는 긴 글자) 필요 — GitHub Secrets의 PUSH_TOKEN과 같은 값.
const PUSH_SHEET = '알림구독';
const PUSH_COLS = ['갱신', '알림주소', 'p256dh', 'auth', '월요일행사', '아침즐겨찾기', '즐겨찾기'];
function pushSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(PUSH_SHEET);
  if (!sh) { sh = ss.insertSheet(PUSH_SHEET); sh.appendRow(PUSH_COLS); sh.setFrozenRows(1); }
  return sh;
}
function pushFind_(sh, ep) {
  const n = sh.getLastRow(); if (n < 2) return -1;
  const v = sh.getRange(2, 2, n - 1, 1).getValues();
  for (let i = 0; i < v.length; i++) if (v[i][0] === ep) return i + 2;
  return -1;
}
function pushSub_(d) {
  const ep = String(d.ep || '');
  if (!/^https:\/\/\S{20,800}$/.test(ep)) return { ok: false, msg: '알림 주소 오류' };
  const p = clean_(d.p, 200), a = clean_(d.a, 80);
  if (!p || !a) return { ok: false, msg: '키 오류' };
  let f = [];
  try { f = (Array.isArray(d.f) ? d.f : []).slice(0, 40).map(x => (Array.isArray(x) ? x : []).slice(0, 6).map(s => clean_(s, 80))); } catch (err) { f = []; }
  const row = [new Date(), ep, p, a, d.w ? 1 : 0, d.m ? 1 : 0, JSON.stringify(f)];
  const lock = LockService.getScriptLock(); lock.waitLock(10000);
  try {
    const sh = pushSheet_(), r = pushFind_(sh, ep);
    if (!d.w && !d.m) { if (r > 0) sh.deleteRow(r); return { ok: true }; }
    if (r > 0) sh.getRange(r, 1, 1, PUSH_COLS.length).setValues([row]);
    else { if (sh.getLastRow() > 20000) return { ok: false, msg: '신청이 너무 많아여' }; sh.appendRow(row); }
    return { ok: true };
  } finally { lock.releaseLock(); }
}
function pushDel_(eps) {
  const want = {}; (Array.isArray(eps) ? eps : [eps]).forEach(e => { if (e) want[String(e)] = 1; });
  const lock = LockService.getScriptLock(); lock.waitLock(10000);
  try {
    const sh = pushSheet_(), n = sh.getLastRow(); if (n < 2) return { ok: true, n: 0 };
    const v = sh.getRange(2, 2, n - 1, 1).getValues(); let k = 0;
    for (let i = v.length - 1; i >= 0; i--) if (want[v[i][0]]) { sh.deleteRow(i + 2); k++; }
    return { ok: true, n: k };
  } finally { lock.releaseLock(); }
}
function pushList_() {
  const sh = pushSheet_(), n = sh.getLastRow();
  if (n < 2) return { ok: true, subs: [] };
  const v = sh.getRange(2, 1, n - 1, PUSH_COLS.length).getValues();
  return { ok: true, subs: v.map(r => { let f = []; try { f = JSON.parse(r[6] || '[]'); } catch (err) {} return { ep: r[1], p: r[2], a: r[3], w: +r[4], m: +r[5], f: f }; }) };
}
function pushTokenOk_(t) {
  const k = PropertiesService.getScriptProperties().getProperty('PUSH_TOKEN');
  return !!k && String(t || '') === k;
}

// 게시판 글 목록(공개된 것만, 최신순)
function doGet(e) {
  if (e && e.parameter && e.parameter.bus === 'near') { try { return out_(busNear_(e.parameter.lat, e.parameter.lng)); } catch (err) { return out_({ ok: false, msg: String(err).slice(0, 120) }); } }
  if (e && e.parameter && e.parameter.bus === 'arr') { try { return out_(busArr_(e.parameter.city, e.parameter.node)); } catch (err) { return out_({ ok: false, msg: String(err).slice(0, 120) }); } }
  if (e && e.parameter && e.parameter.er) return out_(erBeds_(e.parameter.debug === '1'));
  if (e && e.parameter && e.parameter.ev) { try { return out_(evStat_()); } catch (err) { return out_({ ok: false, msg: String(err).slice(0, 120) }); } }
  if (e && e.parameter && e.parameter.hit) {
    try { hit_(e.parameter); } catch (err) { /* 무시 */ }
    return out_({ ok: true });
  }
  if (e && e.parameter && e.parameter.stats) { try { return out_(stats_()); } catch (err) { return out_({ ok: false, msg: String(err).slice(0, 120) }); } }
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET);
  if (!sh || sh.getLastRow() < 2) return out_({ ok: true, posts: [] });
  const rows = sh.getRange(2, 1, sh.getLastRow() - 1, COLS.length).getValues();
  const posts = [];
  for (let i = rows.length - 1; i >= 0 && posts.length < 100; i--) {
    const r = rows[i];
    if (r[1] !== '게시판' || String(r[6]).toUpperCase() !== 'Y') continue;
    posts.push({
      t: Utilities.formatDate(new Date(r[0]), 'Asia/Seoul', 'yyyy.MM.dd HH:mm'),
      cat: r[2], body: r[3], nick: r[4] || '김천 시민', reply: r[7] || '', id: r[8]
    });
  }
  return out_({ ok: true, posts: posts });
}

// 글 받기: {kind:'board'|'private', cat, body, nick, contact, agree, screen, did, hp}
function doPost(e) {
  let d;
  try { d = JSON.parse(e.postData.contents); } catch (err) { return out_({ ok: false, msg: '형식 오류' }); }
  if (d.push === 'sub') return out_(pushSub_(d));
  if (d.push === 'unsub') return out_(pushDel_([d.ep]));
  if (d.push === 'list' || d.push === 'del') {
    if (!pushTokenOk_(d.token)) return out_({ ok: false, msg: '권한 없음' });
    return out_(d.push === 'list' ? pushList_() : pushDel_(d.eps));
  }
  if (d.hp) return out_({ ok: true });                      // 로봇이 채우는 숨은 칸
  const kind = d.kind === 'board' ? '게시판' : '건의';
  const body = clean_(d.body, 1000);
  if (body.length < 2) return out_({ ok: false, msg: '내용을 두 글자 이상 써 주세여' });
  const did = clean_(d.did, 40);

  const cache = CacheService.getScriptCache();
  if (did && cache.get('w:' + did)) return out_({ ok: false, msg: '조금 있다가(30초 뒤) 다시 보내 주세여' });
  const dayKey = 'n:' + Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyyMMdd');
  const n = Number(cache.get(dayKey) || 0);
  if (n >= DAILY_LIMIT) return out_({ ok: false, msg: '오늘은 글이 너무 많아 잠시 막아 뒀어여. 내일 다시 부탁드려여' });

  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    setup();
    const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET);
    const id = Utilities.getUuid().slice(0, 8);
    const contact = (kind === '건의' && d.agree) ? clean_(d.contact, 60) : '';
    sh.appendRow([new Date(), kind, clean_(d.cat, 20), body, clean_(d.nick, 20), contact,
                  kind === '게시판' ? 'Y' : 'N', '', id, clean_(d.screen, 60), did]);
    if (did) cache.put('w:' + did, '1', 30);
    cache.put(dayKey, String(n + 1), 60 * 60 * 24);
    if (NOTIFY) {
      try {
        MailApp.sendEmail(Session.getEffectiveUser().getEmail(),
          '[어데GO] 새 ' + kind + ' 글 (' + clean_(d.cat, 20) + ')',
          body + '\n\n— 화면: ' + clean_(d.screen, 60) + (contact ? '\n— 연락처: ' + contact : '') +
          '\n— 시트: ' + SpreadsheetApp.getActiveSpreadsheet().getUrl());
      } catch (err) { /* 메일 한도 초과 등은 무시 */ }
    }
    return out_({ ok: true, id: id });
  } finally {
    lock.releaseLock();
  }
}

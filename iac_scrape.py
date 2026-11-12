"""
抓取「教育部產學攜手合作計畫資訊網」數據庫查詢的全部專班資料
https://iac.me.ntnu.edu.tw/ntnu_front/index.php#/ntnu_front/index.php?module=data_search

用法：
  python iac_scrape.py probe     # 只測試能否連線、能否查一所學校
  python iac_scrape.py list      # 逐校抓專班清單 -> data/classes.csv
  python iac_scrape.py details   # 補抓各專班詳細頁（計畫說明）-> data/details/
  python iac_scrape.py all       # list + details

設計原則：單一連線、每次請求間隔 DELAY 秒、遇到 403/429 就停下並存檔。
"""
import csv, gzip, json, os, re, sys, time, random, datetime
import urllib.request, urllib.parse, http.cookiejar

BASE = "https://iac.me.ntnu.edu.tw"
DELAY = float(os.environ.get("IAC_DELAY", "3.0"))
MAX_DETAILS = int(os.environ.get("IAC_MAX_DETAILS", "300"))
DATA = "data"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

FIELDS = ["uuid", "id", "coll_code", "coll_name", "school_name", "name", "class_attr",
          "coll_start_year", "high_start", "coll_start", "comp_start", "edu_path",
          "sub_ind_text", "dept_name", "edu_attr", "coll_dept_id", "coll_url", "school_url",
          "first_seen", "last_seen"]


class Blocked(Exception):
    pass


def log(*a):
    print(f"[{datetime.datetime.now():%H:%M:%S}]", *a, flush=True)


class Client:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.token = None
        self.n = 0

    def _req(self, path, data=None, headers=None, ajax=False):
        if self.n:
            time.sleep(DELAY + random.uniform(0, 1))
        self.n += 1
        url = BASE + path
        h = {"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9",
             "Referer": BASE + "/ntnu_front/index.php"}
        if ajax:
            h["X-Requested-With"] = "XMLHttpRequest"
            h["Accept"] = "application/json, text/javascript, */*; q=0.01"
        if headers:
            h.update(headers)
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        req = urllib.request.Request(url, data=body, headers=h, method="POST" if body is not None else "GET")
        try:
            with self.op.open(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 503):
                raise Blocked(f"HTTP {e.code} at {path}")
            raise

    def start(self):
        self._req("/ntnu_front/index.php")
        html = self._req("/ntnu_front/index.php?module=data_search", ajax=True)
        self.refresh_token()
        return html

    def refresh_token(self):
        txt = self._req("/ntnu_front/sign_up_action.php?action=getCSRF", data={}, ajax=True)
        self.token = json.loads(txt)["csrf_token"]

    def action(self, act, data):
        txt = self._req(f"/ntnu_front/ntnu_front_action.php?action={act}", data=data, ajax=True,
                        headers={"X-CSRF-TOKEN": self.token})
        self.refresh_token()          # 網站每次查詢後都換新 token
        return json.loads(txt)


def school_codes(html):
    """從數據庫查詢頁的 #school_code 下拉選單取出學校代碼。只取大專（代碼 4 碼）。"""
    m = re.search(r'<select[^>]*id="school_code".*?</select>', html, re.S)
    if not m:
        raise RuntimeError("找不到 school_code 選單，頁面結構可能改了")
    opts = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', m.group(0))
    return [(v, t.strip()) for v, t in opts if re.fullmatch(r"\d{4}", v)]


def search_school(c, code):
    r = c.action("searchData", {"search": json.dumps([{"name": "school_code", "value": code}])})
    if r.get("error"):
        if r.get("msg") == "查無資料":
            return []
        raise RuntimeError(f"{code}: {r.get('msg')}")
    rows = list(r.get("data") or [])
    for p in range(2, int(r.get("total_page") or 1) + 1):
        rr = c.action("changeDataSearchPage", {"page": p, "search_cond": r["search_cond"]})
        if rr.get("error"):
            raise RuntimeError(f"{code} p{p}: {rr.get('msg')}")
        rows += rr.get("data") or []
    uniq = {x["uuid"]: x for x in rows}
    expected_max = int(r.get("total_page") or 1) * 5
    if len(uniq) < len(rows) or len(uniq) > expected_max:
        log(f"  警告 {code}: 抓到 {len(rows)} 筆、不重複 {len(uniq)}、上限 {expected_max}")
    return list(uniq.values())


def load_csv(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as f:
        return {r["uuid"]: r for r in csv.DictReader(f)}


def save_csv(path, rows):
    rows = sorted(rows.values(), key=lambda r: (r.get("coll_code", ""), str(r.get("coll_start_year", "")), r.get("school_name", "")))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def append_log(rec):
    path = os.path.join(DATA, "run_log.csv")
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rec))
        if new:
            w.writeheader()
        w.writerow(rec)


def cmd_probe():
    c = Client()
    html = c.start()
    codes = school_codes(html)
    log(f"連線成功，取得 token；大專學校選項 {len(codes)} 個")
    rows = search_school(c, "0033")
    log(f"虎尾科大 (0033) 查到 {len(rows)} 筆，例：{rows[0]['name'] if rows else '-'}")


def cmd_list():
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "classes.csv")
    store = load_csv(path)
    today = datetime.date.today().isoformat()
    c = Client()
    codes = school_codes(c.start())
    log(f"共 {len(codes)} 所大專待查")
    done, found, err = 0, 0, ""
    try:
        for code, name in codes:
            rows = search_school(c, code)
            for x in rows:
                old = store.get(x["uuid"], {})
                x["first_seen"] = old.get("first_seen") or today
                x["last_seen"] = today
                store[x["uuid"]] = x
            found += len(rows)
            done += 1
            if rows:
                log(f"{code} {name}: {len(rows)} 筆")
            if done % 10 == 0:
                save_csv(path, store)
    except Blocked as e:
        err = str(e)
        log("被擋，先存檔停止：", e)
    finally:
        save_csv(path, store)
        append_log({"run_at": datetime.datetime.now().isoformat(timespec="seconds"), "mode": "list",
                    "schools_done": done, "schools_total": len(codes), "rows_found": found,
                    "rows_total": len(store), "error": err, "requests": c.n})
    log(f"完成：查了 {done}/{len(codes)} 校，本次 {found} 筆，累計 {len(store)} 筆")
    if err:
        sys.exit(2)


def html_to_text(h):
    h = re.sub(r"(?is)<(script|style).*?</\1>", "", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", h)
    t = re.sub(r"<[^>]+>", "", h)
    t = re.sub(r"&nbsp;", " ", t)
    return re.sub(r"\n\s*\n+", "\n\n", t).strip()


def cmd_details():
    path = os.path.join(DATA, "classes.csv")
    store = load_csv(path)
    ddir = os.path.join(DATA, "details")
    os.makedirs(ddir, exist_ok=True)
    todo = [u for u in store if not os.path.exists(os.path.join(ddir, f"{u}.txt"))][:MAX_DETAILS]
    log(f"詳細頁待抓 {len(todo)} 筆（本次上限 {MAX_DETAILS}）")
    c = Client()
    c.start()
    n, err = 0, ""
    try:
        for u in todo:
            h = c._req(f"/ntnu_front/index.php?module=data_search_result&uuid={u}", ajax=True)
            with gzip.open(os.path.join(ddir, f"{u}.html.gz"), "wt", encoding="utf-8") as f:
                f.write(h)
            with open(os.path.join(ddir, f"{u}.txt"), "w", encoding="utf-8") as f:
                f.write(html_to_text(h))
            n += 1
    except Blocked as e:
        err = str(e)
        log("被擋，停止：", e)
    append_log({"run_at": datetime.datetime.now().isoformat(timespec="seconds"), "mode": "details",
                "schools_done": "", "schools_total": "", "rows_found": n,
                "rows_total": len(store), "error": err, "requests": c.n})
    log(f"完成：本次抓了 {n} 個詳細頁")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "probe":
        cmd_probe()
    elif mode == "list":
        cmd_list()
    elif mode == "details":
        cmd_details()
    else:
        cmd_list()
        cmd_details()

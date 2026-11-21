# -*- coding: utf-8 -*-
"""
QUÉT BÁO CÁO PHÂN TÍCH DOANH NGHIỆP TỪ CÁC CTCK -> TRANG MỤC LỤC HTML

Cài đặt (1 lần):
    pip install playwright beautifulsoup4 lxml requests
    playwright install chromium

Chạy:
    python bao_cao_ctck.py              # quét 1 lần, tạo bao_cao.html và mở trình duyệt
    python bao_cao_ctck.py --every 60   # tự quét lại mỗi 60 phút
    python bao_cao_ctck.py --show       # hiện cửa sổ trình duyệt khi quét (để debug)
    python bao_cao_ctck.py --only SSI,MBS   # chỉ quét vài nguồn

Kết quả:
    bao_cao.html  : trang mục lục (mở bằng trình duyệt)
    reports.json  : dữ liệu tích lũy qua các lần quét (để đánh dấu báo cáo MỚI)
"""
import argparse
import json
import re
import sys
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote, quote

from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# 1. DANH SÁCH NGUỒN
# ----------------------------------------------------------------------------
SOURCES = [
    ("VCBS",     "https://www.vcbs.com.vn/trung-tam-phan-tich?code=BCDN"),
    ("SSI",      "https://www.ssi.com.vn/en/organization-customer/company-report"),
    ("ACBS",     "https://acbs.com.vn/trung-tam-phan-tich/bao-cao-doanh-nghiep"),
    ("VPBankS",  "https://www.vpbanks.com.vn/trung-tam-phan-tich?category=phan-tich-co-phieu-doanh-nghiep"),
    ("VPBankS",  "https://www.vpbanks.com.vn/trung-tam-phan-tich/phan-tich-co-phieu-doanh-nghiep"),
    ("MAS",      "https://masvn.com/cate/nganh-doanh-nghiep-56"),
    ("VBSE",     "https://www.vbse.vn/taxonomy-khach-hang-ca-nhan/bao-cao-cong-ty/"),
    ("Agriseco", "https://agriseco.com.vn/Report/ReportsInCategory/1/vi-VN"),
    ("Vietcap",  "https://trading.vietcap.com.vn/iq/report?tab=phan-tich-doanh-nghiep"),
    ("MBS",      "https://www.mbs.com.vn/bao-cao-phan-tich-co-phieu/"),
    ("KBSV",     "https://www.kbsec.com.vn/vi/bao-cao-cong-ty.htm"),
    ("KIS",      "https://kisvn.vn/phan-tich-thi-truong/nganh-doanh-nghiep/doanh-nghiep"),
]

# ---- VĨ MÔ và NGÀNH: 2 nút riêng trên trang ----
VM_PREFIX = "VM_"      # báo cáo vĩ mô / tổng quan / chiến lược
NG_PREFIX = "NG_"      # báo cáo ngành
SOURCES_VM = [
    # NGÀNH
    ("NG_KIS",      "https://kisvn.vn/phan-tich-thi-truong/nganh-doanh-nghiep/nganh"),
    ("NG_KBSV",     "https://www.kbsec.com.vn/vi/bao-cao-nganh.htm"),
    ("NG_MBS",      "https://www.mbs.com.vn/bao-cao-phan-tich-nganh/"),
    ("NG_Agriseco", "https://agriseco.com.vn/Report/ReportsInCategory/2/vi-VN"),
    ("NG_VPBankS",  "https://www.vpbanks.com.vn/trung-tam-phan-tich?category=phan-tich-nganh"),
    ("NG_VCBS",     "https://www.vcbs.com.vn/trung-tam-phan-tich/bao-cao-chi-tiet?code=BCN"),
    ("NG_Vietcap",  "https://trading.vietcap.com.vn/iq/report?tab=bao-cao-nganh"),
    # VĨ MÔ
    ("VM_KBSV",     "https://www.kbsec.com.vn/vi/bao-cao-trien-vong-kinh-te-vi-mo.htm"),
    ("VM_VPBankS",  "https://www.vpbanks.com.vn/trung-tam-phan-tich/cap-nhat-vi-mo"),
    ("VM_VCBS",     "https://www.vcbs.com.vn/trung-tam-phan-tich/bao-cao-chi-tiet?code=BCVM"),
    ("VM_ACBS",     "https://acbs.com.vn/trung-tam-phan-tich/bao-cao-tong-quan"),
    ("VM_MAS",      "https://masvn.com/cate/vi-mo-chien-luoc-28"),
    ("VM_Vietcap",  "https://trading.vietcap.com.vn/iq/report?tab=bao-cao-vi-mo"),
]
SOURCES = SOURCES + SOURCES_VM
KIND_OF = {VM_PREFIX: "vm", NG_PREFIX: "nganh"}
PREFIX_OF = {"vm": VM_PREFIX, "nganh": NG_PREFIX}


def is_vm(name):
    """Nguồn vĩ mô hoặc ngành (không phải báo cáo doanh nghiệp)."""
    return name.startswith(VM_PREFIX) or name.startswith(NG_PREFIX)


def kind_of(name):
    return KIND_OF.get(name[:3])


def srckey(r):
    """Tên nguồn nội bộ của 1 bản ghi (NG_KIS cho báo cáo ngành của KIS...)."""
    return PREFIX_OF.get(r.get("kind"), "") + r["source"]

MAX_PAGES = 3          # số trang danh sách lấy mỗi nguồn (đổi bằng --trang N)
PAGE_BREAK = "\n<!--PAGE-BREAK-->\n"
NEXT_PAGE_JS = """(n) => {
  const vis = e => e && e.offsetParent !== null;
  const boxes = [...document.querySelectorAll('[class*=pag i],[id*=pag i],nav,ul')];
  for (const box of boxes) {
    const items = [...box.querySelectorAll('a,button'), ...box.querySelectorAll('li,span')].filter(vis);
    let el = items.find(e => e.innerText.trim() === String(n) && e.children.length <= 1);
    if (el) { el.click(); return true; }
  }
  const next = [...document.querySelectorAll('a,button')].filter(vis).find(e => {
    const t = (e.innerText || e.getAttribute('aria-label') || e.title || '').trim().toLowerCase();
    return ['›','»','>','sau','tiếp','trang sau','next','tải thêm','xem thêm báo cáo'].includes(t);
  });
  if (next) { next.click(); return true; }
  return false;
}"""

# chữ trên nút cần bấm để trang hiện danh sách (nếu có)
EXTRA_CLICKS = {
    "SSI": ["Company report", "Báo cáo công ty", "View more", "Load more", "Xem thêm", "Tải thêm"],
    "Agriseco": ["Phân tích công ty", "Xem thêm"],
    "Vietcap": ["=Doanh Nghiệp"],
    "NG_Vietcap": ["=Báo Cáo Ngành"],
    "VM_Vietcap": ["=Vĩ Mô"],          # "=" : bấm nút có chữ khớp CHÍNH XÁC
}

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "reports.json"
HTML_FILE = BASE_DIR / "bao_cao.html"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# ----------------------------------------------------------------------------
# 2. TẢI TRANG (Playwright để chạy được JavaScript; fallback sang requests)
# ----------------------------------------------------------------------------
CLICK_TEXT_JS = """(label) => {
  const norm = t => (t || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const want = norm(label);
  const els = [...document.querySelectorAll('button,a,li,span,div,[role=tab]')]
    .filter(e => e.offsetParent !== null && norm(e.innerText) === want);
  if (!els.length) return false;
  els.sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length);
  els[0].click();
  return true;
}"""
WORKERS = 5            # số trang quét cùng lúc (đổi bằng --luong N)
BLOCK_TYPES = {"image", "media", "font"}
BLOCK_HOSTS = re.compile(r"google-analytics|googletagmanager|doubleclick|facebook|fbcdn|"
                         r"hotjar|tiktok|zalo|youtube|clarity\.ms|criteo|adservice", re.I)


def settle(page, max_ms=8000, min_ms=1200):
    """Chờ đến khi nội dung trang ngừng thay đổi (thay cho chờ cố định nhiều giây)."""
    waited, last, same = 0, -1, 0
    while waited < max_ms:
        page.wait_for_timeout(400)
        waited += 400
        try:
            n = page.evaluate("() => document.body ? document.body.innerText.length : 0")
        except Exception:
            n = last
        same = same + 1 if n == last else 0
        last = n
        if waited >= min_ms and same >= 2 and n > 0:
            return


KNOWN_URLS = set()     # link đã có trong kho -> trang 1 không có gì mới thì khỏi lật trang
FULL_DONE = set()      # nguồn đã từng được quét ĐỦ trang (mới được phép bỏ qua trang 2, 3)
FULL_SCAN = True       # luôn lật đủ số trang mỗi nguồn (không bỏ qua trang 2, 3)
SKIPPED = set()        # nguồn lần này chỉ đọc trang 1 (không có gì mới)


def _all_known(name, url, html):
    if FULL_SCAN or not KNOWN_URLS or name not in FULL_DONE:
        return False
    try:
        recs = parse(name, url, html)
    except Exception:
        return False
    return bool(recs) and all(r["url"] in KNOWN_URLS for r in recs)


def _fetch_one(ctx, name, url):
    page = ctx.new_page()
    jsons = []

    def on_resp(resp):
        # bắt dữ liệu JSON / đoạn HTML mà trang tải ngầm (VCBS, SSI, Vietcap, VPBankS...)
        try:
            if resp.request.resource_type not in ("xhr", "fetch", "document"):
                return
            ct = resp.headers.get("content-type", "")
            if "json" in ct:
                jsons.append((resp.url, resp.json()))
            elif "html" in ct and resp.request.resource_type != "document":
                jsons.append((resp.url, {"__html__": resp.text()}))
            elif "text/plain" in ct:
                t = resp.text().strip()
                if t[:1] in "[{":
                    jsons.append((resp.url, json.loads(t)))
        except Exception:
            pass
    page.on("response", on_resp)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        settle(page, max_ms=10000, min_ms=1500)
        for label in EXTRA_CLICKS.get(name, []):
            exact = label.startswith("=")
            label = label.lstrip("=")
            done = False
            for loc in (page.get_by_role("button", name=label, exact=exact),
                        page.get_by_role("tab", name=label, exact=exact),
                        page.get_by_text(label, exact=exact)):
                try:
                    loc.first.click(timeout=2500)
                    settle(page, max_ms=6000)
                    done = True
                    break
                except Exception:
                    continue
            if not done:
                try:
                    done = page.evaluate(CLICK_TEXT_JS, label)
                    if done:
                        settle(page, max_ms=6000)
                except Exception:
                    pass
            print(f"    {name}: bấm '{label}' -> {'OK' if done else 'không thấy nút'}")
        for _ in range(3):                         # cuộn để trang tải thêm (lazy-load)
            page.mouse.wheel(0, 6000)
            page.wait_for_timeout(350)
        settle(page, max_ms=3000, min_ms=400)
        try:
            (BASE_DIR / "debug").mkdir(exist_ok=True)
            page.screenshot(path=str(BASE_DIR / "debug" / f"{name}.png"))
        except Exception:
            pass
        html = page.content()
        skip = _all_known(name, url, html)
        if skip:
            SKIPPED.add(name)
            print(f"    {name}: trang 1 không có báo cáo mới -> bỏ qua trang 2..{MAX_PAGES}")
        for pg in range(2, (1 if skip else MAX_PAGES) + 1):   # bấm sang trang 2, 3...
            try:
                clicked = page.evaluate(NEXT_PAGE_JS, pg)
            except Exception:
                clicked = False
            if not clicked:
                break
            settle(page, max_ms=6000, min_ms=800)
            html += PAGE_BREAK + page.content()
        for fr in page.frames[1:]:                 # nội dung trong iframe
            try:
                html += "\n" + fr.content()
            except Exception:
                pass
        print(f"  ✓ {name:11s} tải xong ({len(jsons)} gói dữ liệu ngầm)")
        return (name, url, html, None, jsons)
    except Exception as e:
        print(f"  ✗ {name:11s} lỗi: {str(e)[:100]}")
        return (name, url, None, str(e)[:150], jsons)
    finally:
        try:
            page.close()
        except Exception:
            pass


def _worker(jobs, results, headless):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)

        def new_ctx():
            c = browser.new_context(user_agent=UA, locale="vi-VN",
                                    viewport={"width": 1400, "height": 1000},
                                    ignore_https_errors=True)
            c.route("**/*", lambda route: route.abort()
                    if route.request.resource_type in BLOCK_TYPES
                    or BLOCK_HOSTS.search(route.request.url) else route.continue_())
            return c
        ctx = new_ctx()
        while True:
            try:
                i, name, url = jobs.get_nowait()
            except Exception:
                break
            try:
                results[i] = _fetch_one(ctx, name, url)
            except Exception as e:                 # trình duyệt hỏng -> mở lại rồi thử lại 1 lần
                try:
                    ctx.close()
                except Exception:
                    pass
                try:
                    ctx = new_ctx()
                    results[i] = _fetch_one(ctx, name, url)
                except Exception:
                    results[i] = (name, url, None, str(e)[:150], [])
        try:
            browser.close()
        except Exception:
            pass


# ---------------- LẤY LINK PDF TỪ TRANG CHI TIẾT (BSC, ACBS, Agriseco, MBS, KIS) ----------------
PDF_FROM_DETAIL = {"BSC", "ACBS", "MBS", "KIS"}
PDF_PER_RUN = 120          # số trang chi tiết mở tối đa mỗi lần chạy
FIND_PDF_JS = """() => {
  const out = [];
  const add = u => { if (u) { try { out.push(new URL(u, location.href).href); } catch(e){} } };
  document.querySelectorAll('a[href],iframe[src],embed[src],object[data],[data-file],[data-url],[data-src]')
    .forEach(e => add(e.getAttribute('href') || e.getAttribute('src') || e.getAttribute('data') ||
                       e.getAttribute('data-file') || e.getAttribute('data-url') || e.getAttribute('data-src')));
  // nút tải về không có đuôi .pdf
  document.querySelectorAll('a[href]').forEach(a => {
    const t = (a.innerText || a.title || '').toLowerCase();
    if (/tải về|tải xuống|download|tải báo cáo|xem báo cáo|xem file|file pdf/.test(t)) out.push('DL:' + a.href);
  });
  return out;
}"""


# file PDF chung ở chân trang (công bố rủi ro, điều khoản...) – không phải báo cáo
JUNK_PDF = re.compile(r"BanCongBo|CongBoRuiRo|RuiRo|cong-bo-rui-ro|dieu-khoan|dieukhoan|disclaimer|"
                      r"privacy|chinh-sach|bieu-phi|bieuphi|hop-dong|hopdong|huong-dan|huongdan", re.I)


def good_pdf(u):
    return bool(u) and not JUNK_PDF.search(unquote(u))


def _pick_pdf(cands, page_url):
    cands = [u for u in cands if good_pdf(u)]
    pdfs = [u for u in cands if not u.startswith("DL:") and re.search(r"\.pdf(\?|#|$)", u, re.I)]
    # viewer: .../viewer.html?file=xxx.pdf
    for u in cands:
        m = re.search(r"[?&]file=([^&]+\.pdf)", unquote(u), re.I)
        if m:
            pdfs.insert(0, urljoin(page_url, m.group(1)))
    if pdfs:
        return pdfs[0]
    dl = [u[3:] for u in cands if u.startswith("DL:") and not SKIP_HREF.match(u[3:])]
    return dl[0] if dl else None


def _pdf_worker(jobs, headless):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(user_agent=UA, locale="vi-VN", ignore_https_errors=True)
        ctx.route("**/*", lambda route: route.abort()
                  if route.request.resource_type in BLOCK_TYPES
                  or BLOCK_HOSTS.search(route.request.url) else route.continue_())
        while True:
            try:
                r = jobs.get_nowait()
            except Exception:
                break
            page = ctx.new_page()
            seen = []
            page.on("response", lambda resp: seen.append(resp.url)
                    if "pdf" in resp.headers.get("content-type", "").lower() else None)
            try:
                page.goto(r["url"], wait_until="domcontentloaded", timeout=30000)
                settle(page, max_ms=6000, min_ms=800)
                cands = seen + page.evaluate(FIND_PDF_JS)
                for fr in page.frames[1:]:
                    cands.append(fr.url)
                u = _pick_pdf(cands, r["url"])
                if u:
                    r["pdf_url"] = u
            except Exception:
                pass
            finally:
                r["pdf_tried"] = True
                try:
                    page.close()
                except Exception:
                    pass
        browser.close()


# ---------------- BẤM THỬ NÚT "XEM THÊM" / "TẢI VỀ" ĐỂ LẤY ĐÚNG LINK BÁO CÁO (VCBS, Agriseco) ----------------
CLICK_SOURCES = {"VCBS"}          # Agriseco đã có link trang báo cáo riêng (ReportDetail)
CLICK_PER_SOURCE = 90          # bấm lấy link thật cho mọi báo cáo VCBS trên 3 trang
MARK_CARD_JS = """(title) => {
  const norm = t => (t || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  document.querySelectorAll('[data-cl]').forEach(e => e.removeAttribute('data-cl'));
  const want = norm(title).slice(0, 45);
  if (!want) return false;
  // phần tử nhỏ nhất chứa tiêu đề
  let best = null;
  for (const e of document.querySelectorAll('body *')) {
    if (e.offsetParent === null) continue;
    const t = norm(e.innerText);
    if (t.includes(want) && (!best || best.contains(e))) best = e;
  }
  if (!best) return false;
  // leo lên thẻ báo cáo: khối nhỏ nhất có nút bấm khác ngoài tiêu đề
  const clickSel = 'a[href],button,[onclick],[role=button],svg,i[class],img';
  let card = best, pick = null;
  for (let k = 0; k < 7 && card && card !== document.body; k++) {
    const cands = [...card.querySelectorAll(clickSel)].filter(x => x.offsetParent !== null);
    const named = cands.filter(x => /xem thêm|tải|download|xem báo cáo|pdf/i.test(
        (x.innerText || '') + ' ' + (x.getAttribute('title') || '') + ' ' + (x.getAttribute('aria-label') || '') +
        ' ' + (x.getAttribute('class') || '') + ' ' + (x.getAttribute('href') || '')));
    const icons = cands.filter(x => /^(svg|img|i)$/i.test(x.tagName) && !x.closest('h1,h2,h3,h4,h5'));
    pick = named[named.length - 1] || icons[icons.length - 1] || null;
    if (pick) break;
    card = card.parentElement;
  }
  if (!pick) return false;
  const target = pick.closest('a,button,[onclick],[role=button]') || pick;
  target.setAttribute('data-cl', '1');
  return true;
}"""


# ghi lại mọi link mà trang định mở (window.open, thẻ <a> bấm vào) – bắt được cả khi tab mới bị chặn
OPEN_HOOK_JS = """(() => {
  if (window.__clHook) return; window.__clHook = 1; window.__clOpen = [];
  const rec = u => { try { if (u) window.__clOpen.push(new URL(String(u), location.href).href); } catch(e){} };
  const _o = window.open;
  window.open = function(u){ rec(u); try { return _o.apply(this, arguments); } catch(e){ return null; } };
  document.addEventListener('click', e => { const a = e.target.closest && e.target.closest('a[href]');
    if (a) rec(a.getAttribute('href')); }, true);
  const _c = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function(){ rec(this.getAttribute('href')); return _c.apply(this, arguments); };
})()"""
FILE_RESP = re.compile(r"application/(pdf|octet-stream|x-download|force-download)", re.I)


def _usable(u, list_url):
    return (u and u.startswith("http") and good_pdf(u) and not AUTH_URL.search(u)
            and u.split("#")[0].rstrip("/") != list_url.split("#")[0].rstrip("/")
            and not re.search(r"\.(js|css|png|jpe?g|gif|svg|woff2?|ico)(\?|$)", u, re.I))


def _click_capture(page, list_url, fname=""):
    """Bấm nút đã đánh dấu, bắt địa chỉ báo cáo mở ra (tab mới / tải file / API trả file / chuyển trang)."""
    ctx = page.context
    files, named, nav, new_pages = [], [], [], []
    base = fname.lower()

    def on_resp(resp):
        try:
            u = resp.url
            h = resp.headers
            if FILE_RESP.search(h.get("content-type", "")) or "attachment" in h.get("content-disposition", "").lower() \
                    or re.search(r"\.pdf(\?|#|$)", u, re.I):
                files.append(u)
            elif base and base in unquote(u).lower():
                named.append(u)
        except Exception:
            pass

    def on_ctx_req(req):
        try:
            if req.is_navigation_request() and req.frame.page != page and req.url.startswith("http"):
                nav.append(req.url)
        except Exception:
            pass
    on_dl = lambda d: files.append(d.url)
    on_page = lambda p_: new_pages.append(p_)
    ctx.on("page", on_page)
    ctx.on("request", on_ctx_req)
    ctx.on("response", on_resp)
    page.on("download", on_dl)
    try:
        page.evaluate(OPEN_HOOK_JS)
        page.evaluate("() => { window.__clOpen = []; }")
    except Exception:
        pass
    before = page.url
    try:
        page.locator('[data-cl="1"]').first.click(timeout=4000)
    except Exception:
        try:
            page.evaluate("() => { const e = document.querySelector('[data-cl=\"1\"]'); if (e) e.click(); }")
        except Exception:
            pass
    for _ in range(12):                             # chờ tối đa ~3,6 giây, thấy file là đi tiếp
        page.wait_for_timeout(300)
        if files or named or new_pages or nav:
            page.wait_for_timeout(300)
            break
    opened = []
    try:
        opened = page.evaluate("() => window.__clOpen || []") or []
    except Exception:
        pass
    for np_ in new_pages:
        try:
            np_.wait_for_load_state("commit", timeout=6000)
        except Exception:
            pass
        nav.append(np_.url)
    # ưu tiên: file thật (PDF) > link chứa đúng tên file > link trang định mở > tab mới
    url = None
    for group in (files, named, opened, nav):
        for u in group:
            if _usable(u, list_url):
                url = u
                break
        if url:
            break
    for np_ in new_pages:
        try:
            np_.close()
        except Exception:
            pass
    if page.url != before:
        if not url and _usable(page.url, list_url):
            url = page.url
        try:
            page.go_back(wait_until="domcontentloaded", timeout=20000)
            settle(page, max_ms=5000)
            if page.url != before:
                page.goto(before, wait_until="domcontentloaded", timeout=30000)
                settle(page, max_ms=6000)
        except Exception:
            pass
    try:
        ctx.remove_listener("page", on_page)
        ctx.remove_listener("request", on_ctx_req)
        ctx.remove_listener("response", on_resp)
        page.remove_listener("download", on_dl)
    except Exception:
        pass
    return url


def _fname(u):
    n = unquote(urlparse(u).path.rsplit("/", 1)[-1])
    return n if re.search(r"\.pdf$", n, re.I) else ""


def _apply_prefix(db):
    """Bắt được link thật của 1 báo cáo -> suy ra thư mục chứa file, sửa link cho các báo cáo khác cùng nguồn."""
    pref = db.setdefault("file_prefix", {})
    for r in db["reports"].values():
        if r["source"] in CLICK_SOURCES and r.get("open_url"):
            fn = _fname(r["url"])
            ou = r["open_url"].split("?")[0]
            if fn and unquote(ou).endswith("/" + fn):
                folder = ou[: ou.rfind("/") + 1]
                if re.search(r"/20\d{6}/$", folder):   # VCBS: mỗi ngày 1 thư mục -> không đoán được
                    pref.pop(srckey(r), None)
                    continue
                pref[srckey(r)] = folder
    n = 0
    for r in db["reports"].values():
        p = pref.get(srckey(r))
        fn = _fname(r["url"])
        if p and fn and not r.get("open_url") and r["source"] in CLICK_SOURCES:
            r["open_url"] = p + quote(fn)
            r["guess_url"] = True
            r["pdf_url"] = r["open_url"]
            if not r.get("rec") and not r.get("target"):
                r["enriched"] = False           # đọc PDF thật lấy khuyến nghị / giá mục tiêu
            n += 1
    return n


def resolve_click_links(db, headless=True):
    groups = {}
    for r in db["reports"].values():
        if r["source"] in CLICK_SOURCES and not r.get("open_url"):          # chưa có link riêng -> lần nào cũng thử lại
            groups.setdefault(srckey(r), []).append(r)
    if not groups:
        return _apply_prefix(db)
    list_of = {}
    for n, u in SOURCES:
        list_of.setdefault(n, u)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return 0
    total = got = 0
    print("\nBấm thử nút tải để lấy đúng link từng báo cáo (" + ", ".join(sorted(CLICK_SOURCES)) + ")...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(user_agent=UA, locale="vi-VN", accept_downloads=True,
                                  viewport={"width": 1400, "height": 1000}, ignore_https_errors=True)
        ctx.add_init_script(OPEN_HOOK_JS)
        ctx.route("**/*", lambda route: route.abort()
                  if route.request.resource_type in ("media", "font")
                  or BLOCK_HOSTS.search(route.request.url) else route.continue_())
        for key, recs in groups.items():
            url = list_of.get(key)
            if not url:
                continue
            recs.sort(key=lambda r: r.get("date") or "", reverse=True)
            todo = recs[:CLICK_PER_SOURCE]
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                settle(page, max_ms=10000, min_ms=1500)
                for label in EXTRA_CLICKS.get(key, []):
                    try:
                        page.get_by_text(label.lstrip("="), exact=label.startswith("=")).first.click(timeout=2500)
                        settle(page, max_ms=5000)
                    except Exception:
                        pass
                for pg in range(1, MAX_PAGES + 1):
                    left = []
                    for r in todo:
                        try:
                            if not page.evaluate(MARK_CARD_JS, r["title"]):
                                left.append(r)
                                continue
                            total += 1
                            r["click_v2"] = r.get("click_v2", 0) + 1
                            u = _click_capture(page, url, _fname(r["url"]))
                            if u:
                                r["open_url"] = u
                                if re.search(r"\.pdf(\?|#|$)", u, re.I) or "download" in u.lower():
                                    r["pdf_url"] = u
                                    if not r.get("rec") and not r.get("target"):
                                        r["enriched"] = False     # đọc lại PDF thật để lấy khuyến nghị
                                got += 1
                        except Exception:
                            continue
                    todo = left
                    if not todo or pg == MAX_PAGES:
                        break
                    try:                                           # sang trang sau tìm các báo cáo còn lại
                        if not page.evaluate(NEXT_PAGE_JS, pg + 1):
                            break
                        settle(page, max_ms=6000, min_ms=800)
                    except Exception:
                        break
                for r in todo:                                     # không thấy trên 3 trang -> thôi, đỡ bấm mãi
                    r["click_v2"] = r.get("click_v2", 0) + 1
            except Exception as e:
                print(f"  ! {key}: {str(e)[:80]}")
            finally:
                try:
                    page.close()
                except Exception:
                    pass
        browser.close()
    extra = _apply_prefix(db)
    print(f"  Lấy được link báo cáo cho {got}/{total} báo cáo" + (f" (+{extra} suy ra cùng thư mục)" if extra else ""))
    return got


def resolve_pdf_links(db, headless=True):
    """Báo cáo mở qua trang chi tiết (chậm trên điện thoại) -> tìm link file PDF để mở thẳng."""
    todo = [r for r in db["reports"].values()
            if r["source"] in PDF_FROM_DETAIL and not r.get("pdf") and not r.get("pdf_url")
            and not r.get("pdf_tried") and "#" not in r["url"]]
    todo.sort(key=lambda r: r.get("date") or (r.get("first_seen") or "")[:10], reverse=True)
    todo = todo[:PDF_PER_RUN]
    if not todo:
        return 0
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return 0
    import queue
    import threading
    print(f"\nLấy link PDF từ trang chi tiết cho {len(todo)} báo cáo (BSC, ACBS, Agriseco, MBS, KIS)...")
    jobs = queue.Queue()
    for r in todo:
        jobs.put(r)
    th = [threading.Thread(target=_pdf_worker, args=(jobs, headless), daemon=True)
          for _ in range(max(1, min(WORKERS, len(todo))))]
    for t in th:
        t.start()
    for t in th:
        t.join()
    got = sum(1 for r in todo if r.get("pdf_url"))
    print(f"  Tìm được file PDF cho {got}/{len(todo)} báo cáo")
    return got


def fetch_pages(sources, headless=True):
    """Quét song song WORKERS trang cùng lúc. Trả về list (tên, url, html|None, lỗi|None, jsons)."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        print("! Chưa cài Playwright -> dùng requests (trang chạy JS có thể ra 0 kết quả)")
        return fetch_pages_requests(sources)
    import queue
    import threading
    jobs = queue.Queue()
    for i, (name, url) in enumerate(sources):
        jobs.put((i, name, url))
    results = [None] * len(sources)
    n = max(1, min(WORKERS, len(sources)))
    if not headless:
        n = 1                                      # --show: 1 cửa sổ cho dễ xem
    t0 = time.time()
    threads = [threading.Thread(target=_worker, args=(jobs, results, headless), daemon=True)
               for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"  (tải {len(sources)} trang mất {time.time() - t0:.0f} giây, {n} luồng)")
    return [r or (sources[i][0], sources[i][1], None, "Không tải được", [])
            for i, r in enumerate(results)]


def fetch_pages_requests(sources):
    import requests
    out = []
    for name, url in sources:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30, verify=False)
            r.encoding = r.apparent_encoding or "utf-8"
            out.append((name, url, r.text, None, []))
            print(f"  ✓ {name:9s} tải xong")
        except Exception as e:
            out.append((name, url, None, str(e)[:150], []))
            print(f"  ✗ {name:9s} lỗi: {e}")
    return out


# ----------------------------------------------------------------------------
# 3. BÓC TÁCH: mã CP, tiêu đề, ngày, link
# ----------------------------------------------------------------------------
MONTHS_EN = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](20\d{2})\b"), "dmy"),
    (re.compile(r"\b(20\d{2})[/\-.](\d{1,2})[/\-.](\d{1,2})\b"), "ymd"),
    # "11 tháng 9, 2026" / "11 Th09 2026" / "08 Tháng 10 - 2026" (VDSC) / "Ngày 08 tháng 10 năm 2026"
    (re.compile(r"\b(\d{1,2})\s*(?:tháng|thg|th)\.?\s*(\d{1,2})\s*(?:[-–,/]|năm)?\s*(20\d{2})\b", re.I), "dmy"),
    # "08 - 10 - 2026" / "08 / 10 / 2026" (có khoảng trắng)
    (re.compile(r"\b(\d{1,2})\s+[-/.]\s+(\d{1,2})\s+[-/.]\s+(20\d{2})\b"), "dmy"),
    # "Oct 12, 2026" / "12 Oct 2026"
    (re.compile(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(20\d{2})\b"), "mdy_en"),
    (re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?,?\s+(20\d{2})\b"), "dmy_en"),
]

# chữ viết tắt 3 ký tự hay gặp nhưng KHÔNG phải mã cổ phiếu
NOT_TICKERS = set("""
NAM VIE CAN HAN TCT NHA KQK ONE TWO VNM_ YOY QOQ MTD BCN BCDN BCVM SSC UBC HSC VSD

CEO CFO GDP USD VND EUR ETF IPO EPS ROE ROA NIM CPI FDI PMI FED OPEC PDF NPL CAR LDR CIR
BOT BCT KQK NĐT HĐQ TTCK HSX HNX UPC OTC GTGT VAT TNDN EBT EBIT LNST LNTT DCF EVA YOY QOQ
YTD MOM ESG KCN BĐS SXKD NHNN BTC UBND QĐ TP HCM API APP WEB NEW TOP HOT ALL THE AND FOR
VNI VN30 MSCI FTSE FOMC IMF WTO ASEAN TPP EVFTA CPTPP LNG LPG EV AI USA UAE GEN PHẦN
MUA BAN BÁN GIỮ KHẢ QUAN TĂNG GIẢM OUT BUY SELL HOLD ADD RED ACC SUM NET
Q1 Q2 Q3 Q4 H1 H2 FY KQKD ĐHĐCĐ MBS SSI VCBS BSC KIS KBSV VDSC ACBS VPBS MAS AGR
""".split())
# Lưu ý: SSI, MBS, BSC, VDSC... vừa là tên CTCK vừa là mã CP; loại ra để tránh nhận nhầm
# tên nguồn thành mã. Nếu chính CTCK viết báo cáo về mình thì sẽ không bắt được mã.

T = r"([A-Z][A-Z0-9]{2})"
TICKER_PATTERNS = [
    re.compile(r"(?:[Cc]ập nhật|[Uu]pdate|[Ss]napshot)\s+" + r"([A-Z][A-Z0-9]{2})\b"),
    re.compile(r"(?:HOSE|HSX|HNX|UPCOM|UPCoM|Upcom)\s*[:\-–]\s*" + T + r"\b"),
    re.compile(r"^\s*\[?" + T + r"\]?\s*[-–—:|]"),          # "FPT - ...", "FPT: ..."
    re.compile(r"\(\s*" + T + r"\s*\)"),                      # "... (FPT)"
    re.compile(r"(?:[Mm]ã|[Cc]ổ phiếu|CP)\s+" + T + r"\b"),
    re.compile(r"^\s*" + T + r"\s"),                          # "FPT Cập nhật..."
    re.compile(r"(?<![A-Za-z0-9À-ỹ])" + T + r"(?![A-Za-z0-9À-ỹ])"),  # bất kỳ đâu
]
SLUG_TICKER = re.compile(r"(?:^|[/_\-])([A-Za-z]{3})(?:[_\-.]|$)")

REPORT_WORDS = re.compile(
    r"báo cáo|cập nhật|khuyến nghị|định giá|phân tích|kqkd|kết quả kinh doanh|"
    r"triển vọng|mục tiêu|đhđcđ|lợi nhuận|doanh thu|tăng trưởng|initiat|update|report",
    re.I)

SKIP_HREF = re.compile(r"^(javascript:|mailto:|tel:|#)", re.I)
SKIP_TITLE = re.compile(r"^(xem thêm|xem chi tiết|chi tiết|tải về|tải xuống|download|đọc tiếp|xem|"
                        r"trang chủ|đăng nhập|đăng ký|\d+|«|»|<|>|tiếp|trước|sau)$", re.I)


def find_date(text):
    for rx, kind in DATE_PATTERNS:
        m = rx.search(text)
        if m:
            try:
                g = m.groups()
                if kind == "dmy":
                    d, mo, y = map(int, g)
                elif kind == "ymd":
                    y, mo, d = map(int, g)
                elif kind == "mdy_en":
                    mo, d, y = MONTHS_EN.get(g[0].lower()), int(g[1]), int(g[2])
                else:
                    d, mo, y = int(g[0]), MONTHS_EN.get(g[1].lower()), int(g[2])
                if not mo:
                    continue
                return datetime(y, mo, d).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


# tên công ty hay gặp trong tiêu đề không ghi mã
NAME_TICKER = [
    ("vingroup", "VIC"), ("vinhomes", "VHM"), ("vincom retail", "VRE"), ("vinamilk", "VNM"),
    ("hòa phát", "HPG"), ("masan", "MSN"), ("thế giới di động", "MWG"), ("nhựa bình minh", "BMP"),
    ("tiền phong", "NTP"), ("vietcombank", "VCB"), ("vietinbank", "CTG"), ("bidv", "BID"),
    ("techcombank", "TCB"), ("vpbank", "VPB"), ("mb bank", "MBB"), ("quân đội", "MBB"),
    ("acb", "ACB"), ("sacombank", "STB"), ("hdbank", "HDB"), ("tập đoàn fpt", "FPT"),
    ("petrolimex", "PLX"), ("lọc hóa dầu", "BSR"), ("pv gas", "GAS"), ("điện lực dầu khí", "POW"),
    ("cơ điện lạnh", "REE"), ("gemadept", "GMD"), ("hàng hóa sài gòn", "SCS"),
    ("taseco", "AST"), ("nước - môi trường bình dương", "BWE"), ("vĩnh hoàn", "VHC"),
    ("phú nhuận", "PNJ"), ("đức giang", "DGC"), ("đạm cà mau", "DCM"), ("đạm phú mỹ", "DPM"),
    ("kinh bắc", "KBC"), ("becamex", "BCM"), ("coteccons", "CTD"), ("sabeco", "SAB"),
    ("vietjet", "VJC"), ("vietnam airlines", "HVN"), ("viettel post", "VTP"),
]


TICKER_ONLY = re.compile(r"[A-Z][A-Z0-9]{2}")


def card_ticker(node):
    """Mã CP in riêng trên thẻ báo cáo (VCBS: chữ to 'MSN' ở đầu thẻ, tiêu đề không ghi mã)."""
    if node is None:
        return None
    try:
        strings = list(node.stripped_strings)
    except AttributeError:
        return None
    for t in strings:
        t = t.strip()
        if TICKER_ONLY.fullmatch(t) and t not in NOT_TICKERS and not t[1:].isdigit():
            return t
    return None


COMPANY_LEAD = re.compile(r"^\s*(ctcp|công ty|cty|tổng công ty|tổng cty|tct|ngân hàng|tập đoàn|nh tmcp)\b", re.I)


def find_ticker(title, href):
    if COMPANY_LEAD.match(title or ""):               # "CTCP TCT Nước..." -> lấy mã từ tên file / tên cty
        low = title.lower()
        for nm, tk in NAME_TICKER:
            if nm in low:
                return tk
        ft = ticker_from_filename(unquote(urlparse(href).path)) if href else None
        if ft:
            return ft
    for rx in TICKER_PATTERNS:
        for m in rx.finditer(title):
            t = m.group(1)
            if t not in NOT_TICKERS and not t.isdigit():
                return t
    low = title.lower()
    for nm, tk in NAME_TICKER:
        if nm in low:
            return tk
    # thử trong tên file/đường dẫn: .../FPT_update_2026.pdf
    path = unquote(urlparse(href).path)
    fname = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    for tok in re.split(r"[_\-\s.()]+", fname):
        if re.fullmatch(r"[A-Z][A-Z0-9]{2}", tok) and tok not in NOT_TICKERS and not tok[1:].isdigit():
            return tok
    return None


def page_date(ctx, title, url=""):
    """Ngày ĐĂNG báo cáo: ưu tiên ngày in riêng trên trang (ngoài tiêu đề),
    sau đó mới đến ngày ghi trong tiêu đề, cuối cùng là ngày trong link.
    Trả về (ngày, nguồn ngày)."""
    rest = " ".join((ctx or "").split())
    if title:
        rest = rest.replace(" ".join(title.split()), " ")
    d = find_date(rest)
    if d:
        return d, "page"
    d = find_date(title or "")
    if d:
        return d, "title"
    d = find_date(url or "")
    return (d, "url") if d else ("", "")


HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"]


def _text_wo_titles(node, own=""):
    """Chữ của khối, bỏ phần tiêu đề (chữ trong link, heading, chữ đậm, class *title*)."""
    txt = node.get_text(" ", strip=True)
    if own:
        txt = txt.replace(own, " ")
    for el in node.find_all(HEADING_TAGS) + node.find_all(class_=re.compile(r"title|tieu-?de", re.I)):
        t = el.get_text(" ", strip=True)
        if t:
            txt = txt.replace(t, " ")
    return txt


def _date_outside_title(node, own=""):
    return bool(find_date(_text_wo_titles(node, own)))


def _n_dates(txt):
    return len({m.group(0) for rx, _ in DATE_PATTERNS for m in rx.finditer(txt)})


def _other_links(node, href):
    """Số link báo cáo KHÁC trong khối (bỏ link đăng nhập, javascript, #)."""
    hs = set()
    for x in node.find_all("a", href=True):
        h = x["href"].strip()
        if not h or h == href or SKIP_HREF.match(h):
            continue
        if AUTH_URL.search(urlparse(h).path + "?") and "returnUrl" not in h:
            continue
        hs.add(h)
    return len(hs)


def _context(a):
    """(khối có ngày đăng, thẻ báo cáo) chứa link a.
    Thẻ = tổ tiên lớn nhất chưa chứa link báo cáo khác hoặc ngày đăng thứ 2."""
    href = (a.get("href") or "").strip()
    own = a.get_text(" ", strip=True)
    node, card, dated = a, a, None
    if _date_outside_title(a):
        dated = a                                   # cả thẻ là 1 link có ngày bên trong (VDSC)
    for _ in range(8):
        par = node.parent
        if par is None or par.name in ("body", "html", "[document]"):
            break
        txt = par.get_text(" ", strip=True)
        if len(txt) > 5000 or _other_links(par, href) >= 1 or \
                _n_dates(_text_wo_titles(par, own)) > 1:
            break
        node = par
        card = node
        if dated is None and _date_outside_title(node, own):
            dated = node
        if node.name in ("li", "tr", "article") and len(txt) <= 2000:
            break
    return dated, card


def context_node(a):
    """Khối dùng để đọc ngày đăng: thẻ báo cáo (có ngày thì càng tốt)."""
    dated, card = _context(a)
    return card if card is not a else (dated or a)


def context_card(a):
    return _context(a)[1]


def context_block(a):
    return context_node(a).get_text(" ", strip=True)


# ---------- QUY TẮC LỌC: chỉ lấy danh sách báo cáo của trung tâm phân tích ----------
# Link báo cáo của từng nguồn phải khớp mẫu này (lấy từ dữ liệu thật đã quét).
# Nguồn chưa có mẫu thì dùng cách nhận diện "danh sách chính" bên dưới.
SOURCE_URL_RULES = {
    "ACBS":  r"/trung-tam-phan-tich/chi-tiet/|acbsmedia/.+\.pdf",
    "VDSC":  r"file-storage/.+\.pdf|\.pdf",
    "VBSE":  r"/wp-content/uploads/.+\.pdf",
    "BSC":   r"bsc\.com\.vn/bao-cao/\d+",
    "KBSV":  r"/pic/Service/.+\.pdf",
    "KIS":   r"kisvn\.vn/phan-tich-thi-truong/[a-z0-9]{3}-",
    "Vietcap": r"/iq/report-detail/",
    "NG_Vietcap": r"/iq/report-detail/",
    "VM_Vietcap": r"/iq/report-detail/",
}
# link kiểu trang tổng quan mã CP / bảng giá / tin tức -> loại
OVERVIEW_URL = re.compile(
    r"/(co-phieu|ma-chung-khoan|ma-ck|stock|stocks|symbol|ticker|quote|bang-gia|"
    r"tong-quan|overview|company|doanh-nghiep-niem-yet|ho-so|profile|chart|bieu-do|"
    r"tin-tuc|news|su-kien|event|khuyen-mai|uu-dai|tuyen-dung|career|san-pham|"
    r"dich-vu|lien-he|gioi-thieu|about|faq|ho-tro|huong-dan)(/|$|\?|-)", re.I)
# tiêu đề khối không phải danh sách báo cáo
BAD_SECTION = re.compile(
    r"tổng quan|tin tức|tin nổi bật|nổi bật|xem nhiều|đọc nhiều|liên quan|sự kiện|"
    r"ưu đãi|khuyến mãi|tuyển dụng|bảng giá|top cổ phiếu|cổ phiếu tăng|cổ phiếu giảm|"
    r"overview|highlight|most read|related|news", re.I)


def section_title(node):
    """Tìm tiêu đề (h1-h4 / class *title*) gần nhất phía trên khối chứa danh sách."""
    cur = node
    for _ in range(3):
        if cur is None or cur.parent is None:
            break
        for sib in cur.find_previous_siblings(limit=2):
            h = sib if sib.name in ("h1", "h2", "h3", "h4") else sib.find(["h1", "h2", "h3", "h4"])
            if h:
                return h.get_text(" ", strip=True)[:80]
        cur = cur.parent
    return ""


VPBS_LIST_SLUGS = {"phan-tich-co-phieu-doanh-nghiep", "cap-nhat-vi-mo", "phan-tich-nganh",
                   "ban-tin", "chien-luoc-dau-tu", "bao-cao-thi-truong"}
LOGIN_WRAP = re.compile(r"/(?:dang-?nhap|login|signin|sign-in|account/login)[^?]*\?(?:.*&)?"
                        r"(?:returnUrl|ReturnUrl|redirect|redirectUrl|next|url)=(.+)$", re.I)


def clean_url(url):
    """Bỏ lớp trang đăng nhập bọc ngoài link (VDSC: /dang-nhap?returnUrl=/api/...pdf)
    để bấm vào là mở thẳng báo cáo; mã hóa khoảng trắng trong tên file."""
    m = LOGIN_WRAP.search(url)
    if m:
        inner = unquote(m.group(1))
        url = urljoin(url, inner)

    return quote(url, safe=":/?=&%#()-_.~+,;@!$'*[]")


AUTH_URL = re.compile(r"/(dang-?nhap|dang-?ky|login|logout|signin|sign-in|signup|register|"
                      r"account|tai-khoan|mo-tai-khoan|quen-mat-khau)(/|$|\?|#)", re.I)


def url_ok(name, url):
    if AUTH_URL.search(urlparse(url).path + "?") and not LOGIN_WRAP.search(url):
        return False
    if OVERVIEW_URL.search(urlparse(url).path + "?" + urlparse(url).query):
        return False
    rule = SOURCE_URL_RULES.get(name)
    return bool(re.search(rule, url, re.I)) if rule else True


def in_nav(a):
    for p in a.parents:
        if p.name in ("nav", "header", "footer"):
            return True
        cls = " ".join(p.get("class", [])).lower()
        if any(k in cls for k in ("menu", "navbar", "breadcrumb", "footer", "header")):
            return True
    return False


DATE_ONLY = re.compile(r"^[\d\s/\-.:,thángTHGAMPMampm]+$")


NEWS_TITLE = re.compile(r"nghị quyết\s*(hđqt|hội đồng quản trị|đhđcđ|đại hội)|công bố thông tin|"
                        r"thông báo (giao dịch|chốt|ngày|về)|giao dịch cổ phiếu quỹ|"
                        r"thay đổi nhân sự|bổ nhiệm|miễn nhiệm|giấy chứng nhận đăng ký", re.I)
JUNK_TITLE = re.compile(r"^(chính sách|điều khoản|điều kiện|quy định|quy chế|biểu phí|hướng dẫn|"
                        r"bảo mật|khuyến cáo|tuyên bố|giới thiệu|liên hệ|tuyển dụng|mẫu |"
                        r"privacy|terms|disclaimer|cookie)", re.I)
GENERIC_LABEL = re.compile(r"^(phân tích (công ty|doanh nghiệp|ngành|vĩ mô)|báo cáo (công ty|"
                           r"doanh nghiệp|ngành)|company (report|analysis)|equity research)$", re.I)


def _ok_title(t):
    return (len(t) >= 12 and not DATE_ONLY.match(t) and not SKIP_TITLE.match(t)
            and not GENERIC_LABEL.match(t))


def block_title(node):
    """Tiêu đề của 1 khối báo cáo: chữ đậm/heading/class *title* trước, rồi chuỗi dài vừa phải."""
    for el in node.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"]) + \
            node.find_all(class_=re.compile(r"title|name|heading|tieu-?de", re.I)):
        t = " ".join(el.get_text(" ", strip=True).split())
        if _ok_title(t) and len(t) <= 300:
            return t
    best = ""
    for t in node.stripped_strings:
        t = " ".join(t.split())
        if _ok_title(t) and len(t) <= 250 and len(t) > len(best):
            best = t
    return best


def anchor_title(a):
    """Tiêu đề của link. Nếu cả thẻ là 1 link (VDSC: ngày + tiêu đề + lượt tải),
    lấy heading bên trong hoặc ghép các chuỗi không phải ngày/số."""
    if a.find(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"]) or \
            a.find(class_=re.compile(r"title|name|heading|tieu-?de", re.I)):
        t = block_title(a)
        if t:
            return t
    parts = [" ".join(t.split()) for t in a.stripped_strings]
    parts = [t for t in parts if t and not DATE_ONLY.match(t)]
    return " ".join(parts)


LEAD_DATE = re.compile(r"^\s*(\d{1,2}\s*(?:tháng|thg)\s*\d{1,2}\s*[-–,/]?\s*20\d{2})\s+(.+?)(?:\s+\d{1,6})?\s*$",
                       re.I)


def row_title(a):
    node = context_card(a)
    return "" if node is a else block_title(node)[:300]


def parse_rows(name, page_url, soup):
    """Danh sách không dùng thẻ <a href> (bấm bằng onclick / data-url)."""
    out = {}
    for el in soup.find_all(["li", "tr", "article", "div"]):
        txt = el.get_text(" ", strip=True)
        if not (20 <= len(txt) <= 5000) or not find_date(txt):
            continue
        if len({m.group(0) for rx, _ in DATE_PATTERNS for m in rx.finditer(txt)}) > 1:
            continue                                   # cả danh sách, không phải 1 báo cáo
        # khối trong cùng mà vẫn có cả tiêu đề + ngày
        if any(find_date(c.get_text(" ", strip=True)) and block_title(c)
               for c in el.find_all(["li", "tr", "article", "div"])):
            continue
        link = None
        for tag in [el] + el.find_all(True):
            for attr in ("data-href", "data-url", "data-link", "data-file", "href", "onclick"):
                v = tag.get(attr) or ""
                if attr == "href" and SKIP_HREF.match(v):
                    v = ""
                if attr == "onclick" or v.lower().startswith("javascript"):
                    m = re.search(r"['\"]((?:https?:)?/[^'\"]+|[^'\"]+\.pdf[^'\"]*)['\"]", v)
                    v = m.group(1) if m else ""
                if v:
                    link = clean_url(urljoin(page_url, v))
                    break
            if link:
                break
        best = block_title(el)
        if not best:
            continue
        url = link or (page_url + "#" + re.sub(r"\W+", "-", best.lower())[:80])
        if link and not url_ok(name, url):
            continue
        ticker = find_ticker(best, url) or card_ticker(el)
        if not (ticker or REPORT_WORDS.search(best) or ".pdf" in url.lower() or is_vm(name)):
            continue
        out[url] = {"ticker": ticker or "", "title": best[:300], "source": name,
                    "date": page_date(txt, best, url)[0], "date_src": page_date(txt, best, url)[1],
                    "url": url, "pdf": ".pdf" in url.lower(),
                    "_g": id(el.parent), "_gnode": el.parent}
    return pick_main_list(name, list(out.values()))


def parse(name, page_url, html):
    soup = BeautifulSoup(html, "lxml")
    found = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if SKIP_HREF.match(href):
            continue
        url = clean_url(urljoin(page_url, href))
        title = anchor_title(a)
        _t = lang_text(title)
        title = _t if isinstance(_t, str) else title
        weak = is_file_title(title)
        if weak:
            title = nice_file_title(title) if len(nice_file_title(title)) >= 12 else (row_title(a) or nice_file_title(title))
        if len(title) < 12:
            title = (a.get("title") or a.get("aria-label") or title).strip()
        if in_nav(a):
            continue
        if len(title) < 12 or SKIP_TITLE.match(title):
            title = row_title(a)            # nút "Tải về"/"Xem" -> tiêu đề nằm ở ô bên cạnh
            if not title:
                continue

        if not url_ok(name, url):
            continue
        is_pdf = ".pdf" in url.lower()
        node = context_node(a)
        ctx = node.get_text(" ", strip=True)
        date, date_src = page_date(ctx, title, url)
        ticker = find_ticker(title, url) or card_ticker(node) or card_ticker(a)

        report_like = bool(REPORT_WORDS.search(title)) or is_pdf or is_vm(name)
        if not (ticker or report_like):
            continue
        # báo cáo trong danh sách luôn có ngày đăng (trừ khi là link PDF/khớp mẫu nguồn)
        if not date and not (is_pdf or name in SOURCE_URL_RULES):
            continue
        group = node.parent if node is not a else (a.parent.parent or a.parent)

        rec = {
            "ticker": ticker or "",
            "title": title[:300],
            "source": name,
            "date": date or "",
            "date_src": date_src,
            "weak_title": weak,
            "url": url,
            "pdf": is_pdf,
            "_g": id(group),
            "_gnode": group,
        }
        old = found.get(url)
        # cùng 1 link xuất hiện nhiều lần (ảnh + tiêu đề) -> giữ bản đầy đủ nhất
        if (not old or len(rec["title"]) > len(old["title"])
                or (rec["ticker"] and not old["ticker"])):
            if old:
                rec["date"] = rec["date"] or old["date"]
                rec["ticker"] = rec["ticker"] or old["ticker"]
            found[url] = rec
    res = pick_main_list(name, list(found.values()))
    if len(res) < 3:                             # ít link thường -> thử khối onclick/data-url
        seen = {r["title"] for r in res}
        res += [r for r in parse_rows(name, page_url, soup) if r["title"] not in seen]
    return res


NON_COMPANY = re.compile(
    r"vĩ mô|chiến lược|bản tin|nhận định thị trường|triển vọng thị trường|báo cáo ngành|"
    r"cập nhật ngành|ngành\s|trái phiếu|tỷ giá|lãi suất|chính sách tiền tệ|etf|phái sinh|"
    r"macro|strategy|sector|industry|outlook|daily|weekly|monthly|morning|market|bond|fx",
    re.I)
# nguồn mà dữ liệu ngầm chứa báo cáo của MỌI tab (vĩ mô, chiến lược, ngành...):
# chỉ giữ báo cáo có tiêu đề đang HIỆN trên tab đã mở (vd Vietcap: tab Phân tích doanh nghiệp)
ONLY_VISIBLE_TAB = {"Vietcap", "NG_Vietcap", "VM_Vietcap"}


def _norm(t):
    return re.sub(r"\W+", " ", t.lower()).strip()


def visible_filter(recs, html):
    """Giữ báo cáo có tiêu đề xuất hiện trong phần trang đang hiển thị."""
    pages = [BeautifulSoup(part, "lxml") for part in html.split(PAGE_BREAK)]
    for sp in pages:
        for t in sp(["script", "style", "noscript", "template"]):
            t.decompose()
    text = _norm(" ".join(sp.get_text(" ") for sp in pages))
    return [r for r in recs if _norm(r["title"])[:45] in text]


# nguồn trộn nhiều loại báo cáo -> bắt buộc có mã CP
COMPANY_NEEDS_TICKER = {"Vietcap", "VPBankS", "MAS"}


def is_company_report(name, r):
    if r.get("ticker"):
        return True
    if name in COMPANY_NEEDS_TICKER:
        return False
    return not NON_COMPANY.search(r.get("title", ""))


def pick_main_list(name, recs):
    """Giữ các khối danh sách lớn (danh sách báo cáo chính), bỏ khối lẻ ở sidebar/tổng quan."""
    recs = [r for r in recs if not JUNK_TITLE.match(r.get("title", "").strip())
            and not NEWS_TITLE.search(r.get("title", ""))]
    groups = {}
    for r in recs:
        groups.setdefault(r["_g"], []).append(r)
    keep = []
    if groups:
        biggest = max(len(v) for v in groups.values())
        for g, items in groups.items():
            strict = name in SOURCE_URL_RULES          # link đã khớp mẫu nguồn -> tin được
            if not strict and not is_vm(name) and \
                    BAD_SECTION.search(section_title(items[0]["_gnode"])):
                continue
            if strict or biggest < 3 or len(items) >= max(3, biggest * 0.5):
                keep += items
    for r in keep:
        r.pop("_g", None)
        r.pop("_gnode", None)
    if is_vm(name):
        return keep
    return [r for r in keep if is_company_report(name, r)]

# --- đọc dữ liệu JSON ngầm -------------------------------------------------
TITLE_KEYS = ("title", "name", "reportName", "reportTitle", "subject", "tieuDe", "headline")
DATE_KEYS = ("publishDate", "publishedDate", "publishedAt", "published_at", "releaseDate",
             "date", "createdDate", "createdAt", "created_at", "postDate", "issueDate",
             "updatedDate", "modifiedDate", "ngay")
URL_KEYS = ("fileUrl", "file_url", "pdfUrl", "pdf", "attachment", "attachmentUrl", "file",
            "filePath", "downloadUrl", "link", "url", "href", "path", "slug", "permalink")
TICKER_KEYS = ("ticker", "symbol", "stockCode", "stockSymbol", "symbolCode", "code", "maCK",
               "maCP", "ma", "organCode", "stock", "tag", "tags", "companyCode")


FILE_TITLE = re.compile(r"\.(pdf|docx?|xlsx?|pptx?)\b|^[\w\-]+/", re.I)


def is_file_title(t):
    """Tiêu đề thực ra là tên file / đường dẫn (VPBankS: 'trung-tam-phan-tich/.../CTG_xxx.pdf')."""
    return bool(FILE_TITLE.search(t or ""))


def nice_file_title(t):
    base = re.split(r"[/\\]", t)[-1]
    base = re.sub(r"\.(pdf|docx?|xlsx?|pptx?)$", "", base, flags=re.I)
    base = re.sub(r"\s*\(\d+\)$", "", base)
    return re.sub(r"[_]+", " ", base).strip()


def ticker_from_filename(t):
    base = re.split(r"[/\\]", t)[-1]
    for tok in re.split(r"[_\-\s.()]+", base):
        if TICKER_ONLY.fullmatch(tok) and tok not in NOT_TICKERS and not tok[1:].isdigit():
            return tok
    return None


DATE_KEY_RX = re.compile(r"date|time|created|publish|posted|ngay|release|issue", re.I)


def _pick_date(d):
    v = _to_date(_pick(d, DATE_KEYS))
    if v:
        return v
    for k, val in d.items():
        if isinstance(val, (str, int)) and DATE_KEY_RX.search(k) and not re.search(r"update|modif|expire|end", k, re.I):
            v = _to_date(str(val))
            if v:
                return v
    return None


LANG_KEYS = ("vi", "vn", "vi-VN", "vi_VN", "VI", "default", "en")


def lang_text(v):
    """Giá trị đa ngôn ngữ -> chữ tiếng Việt.
    MAS: '{"vi":"Báo cáo ngắn ..."}' (chuỗi JSON) hoặc {"vi": ..., "en": ...}."""
    if isinstance(v, str):
        t = v.strip()
        if t.startswith("{") and t.endswith("}"):
            try:
                v = json.loads(t)
            except ValueError:
                return v
        else:
            return v
    if isinstance(v, dict):
        for k in LANG_KEYS:
            if isinstance(v.get(k), str) and v[k].strip():
                return v[k].strip()
        for x in v.values():
            if isinstance(x, str) and x.strip():
                return x.strip()
        return None
    return v


def _pick(d, keys):
    low = {k.lower(): k for k in d}
    for k in keys:
        if k.lower() in low:
            v = lang_text(d[low[k.lower()]])
            if isinstance(v, dict):
                v = v.get("url") or v.get("name") or v.get("code")
            if isinstance(v, list) and v:
                v = v[0] if isinstance(v[0], str) else (v[0].get("url") if isinstance(v[0], dict) else None)
            if isinstance(v, (str, int)) and str(v).strip():
                return str(v).strip()
    return None


def _to_date(v):
    if not v:
        return None
    if v.isdigit() and len(v) >= 10:           # timestamp
        try:
            ts = int(v) / (1000 if len(v) >= 13 else 1)
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            return None
    m = re.match(r"(20\d{2}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?", v)
    if m:
        from datetime import timezone
        try:
            dt = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
            tz = m.group(3)
            if tz:                                  # có múi giờ (thường UTC) -> giờ Việt Nam
                tz = "+00:00" if tz == "Z" else (tz if ":" in tz else tz[:3] + ":" + tz[3:])
                dt = datetime.fromisoformat(dt.isoformat() + tz).astimezone(
                    timezone(timedelta(hours=7)))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return find_date(v.replace("T", " "))


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


REPORT_API = re.compile(r"report|bao-?cao|baocao|research|analy|phan-?tich|ttpt|document|"
                        r"insight|post|article|publication|tai-?lieu|file", re.I)
BAD_API = re.compile(r"price|quote|market|chart|index|stock-?info|stockinfo|symbol|ticker|"
                     r"company-?info|overview|tong-?quan|menu|banner|config|setting|search|"
                     r"suggest|trading|realtime|board|bang-?gia|top|ranking|news|tin-?tuc", re.I)


def parse_json(name, page_url, jsons):
    found = {}
    for api_url, data in jsons:
        ap = urlparse(api_url).path + "?" + urlparse(api_url).query
        if BAD_API.search(ap) and not REPORT_API.search(urlparse(api_url).path):
            continue
        is_html_part = isinstance(data, dict) and "__html__" in data
        if not is_html_part and not REPORT_API.search(ap):
            continue
        if isinstance(data, dict) and "__html__" in data:
            for r in parse(name, api_url, data["__html__"]):
                found.setdefault(r["url"], r)
            continue
        for d in _walk(data):
            for v in d.values():                       # JSON chứa sẵn đoạn HTML
                if isinstance(v, str) and "<a" in v and len(v) > 200:
                    for r in parse(name, page_url, v):
                        found.setdefault(r["url"], r)
            title = _pick(d, TITLE_KEYS)
            if isinstance(title, str) and "<" in title and len(title) > 300:
                for r in parse(name, page_url, title):   # JSON chứa sẵn HTML
                    found.setdefault(r["url"], r)
                continue
            if not title or len(title) < 8 or title.startswith("http"):
                continue
            date = _pick_date(d)
            link = _pick(d, URL_KEYS)
            if not date or not link:          # báo cáo thật luôn có ngày và link
                continue
            title = BeautifulSoup(title, "lxml").get_text(" ", strip=True)
            weak = is_file_title(title)
            if weak:
                file_tk = ticker_from_filename(title)
                title = nice_file_title(title)
            if name.endswith("Vietcap") and "report-detail" not in (link or ""):
                slug = _pick(d, ("slug", "alias", "urlSlug", "seoUrl", "friendlyUrl"))
                if slug and not slug.startswith("http") and "/" not in slug.strip("/"):
                    link = "https://trading.vietcap.com.vn/iq/report-detail/vi/" + slug.strip("/")
            url = clean_url(urljoin(page_url, link))
            if not url_ok(name, url):
                continue
            tk = _pick(d, TICKER_KEYS)
            if not (tk and re.fullmatch(r"[A-Z][A-Z0-9]{2}", tk)):
                tk = (file_tk if weak else None) or find_ticker(title, url)
            if not tk:                                 # giá trị nào đó là đúng 1 mã CP
                for k, v in d.items():
                    if isinstance(v, str) and TICKER_ONLY.fullmatch(v.strip()) \
                            and v.strip() not in NOT_TICKERS \
                            and re.search(r"sym|tick|stock|code|ma|tag|organ", k, re.I):
                        tk = v.strip()
                        break
            if not (tk or REPORT_WORDS.search(title) or is_vm(name)):
                continue
            # loại báo cáo (category/type) phải là doanh nghiệp nếu JSON có ghi
            cat = " ".join(str(v) for k, v in d.items()
                           if isinstance(v, (str, int)) and re.search(
                               r"cat|type|group|tab|kind|loai|chuyen-?muc", k, re.I))
            if not is_vm(name) and cat and NON_COMPANY.search(cat) and not re.search(
                    r"doanh nghiệp|công ty|company|corporate|equity|stock|cổ phiếu", cat, re.I):
                continue
            if JUNK_TITLE.match(title.strip()) or NEWS_TITLE.search(title):
                continue
            rec = {"weak_title": weak, "ticker": tk or "", "title": title[:300], "source": name,
                   "date": date or "", "date_src": "json" if date else "",
                   "url": url, "pdf": ".pdf" in url.lower()}
            if is_company_report(name, rec):
                found[url] = rec
    return list(found.values())


def save_debug(name, html, jsons):
    d = BASE_DIR / "debug"
    d.mkdir(exist_ok=True)
    if html:
        (d / f"{name}.html").write_text(html, encoding="utf-8")
    if jsons:
        (d / f"{name}_json.txt").write_text(
            "\n\n".join(f"### {u}\n{json.dumps(j, ensure_ascii=False)[:20000]}" for u, j in jsons),
            encoding="utf-8")

# ----------------------------------------------------------------------------
# 4b. ĐỌC PDF: KHUYẾN NGHỊ, GIÁ MỤC TIÊU, GIÁ HIỆN TẠI, UPSIDE
# ----------------------------------------------------------------------------
# (nhãn chuẩn, loại màu) – xếp cụm dài trước để "KÉM KHẢ QUAN" không bị nhận là "KHẢ QUAN"
REC_MAP = [
    ("KHÔNG ĐÁNH GIÁ", "Không đánh giá", "na"), ("KHÔNG XẾP HẠNG", "Không xếp hạng", "na"),
    ("KÉM KHẢ QUAN", "Kém khả quan", "sell"), ("UNDERPERFORM", "Kém khả quan", "sell"),
    ("MUA MẠNH", "Mua mạnh", "buy"), ("STRONG BUY", "Mua mạnh", "buy"),
    ("KHẢ QUAN", "Khả quan", "buy"), ("OUTPERFORM", "Khả quan", "buy"),
    ("TÍCH LŨY", "Tích lũy", "buy"), ("ACCUMULATE", "Tích lũy", "buy"),
    ("NẮM GIỮ", "Nắm giữ", "hold"), ("TRUNG LẬP", "Trung lập", "hold"),
    ("THEO DÕI", "Theo dõi", "hold"), ("NEUTRAL", "Trung lập", "hold"),
    ("HOLD", "Nắm giữ", "hold"), ("GIẢM TỶ TRỌNG", "Giảm tỷ trọng", "sell"),
    ("REDUCE", "Giảm tỷ trọng", "sell"), ("TĂNG TỶ TRỌNG", "Tăng tỷ trọng", "buy"),
    ("OVERWEIGHT", "Tăng tỷ trọng", "buy"), ("UNDERWEIGHT", "Giảm tỷ trọng", "sell"), ("BÁN", "Bán", "sell"), ("SELL", "Bán", "sell"),
    ("MUA", "Mua", "buy"), ("BUY", "Mua", "buy"), ("ADD", "Mua", "buy"),
]
_REC_ALT = "|".join(re.escape(k) for k, _, _ in REC_MAP)
REC_NEAR = re.compile(r"(?:khuyến\s*nghị|khuyen\s*nghi|recommendation|rating|đánh\s*giá)"
                      r"[^\n]{0,25}?\b(" + _REC_ALT + r")\b", re.I)
REC_BRACKET = re.compile(r"[\[\(–\-]\s*(" + _REC_ALT + r")\s*[\]\)]?", re.I)
REC_ALONE = re.compile(r"(?:^|\n)\s*(" + _REC_ALT + r")\s*(?:\n|$)")

NUM = (r"(?:\([^)]{0,20}\)"                                  # bỏ qua "(VND)", "(18/09)"
       r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?"                             # bỏ qua ngày 08/10/2026
       r"|\d{1,2}\s*(?:năm|tháng|th|months?|years?)\b"              # bỏ qua "12 tháng", "1 năm"
       r"|[^\d\n]){0,25}?"
       r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)"
       r"(?![/\d])(?!\s*(?:năm|tháng|ngày|tuần|quý|year|month|%))")
TARGET_RX = [
    re.compile(r"giá\s*(?:mục\s*tiêu|muc\s*tieu)" + NUM, re.I),
    re.compile(r"target\s*price" + NUM, re.I),
    re.compile(r"\bTP\b" + NUM),
    re.compile(r"giá\s*trị\s*hợp\s*lý" + NUM, re.I),
    re.compile(r"giá\s*trị\s*(?:nội\s*tại|mục\s*tiêu)" + NUM, re.I),
]
PRICE_RX = [
    re.compile(r"giá\s*(?:hiện\s*tại|đóng\s*cửa|thị\s*trường|ngày\s*\d[\d/.]*)" + NUM, re.I),
    re.compile(r"(?:current|closing|last)\s*price" + NUM, re.I),
]
UPSIDE_RX = [
    re.compile(r"(?:upside|tiềm\s*năng\s*(?:tăng\s*giá|tăng|sinh\s*lời)|mức\s*sinh\s*lời|"
               r"lợi\s*nhuận\s*kỳ\s*vọng|tỷ\s*suất\s*sinh\s*lời)[^\d\-+\n]{0,30}?([+\-]?\d+(?:[.,]\d+)?)\s*%", re.I),
]
# BSC: "X-Alpha | DCM 33,800 +5%: ..." -> giá mục tiêu & upside ngay trong tiêu đề
TITLE_TP = re.compile(r"\b[A-Z][A-Z0-9]{2}\s+(\d{1,3}(?:[.,]\d{3})+)\s*([+\-]\d+(?:[.,]\d+)?)%")


def to_price(sv):
    """'41.500' / '41,500' -> 41500 ; '41.5' (nghìn đồng) -> 41500"""
    if not sv:
        return None
    sv = sv.strip()
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", sv):
        v = float(re.sub(r"[.,]", "", sv))
    else:
        try:
            v = float(sv.replace(",", "."))
        except ValueError:
            return None
        if v < 1000:
            v *= 1000
    return int(v) if 1000 <= v <= 5_000_000 else None


def norm_rec(word):
    w = word.upper().strip()
    for k, label, kind in REC_MAP:
        if w == k:
            return label, kind
    return None, None


def find_rec(text):
    for rx in (REC_NEAR, REC_BRACKET, REC_ALONE):
        m = rx.search(text)
        if m:
            lab, kind = norm_rec(m.group(1))
            if lab:
                return lab, kind
    return None, None


def first(rxs, text, conv):
    for rx in rxs:
        for m in rx.finditer(text):
            v = conv(m.group(1))
            if v:
                return v
    return None


def extract_fields(text, title=""):
    out = {}
    # tiêu đề trước (KIS "[NẮM GIỮ]", ACBS "– KHẢ QUAN", BSC "DCM 33,800 +5%")
    lab, kind = find_rec(title.upper().replace("–", "-"))
    if title:
        tp = first(TARGET_RX, title, to_price)
        if tp:
            out["target"] = tp
    m = TITLE_TP.search(title)
    if m and not out.get("target"):
        out["target"] = to_price(m.group(1))
        out["upside"] = float(m.group(2).replace(",", "."))
    if text:
        head = text[:6000]
        if not lab:
            lab, kind = find_rec(head)
        out.setdefault("target", first(TARGET_RX, head, to_price))
        out["price"] = first(PRICE_RX, head, to_price)
        if out.get("upside") is None:
            out["upside"] = first(UPSIDE_RX, head, lambda x: float(x.replace(",", ".")))
    if lab:
        out["rec"], out["rec_kind"] = lab, kind
    t, p = out.get("target"), out.get("price")
    if t and p and not (0.4 <= t / p <= 2.5):       # giá hiện tại đọc nhầm (vd KBSV ra 6.000)
        out.pop("price", None)
        if out.get("upside") is not None and not (-60 <= out["upside"] <= 150):
            out.pop("upside", None)
        p = None
    if out.get("upside") is None and t and p:
        out["upside"] = round((t / p - 1) * 100, 1)
    if out.get("upside") is not None and abs(out["upside"]) > 300:
        out["upside"] = None
    return {k: v for k, v in out.items() if v is not None}


def pdf_text(data, pages=3):
    import io
    try:
        import pdfplumber
    except ImportError:
        print("  ! thiếu thư viện pdfplumber: pip install pdfplumber")
        return ""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join((pg.extract_text() or "") for pg in pdf.pages[:pages])
    except Exception:
        return ""


def enrich(db, limit=60):
    """Đọc PDF của các báo cáo chưa xử lý. Mỗi lần chạy tối đa `limit` báo cáo."""
    import requests, urllib3
    urllib3.disable_warnings()
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    todo = [r for r in db["reports"].values() if not r.get("enriched") and r.get("kind") not in ("vm", "nganh")]
    todo.sort(key=lambda r: r.get("date") or (r.get("first_seen") or "")[:10], reverse=True)
    todo = todo[:limit]
    if not todo:
        return 0
    print(f"\nĐọc PDF lấy khuyến nghị / giá mục tiêu: {len(todo)} báo cáo...")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(lambda r: _enrich_one(sess, r), todo))
    return len(todo)


def _enrich_one(sess, r):
    if True:
        text, pdf_url = "", None
        try:
            if good_pdf(r.get("pdf_url")) and r.get("pdf_url", "").startswith("http"):
                pdf_url = r["pdf_url"]                  # link thật bắt được khi bấm nút tải (VCBS)
            elif r.get("pdf"):
                pdf_url = r["url"]
            else:                                      # trang chi tiết -> tìm link PDF
                resp = sess.get(r["url"], timeout=25, verify=False)
                resp.encoding = resp.apparent_encoding or "utf-8"
                soup = BeautifulSoup(resp.text, "lxml")
                for tag in soup.find_all(["a", "iframe", "embed", "object"]):
                    h = tag.get("href") or tag.get("src") or tag.get("data") or ""
                    if ".pdf" in h.lower() and good_pdf(h) and not tag.find_parent("footer"):
                        pdf_url = urljoin(r["url"], h.split("#")[0])
                        break
                main = soup.find("article") or soup.find("main") or soup.body or soup
                text = main.get_text("\n", strip=True)[:8000]
            if pdf_url:
                resp = sess.get(pdf_url, timeout=40, verify=False)
                if resp.ok and resp.content[:4] == b"%PDF" and len(resp.content) < 30_000_000:
                    text = pdf_text(resp.content) or text
                    r["pdf_url"] = pdf_url
        except Exception as e:
            r["enrich_err"] = str(e)[:100]
        if not r.get("date") and text:              # ngày in trên trang đầu của báo cáo
            d = find_date(text[:2500])
            try:
                age = (datetime.now() - datetime.strptime(d, "%Y-%m-%d")).days if d else None
            except Exception:
                age = None
            if age is not None and -1 <= age <= 400:
                r["date"], r["date_src"] = d, "pdf"
        fields = extract_fields(text, r["title"])
        for k in ("rec", "rec_kind", "target", "price", "upside"):
            r.pop(k, None)
        r.update(fields)
        r["enriched"] = True
        flag = ", ".join(f"{k}={v}" for k, v in fields.items() if k != "rec_kind") or "không thấy"
        print(f"  {r.get('ticker') or '---':4s} {r['source']:8s} {flag}")

# --- NGÀY ĐĂNG cho báo cáo mà trang danh sách không ghi ngày ----------------
META_DATE = re.compile(r'(?:article:published_time|datePublished|pubdate|publish[_-]?date|'
                       r'og:published_time|dc\.date)["\']?\s*(?:content|:)\s*=?\s*["\']([^"\']+)', re.I)


def _pdf_meta_date(data):
    import io
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            md = pdf.metadata or {}
        for k in ("CreationDate", "ModDate"):
            m = re.search(r"(20\d{2})(\d{2})(\d{2})", str(md.get(k, "")))
            if m:
                return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    except Exception:
        pass
    return None


def _date_of_file(sess, r):
    """Ngày đăng thật: header Last-Modified của file / ngày tạo PDF / thẻ ngày của trang chi tiết."""
    from email.utils import parsedate_to_datetime
    url = (r.get("pdf_url") or r["url"]).split("#")[0]
    if "#" in r["url"] and not r.get("pdf_url"):
        return None                                 # link giả (không có trang riêng)
    resp = sess.get(url, timeout=25, verify=False, stream=True)
    if not resp.ok:
        return None
    lm = None
    try:
        lm = parsedate_to_datetime(resp.headers.get("Last-Modified", "")).astimezone(
            __import__("datetime").timezone(timedelta(hours=7)))
    except Exception:
        pass
    ct = resp.headers.get("content-type", "").lower()
    if "pdf" in ct or url.lower().split("?")[0].endswith(".pdf"):
        if lm and (datetime.now(lm.tzinfo) - lm).days >= 1:
            resp.close()                            # có ngày tải lên -> không cần tải cả file
            return lm.strftime("%Y-%m-%d")
        data = resp.raw.read(15_000_000, decode_content=True)
        meta = _pdf_meta_date(data) if data[:4] == b"%PDF" else None
        # Last-Modified là ngày file được tải lên; nếu nó là "vừa xong" (máy chủ tự sinh) thì dùng ngày tạo PDF
        if lm and (datetime.now(lm.tzinfo) - lm).days >= 1:
            return lm.strftime("%Y-%m-%d")
        return meta or (lm.strftime("%Y-%m-%d") if lm else None)
    html = resp.text[:400000]
    m = META_DATE.search(html)
    if m:
        d = _to_date(m.group(1))
        if d:
            return d
    soup = BeautifulSoup(html, "lxml")
    t = soup.find("time")
    if t and (t.get("datetime") or t.get_text(strip=True)):
        d = _to_date(t.get("datetime") or "") or find_date(t.get_text(" ", strip=True))
        if d:
            return d
    main = soup.find("article") or soup.find("main") or soup.body
    if main:
        for el in main.find_all(class_=re.compile(r"date|time|ngay|publish|post-?meta", re.I)):
            d = find_date(el.get_text(" ", strip=True))
            if d:
                return d
    return None


def fill_dates(db, limit=800):
    """Tìm ngày đăng cho báo cáo còn thiếu ngày (song song 8 luồng)."""
    import requests, urllib3
    urllib3.disable_warnings()
    todo = [r for r in db["reports"].values()
            if not r.get("date") and int(r.get("date_tried") or 0) < 3][:limit]
    if not todo:
        return 0
    print(f"\nTìm ngày đăng cho {len(todo)} báo cáo trang không ghi ngày...")
    sess = requests.Session()
    sess.headers["User-Agent"] = UA

    def one(r):
        try:
            d = _date_of_file(sess, r)
        except Exception:
            d = None
        r["date_tried"] = int(r.get("date_tried") or 0) + 1   # thử lại ở lần chạy sau (tối đa 3 lần)
        if d:
            r["date"], r["date_src"] = d, "file"
        return d
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        got = sum(1 for d in ex.map(one, todo) if d)
    print(f"  Tìm được ngày cho {got}/{len(todo)} báo cáo")
    return got

# ----------------------------------------------------------------------------
# 4. LƯU TRỮ TÍCH LŨY
# ----------------------------------------------------------------------------
def _old_db():
    """Giải nén ra thư mục mới -> tìm kho reports.json ở các thư mục BaoCaoCTCK cũ bên cạnh."""
    cands = []
    home = Path.home()
    roots = {BASE_DIR.parent, BASE_DIR.parent.parent, home / "Desktop", home / "Downloads",
             home / "OneDrive" / "Desktop", home / "OneDrive" / "Máy tính", home / "Documents"}
    for root in roots:
        if not root.exists():
            continue
        for pat in ("*/reports.json", "*/BaoCaoCTCK/reports.json", "*/*/BaoCaoCTCK/reports.json"):
            try:
                cands += [f for f in root.glob(pat) if f.resolve() != DB_FILE.resolve()]
            except Exception:
                pass
    cands = [f for f in cands if f.stat().st_size > 1000]
    return max(cands, key=lambda f: f.stat().st_mtime) if cands else None


def load_db():
    seed = BASE_DIR / "du_lieu_mau.json"
    if not DB_FILE.exists():
        old = _old_db()
        if old:
            DB_FILE.write_text(old.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  Lấy lại kho báo cáo cũ từ: {old}")
    if not DB_FILE.exists() and seed.exists():      # lần chạy đầu: lấy dữ liệu mẫu làm nền
        DB_FILE.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
    if DB_FILE.exists():
        try:
            return json.loads(DB_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"reports": {}, "runs": []}


def clean_db(db):
    """Bỏ các bản ghi cũ không còn đạt quy tắc lọc (tổng quan, tin tức, link lạ)."""
    before = len(db["reports"])
    fixed = {}
    for u, r in db["reports"].items():               # sửa link cũ kiểu /dang-nhap?returnUrl=
        nu = clean_url(u)
        r["url"] = nu
        r["pdf"] = ".pdf" in nu.lower()
        fixed[nu] = r
    db["reports"] = fixed
    db["reports"] = {u: r for u, r in db["reports"].items()
                     if url_ok(srckey(r), u) and not JUNK_TITLE.match(r.get("title", "").strip())
                     and not NEWS_TITLE.search(r.get("title", ""))}
    for u, r in db["reports"].items():               # tiêu đề cũ dạng {"vi":"..."} (MAS)
        t = lang_text(r.get("title", ""))
        if isinstance(t, str) and t != r.get("title"):
            r["title"] = t
    for u, r in db["reports"].items():               # tiêu đề là tên file (VPBankS cũ)
        if is_file_title(r.get("title", "")):
            ftk = ticker_from_filename(r["title"])
            r["weak_title"] = True
            r["title"] = nice_file_title(r["title"])
            if ftk:
                r["ticker"] = ftk
        if r.get("ticker") in NOT_TICKERS:
            r["ticker"] = ""
        if COMPANY_LEAD.match(r.get("title", "")) and r.get("kind") not in ("vm", "nganh"):
            r["ticker"] = find_ticker(r["title"], r.get("url", "")) or r.get("ticker", "")
        up = r.get("upside")
        if up is not None and not (-60 <= up <= 200):  # giá mục tiêu / giá hiện tại đọc nhầm
            for f in ("target", "price", "upside"):
                r.pop(f, None)
    for u, r in db["reports"].items():               # tiêu đề dính ngày + lượt tải (VDSC cũ)
        m = LEAD_DATE.match(r.get("title", ""))
        if m:
            r["date"] = find_date(m.group(1)) or r.get("date", "")
            r["title"] = m.group(2).strip()
    for u, r in db["reports"].items():               # bổ sung mã còn thiếu (vd KBSV lấy từ tên file)
        if r.get("kind") in ("vm", "nganh"):
            if r["kind"] == "vm" and re.search(r"ngành|sector", r.get("title", ""), re.I) \
                    and r["source"] != "ACBS":
                r["kind"] = "nganh"                 # dữ liệu cũ gộp chung -> tách ra
            if not r.get("rec"):                    # KIS ngành: "[TRUNG LẬP]" trong tiêu đề
                lab, kind = find_rec(r.get("title", "").upper())
                if lab:
                    r["rec"], r["rec_kind"] = lab, kind
            continue
        if not r.get("ticker"):
            r["ticker"] = find_ticker(r.get("title", ""), u) or ""
        if not r.get("rec") and not r.get("target"):  # khuyến nghị/giá MT ghi sẵn trong tiêu đề
            r.update(extract_fields("", r.get("title", "")))
    # chỉ giữ báo cáo doanh nghiệp (bỏ vĩ mô / chiến lược / ngành ... đã lưu trước đây)
    db["reports"] = {u: r for u, r in db["reports"].items()
                     if r.get("kind") in ("vm", "nganh") or is_company_report(r["source"], r)}
    dedupe_db(db)
    removed = before - len(db["reports"])
    if removed:
        print(f"  Đã dọn {removed} mục không phải báo cáo / trùng khỏi kho cũ")
    return removed


DATE_RANK = {"json": 0, "page": 1, "file": 2, "pdf": 3, "title": 4, "url": 5, "": 8}


def dedupe_db(db):
    """Gộp báo cáo trùng: cùng nguồn + cùng tiêu đề nhưng khác link
    (vd VCBS: 1 bản lấy từ danh sách, 1 bản từ dữ liệu ngầm có PDF)."""
    groups = {}
    for u, r in db["reports"].items():
        k = (srckey(r), _norm(r.get("title", "")))
        groups.setdefault(k, []).append((u, r))
    removed = 0
    for k, items in groups.items():
        if len(items) < 2 or not k[1]:
            continue
        # ưu tiên: link thật (không phải #neo), có PDF, đã đọc PDF
        items.sort(key=lambda x: (bool(x[1].get("weak_title")), "#" in x[0],
                                  not x[1].get("pdf"), not x[1].get("enriched")))
        keep_u, keep = items[0]
        for u, r in items[1:]:
            d1, d2 = keep.get("date") or "", r.get("date") or ""
            if d1 and d2:                            # cùng tên nhưng khác kỳ -> không gộp
                try:
                    if abs((datetime.fromisoformat(d1) - datetime.fromisoformat(d2)).days) > 3:
                        continue
                except ValueError:
                    pass
            for f in ("rec", "rec_kind", "target", "price", "upside", "ticker"):
                if not keep.get(f) and r.get(f):
                    keep[f] = r[f]
            if not keep.get("pdf") and not keep.get("pdf_url") and \
                    (r.get("pdf") or u.lower().split("?")[0].endswith(".pdf")):
                keep["pdf_url"] = r.get("pdf_url") or u
            keep["first_seen"] = min(keep.get("first_seen", ""), r.get("first_seen", "")) or keep.get("first_seen")
            del db["reports"][u]
            removed += 1
        best = keep
        for _, r in items:                           # ngày đăng: dữ liệu ngầm > trang > tiêu đề
            if r.get("date") and DATE_RANK.get(r.get("date_src", ""), 9) < \
                    DATE_RANK.get(best.get("date_src", ""), 9):
                best = r
        if best.get("date"):
            keep["date"], keep["date_src"] = best["date"], best.get("date_src", "")
    # Lượt 2: bản ghi tiêu đề là tên file -> gộp vào báo cáo thật cùng nguồn + mã + ngày (±2)
    def _dd(a, b_):
        try:
            return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b_)).days)
        except ValueError:
            return 99
    strong = {}
    for u, r in db["reports"].items():
        if not r.get("weak_title") and r.get("ticker"):
            strong.setdefault((srckey(r), r["ticker"]), []).append(r)
    for u in [u for u, r in db["reports"].items() if r.get("weak_title")]:
        r = db["reports"][u]
        cands = [x for x in strong.get((srckey(r), r.get("ticker")), [])
                 if r.get("date") and x.get("date") and _dd(r["date"], x["date"]) <= 2]
        if not cands:
            continue
        keep = min(cands, key=lambda x: _dd(r["date"], x["date"]))
        if r.get("pdf") or r["url"].lower().split("?")[0].endswith(".pdf"):
            keep.setdefault("pdf_url", r["url"])
        for f in ("rec", "rec_kind", "target", "price", "upside"):
            if not keep.get(f) and r.get(f):
                keep[f] = r[f]
        if DATE_RANK.get(r.get("date_src", ""), 9) < DATE_RANK.get(keep.get("date_src", ""), 9):
            keep["date"], keep["date_src"] = r["date"], r.get("date_src", "")
        del db["reports"][u]
        removed += 1
    if removed:
        print(f"  Đã gộp {removed} báo cáo trùng")
    return removed


def merge(db, records, now):
    new = 0
    for r in records:
        key = r["url"]
        if key in db["reports"]:
            old = db["reports"][key]
            r["first_seen"] = old.get("first_seen", now)
            r["ticker"] = r["ticker"] or ("" if r.get("kind") else old.get("ticker", ""))
            if not r.get("date") or DATE_RANK.get(old.get("date_src", ""), 9) < \
                    DATE_RANK.get(r.get("date_src", ""), 9):
                r["date"] = old.get("date", "") or r.get("date", "")
                r["date_src"] = old.get("date_src", r.get("date_src", ""))
            for k in ("enriched", "rec", "rec_kind", "target", "price", "upside", "pdf_url",
                      "open_url", "click_tries", "click_v2", "guess_url", "pdf_tried", "date_tried"):
                if k in old:
                    r[k] = old[k]
        else:
            r["first_seen"] = now
            new += 1
        db["reports"][key] = r
    dedupe_db(db)
    return new

# ----------------------------------------------------------------------------
# 5. TẠO TRANG HTML
# ----------------------------------------------------------------------------
HTML_TEMPLATE = r"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Báo cáo phân tích CTCK</title>
<style>
:root{--bg:#0f0f12;--panel:#17171c;--row:#131317;--rowalt:#1b1b21;--line:#26262e;
--text:#e8e8ec;--muted:#8b8b96;--acc:#8b5cf6;--chip:#25252d;--new:#22c55e}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.45 "Segoe UI",Inter,Roboto,Arial,sans-serif}
.wrap{max-width:1500px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--muted);font-size:12.5px;margin-bottom:14px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}
.chip{background:var(--chip);border:1px solid var(--line);color:var(--text);
border-radius:999px;padding:5px 12px;cursor:pointer;font-size:13px;white-space:nowrap}
.chip.on{background:#fff;color:#111;border-color:#fff;font-weight:600}
.chip .err{color:#f87171}
.chip.vm{background:#3a2f0e;border-color:#a16207;color:#fcd34d;font-weight:600}
.chip.vm.on{background:#facc15;color:#1a1400;border-color:#facc15}
.chip.ng{background:#0f2a3a;border-color:#0369a1;color:#7dd3fc;font-weight:600}
.chip.ng.on{background:#38bdf8;color:#04121a;border-color:#38bdf8}
input[type=search],select{background:var(--panel);border:1px solid var(--line);
color:var(--text);border-radius:8px;padding:7px 10px;font-size:13px}
input[type=search]{min-width:260px;flex:1;max-width:420px}
label.ck{color:var(--muted);font-size:13px;display:flex;gap:6px;align-items:center}
.tbl{background:var(--panel);border-radius:10px;overflow:auto;border:1px solid var(--line)}
table{border-collapse:collapse;width:100%;min-width:1080px}
th{position:sticky;top:0;background:var(--panel);text-align:left;font-weight:600;
padding:14px 12px;border-bottom:1px solid var(--line);font-size:13.5px;cursor:pointer;
user-select:none;white-space:nowrap}
td{padding:13px 12px;border-bottom:1px solid #1f1f26;vertical-align:middle}
tr:nth-child(even) td{background:var(--rowalt)}
tr:hover td{background:#23232b}
.tk{font-weight:700;white-space:nowrap;cursor:pointer}
.tk .dot{display:inline-block;width:9px;height:9px;border-radius:50%;
background:var(--acc);margin-left:6px;vertical-align:middle}
.tk.none{color:var(--muted);font-weight:400}
td.title a{color:var(--text);text-decoration:none;font-weight:500}
td.title a:hover{text-decoration:underline;color:#c4b5fd}
.new{background:var(--new);color:#04130a;font-size:10.5px;font-weight:700;
border-radius:4px;padding:1px 5px;margin-left:6px;vertical-align:middle}
.src{color:var(--muted);white-space:nowrap}
.date{white-space:nowrap;text-align:center}
.date.est{color:var(--muted);font-style:italic}
.doc{text-align:center}
.doc a{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;
border:1px solid #3a3a44;border-radius:6px;color:#bbb;text-decoration:none;font-size:10px;
font-weight:700}
.doc a:hover{border-color:var(--acc);color:#fff}
.rec{display:inline-block;padding:3px 10px;border-radius:6px;font-weight:600;font-size:12.5px;white-space:nowrap}
.rec.buy{background:rgba(34,197,94,.15);color:#4ade80}
.rec.hold{background:rgba(234,179,8,.15);color:#facc15}
.rec.sell{background:rgba(239,68,68,.15);color:#f87171}
.rec.na{background:#26262e;color:#a1a1aa}
.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.up{color:#4ade80}.dn{color:#f87171}.mut{color:#55555f}
.empty{padding:40px;text-align:center;color:var(--muted)}
.foot{color:var(--muted);font-size:12px;margin-top:10px}
.more{display:block;width:100%;margin:10px 0 0;padding:12px;background:var(--panel);color:var(--text);
      border:1px solid var(--line);border-radius:10px;font-size:14px;cursor:pointer}
.more[hidden]{display:none}
.doc a.lst,.doc a.rd{margin-left:6px}
.scanbar{display:flex;align-items:center;gap:10px;margin:0 0 12px;flex-wrap:wrap}
.scanbar[hidden]{display:none}
#scanbtn{background:#8b5cf6;color:#fff;border:none;border-radius:8px;padding:9px 16px;font-size:14px;
         font-weight:600;cursor:pointer}
#scanbtn:disabled{background:#4c3a86;cursor:wait}
#rfbtn{background:#16a34a;color:#fff;border:none;border-radius:8px;padding:9px 16px;font-size:14px;
       font-weight:600;cursor:pointer}
#rfbtn:disabled{background:#14532d;cursor:wait}
#rfmsg{color:var(--muted);font-size:12.5px;flex:1;min-width:0}
#scanmsg{color:var(--muted);font-size:12.5px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* ---------- ĐIỆN THOẠI: mỗi báo cáo là 1 thẻ ---------- */
@media (max-width: 760px){
  .wrap{padding:12px 10px 30px}
  h1{font-size:17px}
  .sub{font-size:11.5px}
  #chips{flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch;padding-bottom:4px;
         scrollbar-width:none;margin-left:-10px;margin-right:-10px;padding-left:10px;padding-right:10px}
  #chips::-webkit-scrollbar{display:none}
  .chip{flex:0 0 auto;font-size:13px;padding:7px 12px}
  input[type=search]{min-width:0;max-width:none;width:100%;flex:1 1 100%;font-size:15px;padding:10px}
  select{flex:1 1 auto;font-size:14px;padding:9px}
  label.ck{font-size:12.5px}
  .tbl{background:transparent;border:none;overflow:visible}
  table{min-width:0}
  table,tbody{display:block}
  thead{display:none}
  tr{display:grid;grid-template-columns:auto 1fr auto;gap:6px 10px;align-items:center;
     background:var(--panel);border:1px solid var(--line);border-radius:10px;
     padding:11px 12px;margin-bottom:8px}
  tr:nth-child(even) td,tr:hover td{background:transparent}
  td{display:block;padding:0;border:none;background:transparent!important}
  td.tk{grid-row:1;grid-column:1;font-size:15px}
  td.src{grid-row:1;grid-column:2;font-size:12.5px}
  td.date{grid-row:1;grid-column:3;text-align:right;font-size:12.5px;color:var(--muted)}
  td.title{grid-row:2;grid-column:1/-1;font-size:14.5px;line-height:1.4}
  td.rc{grid-row:3;grid-column:1}
  td.tg,td.ups{grid-row:3;text-align:left;font-size:13px}
  td.tg{grid-column:2}
  td.ups{grid-column:3;text-align:right}
  td.tg::before,td.ups::before{content:attr(data-l) " ";color:var(--muted);font-size:11.5px}
  td.rc:has(.mut),td.tg.na,td.ups.na{display:none}
  td.doc{grid-row:4;grid-column:1/-1;text-align:left}
  td.doc{display:flex;gap:8px}
  td.doc a{width:auto;height:auto;padding:7px 14px;font-size:0}
  td.doc a::after{content:"Mở báo cáo";font-size:12.5px;font-weight:600}
  td.doc a.lst{margin-left:0}
  td.doc a.lst::after{content:"Trang nguồn"}
  td.doc a.rd{border-color:#8b5cf6;color:#c4b5fd}
  td.doc a.rd::after{content:"Đọc chữ to"}
  .empty{display:block}
  .foot{font-size:11.5px}
}
</style></head><body><div class="wrap">
<h1>Báo cáo phân tích doanh nghiệp – các CTCK</h1>
<div class="sub" id="sub"></div>
<div class="scanbar" id="rfbar" hidden><button id="rfbtn" type="button">↻ Làm mới</button><span id="rfmsg"></span></div>
<div id="scanbar" class="scanbar" hidden><button id="scanbtn" type="button">⟳ Quét ngay</button><span id="scanmsg"></span></div>
<div class="bar" id="chips"></div>
<div class="bar">
  <input type="search" id="q" placeholder="Tìm mã hoặc tiêu đề… (vd: FPT, ngân hàng)">
  <select id="days">
    <option value="0" selected>Tất cả thời gian</option><option value="1">Hôm nay</option>
    <option value="3">3 ngày gần nhất</option><option value="7">7 ngày gần nhất</option>
    <option value="30">30 ngày gần nhất</option></select>
  <label class="ck"><input type="checkbox" id="onlyTk" checked> Chỉ hiện báo cáo có mã CP</label>
  <label class="ck"><input type="checkbox" id="onlyNew"> Chỉ báo cáo mới</label>
</div>
<div class="tbl"><table>
<thead><tr>
 <th data-k="ticker" style="width:90px">Mã</th>
 <th data-k="title">Tiêu đề</th>
 <th data-k="source" style="width:100px">Nguồn</th>
 <th data-k="rec" style="width:130px">Khuyến nghị</th>
 <th data-k="target" style="width:120px;text-align:right">Giá mục tiêu</th>
 <th data-k="upside" style="width:150px;text-align:right">Upside so với<br>giá lúc ra BC</th>
 <th data-k="date" style="width:150px;text-align:center">Ngày khuyến nghị ▼</th>
 <th style="width:80px;text-align:center">Tài liệu</th>
</tr></thead><tbody id="tb"></tbody></table></div>
<button id="more" class="more" type="button" hidden></button>
<div class="foot" id="foot"></div>
</div>
<script>
const DATA = __DATA__;
const META = __META__;
let src = "ALL", sortK = "date", sortDir = -1;
const $ = s => document.querySelector(s);
const esc = s => (s||"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmt = d => d ? d.split("-").reverse().join("/") : "";
const NEW_MS = 24*3600*1000, now = Date.now();
DATA.forEach(r => {
  r.isNew = (now - new Date(r.first_seen).getTime()) < NEW_MS;
  r.sortDate = r.date || (r.first_seen||"").slice(0,10);   // chỉ để xếp thứ tự; ô ngày vẫn hiện "—" khi chưa có ngày đăng
});
$("#sub").textContent = `Cập nhật lúc ${META.updated} · ${DATA.length} báo cáo trong kho · `
  + `${META.new_count} báo cáo mới ở lần quét này`;

const isMacro = r => r.kind==="vm" || r.kind==="nganh";
function rows(){
  const q = $("#q").value.trim().toLowerCase();
  const days = +$("#days").value, onlyTk = $("#onlyTk").checked, onlyNew = $("#onlyNew").checked;
  const cutoff = days ? new Date(now - (days-1)*864e5).toISOString().slice(0,10) : "";
  const vm = src==="VM" || src==="NG";
  const want = src==="VM" ? "vm" : "nganh";
  return DATA.filter(r =>
    (vm ? r.kind===want : (!isMacro(r) && (src==="ALL" || r.source===src))) &&
    (vm || !onlyTk || r.ticker) && (!onlyNew || r.isNew) &&
    (!cutoff || r.sortDate >= cutoff) &&
    (!q || r.ticker.toLowerCase()===q || (r.ticker+" "+r.title).toLowerCase().includes(q)));
}
function chips(){
  const base = DATA.filter(r => !isMacro(r) && (!$("#onlyTk").checked || r.ticker));
  const vmN = DATA.filter(r => r.kind==="vm").length;
  const ngN = DATA.filter(r => r.kind==="nganh").length;
  const cnt = {}; base.forEach(r => cnt[r.source]=(cnt[r.source]||0)+1);
  let h = `<span class="chip ${src==="ALL"?"on":""}" data-s="ALL">Tất cả (${base.length})</span>`;
  META.sources.forEach(s => {
    const e = META.errors[s] ? ` <span class="err" title="${esc(META.errors[s])}">⚠</span>` : "";
    h += `<span class="chip ${src===s?"on":""}" data-s="${esc(s)}">${esc(s)} (${cnt[s]||0})${e}</span>`;
  });
  const warn = k => META.errors[k] ? ` <span class="err" title="${esc(META.errors[k])}">⚠</span>` : "";
  h += `<span class="chip vm ${src==="VM"?"on":""}" data-s="VM">Vĩ mô (${vmN})${warn("__VM__")}</span>`;
  h += `<span class="chip ng ${src==="NG"?"on":""}" data-s="NG">Ngành (${ngN})${warn("__NG__")}</span>`;
  $("#chips").innerHTML = h;
  document.querySelectorAll(".chip").forEach(c => c.onclick = () => { src = c.dataset.s; render(); });
}
let SERVED = false;   // true khi mở qua máy chủ ở nhà (7_XEM_TREN_DIEN_THOAI) -> có Quét ngay / Đọc chữ to
let shown = 100;
function render(keep){
  if (!keep) shown = 100;
  chips();
  const list = rows().sort((a,b) => {
    const k = sortK==="date" ? "sortDate" : sortK;
    if (k==="target" || k==="upside") {
      const x = a[k] ?? -1e12, y = b[k] ?? -1e12;
      return (x - y) * sortDir;
    }
    return (a[k]||"").localeCompare(b[k]||"") * sortDir
        || (b.first_seen||"").localeCompare(a.first_seen||"");
  });
  const total = list.length;
  $("#tb").innerHTML = list.length ? list.slice(0, shown).map(r => `<tr>
    <td class="tk ${r.ticker?"":"none"}" data-t="${esc(r.ticker)}">${r.ticker?esc(r.ticker)+'<span class="dot"></span>':"—"}</td>
    <td class="title"><a href="${esc(r.link||r.url)}" target="_blank" rel="noopener" title="${r.via_list?"Mở trang danh sách báo cáo doanh nghiệp của "+esc(r.source)+" (file gốc cần đăng nhập)":""}">${esc(r.title)}</a>${r.isNew?'<span class="new">MỚI</span>':""}</td>
    <td class="src">${esc(r.source)}</td>
    <td class="rc">${r.rec?`<span class="rec ${r.rec_kind||"na"}">${esc(r.rec)}</span>`:'<span class="mut">—</span>'}</td>
    <td class="num tg ${r.target?"":"na"}" data-l="Giá MT">${r.target?r.target.toLocaleString("vi-VN"):'<span class="mut">—</span>'}</td>
    <td class="num ups ${r.upside!=null?"":"na"}" data-l="Upside">${r.upside!=null?`<span class="${r.upside>=0?"up":"dn"}">${r.upside>0?"+":""}${r.upside.toLocaleString("vi-VN")}%</span>`:'<span class="mut">—</span>'}</td>
    <td class="date ${r.date?"":"est"}" title="${!r.date?"Chưa tìm được ngày đăng báo cáo":({json:"Ngày đăng (dữ liệu của trang)",page:"Ngày đăng in trên trang",file:"Ngày tải file lên",pdf:"Ngày in trên báo cáo",title:"Ngày ghi trong tiêu đề",url:"Ngày trong đường link"}[r.date_src]||"")}">${r.date?fmt(r.date):'<span class="mut">—</span>'}</td>
    <td class="doc"><a href="${esc(r.doc||r.link||r.url)}" target="_blank" rel="noopener" title="${r.via_list?"Mở trang danh sách của "+esc(r.source):"Mở báo cáo"}">${r.via_list?"≡":((r.pdf||r.pdf_url)?"PDF":"↗")}</a>${(SERVED && !r.via_list && !r.noread && (r.pdf || r.pdf_url)) ? `<a class="rd" href="/doc?u=${encodeURIComponent(r.doc)}&t=${encodeURIComponent(r.title)}" target="_blank" rel="noopener" title="Đọc dạng chữ to">Aa</a>` : ""}${(!r.via_list && r.list && r.list!==(r.doc||r.link))?`<a class="lst" href="${esc(r.list)}" target="_blank" rel="noopener" title="Mở trang danh sách báo cáo của ${esc(r.source)}">≡</a>`:""}</td>
  </tr>`).join("") : `<tr><td colspan="8" class="empty">Không có báo cáo phù hợp bộ lọc</td></tr>`;
  $("#more").hidden = total <= shown;
  $("#more").textContent = `Xem thêm (${Math.min(100, total - shown)} / còn ${total - shown} báo cáo)`;
  $("#foot").textContent = `Đang hiển thị ${Math.min(shown, total)}/${total} báo cáo. Bấm vào mã để lọc theo mã, bấm tiêu đề cột để sắp xếp.`;
  document.querySelectorAll("td.tk[data-t]").forEach(td => td.onclick = () => {
    if (td.dataset.t){ $("#q").value = td.dataset.t; $("#days").value="0"; render(); } });
}
document.querySelectorAll("th[data-k]").forEach(th => th.onclick = () => {
  const k = th.dataset.k; sortDir = (sortK===k) ? -sortDir : (["date","target","upside"].includes(k)?-1:1); sortK = k;
  document.querySelectorAll("th[data-k]").forEach(x => x.textContent = x.textContent.replace(/ [▲▼]$/,""));
  th.textContent += sortDir<0 ? " ▼" : " ▲"; render();
});
let qTimer = null;   // gõ tìm kiếm: chờ 250ms rồi mới lọc, tránh giật
$("#q").addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(() => render(), 250); });
["#days","#onlyTk","#onlyNew"].forEach(s => $(s).addEventListener("input", () => render()));
$("#more").onclick = () => { shown += 100; render(true); };
render();
// ---- Nút "Làm mới": tải bản mới nhất, bỏ qua bản cũ lưu trong bộ nhớ đệm của điện thoại / GitHub ----
(function(){
  if (!/^https?:/.test(location.protocol)) return;
  const bar=$("#rfbar"), btn=$("#rfbtn"), msg=$("#rfmsg");
  bar.hidden=false;
  const fresh = () => location.replace(location.pathname + "?v=" + Date.now());
  function check(manual){
    if (manual){ btn.disabled=true; msg.textContent="Đang kiểm tra bản mới…"; }
    fetch(location.pathname + "?t=" + Date.now(), {cache:"no-store"}).then(r => r.text()).then(t => {
      const m = [...t.matchAll(/"updated":\s*"([^"]+)"/g)].pop();
      if (!m) throw 0;
      if (m[1] !== META.updated){ msg.textContent = "Có bản mới (cập nhật lúc " + m[1] + ") – đang tải…"; fresh(); }
      else { btn.disabled=false; if (manual) msg.textContent = "Đã là bản mới nhất – cập nhật lúc " + META.updated; }
    }).catch(() => { if (manual) fresh(); else btn.disabled=false; });
  }
  btn.onclick = () => check(true);
  check(false);                                   // mở trang là tự kiểm tra
  document.addEventListener("visibilitychange", () => { if (!document.hidden) check(false); });
})();
// ---- Nút "Quét ngay": chỉ hiện khi mở qua máy chủ (7_XEM_TREN_DIEN_THOAI.bat) ----
(function(){
  if (!/^https?:/.test(location.protocol)) return;
  const bar=$("#scanbar"), btn=$("#scanbtn"), msg=$("#scanmsg");
  let timer=null, wasRunning=false;
  function show(st){
    if (st.running){
      wasRunning=true; btn.disabled=true; btn.textContent="Đang quét…";
      const last=(st.lines||[]).filter(l=>/✓|✗|Kết quả|báo cáo mới|Tìm|Đọc PDF|tải/.test(l)).slice(-1)[0]||"Bắt đầu lúc "+st.started;
      msg.textContent=last.trim();
      if(!timer) timer=setInterval(poll,2500);
    } else {
      btn.disabled=false; btn.textContent="⟳ Quét ngay";
      if (timer){clearInterval(timer); timer=null;}
      if (wasRunning){ msg.textContent="Xong lúc "+st.finished+" – đang tải lại…"; setTimeout(()=>location.reload(),800); }
      else msg.textContent=st.finished?("Lần quét gần nhất xong lúc "+st.finished):"";
    }
  }
  function poll(){ fetch("/api/trang-thai",{cache:"no-store"}).then(r=>r.json()).then(show).catch(()=>{}); }
  fetch("/api/trang-thai",{cache:"no-store"}).then(r=>r.json()).then(st=>{
    if (typeof st.running !== "boolean") return;
    SERVED=true; bar.hidden=false; show(st); render(true);
  }).catch(()=>{});
  btn.onclick=()=>{ btn.disabled=true; msg.textContent="Đang gửi lệnh quét…";
    fetch("/api/quet",{method:"POST"}).then(r=>r.json()).then(show).catch(()=>{msg.textContent="Không gửi được lệnh – máy tính đã bật 7_XEM_TREN_DIEN_THOAI chưa?"; btn.disabled=false;}); };
})();
</script></body></html>"""


# Nguồn mà link file/báo cáo bắt đăng nhập -> bấm vào sẽ mở trang danh sách báo cáo DN của nguồn
# Nguồn mà báo cáo không có PDF thì không có trang riêng -> mở trang danh sách của nguồn
LIST_IF_NO_PDF = {"VCBS"}
# trang danh sách mở khi bấm (nếu khác link quét)
LIST_PAGE = {
    "VPBankS": "https://www.vpbanks.com.vn/trung-tam-phan-tich/phan-tich-co-phieu-doanh-nghiep",
    "VCBS": "https://www.vcbs.com.vn/trung-tam-phan-tich/bao-cao-chi-tiet?code=BCDN",
    "Agriseco": "https://agriseco.com.vn/Report/ReportsInCategory/1/vi-VN",
}
# Nguồn mà trang chi tiết / file cần đăng nhập hoặc hay lỗi -> tiêu đề luôn mở trang danh sách của nguồn
DETAIL_FIRST = set()                   # nguồn muốn mở trang chi tiết thay vì file PDF
LIST_ALWAYS = {"SSI", "VDSC"}          # file cần đăng nhập -> mở trang danh sách báo cáo doanh nghiệp
NO_READER = {"VCBS", "Agriseco"}       # không hiện nút "Đọc chữ to"
LINK_TO_LIST = {
    "SSI":  "https://www.ssi.com.vn/en/organization-customer/company-report",
    "VDSC": "https://www.vdsc.com.vn/trung-tam-phan-tich/doanh-nghiep",
}


# ---------------- ĐẨY TRANG LÊN MẠNG (GitHub Pages) để xem trên điện thoại mọi lúc ----------------
ONLINE_CFG = BASE_DIR / "dien_thoai_online.txt"


ONLINE_TEMPLATE = (
    "# ĐƯA TRANG LÊN MẠNG ĐỂ XEM TRÊN ĐIỆN THOẠI MỌI LÚC (máy tính tắt vẫn xem được)\n"
    "# Làm 1 lần theo HUONG_DAN.txt (mục XEM Ở MỌI NƠI), rồi điền 2 dòng dưới đây:\n"
    "#   REPO  = <tên tài khoản GitHub>/<tên kho>      ví dụ:  tungnguyen/baocao-ctck-8k2p\n"
    "#   TOKEN = mã token GitHub (bắt đầu bằng github_pat_ hoặc ghp_)\n"
    "REPO = tentaikhoan/baocao-ctck\n"
    "TOKEN = github_pat_xxxx\n")


def _find_online_cfg():
    """Tìm file cấu hình, kể cả khi Windows đổi tên: dien_thoai_online.txt.txt, dien_thoai_online (1).txt"""
    if ONLINE_CFG.exists():
        return ONLINE_CFG
    cands = sorted(BASE_DIR.glob("dien_thoai_online*.txt*"), key=lambda f: f.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def _read_online_cfg():
    global ONLINE_CFG
    found = _find_online_cfg()
    if found:
        ONLINE_CFG = found
    if not ONLINE_CFG.exists():
        try:
            ONLINE_CFG.write_text(ONLINE_TEMPLATE, encoding="utf-8")   # tạo sẵn file mẫu để điền
        except Exception:
            pass
        print(f"=> Chưa đưa lên mạng: chưa có cấu hình. Đã tạo file mẫu {ONLINE_CFG} – mở ra điền REPO / TOKEN.")
        return None
    cfg = {}
    for line in ONLINE_CFG.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            cfg[k.strip().upper()] = v.strip()
    if cfg.get("TOKEN") and cfg.get("REPO") and "/" in cfg["REPO"] and "xxxx" not in cfg["TOKEN"]:
        return cfg
    print(f"=> Chưa đưa lên mạng: file {ONLINE_CFG.name} chưa điền REPO / TOKEN.")
    return None


def _online_status(msg):
    try:
        (BASE_DIR / "trang_thai_dua_len_mang.txt").write_text(msg + "\n", encoding="utf-8")
    except Exception:
        pass


def _publish_from_github_actions():
    """Chạy trên máy chủ GitHub (GitHub Actions): ghi index.html + kho vào chính kho này rồi đẩy lên.
    Mỗi lần đẩy THAY THẾ toàn bộ lịch sử bằng đúng 1 bản mới nhất -> kho không phình theo thời gian."""
    import subprocess
    import shutil

    def git(*a):
        return subprocess.run(["git", *a], cwd=BASE_DIR, capture_output=True, text=True)
    shutil.copyfile(HTML_FILE, BASE_DIR / "index.html")
    # chẩn đoán: nguồn nào ra 0 báo cáo -> lưu ảnh chụp + trang gốc vào chan_doan/ để xem từ xa
    cd = BASE_DIR / "chan_doan"
    shutil.rmtree(cd, ignore_errors=True)
    dbg = BASE_DIR / "debug"
    for f in dbg.glob("*_lay_duoc.txt") if dbg.exists() else []:
        try:
            head = f.read_text(encoding="utf-8").split("\n", 1)[0]
        except Exception:
            continue
        if re.search(r":\s*0 báo cáo", head):
            name = f.name[: -len("_lay_duoc.txt")]
            cd.mkdir(exist_ok=True)
            for src in (dbg / f"{name}.png", dbg / f"{name}.html", f):
                if src.exists() and src.stat().st_size < 3_000_000:
                    shutil.copyfile(src, cd / src.name)
    git("add", "-A", "chan_doan")
    git("add", "index.html", "reports.json")
    tree = git("write-tree").stdout.strip()
    if tree and tree == git("rev-parse", "HEAD^{tree}").stdout.strip():
        print("=> Trang không thay đổi, không cần đưa lên.")
        return None
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    msg = f"Tự quét {datetime.now():%H:%M %d/%m/%Y}"
    commit = git("commit-tree", tree, "-m", msg).stdout.strip()   # bản mới, không kèm lịch sử cũ
    r = None
    for attempt in range(1, 5):
        r = git("push", "--force", "origin", f"{commit}:refs/heads/{branch}")
        if r.returncode == 0:
            git("reset", "--soft", commit)
            print(f"=> Đã đưa lên mạng lúc {datetime.now():%H:%M %d/%m/%Y} (GitHub tự quét, kho gọn 1 bản)")
            return True
        time.sleep(5 * attempt)
    print("  ! KHÔNG đưa lên được:", ((r.stderr if r else "") or "")[-300:])
    return None


def publish_online():
    """Đưa bao_cao.html lên GitHub (index.html) -> xem tại https://<tài khoản>.github.io/<kho>/"""
    import os
    if os.environ.get("GITHUB_ACTIONS") == "true":        # đang chạy trên máy chủ GitHub
        return _publish_from_github_actions()
    cfg = _read_online_cfg()
    if not cfg:
        return None
    print(f"=> Đang đưa trang lên mạng ({cfg['REPO']})...")
    import base64
    import requests
    repo, token = cfg["REPO"].strip("/"), cfg["TOKEN"]
    api = f"https://api.github.com/repos/{repo}/contents/index.html"
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
         "User-Agent": "BaoCaoCTCK"}
    owner, name = repo.split("/", 1)
    site = f"https://{owner.lower()}.github.io/{name}/"
    content = base64.b64encode(HTML_FILE.read_bytes()).decode()
    last = ""
    for attempt in range(1, 5):                 # thử lại tối đa 4 lần (mạng chập chờn / xung đột phiên bản)
        try:
            sha = None
            r = requests.get(api, headers={**h, "Accept": "application/vnd.github.object+json"},   # chạy cả khi file > 1MB
                             params={"ref": cfg.get("BRANCH", "main")}, timeout=30)
            if r.status_code == 200:
                sha = r.json().get("sha")
            elif r.status_code in (401, 403):
                last = f"GitHub từ chối mã TOKEN ({r.status_code}) – mã hết hạn hoặc thiếu quyền Contents: Read and write"
                break
            body = {"message": f"Cập nhật {datetime.now():%d/%m/%Y %H:%M}", "content": content,
                    "branch": cfg.get("BRANCH", "main")}
            if sha:
                body["sha"] = sha
            r = requests.put(api, headers=h, json=body, timeout=90)
            if r.status_code in (200, 201):
                msg = f"Đã đưa lên mạng lúc {datetime.now():%H:%M %d/%m/%Y}: {site}"
                print(f"=> {msg}  (1-2 phút sau mở link, bấm ⟳ Làm mới trên trang)")
                _online_status(msg)
                return site
            last = f"GitHub trả lời {r.status_code}: {r.text[:150]}"
            if r.status_code in (401, 403, 404):
                break                                   # sai mã / sai tên kho: thử lại cũng vô ích
        except Exception as e:
            last = str(e)[:150]
        print(f"  ! Lần {attempt} chưa đưa lên được ({last[:90]}) – thử lại...")
        time.sleep(5 * attempt)
    print(f"  ! KHÔNG đưa lên mạng được: {last}")
    print("    -> Lần quét sau sẽ tự đưa lại.")
    _online_status("LỖI lúc " + f"{datetime.now():%H:%M %d/%m/%Y}: " + last)
    return None


UPSIDE_TITLE = re.compile(r"upside\s*[:+]?\s*(-?\d+(?:[.,]\d+)?)\s*%", re.I)


def title_fields(r):
    """Khuyến nghị / giá mục tiêu / upside ghi ngay trong tiêu đề – hiện luôn, không chờ đọc PDF."""
    try:
        f = extract_fields("", r.get("title", ""))
    except Exception:
        f = {}
    if "upside" not in f:
        m = UPSIDE_TITLE.search(r.get("title", ""))
        if m:
            f["upside"] = float(m.group(1).replace(",", "."))
    return f


def build_html(db, new_count, errors):
    data = sorted(db["reports"].values(),
                  key=lambda r: (r.get("date") or r["first_seen"][:10]), reverse=True)
    list_url = dict(LIST_PAGE)
    for n, u in SOURCES:
        list_url.setdefault(n, u)
    for r in data:
        if r.get("kind") not in ("vm", "nganh"):
            for k, v in title_fields(r).items():   # chưa đọc PDF / PDF không ghi -> lấy từ tiêu đề
                if r.get(k) in (None, ""):
                    r[k] = v
        own = list_url.get(srckey(r), r["url"])
        no_page = ("#" in r["url"] or r["source"] in LIST_ALWAYS
                   or (r["source"] in LIST_IF_NO_PDF and not r.get("pdf")))
        r["link"] = own if no_page else r["url"]
        r["list"] = own                             # trang danh sách của nguồn (luôn mở được)
        if r["source"] in CLICK_SOURCES:            # VCBS, Agriseco: link bắt được khi bấm thử, không thì trang danh sách
            r["link"] = r.get("open_url") or own
        if r.get("pdf_url") and r["source"] in PDF_FROM_DETAIL and r["source"] not in DETAIL_FIRST:
            r["link"] = r["pdf_url"]                # mở thẳng file, không qua trang chi tiết chậm
        if r["source"] == "VPBankS":                # mở thẳng file PDF của đúng báo cáo, không có thì trang chi tiết
            r["link"] = r.get("pdf_url") or r.get("detail") or r["url"]
        if r["source"] in LIST_ALWAYS or r["source"] in CLICK_SOURCES:
            r["doc"] = r["link"]
        elif r.get("pdf_url"):
            r["doc"] = r["pdf_url"]
        elif r.get("pdf"):
            r["doc"] = r["url"]
        else:
            r["doc"] = r["link"]
        r["via_list"] = (r["source"] in LIST_ALWAYS) or (r["link"] == own) or (r["link"] != r["url"] and not r.get("pdf")
                                                         and not r.get("pdf_url"))
        if r["source"] in CLICK_SOURCES or r["source"] in ("VPBankS",):
            r["via_list"] = r["link"] == own         # chỉ là ≡ khi chưa lấy được link riêng
        r["noread"] = r["source"] in NO_READER
    sources = []
    for n, _ in SOURCES:
        if n not in sources and not is_vm(n):
            sources.append(n)
    for pre, key in ((VM_PREFIX, "__VM__"), (NG_PREFIX, "__NG__")):
        e = "; ".join(f"{k[3:]}: {v}" for k, v in errors.items() if k.startswith(pre))
        if e:
            errors[key] = e[:400]
    errors = {k: v for k, v in errors.items() if not is_vm(k)}
    meta = {
        "updated": datetime.now().strftime("%H:%M %d/%m/%Y"),
        "new_count": new_count,
        "sources": sources,
        "errors": errors,
    }
    page = (HTML_TEMPLATE
            .replace("__DATA__", json.dumps(data, ensure_ascii=False))
            .replace("__META__", json.dumps(meta, ensure_ascii=False)))
    HTML_FILE.write_text(page, encoding="utf-8")

# ----------------------------------------------------------------------------
# 6. CHẠY
# ----------------------------------------------------------------------------
VN_CHARS = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)
EN_WORDS = re.compile(r"\b(update|industry|report|outlook|sector|strategy|quick|weekly|monthly|daily|market|"
                      r"review|preview|earnings|results?|securities|services|banking|insurance|logistics|ports?|"
                      r"steel|retail|consumer|the|and|of|for|with|to|on|in|is|are|below|above)\b", re.I)
EN_DAILY = re.compile(r"^\s*(market commentary|market trader|chart of the day|derivatives|covered warrant|"
                      r"morning (brief|note)|daily|weekly|\d{1,2}W\d{2}\s*:)", re.I)
HASH_TITLE = re.compile(r"^[0-9a-f]{6}\s+[0-9a-f]{32}$", re.I)       # tên file mã hóa, không đọc được
COMPANY_HEAD = re.compile(r"^\s*([A-Z][A-Z0-9]{2})\s*(?:\[|-|–|:|_|\|)")


def is_english(title):
    """Bản tiếng Anh: tiêu đề không có chữ tiếng Việt và có từ tiếng Anh / đuôi _ENG."""
    t = title or ""
    if re.search(r"(_|\b)(ENG|EN)(_|\b|\.pdf)", t):
        return True
    if VN_CHARS.search(t) or re.search(r"(_|\b)(VIE|VN)(_|\b)", t):
        return False
    return bool(EN_DAILY.search(t)) or len(EN_WORDS.findall(t)) >= 2


def nice_sector_title(t):
    """'PDF KIS Research_Outlook_Ngành Thép_2026.H2_Full_2026.07.10' -> 'Triển vọng Ngành Thép 2026.H2'"""
    if "_" not in t and not t.upper().startswith("PDF "):
        return t
    x = re.sub(r"^\s*PDF\s+", "", t)
    x = re.sub(r"^KIS\s+Research[_\s]*", "", x, flags=re.I)
    x = re.sub(r"_?20\d{2}\.\d{2}\.\d{2}\s*$", "", x)            # ngày ở cuối tên file
    x = re.sub(r"_(VIE|VN|Full)\b", "", x, flags=re.I)
    x = re.sub(r"(?<![A-Za-z])Outlook(?![A-Za-z])", "Triển vọng", x, flags=re.I)
    x = x.replace("O&G", "ngành Dầu khí")
    x = re.sub(r"\s*_\s*", " ", x).strip(" -_")
    return x or t


# ---- Dịch tiêu đề tiếng Anh (SSI trang tiếng Anh) sang tiếng Việt ----
TRANSLATE_SOURCES = {"SSI"}
EN_VI = [  # dự phòng khi không dịch tự động được
    (r"\bearnings? update\b", "Cập nhật KQKD"), (r"\bresults? update\b", "Cập nhật kết quả"),
    (r"\bflash note\b", "Cập nhật nhanh"), (r"\bquick update\b", "Cập nhật nhanh"),
    (r"\binitiation( of coverage)?\b", "Báo cáo lần đầu"), (r"\bcompany update\b", "Cập nhật doanh nghiệp"),
    (r"\bupdate\b", "Cập nhật"), (r"\bcompany report\b", "Báo cáo doanh nghiệp"),
    (r"\btarget price\b", "giá mục tiêu"), (r"\boutperform\b", "Khả quan"), (r"\bunderperform\b", "Kém khả quan"),
    (r"\bstrong buy\b", "Mua mạnh"), (r"\bbuy\b", "Mua"), (r"\bsell\b", "Bán"), (r"\bhold\b", "Nắm giữ"),
    (r"\bneutral\b", "Trung lập"), (r"\bnet profit\b", "lợi nhuận ròng"), (r"\brevenue\b", "doanh thu"),
    (r"\bgrowth\b", "tăng trưởng"), (r"\boutlook\b", "triển vọng"), (r"\brecovery\b", "phục hồi"),
]


def _dict_vi(t):
    x = t
    for rx, vi in EN_VI:
        x = re.sub(rx, vi, x, flags=re.I)
    return x


def _google_vi(texts):
    """Dịch nhiều tiêu đề một lần (dịch vụ dịch miễn phí của Google); lỗi -> None."""
    import requests
    try:
        r = requests.get("https://translate.googleapis.com/translate_a/single",
                         params={"client": "gtx", "sl": "en", "tl": "vi", "dt": "t", "q": "\n".join(texts)},
                         headers={"User-Agent": UA}, timeout=20)
        out = "".join(seg[0] for seg in r.json()[0] if seg and seg[0])
        lines = [x.strip() for x in out.split("\n")]
        return lines if len(lines) == len(texts) else None
    except Exception:
        return None


def translate_titles(db):
    cache = db.setdefault("dich", {})
    todo = [r for r in db["reports"].values()
            if r.get("source") in TRANSLATE_SOURCES and r.get("title") and not VN_CHARS.search(r["title"])]
    need = sorted({r["title"] for r in todo if r["title"] not in cache})
    for i in range(0, len(need), 30):
        part = need[i:i + 30]
        vi = _google_vi(part)
        if vi:                                       # chỉ lưu bản dịch tự động; lỗi thì lần sau dịch lại
            for en, v in zip(part, vi):
                if v:
                    cache[en] = v
    for r in todo:
        r["title_en"] = r["title"]
        r["title"] = cache.get(r["title"]) or _dict_vi(r["title"])
    if need:
        print(f"  Dịch {len(need)} tiêu đề tiếng Anh sang tiếng Việt.")


REMOVED_SOURCES = {"BSC", "VDSC"}                       # nguồn đã bỏ: xóa khỏi kho ở mọi mục


def fix_links(db):
    """Sửa dữ liệu cũ: bỏ PDF chân trang (công bố rủi ro...) và link bắt hỏng (blob:, trang danh sách)."""
    for u in [u for u, r in db["reports"].items() if r.get("source") in REMOVED_SOURCES]:
        del db["reports"][u]
    for u in [u for u, r in db["reports"].items()
              if (is_english(r.get("title", "")) and r.get("source") not in TRANSLATE_SOURCES)
              or HASH_TITLE.match(r.get("title", "").strip())
              or re.fullmatch(r"\s*báo cáo( chi tiết| phân tích)?\s*", r.get("title", ""), re.I)]:
        del db["reports"][u]                        # bản tiếng Anh / mục rác không có tên báo cáo
    for r in db["reports"].values():
        if r.get("kind") in ("vm", "nganh"):
            m = COMPANY_HEAD.match(r.get("title", ""))
            if m and m.group(1) not in NOT_TICKERS:   # "HPG [MUA +58.6%] - ..." là báo cáo doanh nghiệp
                r.pop("kind", None)
                r["ticker"] = m.group(1)
                r["enriched"] = False
            else:
                r["ticker"] = ""                      # vĩ mô / ngành không gắn mã riêng lẻ
                if r["source"] == "KIS" or r.get("title", "").upper().startswith("PDF "):
                    r["title"] = nice_sector_title(r["title"])
    for r in db["reports"].values():
        if r.get("pdf_url") and not good_pdf(r["pdf_url"]):
            r.pop("pdf_url", None)
            if r.get("kind") not in ("vm", "nganh"):
                r["enriched"] = False                   # đọc lại để tìm đúng file báo cáo
        if r.get("guess_url") and re.search(r"/20\d{6}/", r.get("open_url") or ""):
            r.pop("open_url", None)                 # link đoán theo thư mục ngày khác -> bỏ, bấm lấy link thật
            r.pop("guess_url", None)
            if r.get("pdf_url") and "/storage/" in r["pdf_url"]:
                r.pop("pdf_url", None)
        ou = r.get("open_url")
        if ou and (not ou.startswith("http") or not good_pdf(ou)):
            r.pop("open_url", None)
        if r["source"] not in CLICK_SOURCES:
            r.pop("open_url", None)
        if r["source"] == "BSC" and not r.get("pdf_url"):   # file báo cáo BSC: /Report/ReportFile/<số>
            m = re.search(r"bsc\.com\.vn/bao-cao/(\d+)", r["url"])
            if m:
                r["pdf_url"] = f"https://www.bsc.com.vn/Report/ReportFile/{m.group(1)}"
                r["pdf_tried"] = True
        if r["source"] == "VPBankS":                         # link chi tiết bị ghép thiếu /trung-tam-phan-tich/
            u = urlparse(r["url"])
            if "vpbanks.com.vn" in u.netloc and u.path.strip("/") and "/" not in u.path.strip("/"):
                r["detail"] = f"https://www.vpbanks.com.vn/trung-tam-phan-tich/{u.path.strip('/')}"


def run_once(headless=True, only=None, open_browser=True, pdf_limit=200, fresh=False):
    try:
        _db0 = load_db()
        KNOWN_URLS.clear()
        KNOWN_URLS.update(_db0["reports"].keys())
        SKIPPED.clear()
        FULL_DONE.clear()
        FULL_DONE.update(_db0.get("full_scanned", {}).keys())
    except Exception:
        pass
    srcs = [s for s in SOURCES if not only or s[0].upper() in only
            or ("VM" in only and is_vm(s[0]))
            or ("NGANH" in only and s[0].startswith(NG_PREFIX))]
    print(f"[{datetime.now():%H:%M:%S}] Đang quét {len(srcs)} trang...")
    pages = fetch_pages(srcs, headless=headless)

    now = datetime.now().isoformat(timespec="seconds")
    db = load_db()
    all_recs, errors, stats = [], {}, {}
    for name, url, html, err, jsons in pages:
        if err or not html:
            errors[name] = err or "Không tải được"
            continue
        try:
            recs, seen_u = [], set()
            for part in html.split(PAGE_BREAK):
                for r in parse(name, url, part):
                    if r["url"] not in seen_u:
                        seen_u.add(r["url"])
                        recs.append(r)
            seen = {r["url"] for r in recs}
            jrecs = [r for r in parse_json(name, url, jsons) if r["url"] not in seen]
            if name in ONLY_VISIBLE_TAB:
                n0 = len(jrecs)
                jrecs = visible_filter(jrecs, html)
                print(f"  {name}: giữ {len(jrecs)}/{n0} báo cáo đang hiện ở tab đã chọn")
            recs += jrecs
        except Exception as e:
            import traceback
            traceback.print_exc()
            errors[name] = f"Lỗi khi đọc trang: {e}"[:150]
            recs = []
        try:                                       # lưu trang gốc + danh sách lấy được để đối chiếu
            save_debug(name, html, jsons)
            lines = [f"{r.get('date') or '----------'}  {r.get('ticker') or '---':4s}  {r['title']}  |  {r['url']}"
                     for r in sorted(recs, key=lambda r: r.get("date") or "", reverse=True)]
            (BASE_DIR / "debug" / f"{name}_lay_duoc.txt").write_text(
                f"{name}: {len(recs)} báo cáo\n" + "\n".join(lines), encoding="utf-8")
        except Exception:
            pass
        if name == "BSC":
            for r in recs:                          # BSC lẫn báo cáo ngành / chiến lược -> đưa sang Ngành / Vĩ mô
                if not r.get("ticker"):
                    r["kind"] = "vm" if re.search(r"insight|vĩ mô|vi mo|chiến lược|thị trường", r["title"], re.I) else "nganh"
                    r["enriched"] = True
        if is_vm(name):
            for r in recs:
                r["source"] = name[3:]
                r["kind"] = kind_of(name)
                r["ticker"] = ""            # báo cáo ngành/vĩ mô không gắn 1 mã
                r["enriched"] = True        # không đọc PDF lấy giá mục tiêu
        stats[name] = stats.get(name, 0) + len(recs)
        all_recs += recs
    for name, n in stats.items():
        if n == 0:
            errors.setdefault(name, "Tải được trang nhưng không tìm thấy báo cáo "
                                    "(trang có thể cần đăng nhập hoặc đổi cấu trúc)")

    if fresh:
        # làm mới: bỏ dữ liệu cũ của các nguồn vừa quét thành công, giữ kết quả đọc PDF
        ok = {n for n, v in stats.items() if v}
        cache = {u: r for u, r in db["reports"].items() if r.get("enriched")}
        db["reports"] = {u: r for u, r in db["reports"].items() if srckey(r) not in ok}
        print(f"  Làm mới kho: xóa dữ liệu cũ của {len(ok)} nguồn, quét lại từ đầu")
        for r in all_recs:
            old = cache.get(r["url"])
            if old:
                for k in ("enriched", "rec", "rec_kind", "target", "price", "upside", "pdf_url"):
                    if k in old:
                        r[k] = old[k]
    clean_db(db)
    new_count = merge(db, all_recs, now)
    try:
        translate_titles(db)                         # SSI: tiêu đề tiếng Anh -> tiếng Việt
    except Exception as e:
        print("  ! lỗi khi dịch tiêu đề:", e)
    fix_links(db)

    def push(note):
        try:
            DB_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
            build_html(db, new_count, errors)
            print(f"\n{note}")
            publish_online()
        except Exception as e:
            print("  ! lỗi khi đưa lên mạng:", e)
    try:
        resolve_pdf_links(db, headless)             # ACBS, MBS, KIS: lấy file PDF xem trước của từng báo cáo
    except Exception as e:
        print("  ! lỗi khi lấy link PDF:", e)
    try:
        resolve_click_links(db, headless)           # VCBS: bấm nút tải lấy đúng file của từng mã (~2-3 phút)
    except Exception as e:
        print("  ! lỗi khi lấy link VCBS:", e)
    push("[1/2] Quét xong, đã có link riêng từng mã – đưa lên mạng...")
    fs = db.setdefault("full_scanned", {})          # nguồn quét thành công lần này = đã quét đủ trang
    for n, v in stats.items():
        if v and (n not in FULL_DONE or FULL_SCAN):
            fs[n] = now
    try:
        fill_dates(db)
    except Exception as e:
        print("  ! lỗi khi tìm ngày đăng:", e)
    if pdf_limit:
        try:
            enrich(db, pdf_limit)
        except Exception as e:
            print("  ! lỗi khi đọc PDF:", e)
    try:
        fill_dates(db)                              # lần 2: báo cáo vừa tìm được file PDF
    except Exception as e:
        print("  ! lỗi khi tìm ngày đăng:", e)
    db["runs"] = (db.get("runs", []) + [{"time": now, "new": new_count, "stats": stats}])[-50:]
    DB_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    build_html(db, new_count, errors)
    print("\n[2/2] Đọc PDF xong (khuyến nghị, giá mục tiêu, ngày đăng) – đưa lên mạng...")
    publish_online()

    print(f"\nKết quả theo nguồn (đã quét {len(srcs)}/{len(SOURCES)} link):")
    shown = set()
    for name, _ in srcs:
        if name in shown:
            continue
        shown.add(name)
        n = stats.get(name, 0)
        mark = "OK " if n else "LỖI"
        note = "  (chỉ đọc trang 1 – không có báo cáo mới)" if name in SKIPPED else ""
        print(f"  [{mark}] {name:11s} {n:4d} báo cáo{note}   {errors.get(name, '')[:90]}")
    print(f"\n=> {new_count} báo cáo mới, tổng {len(db['reports'])} trong kho.")
    if any(n not in stats or stats[n] == 0 for n, _ in srcs):
        print(f"=> Nguồn ra 0: xem thư mục {BASE_DIR / 'debug'} (gửi file của nguồn đó để sửa)")
    print(f"=> Danh sách lấy được của từng nguồn: {BASE_DIR / 'debug'}\\<Nguồn>_lay_duoc.txt")
    print(f"=> Mở file: {HTML_FILE}")
    if open_browser:
        webbrowser.open(HTML_FILE.as_uri())


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
                st.flush()
            except Exception:
                pass

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


def main():
    log = open(BASE_DIR / "nhat_ky.txt", "w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, log)
    sys.stderr = _Tee(sys.__stderr__, log)
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=0, help="quét lặp lại mỗi N phút")
    ap.add_argument("--show", action="store_true", help="hiện cửa sổ trình duyệt")
    ap.add_argument("--only", default="", help="chỉ quét các nguồn, vd: SSI,MBS")
    ap.add_argument("--pdf", type=int, default=200,
                    help="số báo cáo tối đa đọc PDF mỗi lần (0 = bỏ qua)")
    ap.add_argument("--pdf-only", action="store_true",
                    help="không quét web, chỉ đọc PDF các báo cáo đã có trong kho")
    ap.add_argument("--trang", type=int, default=3, help="số trang danh sách lấy mỗi nguồn")
    ap.add_argument("--luong", type=int, default=6, help="số trang quét cùng lúc (mặc định 5)")
    ap.add_argument("--lam-moi", action="store_true",
                    help="xóa các mục cũ (kể cả mục quét nhầm) rồi quét lại từ đầu")
    ap.add_argument("--no-open", action="store_true", help="không tự mở trình duyệt")
    a = ap.parse_args()
    global MAX_PAGES, WORKERS
    MAX_PAGES = max(1, a.trang)
    WORKERS = max(1, a.luong)
    only = {s.strip().upper() for s in a.only.split(",") if s.strip()} or None

    if a.pdf_only:
        db = load_db()
        fix_links(db)
        enrich(db, a.pdf or 10**6)
        DB_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
        build_html(db, 0, {})
        print(f"=> Xong. Mở file: {HTML_FILE}")
        if not a.no_open:
            webbrowser.open(HTML_FILE.as_uri())
        return

    first = True
    while True:
        try:
            run_once(headless=not a.show, only=only, open_browser=first and not a.no_open,
                     pdf_limit=a.pdf, fresh=a.lam_moi and first)
        except Exception:
            import traceback
            print("LỖI NGHIÊM TRỌNG – gửi file nhat_ky.txt để sửa:")
            traceback.print_exc()
        first = False
        if not a.every:
            break
        print(f"... quét lại sau {a.every} phút (Ctrl+C để dừng)\n")
        time.sleep(a.every * 60)


if __name__ == "__main__":
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    main()

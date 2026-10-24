#!/usr/bin/env python3
"""The Library: AI Simplified as a digital library. Every post is a book on a shelf (hover one and it slides out,
turns to its cover and shows a line from the post), plus Notes, the reading list (Substack recommendations), About,
a sponsor ask, and a plans panel that opens from every Subscribe button.

    python3 -I library/build_library.py            # fetch from Substack, write data.json + index.html
    python3 -I library/build_library.py --offline  # rebuild index.html from the saved data.json

Everything shown comes from Substack's public data at build time (posts, prices, benefits, subscriber count, Notes,
recommendations), so the page never drifts from what Substack says. If a fetch fails, that section keeps the last
saved data. Shelves, spine titles, colours, quotes and the tool cubes live in shelves.json.
Substack's website editor takes no custom code, so this page is hosted on its own (GitHub Pages, rebuilt hourly)."""
import datetime as dt, email.utils, hashlib, html, json, math, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

L = Path(__file__).resolve().parent
CFG = json.loads((L / "shelves.json").read_text())
PUB = CFG["publication"].rstrip("/")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
CW = 240  # cover width in px; spine thickness and height vary per book
ACCESS = {"everyone": "Free", "only_subscribers": "Free with email", "only_paid": "Paid", "founding": "Founding", "new": "New"}
SKIP = re.compile(r"^(subscribe now|share|leave a comment|watch the video|in this post|this guide covers|.*reader-supported publication)", re.I)
GREETING = re.compile(r"^hi guys, nishant here\.\s*", re.I)
E = lambda s: html.escape(str(s), quote=True)


def get(url, as_json=True):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30) as r:
                body = r.read().decode("utf-8", "ignore")
                return json.loads(body) if as_json else body
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(1.5 * (i + 1))
    raise err


def fetch_posts():
    posts, off = [], 0
    while True:
        page = get(f"{PUB}/api/v1/archive?sort=new&offset={off}&limit=25")
        if not page:
            break
        posts += page
        off += len(page)
    out = []
    for p in posts:
        if p.get("type") != "newsletter":
            continue
        full = get(f"{PUB}/api/v1/posts/{p['slug']}")
        body = full.get("body_html") or full.get("truncated_body_html") or ""
        out.append({k: p.get(k) for k in ("slug", "title", "subtitle", "post_date", "audience", "wordcount", "cover_image", "canonical_url")}
                   | {"tags": [t["name"] for t in p.get("postTags") or []], "paras": paras_of(body)})
    return out


def paras_of(body_html):
    return [x for x in (html.unescape(re.sub(r"<[^>]+>", "", m)).strip()
            for m in re.findall(r"<(?:p|h[1-4]|li|blockquote)[^>]*>(.*?)</(?:p|h[1-4]|li|blockquote)>", body_html, re.S)) if x]


def fetch_posts_rss(prev_posts):
    """Fallback when Substack's API refuses the request (it returns 403 to GitHub's servers; RSS still works).
    Posts already in the snapshot keep their saved data. New ones come from the feed, marked "new" because RSS
    doesn't say whether a post is free or paid, so the page never labels a paid post as free."""
    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    known = {p["slug"]: p for p in prev_posts}
    out = list(prev_posts)
    for it in ET.fromstring(get(PUB + "/feed", as_json=False)).iter("item"):
        link = (it.findtext("link") or "").strip()
        slug = link.rstrip("/").rsplit("/p/", 1)[-1]
        if not slug or slug in known:
            continue
        body = it.findtext("content:encoded", default="", namespaces=ns)
        enc = it.find("enclosure")
        when = email.utils.parsedate_to_datetime(it.findtext("pubDate")).astimezone(dt.timezone.utc)
        out.append({"slug": slug, "title": (it.findtext("title") or "").strip(), "subtitle": (it.findtext("description") or "").strip(),
                    "post_date": when.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "audience": "new",
                    "wordcount": len(re.sub(r"<[^>]+>", " ", body).split()), "cover_image": enc.get("url") if enc is not None else None,
                    "canonical_url": link, "tags": [], "paras": paras_of(body)})
        print(f"rss: added {slug}")
    return out


def fetch_pub():
    page = get(PUB + "/", as_json=False)
    m = re.search(r'<script[^>]*id="preloads-json"[^>]*>(.*?)</script>', page, re.S)
    pub = json.loads(m.group(1))["pub"]
    plans = {p["interval"]: p["amount"] / 100 for p in pub.get("plans") or [] if p.get("active", True) and p.get("currency") == "usd" and not p["id"].startswith("founding")}
    founding = (pub.get("iap_founding_plan") or {}).get("minimum_amount")
    return {"id": pub["id"], "author_id": pub["author_id"], "subscribers": pub.get("rankingDetailFreeSubscriberCount") or "",
            "monthly": plans.get("month"), "yearly": plans.get("year"), "founding": founding / 100 if founding else None,
            "free_benefits": pub.get("free_subscription_benefits") or [], "paid_benefits": pub.get("paid_subscription_benefits") or [],
            "founding_benefits": pub.get("founding_subscription_benefits") or []}


def fetch_recs(pub_id):
    out = []
    for r in get(f"{PUB}/api/v1/recommendations/from/{pub_id}"):
        p = r.get("recommendedPublication") or {}
        url = f"https://{p['custom_domain']}" if p.get("custom_domain") else f"https://{p['subdomain']}.substack.com"
        out.append({"name": (p.get("name") or "").strip(), "url": url, "logo": p.get("logo_url"), "hero": (p.get("hero_text") or "").strip(),
                    "author": (p.get("author_name") or "").strip()})
    return out


def fetch_notes(author_id):
    out = []
    for it in get(f"https://substack.com/api/v1/reader/feed/profile/{author_id}?types%5B%5D=note").get("items", []):
        c = it.get("comment") or {}
        body = (c.get("body") or "").strip()
        if it.get("type") != "comment" or len(body) < 40:
            continue  # skip restack-only notes (recommendations, "new post" shares)
        img = next((a.get("imageUrl") for a in c.get("attachments") or [] if a.get("type") == "image" and a.get("imageUrl")), None)
        out.append({"id": c["id"], "date": c["date"], "body": body, "likes": c.get("reaction_count") or 0, "image": img,
                    "url": f"https://substack.com/@{c.get('handle')}/note/c-{c['id']}"})
    return out


def fetch():
    """Fetch every section; a section that fails keeps its last saved copy (so a flaky API never blanks the page)."""
    prev = json.loads((L / "data.json").read_text()) if (L / "data.json").exists() else {}
    data = {}
    try:
        data["pub"] = fetch_pub()
    except Exception as e:  # noqa: BLE001
        print(f"warn: pub fetch failed ({e}); keeping the saved copy")
        data["pub"] = prev.get("pub", {})
    try:
        data["posts"] = fetch_posts()
    except Exception as e:  # noqa: BLE001
        print(f"warn: posts API failed ({e}); trying the RSS feed")
        try:
            data["posts"] = fetch_posts_rss(prev.get("posts", []))
        except Exception as e2:  # noqa: BLE001
            print(f"warn: RSS failed too ({e2}); keeping the saved copy")
            data["posts"] = prev.get("posts", [])
    for key, fn, arg in (("recs", fetch_recs, "id"), ("notes", fetch_notes, "author_id")):
        try:
            data[key] = fn(data["pub"][arg])
        except Exception as e:  # noqa: BLE001
            print(f"warn: {key} fetch failed ({e}); keeping the saved copy")
            data[key] = prev.get(key, [])
    (L / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    return data


def auto_excerpt(paras):
    for x in paras:
        x = GREETING.sub("", x).strip()
        if len(x) < 40 or SKIP.match(x):
            continue
        sents = re.findall(r".+?[.!?](?:\s|$)", x + " ")
        line = ""
        for s in sents:
            if line and len(line) + len(s) > 190:
                break
            line += s
        return (line or x[:190]).strip()
    return ""


def ink_on(hexcol):
    r, g, b = (int(hexcol[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return "#080808" if 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b) > 0.35 else "#ffffff"


def fmt_date(iso):
    d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return f"{d.day} {d:%b %Y}"


def books(data):
    palette = ["#080808", "#DC6E4E", "#3475B1", "#E4A60D", "#0F458F"]
    posts = sorted(data["posts"], key=lambda p: p["post_date"])
    out = []
    for n, p in enumerate(posts, 1):
        conf = CFG["books"].get(p["slug"], {})
        text = " ".join(p["paras"])
        ex = conf.get("excerpt")
        if ex and ex not in text:
            sys.exit(f"excerpt for {p['slug']} is not in the post, word for word:\n  {ex}")
        shelf = next((s["id"] for s in CFG["shelves"] if p["slug"] in s.get("slugs", []) or set(s.get("tags", [])) & set(p["tags"])), CFG["default_shelf"])
        h = int(hashlib.md5(p["slug"].encode()).hexdigest(), 16)
        color = conf.get("color") or palette[n % len(palette)]
        spine = conf.get("spine") or re.split(r"[:(.?]", p["title"])[0].strip()[:40]
        out.append({"slug": p["slug"], "n": n, "title": p["title"], "subtitle": p.get("subtitle") or "", "spine": spine,
                    "url": p["canonical_url"], "cover": p.get("cover_image"), "access": ACCESS.get(p["audience"], "Free"),
                    "date": fmt_date(p["post_date"]), "iso": p["post_date"], "mins": max(1, math.ceil((p.get("wordcount") or 0) / 250)),
                    "quote": ex or auto_excerpt(p["paras"]), "shelf": shelf, "color": color, "ink": ink_on(color),
                    "t": max(52, min(100, round(34 + (p.get("wordcount") or 0) / 30))), "h": 340 + (h % 5) * 14})
    return out


def book_html(b, i):
    plate = (f'<span class="cv-plate"><img src="{E(b["cover"])}" alt="" loading="lazy"></span>' if b["cover"]
             else f'<span class="cv-plate empty"><b>No. {b["n"]}</b></span>')
    return (f'<div class="slot" style="--t:{b["t"]}px;--h:{b["h"]}px;--c:{b["color"]};--ink:{b["ink"]}">'
            f'<a class="book" href="{E(b["url"])}" data-i="{i}" aria-label="{E(b["title"])}. {E(b["access"])}, {b["mins"]} minute read.">'
            f'<span class="face spine" aria-hidden="true"><span class="sp-mark">AI·S</span><span class="sp-t">{E(b["spine"])}</span>'
            f'<span class="sp-n">No.{b["n"]}</span></span>'
            f'<span class="face cover" aria-hidden="true"><span class="cv-k">{E(CFG["name"])} · No. {b["n"]}</span>'
            f'<span class="cv-t">{E(b["spine"])}</span>{plate}<span class="cv-by">{E(CFG["author"])}</span></span></a></div>')


def cube(cls, style, img=False, title=""):
    faces = "".join(f'<i class="f{j}"></i>' for j in range(1, 7))
    t = f' title="{E(title)}"' if title else ""
    return f'<span class="cw {cls}{" img" if img else ""}"{t} aria-hidden="true" style="{style}"><span class="cube">{faces}</span></span>'


def portrait_html(prefix=""):
    """The publication photo with a cube per tool (shelves.json "tools"), each in that tool's own colours."""
    ph = CFG["photo"]
    cubes = "".join(cube(f"c{k}", f'--face:{t["face"]};--ink:{t["ink"] if t["ink"] != "image" else "none"};--logo:url({E(prefix + t["logo"])})',
                         t["ink"] == "image", t["name"]) for k, t in enumerate(CFG.get("tools", []), 1))
    return (f'<a class="portrait" href="{E(PUB + ph["link"])}" aria-label="About {E(ph["alt"])}"><span class="sky">'
            f'<img src="{E(prefix + ph["src"])}" alt="{E(ph["alt"])}" width="640" height="640"></span>{cubes}'
            f'<span class="who">{E(ph["alt"])}<b>About →</b></span></a>')


def head(lead, big, k=""):
    kk = f'<div class="k">{E(k)}</div>' if k else ""
    return f'<div class="sec-head">{kk}<h2><span class="ser">{E(lead)}</span><span class="big">{E(big)}</span></h2></div>'


def notes_html(notes):
    if not notes:
        return ""
    cards = []
    for n in notes[:6]:
        body = n["body"] if len(n["body"]) <= 300 else n["body"][:300].rsplit(" ", 1)[0] + "…"
        img = f'<img src="{E(n["image"])}" alt="" loading="lazy">' if n.get("image") else ""
        likes = f' · ♥ {n["likes"]}' if n["likes"] else ""
        cards.append(f'<a class="note" href="{E(n["url"])}"><span class="note-k">Note · {fmt_date(n["date"])}{likes}</span>'
                     f'<span class="note-b">{E(body)}</span>{img}<span class="note-go">Read the note →</span></a>')
    return (f'<section class="sec" id="notes">{head("from my", "Notes.", "Short ones, from Substack Notes")}'
            f'<div class="notes">{"".join(cards)}</div></section>')


def recs_html(recs):
    if not recs:
        return ""
    palette = ["#DC6E4E", "#3475B1", "#0F458F", "#E4A60D", "#080808"]
    covers = []
    for i, r in enumerate(recs):
        initials = f'<b style="background:{palette[i % len(palette)]}">{E("".join(w[0] for w in r["name"].split()[:2]).upper())}</b>'
        art = initials + (f'<img src="{E(r["logo"])}" alt="" loading="lazy" onerror="this.remove()">' if r.get("logo") else "")
        covers.append(f'<a class="pick" href="{E(r["url"])}"><span class="pick-art">{art}</span><span class="pick-n">{E(r["name"])}</span>'
                      f'<span class="pick-a">{E(r["author"])}</span><span class="pick-h">{E(r["hero"])}</span></a>')
    return (f'<section class="sec" id="reading">{head("on my", "Reading list.", "Publications I recommend on Substack")}'
            f'<div class="ledge-wrap"><div class="ledge">{"".join(covers)}</div><div class="plank" aria-hidden="true"></div>'
            f'<div class="plaque"><span>The Library</span>Recommended reading<span>{len(recs)}</span></div></div></section>')


def price(v):
    return f"${v:g}" if v is not None else ""


def about_html(pub, est=""):
    a = CFG["about"]
    paras = "".join(f"<p>{E(x)}</p>" for x in a["paras"])
    links = "".join(f'<a href="{E(u)}">{E(t)} →</a>' for t, u in a["links"])
    rows = [("Free", "$0"), ("Paid", f'{price(pub.get("monthly"))} a month or {price(pub.get("yearly"))} a year'), ("Founding", f'{price(pub.get("founding"))} a year')]
    rows_html = "".join(f'<div class="lc-row"><span>{E(k)}</span><b>{E(v)}</b></div>' for k, v in rows if v.strip() and "$" in v or k == "Free")
    subs = f'<div class="lc-subs">{E(pub["subscribers"])}</div>' if pub.get("subscribers") else ""
    return (f'<section class="sec" id="about">{head("about", "Me.", "Who writes " + CFG["name"])}<div class="about">'
            f'<div class="about-t">{paras}<div class="about-links">{links}</div></div>'
            f'<div class="lcard"><div class="lc-top"><span>Library card</span><span>{E(est)}</span></div>'
            f'<div class="lc-name">AI <em>simplified</em></div>{subs}{rows_html}'
            f'<a class="btn" href="{E(PUB)}/subscribe" data-sub>Get your card →</a></div></div></section>')


def sponsor_html(pub):
    s = CFG["sponsor"]
    mail = f'mailto:{s["email"]}?subject={urllib.parse.quote(s["subject"])}'
    chip = f'<span class="chip">{E(pub["subscribers"])}</span>' if pub.get("subscribers") else ""
    tools = "".join(cube(f"m{k}", f'--face:{t["face"]};--ink:{t["ink"] if t["ink"] != "image" else "none"};--logo:url({E(t["logo"])})',
                         t["ink"] == "image", t["name"]) for k, t in enumerate(CFG.get("tools", [])[:3], 1))
    you = cube("m0 you", "--face:#fff;--ink:none;--logo:none")
    return (f'<section class="sec sponsor" id="sponsor"><div class="sp-l">{head("do you want to", "Sponsor?", "Sponsorships")}'
            f'<p>{E(s["line"])}</p><div class="sp-row">{chip}<a class="btn" href="{E(mail)}">Email {E(s["email"])}</a></div></div>'
            f'<div class="sp-r" aria-hidden="true">{you}{tools}<span class="you-tag">your logo here</span></div></section>')


def paywall_html(pub):
    yearly, monthly = pub.get("yearly"), pub.get("monthly")
    save = f"Save {price(monthly * 12 - yearly)} a year" if yearly and monthly and monthly * 12 > yearly else ""
    li = lambda xs: "".join(f"<li>{E(x)}</li>" for x in xs)
    sub = f'<p class="pw-subs">{E(pub["subscribers"])} read it.</p>' if pub.get("subscribers") else ""
    go = f"{E(PUB)}/subscribe"
    return f"""<dialog id="paywall" class="paywall" aria-labelledby="pw-title"><button class="x pw-x" type="button" aria-label="Close" data-close>×</button>
<div class="pw-head"><div class="k">Subscribe</div><h2 id="pw-title"><span class="ser">join</span><span class="big">AI Simplified.</span></h2>{sub}
<div class="pw-toggle" role="group" aria-label="Billing"><button type="button" data-bill="month">Monthly</button><button type="button" data-bill="year" class="on">Yearly</button>{f'<span class="pw-save">{E(save)}</span>' if save else ""}</div></div>
<div class="plans">
<div class="plan"><div class="pl-k">Free</div><div class="pl-p">$0</div><ul>{li(pub.get("free_benefits", []))}</ul><a class="btn ghost" href="{go}">Subscribe free</a></div>
<div class="plan pro"><div class="pl-tag">Pro</div><div class="pl-k">Paid</div><div class="pl-p"><span data-price="year">{price(yearly)}<small> / year</small></span><span data-price="month" hidden>{price(monthly)}<small> / month</small></span></div>
<ul>{li(pub.get("paid_benefits", []))}</ul><a class="btn" href="{go}">Go Pro →</a></div>
<div class="plan"><div class="pl-k">Founding</div><div class="pl-p">{price(pub.get("founding"))}<small> / year</small></div><ul><li>Everything in Pro.</li>{li(pub.get("founding_benefits", []))}</ul><a class="btn ghost" href="{go}">Become a founding member</a></div>
</div><p class="pw-fine">Prices and benefits come straight from Substack. Checkout happens on Substack.</p></dialog>"""


def fill(tpl, values):
    """One pass, so text fetched from Substack can never be mistaken for a placeholder."""
    return re.sub(r"%([A-Z]+)%", lambda m: values.get(m.group(1), m.group(0)), tpl)


def page(bs, data):
    pub = data.get("pub") or {}
    shelves = []
    for k, s in enumerate(CFG["shelves"], 1):
        row = [(i, b) for i, b in enumerate(bs) if b["shelf"] == s["id"]]
        if not row:
            continue
        row.sort(key=lambda ib: -ib[1]["n"])
        shelves.append(f'<section class="shelf"><div class="row">{"".join(book_html(b, i) for i, b in row)}'
                       f'<span class="bookend" aria-hidden="true"></span></div><div class="plank" aria-hidden="true"></div>'
                       f'<h2 class="plaque"><span>Shelf {k:02d}</span>{E(s["name"])}<span>{len(row)} book{"s" if len(row) != 1 else ""}</span></h2></section>')
    counts = {}
    for b in bs:
        counts[b["access"]] = counts.get(b["access"], 0) + 1
    newest = max(bs, key=lambda b: b["iso"])["date"] if bs else ""
    tally = " · ".join([f"{len(bs)} books"] + [f"{v} {k.lower()}" for k, v in counts.items()] + ([f"newest {newest}"] if newest else []))
    return fill(TEMPLATE, {
        "CSS": CSS.replace("%CW%", str(CW)), "JS": JS.replace("%CW%", str(CW)), "PUB": E(PUB), "NAME": E(CFG["name"]),
        "AUTHOR": E(CFG["author"]), "HERO": E(CFG["hero"]), "PORTRAIT": portrait_html(), "TALLY": E(tally), "SHELVES": "".join(shelves),
        "NOTES": notes_html(data.get("notes") or []), "RECS": recs_html(data.get("recs") or []), "ABOUT": about_html(pub, f'Est. {min(b["iso"] for b in bs)[:4]}' if bs else ""),
        "SPONSOR": sponsor_html(pub), "PAYWALL": paywall_html(pub), "DATA": json.dumps(bs, ensure_ascii=False).replace("</", "<\\/")})


CSS = r"""
@font-face{font-family:InterX;src:url(fonts/Inter-var.ttf);font-weight:100 900;font-display:swap}
@font-face{font-family:SerifX;src:url(fonts/InstrumentSerif-Italic.ttf);font-style:italic;font-display:swap}
@font-face{font-family:MonoX;src:url(fonts/JetBrainsMono-500.ttf);font-display:swap}
/* palette: house paper/ink + colours sampled from the publication photo (cubes, sky, sweater, flowers) */
:root{--paper:#F1F1ED;--ink:#080808;--soft:#5b5b63;--claude:#DC6E4E;--cube:#DC6E4E;--cube-hi:#E97E5E;--cube-lo:#CA5F40;--sky:#4286BF;--sky-deep:#3475B1;
 --sweater:#0F458F;--sun:#E4A60D;--blue:#3475B1;--green:#13A046;--wall:#2a2622;--rule:#dcdcd5;--cw:%CW%px;color-scheme:light}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth;background:radial-gradient(ellipse 55% 560px at 82% 0%,rgba(66,134,191,.20),rgba(66,134,191,0) 100%) no-repeat,
 radial-gradient(ellipse 90% 700px at 30% 0%,#FBFBF9 0%,rgba(251,251,249,0) 100%) no-repeat,var(--paper)}
body{font-family:InterX,system-ui,sans-serif;color:var(--ink);min-height:100vh;overflow-x:hidden}
.on-touch{display:none}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
 background-image:linear-gradient(rgba(0,0,0,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(0,0,0,.045) 1px,transparent 1px);background-size:28px 28px}
a{color:inherit}
.wrap{position:relative;z-index:1;max-width:1180px;margin:0 auto;padding:0 32px}
.k{font:500 13px MonoX;letter-spacing:.08em;text-transform:uppercase;color:var(--claude)}
.ser{font-family:SerifX;font-style:italic;font-weight:400}
.top{position:sticky;top:0;z-index:40;display:flex;align-items:center;justify-content:space-between;padding:16px 0;margin:0 -32px;padding-inline:32px;
 background:rgba(241,241,237,.82);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid transparent;transition:border-color .3s}
.top.scrolled{border-color:var(--rule)}
.brand{font:900 26px InterX;letter-spacing:-.04em;text-decoration:none}
.brand em{font:400 30px SerifX;font-style:italic;letter-spacing:0;margin-left:4px}
.top nav{display:flex;gap:22px;align-items:center;font:500 13px MonoX;text-transform:uppercase;letter-spacing:.06em}
.top nav a{text-decoration:none}
.top nav a:not(.btn):hover{color:var(--claude)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;height:44px;padding:0 20px;border-radius:12px;background:var(--ink);color:#fff!important;border:3px solid var(--ink);
 font:700 15px InterX;text-decoration:none;letter-spacing:-.01em;transition:background .2s,border-color .2s,transform .2s;cursor:pointer}
.btn:hover{background:var(--claude);border-color:var(--claude)}
.btn:active{transform:translateY(1px)}
.btn.ghost{background:#fff;color:var(--ink)!important}
.btn.ghost:hover{background:var(--ink);color:#fff!important;border-color:var(--ink)}
.top .btn{height:38px;font-size:14px;text-transform:none;letter-spacing:0;font-family:InterX}
.chip{display:inline-flex;align-items:center;height:44px;padding:0 16px;border:3px solid var(--ink);border-radius:12px;background:#fff;font:500 14px MonoX}
.intro{display:flex;align-items:center;justify-content:space-between;gap:40px;padding:30px 0 10px}
.intro h1{margin-top:10px;line-height:.86}
.intro h1 .ser{display:block;font-size:46px;line-height:1}
.intro h1 .big{display:block;font:900 104px/.86 InterX;letter-spacing:-.06em;text-transform:uppercase;margin-top:4px}
.intro-r{max-width:470px;margin-top:22px}
.intro p{font:400 25px/1.2 SerifX;font-style:italic;color:var(--soft)}
.tally{font:500 12px/1.6 MonoX;color:var(--soft);margin-top:12px;letter-spacing:.02em}
/* the photo and the tool cubes */
.portrait{position:relative;display:block;flex:none;width:300px;aspect-ratio:1;margin:6px 46px 26px 0;perspective:900px;text-decoration:none}
.sky{position:absolute;inset:0;border-radius:30px;overflow:hidden;border:3px solid var(--ink);background:var(--sky);
 box-shadow:0 34px 60px -26px rgba(15,69,143,.6);transform:rotate(2deg);transition:transform .5s cubic-bezier(.2,.75,.2,1)}
.sky img{display:block;width:100%;height:100%;object-fit:cover;transition:transform .8s cubic-bezier(.2,.75,.2,1)}
.portrait:hover .sky,.portrait:focus-visible .sky{transform:rotate(0) translateY(-6px)}
.portrait:hover .sky img{transform:scale(1.05)}
.portrait:focus-visible{outline:none}
.portrait:focus-visible .sky{outline:3px solid var(--cube);outline-offset:4px}
.who{position:absolute;left:50%;bottom:-20px;z-index:3;transform:translateX(-50%) rotate(2deg);white-space:nowrap;padding:8px 14px;border:3px solid var(--ink);
 border-radius:12px;background:#fff;font:500 12px MonoX;letter-spacing:.06em;text-transform:uppercase;color:var(--ink)}
.who b{font-weight:500;color:var(--cube);margin-left:10px}
.cw{position:absolute;z-index:2;width:var(--s);height:var(--s);transform-style:preserve-3d;animation:bob var(--d) ease-in-out infinite alternate;
 transition:translate .7s cubic-bezier(.2,.75,.2,1)}
.c1{--s:58px;--d:3.6s;left:-38px;top:112px;--hx:-16px;--hy:-10px}
.c2{--s:50px;--d:4.4s;right:-30px;top:30px;--hx:14px;--hy:-16px;animation-delay:-1.4s}
.c3{--s:46px;--d:3.2s;right:-22px;bottom:80px;--hx:16px;--hy:10px;animation-delay:-.7s}
.c4{--s:40px;--d:3.9s;left:-24px;bottom:34px;--hx:-14px;--hy:12px;animation-delay:-2.1s}
.c5{--s:40px;--d:4.8s;left:66px;top:-26px;--hx:-6px;--hy:-16px;animation-delay:-.3s}
.portrait:hover .cw{translate:var(--hx) var(--hy)}
.cube{position:absolute;inset:0;transform-style:preserve-3d;animation:spin 16s linear infinite}
.c2 .cube,.m2 .cube{animation-duration:21s;animation-direction:reverse}
.c3 .cube,.m3 .cube{animation-duration:12s}
.c4 .cube{animation-duration:18s;animation-direction:reverse}
.c5 .cube{animation-duration:14s}
.cube i{position:absolute;inset:0;border-radius:7px;background:var(--face);box-shadow:inset 0 0 0 1.5px rgba(0,0,0,.12)}
.cube i::before{content:"";position:absolute;inset:21%;background:var(--ink);-webkit-mask:var(--logo) center/contain no-repeat;mask:var(--logo) center/contain no-repeat}
.img .cube i::before{background:var(--logo) center/contain no-repeat;-webkit-mask:none;mask:none}
.cube i::after{content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none}
.cube .f1{transform:translateZ(calc(var(--s) / 2))}
.cube .f2{transform:rotateY(90deg) translateZ(calc(var(--s) / 2))}
.cube .f3{transform:rotateY(180deg) translateZ(calc(var(--s) / 2))}
.cube .f4{transform:rotateY(-90deg) translateZ(calc(var(--s) / 2))}
.cube .f5{transform:rotateX(90deg) translateZ(calc(var(--s) / 2))}
.cube .f6{transform:rotateX(-90deg) translateZ(calc(var(--s) / 2))}
.cube .f1::after,.cube .f5::after{background:rgba(255,255,255,.10)}
.cube .f2::after,.cube .f4::after,.cube .f6::after{background:rgba(0,0,0,.13)}
@keyframes bob{from{transform:translateY(-9px)}to{transform:translateY(9px)}}
@keyframes spin{from{transform:rotateX(-22deg) rotateY(0)}to{transform:rotateX(-22deg) rotateY(360deg)}}
/* the bookcase */
.case{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));margin:30px 0 10px;border:16px solid var(--wall);border-radius:12px;
 background:linear-gradient(180deg,#dcd8cc,#e6e2d8 40%,#d6d1c3);box-shadow:0 40px 70px -30px rgba(0,0,0,.45),inset 0 0 0 1px rgba(0,0,0,.2)}
.shelf{position:relative;padding:88px 30px 0;border-right:14px solid var(--wall);box-shadow:inset 0 22px 26px -18px rgba(0,0,0,.45)}
.shelf:nth-child(2n),.shelf:last-child{border-right:0}
.shelf:last-child:nth-child(odd){grid-column:1/-1}
.row{display:flex;align-items:flex-end;gap:5px;min-height:410px;padding:0 6px}
.slot{position:relative;z-index:1;width:var(--t);height:var(--h);flex:none;perspective:1500px;perspective-origin:50% 22%}
.slot.on{z-index:30}
.slot::after{content:"";position:absolute;left:-10%;right:-10%;bottom:-7px;height:14px;border-radius:50%;background:rgba(0,0,0,.28);filter:blur(5px);transition:opacity .4s,transform .4s;z-index:-1}
.slot.on::after{opacity:.35;transform:scaleX(2.4)}
.book{position:absolute;inset:0;display:block;transform-style:preserve-3d;text-decoration:none;cursor:pointer;
 transform-origin:calc(var(--t) / 2) 50% calc(var(--cw) / -2);transition:transform .6s cubic-bezier(.2,.75,.2,1)}
.slot:not(.on) .book:hover{transform:translateY(-10px)}
.slot.on .book{transform:translate3d(0,-34px,calc(var(--cw) / 2 + 60px)) rotateY(-90deg)}
.book:focus-visible{outline:none}
.slot.on .book:focus-visible .cover{outline:3px solid var(--claude);outline-offset:4px}
.face{position:absolute;top:0;height:100%;backface-visibility:hidden;-webkit-backface-visibility:hidden;overflow:hidden}
.spine{left:0;width:var(--t);background:var(--c);color:var(--ink);display:flex;flex-direction:column;align-items:center;border-radius:3px 3px 2px 2px;
 box-shadow:inset 0 0 0 1px rgba(0,0,0,.18)}
.spine::before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(0,0,0,.28),rgba(255,255,255,.10) 28%,rgba(255,255,255,0) 55%,rgba(0,0,0,.30))}
.spine::after{content:"";position:absolute;left:0;right:0;top:44px;height:calc(100% - 96px);border-top:2px solid currentColor;border-bottom:2px solid currentColor;opacity:.35}
.sp-mark{position:relative;margin-top:16px;font:500 10px MonoX;letter-spacing:.04em;opacity:.75}
.sp-t{position:relative;flex:1;writing-mode:vertical-rl;display:flex;align-items:center;justify-content:center;padding:14px 0;
 font:900 calc(min(19px, var(--t) * .3)) / 1 InterX;text-transform:uppercase;letter-spacing:-.01em;text-align:center;max-height:calc(100% - 96px)}
.sp-n{position:relative;margin-bottom:16px;font:500 10px MonoX;opacity:.75}
.cover{left:var(--t);width:var(--cw);transform-origin:left center;transform:rotateY(90deg);background:var(--c);color:var(--ink);padding:18px 18px 16px;
 display:flex;flex-direction:column;border-radius:2px 6px 6px 2px;box-shadow:0 30px 60px rgba(10,10,20,.35)}
.cover::before{content:"";position:absolute;left:0;top:0;bottom:0;width:12px;background:linear-gradient(90deg,rgba(0,0,0,.35),rgba(0,0,0,0))}
.cover::after{content:"";position:absolute;inset:0;background:linear-gradient(160deg,rgba(255,255,255,.14),rgba(255,255,255,0) 40%)}
.cv-k{position:relative;font:500 10px MonoX;letter-spacing:.06em;text-transform:uppercase;opacity:.8}
.cv-t{position:relative;margin-top:12px;font:900 23px/.95 InterX;letter-spacing:-.04em;text-transform:uppercase}
.cv-plate{position:relative;margin-top:auto;aspect-ratio:16/9;border:3px solid currentColor;border-radius:6px;overflow:hidden;background:#fff}
.cv-plate img{width:100%;height:100%;object-fit:cover;display:block}
.cv-plate.empty{display:grid;place-items:center;background:transparent}
.cv-plate.empty b{font:400 44px SerifX;font-style:italic}
.cv-by{position:relative;margin-top:12px;font:500 10px MonoX;letter-spacing:.08em;text-transform:uppercase;opacity:.85}
.bookend{flex:none;align-self:flex-end;width:14px;height:170px;margin-left:8px;background:linear-gradient(90deg,#1b1714,#3a332c);border-radius:3px 3px 0 0;box-shadow:6px 0 12px rgba(0,0,0,.15)}
.plank{height:30px;margin:0 -30px;background:linear-gradient(#4a4037 0,#3a332c 8px,#2a2622 9px,#1d1a17 100%)}
.plaque{position:absolute;left:50%;bottom:6px;transform:translateX(-50%);display:flex;gap:10px;align-items:center;white-space:nowrap;padding:3px 12px;border-radius:4px;background:#efe9da;border:1px solid #b9ae94;box-shadow:inset 0 1px 0 #fff,0 1px 2px rgba(0,0,0,.4);font:500 11px MonoX;letter-spacing:.08em;text-transform:uppercase;color:#3a332c}
.plaque span{opacity:.6}
/* the book card */
.card{position:absolute;z-index:50;width:340px;padding:22px 22px 20px;background:#fff;border:3px solid var(--ink);border-radius:18px;
 box-shadow:0 30px 70px rgba(10,10,20,.25);opacity:0;transform:translateY(8px);transition:opacity .25s,transform .25s;pointer-events:none}
.card.show{opacity:1;transform:none;pointer-events:auto}
.x{display:none;place-items:center;position:absolute;top:8px;right:8px;width:34px;height:34px;border:0;border-radius:50%;background:var(--ink);color:#fff;font:600 20px/1 InterX;cursor:pointer;z-index:2}
.card-cover{display:none;width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:10px;border:2px solid var(--ink);margin-bottom:14px}
.card h3{font:800 22px/1.08 InterX;letter-spacing:-.03em;margin-top:8px}
.c-lead{font:500 11px MonoX;text-transform:uppercase;letter-spacing:.08em;color:var(--soft);margin-top:16px}
.card blockquote{font:400 23px/1.18 SerifX;font-style:italic;margin-top:6px;padding-left:14px;border-left:3px solid var(--claude)}
.c-sub{font:400 15px/1.4 InterX;color:var(--soft);margin-top:14px}
.card .btn{margin-top:18px}
/* sections */
.sec{padding:90px 0 0;scroll-margin-top:60px}
.sec-head h2{margin-top:8px;line-height:.9}
.sec-head .ser{display:block;font-size:36px;line-height:1}
.sec-head .big{display:block;font:900 72px/.9 InterX;letter-spacing:-.055em;text-transform:uppercase}
.reveal{opacity:0;transform:translateY(24px);transition:opacity .7s ease,transform .7s cubic-bezier(.2,.75,.2,1)}
.reveal.in{opacity:1;transform:none}
@media print{.reveal{opacity:1;transform:none}}
/* notes: catalogue cards */
.notes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px;margin-top:30px;align-items:start}
.note{position:relative;display:flex;flex-direction:column;gap:12px;padding:20px 20px 18px;text-decoration:none;border:3px solid var(--ink);border-radius:6px;
 background:repeating-linear-gradient(#fffdf7 0 27px,#dfe9f3 27px 28px);box-shadow:6px 6px 0 var(--ink);transition:transform .35s cubic-bezier(.2,.75,.2,1),box-shadow .35s}
.note::before{content:"";position:absolute;left:0;right:0;top:44px;height:2px;background:var(--cube);opacity:.7}
.note:nth-child(3n+1){transform:rotate(-1deg)}.note:nth-child(3n+2){transform:rotate(.8deg)}.note:nth-child(3n){transform:rotate(-.4deg)}
.note:hover{transform:translateY(-8px) rotate(0);box-shadow:10px 14px 0 var(--ink)}
.note-k{font:500 11px MonoX;letter-spacing:.08em;text-transform:uppercase;color:var(--soft);height:20px}
.note-b{font:400 16px/1.75 InterX;white-space:pre-line;margin-top:6px}
.note img{width:100%;aspect-ratio:16/10;object-fit:cover;border:2px solid var(--ink);border-radius:6px;background:#fff}
.note-go{font:700 14px InterX;color:var(--cube)}
/* reading list: face-out covers on a ledge */
.ledge-wrap{position:relative;margin-top:30px;border:16px solid var(--wall);border-bottom:0;border-radius:12px 12px 0 0;
 background:linear-gradient(180deg,#dcd8cc,#e6e2d8 40%,#d6d1c3);box-shadow:inset 0 22px 26px -18px rgba(0,0,0,.45)}
.ledge-wrap .plank{margin:0}
.ledge{display:flex;gap:16px;padding:46px 26px 0;overflow-x:auto;scroll-snap-type:x proximity;scrollbar-width:thin;perspective:1200px}
.pick{flex:none;width:140px;height:212px;display:flex;flex-direction:column;padding:12px 12px 10px;text-decoration:none;scroll-snap-align:start;background:#fff;
 border-radius:3px 7px 7px 3px;box-shadow:inset 6px 0 0 rgba(0,0,0,.07),0 14px 22px -12px rgba(0,0,0,.5);transform-origin:50% 100%;transition:transform .45s cubic-bezier(.2,.75,.2,1),box-shadow .45s}
.pick:hover,.pick:focus-visible{transform:rotateX(10deg) translateY(-12px);box-shadow:inset 6px 0 0 rgba(0,0,0,.07),0 28px 30px -14px rgba(0,0,0,.55);outline:none}
.pick-art{position:relative;display:block;width:52px;height:52px;border-radius:10px;overflow:hidden;border:2px solid var(--ink)}
.pick-art img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block;background:#fff}
.pick-art b{display:grid;place-items:center;width:100%;height:100%;color:#fff;font:900 18px InterX;letter-spacing:-.03em}
.pick-n{font:800 14px/1.12 InterX;letter-spacing:-.02em;margin-top:10px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.pick-a{font:500 9.5px MonoX;letter-spacing:.06em;text-transform:uppercase;color:var(--soft);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pick-h{font:400 14px/1.15 SerifX;font-style:italic;color:var(--soft);margin-top:auto;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
/* about + library card */
.about{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,.9fr);gap:56px;margin-top:30px;align-items:start}
.about-t p{font:400 19px/1.6 InterX;margin-bottom:16px;max-width:620px}
.about-t p:first-child{font:400 28px/1.25 SerifX;font-style:italic}
.about-links{display:flex;flex-wrap:wrap;gap:12px 24px;margin-top:6px}
.about-links a{font:700 15px InterX;color:var(--cube);text-decoration:none}
.about-links a:hover{text-decoration:underline}
.lcard{position:relative;padding:22px 24px 24px;border:3px solid var(--ink);border-radius:16px;background:#fbf8f1;box-shadow:8px 8px 0 var(--sweater);transform:rotate(1.5deg);transition:transform .4s cubic-bezier(.2,.75,.2,1)}
.lcard:hover{transform:rotate(0) translateY(-4px)}
.lcard::before{content:"";position:absolute;left:0;right:0;top:52px;height:3px;background:repeating-linear-gradient(90deg,var(--cube) 0 14px,transparent 14px 22px)}
.lc-top{display:flex;justify-content:space-between;font:500 11px MonoX;letter-spacing:.1em;text-transform:uppercase;color:var(--soft)}
.lc-name{font:900 40px InterX;letter-spacing:-.05em;margin-top:30px}
.lc-name em{font:400 44px SerifX;font-style:italic;letter-spacing:0;margin-left:6px}
.lc-subs{font:500 12px MonoX;color:var(--sweater);margin-top:4px}
.lc-row{display:flex;justify-content:space-between;gap:16px;padding:12px 0;border-bottom:1px dashed #b9ae94;font:400 15px InterX}
.lc-row b{font-weight:700;text-align:right}
.lcard .btn{width:100%;margin-top:18px}
/* sponsor */
.sponsor{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,.7fr);gap:40px;align-items:center}
.sp-l p{font:400 26px/1.25 SerifX;font-style:italic;color:var(--soft);margin-top:18px;max-width:560px}
.sp-row{display:flex;flex-wrap:wrap;gap:12px;margin-top:24px}
.sp-r{position:relative;height:260px;perspective:900px}
.m0{--s:96px;--d:3.4s;left:50%;top:70px;margin-left:-48px}
.m1{--s:52px;--d:4.1s;left:12%;top:24px;animation-delay:-1s}
.m2{--s:46px;--d:3.7s;right:10%;top:40px;animation-delay:-2s}
.m3{--s:42px;--d:4.6s;left:14%;bottom:34px;animation-delay:-.5s}
.you .cube i{background:#fff;box-shadow:none;outline:3px dashed var(--ink);outline-offset:-3px;backface-visibility:hidden;-webkit-backface-visibility:hidden}
.you .cube i::before{content:"YOUR LOGO";inset:0;display:grid;place-items:center;background:none;-webkit-mask:none;mask:none;font:500 12px MonoX;letter-spacing:.06em;color:var(--ink)}
.you-tag{position:absolute;left:50%;bottom:-18px;transform:translateX(-50%);white-space:nowrap;font:500 12px MonoX;letter-spacing:.06em;text-transform:uppercase;color:var(--soft)}
/* footer */
.foot{padding:40px 0 70px;display:flex;gap:28px;align-items:center;flex-wrap:wrap;border-top:2px solid var(--rule);margin-top:90px}
.foot p{font:400 26px/1.2 SerifX;font-style:italic;max-width:620px}
.foot small{display:block;width:100%;font:500 12px MonoX;color:var(--soft)}
/* plans panel */
.paywall{margin:auto;width:min(1040px,calc(100vw - 32px));max-height:calc(100vh - 32px);padding:34px 34px 26px;border:3px solid var(--ink);border-radius:22px;
 background:var(--paper);color:var(--ink);box-shadow:0 40px 120px rgba(0,0,0,.45);overflow:auto}
.paywall::backdrop{background:rgba(8,8,8,.55);backdrop-filter:blur(5px);-webkit-backdrop-filter:blur(5px)}
.paywall[open]{animation:pw-in .35s cubic-bezier(.2,.75,.2,1)}
@keyframes pw-in{from{opacity:0;transform:translateY(18px) scale(.98)}to{opacity:1;transform:none}}
.pw-x{display:grid;top:14px;right:14px}
.pw-head h2{margin-top:6px;line-height:.9}
.pw-head .ser{display:block;font-size:34px}
.pw-head .big{display:block;font:900 64px/.9 InterX;letter-spacing:-.055em;text-transform:uppercase}
.pw-subs{font:500 13px MonoX;color:var(--sweater);margin-top:10px}
.pw-toggle{display:inline-flex;align-items:center;gap:4px;margin-top:20px;padding:4px;border:3px solid var(--ink);border-radius:12px;background:#fff}
.pw-toggle button{border:0;background:none;padding:8px 14px;border-radius:8px;font:700 14px InterX;cursor:pointer;color:var(--ink)}
.pw-toggle button.on{background:var(--ink);color:#fff}
.pw-save{font:500 12px MonoX;color:var(--green);padding:0 10px}
.plans{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-top:24px}
.plan{position:relative;display:flex;flex-direction:column;padding:22px;border:3px solid var(--ink);border-radius:16px;background:#fff;opacity:0;animation:pl-in .5s cubic-bezier(.2,.75,.2,1) forwards}
.plan:nth-child(2){animation-delay:.07s}.plan:nth-child(3){animation-delay:.14s}
@keyframes pl-in{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
.plan.pro{background:var(--ink);color:#fff;box-shadow:8px 8px 0 var(--cube)}
.pl-tag{position:absolute;top:-14px;right:18px;padding:4px 12px;border-radius:999px;background:var(--cube);color:#fff;font:700 12px MonoX;letter-spacing:.08em;text-transform:uppercase}
.pl-k{font:500 12px MonoX;letter-spacing:.1em;text-transform:uppercase;opacity:.7}
.pl-p{font:900 44px InterX;letter-spacing:-.04em;margin-top:6px}
.pl-p small{font:500 14px MonoX;letter-spacing:0;opacity:.7}
.plan ul{list-style:none;margin:16px 0 20px;display:grid;gap:10px}
.plan li{position:relative;padding-left:24px;font:400 15px/1.4 InterX}
.plan li::before{content:"";position:absolute;left:0;top:5px;width:12px;height:12px;border-radius:3px;background:var(--cube)}
.plan .btn{margin-top:auto}
.plan.pro .btn{background:var(--cube);border-color:var(--cube)}
.plan.pro .btn:hover{background:#fff;color:var(--ink)!important;border-color:#fff}
.pw-fine{font:500 11px MonoX;color:var(--soft);margin-top:18px;text-align:center}
@media (hover:none),(max-width:820px){
 .on-mouse{display:none}
 .on-touch{display:inline}
 .slot.on .book{transform:translateY(-22px)}
 .slot:not(.on) .book:hover{transform:none}
 .card{position:fixed;left:12px;right:12px;bottom:12px;top:auto!important;width:auto;max-width:520px;margin:0 auto;max-height:80vh;overflow:auto}
 .card-cover{display:block;aspect-ratio:auto;height:120px}
 .card{padding:16px 16px 14px}
 .card h3{font-size:19px}
 .card blockquote{font-size:19px}
 .c-sub{display:none}
 .card .btn{margin-top:14px}
 .x{display:grid}
}
@media (max-width:980px){
 .top nav a.opt{display:none}
}
@media (max-width:820px){
 .wrap{padding:0 16px}
 .top{margin:0 -16px;padding-inline:16px}
 .top nav a:not(.btn){display:none}
 .intro{display:block;padding-top:20px}
 .intro-r{margin-top:16px}
 .portrait{width:min(250px,70vw);margin:40px auto 34px}
 .intro h1 .ser{font-size:34px}
 .intro h1 .big{font-size:68px}
 .intro p{font-size:21px}
 .case{grid-template-columns:1fr;border-width:10px}
 .shelf{border-right:0;padding:40px 0 0}
 .plank{margin:0}
 .row{overflow-x:auto;overflow-y:hidden;padding:30px 16px 0;min-height:420px;scroll-snap-type:x proximity;scrollbar-width:none}
 .row::-webkit-scrollbar{display:none}
 .slot{scroll-snap-align:center}
 .sec{padding-top:64px}
 .sec-head .ser{font-size:28px}
 .sec-head .big{font-size:48px}
 .notes{grid-template-columns:1fr}
 .ledge-wrap{border-width:10px}
 .ledge{padding:34px 16px 0}
 .about,.sponsor{grid-template-columns:1fr;gap:34px}
 .about-t p:first-child{font-size:24px}
 .sp-r{height:200px}
 .paywall{padding:26px 18px 20px}
 .pw-head .big{font-size:42px}
 .plans{grid-template-columns:1fr}
}
@media (prefers-reduced-motion:reduce){
 html{scroll-behavior:auto}
 .cw,.cube,.plan,.paywall[open]{animation:none}
 .plan{opacity:1}
 .cube{transform:rotateX(-22deg) rotateY(35deg)}
 .book,.card,.slot::after,.reveal{transition:none}
 .reveal{opacity:1;transform:none}
 .slot.on .book{transform:translateY(-16px)}
 .card-cover{display:block}
}
"""

JS = r"""
const D = JSON.parse(document.getElementById("data").textContent);
const card = document.getElementById("card"), CW = %CW%;
const $ = (id) => document.getElementById(id);
const touch = () => matchMedia("(hover: none), (max-width: 820px)").matches;
let cur = null, hideT = null;
function fill(b) {
  $("c-meta").textContent = `${b.access} · ${b.mins} min read · ${b.date}`;
  $("c-title").textContent = b.title;
  $("c-quote").textContent = b.quote ? `“${b.quote}”` : "";
  $("c-lead").hidden = !b.quote;
  $("c-sub").textContent = b.subtitle;
  $("c-go").href = b.url;
  const img = $("c-cover");
  if (b.cover) { img.src = b.cover; img.hidden = false; } else { img.hidden = true; }
}
function place(slot) {
  if (touch()) { card.style.left = card.style.top = ""; return; }
  const r = slot.getBoundingClientRect(), half = CW * 1.13 / 2 + 22, cx = r.left + r.width / 2, w = card.offsetWidth;
  let left = cx + half;
  if (left + w > innerWidth - 16) left = cx - half - w;
  if (left < 16) left = Math.max(16, innerWidth - w - 16);
  card.style.left = `${left + scrollX}px`;
  card.style.top = `${r.top + scrollY - 70}px`;
}
function open(book) {
  clearTimeout(hideT);
  const slot = book.parentElement;
  if (cur && cur !== slot) cur.classList.remove("on");
  cur = slot; slot.classList.add("on");
  fill(D[book.dataset.i]);
  card.hidden = false; place(slot);
  requestAnimationFrame(() => card.classList.add("show"));
}
function close() {
  if (cur) cur.classList.remove("on");
  cur = null; card.classList.remove("show");
}
const later = () => { clearTimeout(hideT); hideT = setTimeout(close, 200); };
// A tap focuses the book before its click fires, so remember whether it was already out when the finger went down:
// first tap pulls it out, second tap opens the post.
let pointer = false, wasOut = false;
document.querySelectorAll(".book").forEach((book) => {
  book.addEventListener("pointerdown", () => { pointer = true; wasOut = cur === book.parentElement; setTimeout(() => (pointer = false), 600); });
  book.addEventListener("mouseenter", () => { if (!touch()) open(book); });
  book.addEventListener("mouseleave", () => { if (!touch()) later(); });
  book.addEventListener("focus", () => { if (!pointer) open(book); });
  book.addEventListener("click", (e) => { if (touch() && !wasOut) { e.preventDefault(); open(book); } wasOut = true; });
});
card.addEventListener("mouseenter", () => clearTimeout(hideT));
card.addEventListener("mouseleave", () => { if (!touch()) later(); });
document.addEventListener("focusout", (e) => { if (!card.contains(e.relatedTarget) && !(e.relatedTarget && e.relatedTarget.classList.contains("book"))) later(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
$("c-x").addEventListener("click", close);
document.addEventListener("click", (e) => { if (cur && !card.contains(e.target) && !e.target.closest(".book")) close(); });
addEventListener("resize", () => { if (cur) place(cur); });

// Plans panel: every Subscribe button opens it; without JS the buttons still go to Substack's own subscribe page.
const pw = $("paywall");
document.querySelectorAll("[data-sub]").forEach((a) => a.addEventListener("click", (e) => {
  if (!pw || !pw.showModal) return;
  e.preventDefault(); close(); pw.showModal();
}));
pw.addEventListener("click", (e) => { if (e.target === pw || e.target.closest("[data-close]")) pw.close(); });
pw.querySelectorAll("[data-bill]").forEach((b) => b.addEventListener("click", () => {
  pw.querySelectorAll("[data-bill]").forEach((x) => x.classList.toggle("on", x === b));
  pw.querySelectorAll("[data-price]").forEach((p) => (p.hidden = p.dataset.price !== b.dataset.bill));
}));

// Sections rise in as they scroll into view; the header gets a hairline once the page moves.
const io = new IntersectionObserver((es) => es.forEach((en) => { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } }), { rootMargin: "0px 0px -10% 0px" });
// Only hide what is still below the fold, so nothing can stay invisible if the observer never fires.
document.querySelectorAll(".sec").forEach((s) => { if (s.getBoundingClientRect().top > innerHeight) { s.classList.add("reveal"); io.observe(s); } });
const bar = document.querySelector(".top");  // not "top": that name is taken by window.top
addEventListener("scroll", () => bar.classList.toggle("scrolled", scrollY > 8), { passive: true });
"""

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Library · %NAME%</title>
<meta name="description" content="Every post from %NAME% by %AUTHOR%, on one bookshelf.">
<meta property="og:title" content="The Library · %NAME%"><meta property="og:description" content="Every post from %NAME% by %AUTHOR%, on one bookshelf.">
<style>%CSS%</style></head>
<body><div class="wrap">
<header class="top"><a class="brand" href="#">AI <em>simplified</em></a>
<nav><a href="#shelves">Library</a><a href="#notes" class="opt">Notes</a><a href="#reading" class="opt">Reading list</a><a href="#about">About</a><a href="#sponsor">Sponsor</a><a class="btn" href="%PUB%/subscribe" data-sub>Subscribe</a></nav></header>
<section class="intro"><div class="intro-l"><div class="k">%NAME% · by %AUTHOR%</div>
<h1><span class="ser">the</span><span class="big">Library.</span></h1>
<div class="intro-r"><p><span class="on-mouse">Hover a book to pull it off the shelf. Click it to read.</span><span class="on-touch">Tap a book to pull it off the shelf. Tap again to read.</span></p><div class="tally">%TALLY%</div></div></div>
%PORTRAIT%</section>
<main class="case" id="shelves">%SHELVES%</main>
%NOTES%
%RECS%
%ABOUT%
%SPONSOR%
<footer class="foot"><p>%HERO%</p><a class="btn" href="%PUB%/subscribe" data-sub>Subscribe →</a>
<small>Built from Substack every hour. Every quote on a book is copied word for word from the post.</small></footer>
</div>
<aside id="card" class="card" hidden aria-live="polite"><button class="x" id="c-x" type="button" aria-label="Close">×</button><img id="c-cover" class="card-cover" alt="">
<div class="k" id="c-meta"></div><h3 id="c-title"></h3><p class="c-lead" id="c-lead">From the post</p>
<blockquote id="c-quote"></blockquote><p class="c-sub" id="c-sub"></p><a id="c-go" class="btn" href="#">Read on Substack →</a></aside>
%PAYWALL%
<script id="data" type="application/json">%DATA%</script>
<script>%JS%</script>
</body></html>
"""

if __name__ == "__main__":
    data = json.loads((L / "data.json").read_text()) if "--offline" in sys.argv else fetch()
    bs = books(data)
    (L / "index.html").write_text(page(bs, data))
    for b in bs:
        print(f'No.{b["n"]:<2} {b["shelf"]:<7} {b["access"]:<16} {b["spine"]}  |  {b["quote"][:70]}')
    pub = data.get("pub") or {}
    print(f'pub: {pub.get("subscribers")} · ${pub.get("monthly")}/mo · ${pub.get("yearly")}/yr · founding ${pub.get("founding")} | '
          f'{len(data.get("notes") or [])} notes · {len(data.get("recs") or [])} recommendations')
    print("wrote", L / "index.html")

"""
scanner.py — يمسح أسهم EGX30 ويرسل أفضل فرص الشراء والخروج على Telegram.

التشغيل:
    python scanner.py            # يحلل ويرسل ويحفظ
    python scanner.py --dry      # يعرض النتيجة على الشاشة فقط (بدون إرسال/حفظ/قراءة أوامر)
    python scanner.py --sync     # يقرأ أوامر Telegram وينفذها فقط (بدون تحليل)
    python scanner.py --auto     # للتشغيل التلقائي: يختار الرسالة المناسبة حسب الوقت (جلسة 1، جلسة 2، بعد الإغلاق)
    python scanner.py --live     # يجبر وضع "أثناء الجلسة" (سعر اليوم الحالي بدل آخر إغلاق)
    python scanner.py --no-live  # يجبر وضع آخر إغلاق حتى لو السوق مفتوح
    python scanner.py --no-news  # بدون تحليل الأخبار

أوامر Telegram (ابعتها للبوت):
    رصيد 80000 | محفظتي COMI ETEL | محفظتي مفيش | حالة
"""
import html
import json
import re
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

import config
import news
import strategy as st

warnings.filterwarnings("ignore")

MAX_AGE_DAYS = 5            # نتجاهل السهم لو آخر سعر أقدم من كده
TOP_N = 10                  # عدد الفرص في كل قائمة
# رسايل التشغيل التلقائي (بتوقيت القاهرة): الاسم، من، إلى، هل هي أثناء الجلسة؟
# النوافذ واسعة عمداً لتغطية تأخر GitHub والتوقيت الصيفي/الشتوي، وكل رسالة بتتبعت مرة واحدة في اليوم.
SLOTS = [
    ("live1", (10, 45), (12, 45), True),    # رسالة أثناء الجلسة (~11:30)
    ("live2", (12, 45), (14, 45), True),    # رسالة أثناء الجلسة (~13:30)
    ("close", (15, 0), (18, 0), False),     # بعد الإغلاق بأسعار الإغلاق النهائية
]
SESSION_START, SESSION_END = (10, 0), (14, 45)
WORKDAYS = (6, 0, 1, 2, 3)                  # الأحد=6 ... الخميس=3 (weekday في بايثون)
SETTINGS_FILE = Path(__file__).with_name("settings.json")
DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")


# ───────────────────────── الإعدادات المحفوظة (Supabase + ملف محلي) ─────────────────────────
def _supabase():
    if not (config.SUPABASE_URL and config.SUPABASE_KEY):
        return None
    from supabase import create_client
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


def load_settings() -> dict:
    s = {"capital": config.CAPITAL_EGP, "holdings": [], "last_update_id": 0, "last_runs": {}}
    loaded = None
    try:
        client = _supabase()
        if client:
            res = client.table("settings").select("value").eq("key", "main").execute()
            if res.data:
                loaded = res.data[0]["value"]
    except Exception as e:
        print(f"تعذر قراءة الإعدادات من Supabase ({type(e).__name__}). هل أنشأت جدول settings؟")
    if loaded is None:
        try:
            loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            loaded = None
    if loaded:
        s.update(loaded)
    return s


def save_settings(s: dict) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    try:
        client = _supabase()
        if client:
            client.table("settings").upsert({"key": "main", "value": s}).execute()
    except Exception as e:
        print(f"تعذر حفظ الإعدادات في Supabase ({type(e).__name__})")


# ───────────────────────── أوامر Telegram ─────────────────────────
def parse_command(text: str, settings: dict) -> str | None:
    t = text.strip().translate(DIGITS)
    m = re.match(r"^/?(capital|رصيد)\s+([\d.,]+)\s*(?:جنيه|ج|egp)?$", t, re.I)
    if m:
        try:
            value = float(m.group(2).replace(",", ""))
        except ValueError:
            return "⚠️ ما فهمتش الرقم. مثال: رصيد 80000"
        if not 1_000 <= value <= 1_000_000_000:
            return "⚠️ الرقم غير منطقي (لازم بين 1,000 و1,000,000,000)."
        settings["capital"] = value
        return f"✅ تم تحديث رأس المال إلى {value:,.0f} جنيه"

    m = re.match(r"^/?(holdings?|hold|محفظتي|اسهمي|أسهمي)\s*(.*)$", t, re.I)
    if m:
        raw = m.group(2).strip()
        if not raw:
            cur = ", ".join(settings["holdings"]) or "لا يوجد"
            return f"📦 أسهمك الحالية: {cur}\nلتغييرها: محفظتي COMI ETEL"
        if raw.lower() in ("none", "0") or raw in ("مفيش", "فاضي", "لا شيء"):
            settings["holdings"] = []
            return "✅ تم مسح قائمة الأسهم"
        names = [x.upper().removesuffix(".CA") for x in re.split(r"[,\s،]+", raw) if x]
        bad = [n for n in names if not re.fullmatch(r"[A-Z0-9]{2,6}", n)]
        if bad:
            return f"⚠️ رموز غير صالحة: {', '.join(bad)}. مثال: محفظتي COMI ETEL"
        settings["holdings"] = names
        return f"✅ أسهمك الآن: {', '.join(names)}"

    if re.match(r"^/?(status|حالة|الحالة)$", t, re.I):
        cur = ", ".join(settings["holdings"]) or "لا يوجد"
        return f"⚙️ رأس المال: {settings['capital']:,.0f} ج\n📦 أسهمك: {cur}"
    return None


def sync_commands(settings: dict) -> list[str]:
    replies = []
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"offset": settings["last_update_id"] + 1, "timeout": 0,
                    "allowed_updates": json.dumps(["message"])}, timeout=15)
        if not r.ok:
            print(f"تعذر قراءة أوامر Telegram ({r.status_code})")
            return replies
        for upd in r.json().get("result", []):
            settings["last_update_id"] = max(settings["last_update_id"], upd["update_id"])
            msg = upd.get("message", {})
            if str(msg.get("chat", {}).get("id")) != str(config.TELEGRAM_CHAT_ID):
                continue                       # نتجاهل أي حد غيرك
            reply = parse_command(msg.get("text", ""), settings)
            if reply:
                replies.append(reply)
    except Exception as e:
        print(f"تعذر قراءة أوامر Telegram ({type(e).__name__})")
    return replies


# ───────────────────────── التوقيت والتشغيل التلقائي ─────────────────────────
def cairo_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Africa/Cairo"))
    except Exception:                       # على ويندوز: pip install tzdata
        return None


def in_session(now) -> bool:
    return bool(now and now.weekday() in WORKDAYS and SESSION_START <= (now.hour, now.minute) < SESSION_END)


def should_run_now(settings: dict, now=None):
    """يرجع (هل نشتغل؟، السبب، اسم الرسالة، هل هي أثناء الجلسة؟)."""
    now = now or cairo_now()
    if now is None:
        return False, "تعذر تحديد توقيت القاهرة (ثبّت tzdata)", None, False
    if now.weekday() not in WORKDAYS:
        return False, "اليوم إجازة البورصة (جمعة/سبت)", None, False
    today, runs = now.date().isoformat(), settings.get("last_runs", {})
    for name, a, b, live in SLOTS:
        if a <= (now.hour, now.minute) < b:
            if runs.get(name) == today:
                return False, f"رسالة ({name}) اتبعتت اليوم بالفعل", None, False
            return True, "", name, live
    return False, f"خارج نوافذ التشغيل (الوقت الآن {now:%H:%M} بتوقيت القاهرة)", None, False


# ───────────────────────── التحليل ─────────────────────────
def analyze(name: str, df: pd.DataFrame, capital: float, live_ts=None) -> dict | None:
    if df is None or len(df) < 60:
        return None
    df = st.add_indicators(df.copy())
    last = df.iloc[-1]
    if last[["SMA50", "RSI", "ATR", "MACD_HIST", "TURNOVER"]].isna().any():
        return None

    price, rsi, atr = float(last["Close"]), float(last["RSI"]), float(last["ATR"])
    sma20, sma50, hist = float(last["SMA20"]), float(last["SMA50"]), float(last["MACD_HIST"])
    action, score = st.decide(price, sma20, sma50, hist, rsi)
    stop, target = st.stop_target(price, atr)
    shares = st.position_size(capital, config.RISK_PCT, price, stop)

    reasons = ["السعر فوق متوسط 50 يوم" if price > sma50 else "السعر تحت متوسط 50 يوم",
               "MACD إيجابي" if hist > 0 else "MACD سلبي", f"RSI = {rsi:.0f}"]
    return {
        "symbol": name, "action": action, "score": score, "price": round(price, 2),
        "stop": round(stop, 2), "target": round(target, 2), "shares": shares,
        "risk": round(shares * (price - stop), 0), "value": round(shares * price, 0),
        "rsi": round(rsi, 1),
        # أثناء الجلسة حجم اليوم ناقص، فنقيس السيولة من آخر 20 يوم مكتمل
        "turnover": float(df["TURNOVER"].iloc[-2] if live_ts is not None else last["TURNOVER"]),
        "reason": "، ".join(reasons), "last_date": df.index[-1], "live_time": live_ts,
    }


def _with_live_bar(name: str, df: pd.DataFrame):
    """يضيف شمعة اليوم الحالية (من بيانات 5 دقايق) لو Yahoo بيدّيها. يرجع (df، وقت آخر تحديث أو None)."""
    try:
        intra = yf.Ticker(f"{name}.CA").history(period="1d", interval="5m")
        if intra is None or intra.empty:
            return df, None
        if intra.index.tz is not None:
            intra.index = intra.index.tz_convert("Africa/Cairo").tz_localize(None)
        now = cairo_now()
        today = intra.index[-1].normalize()
        if now is None or today != pd.Timestamp(now.date()):
            return df, None                              # مفيش بيانات النهارده (إجازة/لسه ما بدأش)
        bar = pd.DataFrame({"Open": [intra["Open"].iloc[0]], "High": [intra["High"].max()],
                            "Low": [intra["Low"].min()], "Close": [intra["Close"].iloc[-1]],
                            "Volume": [intra["Volume"].sum()]}, index=[today])
        return pd.concat([df[df.index < today], bar]), intra.index[-1]
    except Exception:
        return df, None


def fetch(name: str, live: bool = False):
    """يرجع (DataFrame يومي، وقت آخر سعر لحظي أو None)."""
    try:
        df = yf.Ticker(f"{name}.CA").history(period="1y", interval="1d")
    except Exception:
        return None, None
    if df is None or df.empty:
        return None, None
    df.index = df.index.tz_localize(None)
    return _with_live_bar(name, df) if live else (df, None)


def scan(capital: float, holdings: list[str], live: bool = False):
    results, skipped = [], []
    for name in st.WATCHLIST + [h for h in holdings if h not in st.WATCHLIST]:
        df, live_ts = fetch(name, live)
        res = analyze(name, df, capital, live_ts) if df is not None else None
        if res is None:
            skipped.append(name)
        elif (datetime.now() - res["last_date"].to_pydatetime()).days > MAX_AGE_DAYS:
            skipped.append(name)
        elif res["turnover"] < st.MIN_TURNOVER_EGP and name not in holdings:
            skipped.append(name)
        else:
            results.append(res)
        time.sleep(0.3)
    return results, skipped


# ───────────────────────── الرسالة ─────────────────────────
def top_buys(results):
    return sorted([r for r in results if r["action"] == "BUY"],
                  key=lambda r: (-r["score"], -r["turnover"]))[:TOP_N]


def build_message(results, skipped, capital, holdings, news_map=None, live=False) -> str:
    news_map = news_map or {}
    buys = top_buys(results)
    sells = sorted([r for r in results if r["action"] == "SELL"],
                   key=lambda r: (r["symbol"] not in holdings, -r["turnover"]))[:TOP_N]
    date = max((r["last_date"] for r in results), default=datetime.now()).date()

    live_times = [r["live_time"] for r in results if r.get("live_time") is not None]
    if live and live_times:
        lines = [f"📊 <b>ماسح EGX30 — أثناء الجلسة</b>",
                 f"🕒 آخر تحديث للأسعار {max(live_times):%H:%M} (متأخرة حوالي 20 دقيقة حسب Yahoo) — "
                 f"{len(live_times)} من {len(results)} سهم بأسعار اليوم"]
    elif live:
        lines = [f"📊 <b>ماسح EGX30 — أثناء الجلسة</b>",
                 f"⚠️ تعذر جلب أسعار اليوم، الأسعار الظاهرة آخر إغلاق ({date})"]
    else:
        lines = [f"📊 <b>ماسح EGX30 — بيانات إغلاق {date}</b>"]
    lines += [f"تم تحليل {len(results)} سهم" + (f" (تخطي {len(skipped)})" if skipped else ""),
              f"💰 رأس المال المحسوب عليه: {capital:,.0f} ج", "", "🟢 <b>فرص شراء</b>"]
    if buys:
        for i, r in enumerate(buys, 1):
            lines += [f"{i}) <b>{html.escape(r['symbol'])}</b> — سعر {r['price']}",
                      f"   🛑 وقف {r['stop']} | 🎯 هدف {r['target']}",
                      f"   📦 {r['shares']} سهم (≈ {r['value']:,.0f} ج) | ⚠️ مخاطرة {r['risk']:,.0f} ج",
                      f"   📝 {html.escape(r['reason'])}"]
            n = news_map.get(r["symbol"])
            if n:
                warn = " — راجع الخبر قبل الشراء!" if n["verdict"] == "سلبي" else ""
                lines.append(f"   📰 {news.EMOJI.get(n['verdict'], '')} <b>{n['verdict']}</b>: "
                             f"{html.escape(n['line'])}{warn}")
    else:
        lines.append("لا توجد فرصة شراء واضحة الآن. الانتظار أفضل.")

    lines += ["", "🔴 <b>إشارات ضعف / خروج</b> (اخرج لو شايل السهم)"]
    if sells:
        for r in sells:
            mine = " ⭐ في محفظتك" if r["symbol"] in holdings else ""
            lines.append(f"• <b>{html.escape(r['symbol'])}</b> — سعر {r['price']}{mine}"
                         f"\n   📝 {html.escape(r['reason'])}")
    else:
        lines.append("لا توجد إشارات خروج.")

    mine_ok = [r["symbol"] for r in results if r["symbol"] in holdings and r["action"] != "SELL"]
    if mine_ok:
        lines += ["", f"✅ أسهمك بدون إشارة خروج: {', '.join(mine_ok)}"]
    holds = [r["symbol"] for r in results if r["action"] == "HOLD"]
    lines += ["", f"🟡 <b>محايد/احتفاظ ({len(holds)}):</b> {', '.join(holds) or 'لا يوجد'}"]
    if skipped:
        lines.append(f"⚪ <b>تم تخطيها (بيانات ناقصة أو سيولة ضعيفة):</b> {', '.join(skipped)}")
    if live:
        lines += ["", "<i>أسعار أثناء الجلسة والمؤشرات مبنية على سعر اليوم الحالي، وممكن تتغير الإشارة عند الإغلاق. "
                      "تأكد من السعر على Thndr قبل التنفيذ. للاستئناس فقط وليست نصيحة استثمارية.</i>"]
    else:
        lines += ["", "<i>الأسعار آخر إغلاق وليست لحظية. توصية آلية للاستئناس فقط وليست نصيحة استثمارية.</i>"]
    return "\n".join(lines)


# ───────────────────────── الإرسال والحفظ ─────────────────────────
def send_telegram(text: str) -> None:
    chunks, cur = [], ""
    for line in text.split("\n"):                       # حد Telegram ~4096 حرف: نقسم عند السطور
        if cur and len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    for ch in chunks:
        r = requests.post(url, json={"chat_id": config.TELEGRAM_CHAT_ID, "text": ch,
                                     "parse_mode": "HTML"}, timeout=15)
        if not r.ok:                                    # نعرض رد Telegram بدون الرابط (فيه التوكن)
            raise RuntimeError(f"{r.status_code}: {r.text}")


def save_signals(results: list[dict]) -> int:
    client = _supabase()
    if not client:
        return 0
    rows = [{"symbol": f"{r['symbol']}.CA", "action": r["action"], "entry_price": r["price"],
             "stop_loss": r["stop"], "target_price": r["target"], "shares": r["shares"],
             "risk_amount": r["risk"], "reasoning": r["reason"]}
            for r in results if r["action"] in ("BUY", "SELL")]
    if rows:
        client.table("signals").insert(rows).execute()
    return len(rows)


def main() -> None:
    args = set(sys.argv[1:])
    dry = "--dry" in args
    settings = load_settings()
    replies: list[str] = []

    if not dry:                                         # ننفذ أوامرك من Telegram
        replies = sync_commands(settings)
        save_settings(settings)
        if replies:
            try:
                send_telegram("\n".join(replies))
            except Exception as e:
                print(f"Telegram (تأكيد الأوامر): فشل ❌ ({e})")
    if "--sync" in args:
        print(f"تمت المزامنة ({len(replies)} أمر).")
        return
    slot, now = None, cairo_now()
    if "--auto" in args:
        ok, why, slot, live = should_run_now(settings)
        if not ok:
            print(f"تخطي: {why}")
            return
    else:                                               # تشغيل يدوي: نختار الوضع حسب الوقت
        live = "--live" in args or (in_session(now) and "--no-live" not in args)

    capital, holdings = settings["capital"], settings["holdings"]
    print(f"⏳ جاري تحليل السوق ({'أثناء الجلسة' if live else 'آخر إغلاق'})... (رأس المال {capital:,.0f} ج)")
    results, skipped = scan(capital, holdings, live)
    if not results:
        sys.exit("لم يتم جلب أي بيانات. تأكد من الإنترنت أو من رموز الأسهم.")

    news_map = {}
    if "--no-news" not in args:
        news_map = news.news_for([r["symbol"] for r in top_buys(results)])
    message = build_message(results, skipped, capital, holdings, news_map, live)

    if dry:
        print(re.sub(r"</?[a-z]+>", "", message))
        return
    if not live:                                        # إشارات أثناء الجلسة مؤقتة: منسجلهاش في قاعدة البيانات
        try:
            print(f"Supabase: تم حفظ {save_signals(results)} إشارة ✅")
        except Exception as e:
            print(f"Supabase: فشل ❌ ({e})")
    try:
        send_telegram(message)
        print("Telegram: تم الإرسال ✅")
        done = slot or ("close" if (not live and now and now.hour >= 15) else None)
        if done:
            settings.setdefault("last_runs", {})[done] = (now or datetime.now()).date().isoformat()
            save_settings(settings)
    except Exception as e:
        print(f"Telegram: فشل ❌ ({e})")
        sys.exit(1)


if __name__ == "__main__":
    main()

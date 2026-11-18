"""
backtest.py — يختبر الاستراتيجية على الماضي بنفس قواعد الماسح بالظبط.

التشغيل:
    python backtest.py                 # آخر 5 سنين، رأس مال 50,000
    python backtest.py --years 3 --capital 100000
    python backtest.py --send          # يبعت الملخص على Telegram كمان

طريقة المحاكاة (بدون "غش" بالمستقبل):
  • الإشارة بتطلع من إغلاق اليوم، والدخول بيتم على سعر **افتتاح اليوم التالي**.
  • وقف الخسارة والهدف بيتحسبوا من إغلاق يوم الإشارة (نفس الماسح).
  • لو الوقف والهدف اتلمسوا في نفس اليوم، بنفترض إن الوقف حصل الأول (تحفظ).
  • الخروج: وقف / هدف / إشارة خروج (على افتتاح اليوم التالي) / انتهاء المدة.
  • محفظة واحدة: مخاطرة 2% من الرصيد الحالي، وحد أقصى 5 أسهم مفتوحة في نفس الوقت.
"""
import argparse
import math
import sys
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

import config
import strategy as st

warnings.filterwarnings("ignore")

FEE_PER_SIDE = 0.0025   # 0.25% لكل عملية (عمولة + رسوم + انزلاق). افتراض، عدّله حسب رسوم Thndr الفعلية
MAX_POSITIONS = 5       # أقصى عدد صفقات مفتوحة معاً
MAX_HOLD_DAYS = 40      # أقصى مدة احتفاظ (أيام تداول)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """يضيف المؤشرات والقرار (ACTION/SCORE) لكل يوم."""
    df = st.add_indicators(df.copy())
    df = df.dropna(subset=["SMA50", "RSI", "ATR", "MACD_HIST", "TURNOVER"])
    decisions = [st.decide(p, a, b, h, r) for p, a, b, h, r in
                 zip(df["Close"], df["SMA20"], df["SMA50"], df["MACD_HIST"], df["RSI"])]
    df["ACTION"] = [d[0] for d in decisions]
    df["SCORE"] = [d[1] for d in decisions]
    return df


def simulate(data: dict, capital: float, risk_pct: float = None, fee: float = FEE_PER_SIDE,
             max_pos: int = MAX_POSITIONS, max_hold: int = MAX_HOLD_DAYS):
    risk_pct = risk_pct or config.RISK_PCT
    P = {s: prepare(df) for s, df in data.items()}
    P = {s: d for s, d in P.items() if len(d)}
    if not P:
        raise ValueError("لا توجد بيانات كافية")
    dates = sorted(set().union(*[set(d.index) for d in P.values()]))

    cash, pos, trades, curve = capital, {}, [], []
    last_close, pend_entries, pend_exits = {}, [], set()

    def close_pos(sym, date, price, reason):
        nonlocal cash
        p = pos.pop(sym)
        proceeds = p["shares"] * price * (1 - fee)
        cost = p["shares"] * p["entry"] * (1 + fee)
        cash += proceeds
        trades.append({"symbol": sym, "entry_date": p["date"].date(), "exit_date": date.date(),
                       "entry": round(p["entry"], 2), "exit": round(price, 2), "shares": p["shares"],
                       "pnl": proceeds - cost, "ret_pct": (proceeds - cost) / cost * 100,
                       "days": p["days"], "reason": reason})

    for d in dates:
        # 1) تنفيذ إشارات الخروج على افتتاح اليوم
        waiting = set()
        for s in pend_exits:
            if s in pos:
                if d in P[s].index:
                    close_pos(s, d, float(P[s].at[d, "Open"]), "إشارة خروج")
                else:
                    waiting.add(s)
        pend_exits = waiting

        # 2) تنفيذ إشارات الشراء على افتتاح اليوم
        equity = cash + sum(p["shares"] * last_close.get(s, p["entry"]) for s, p in pos.items())
        for c in sorted(pend_entries, key=lambda c: (-c["score"], -c["turnover"])):
            s = c["symbol"]
            if s in pos or len(pos) >= max_pos or d not in P[s].index:
                continue
            o = float(P[s].at[d, "Open"])
            if not (c["stop"] < o < c["target"]):
                continue                                   # فجوة غيّرت الصورة: نتجاهل
            shares = min(st.position_size(equity, risk_pct, o, c["stop"]),
                         math.floor(cash / (o * (1 + fee))))
            if shares < 1:
                continue
            cash -= shares * o * (1 + fee)
            pos[s] = {"date": d, "entry": o, "shares": shares, "stop": c["stop"],
                      "target": c["target"], "days": 0}
        pend_entries = []

        # 3) فحص الوقف والهدف خلال اليوم
        for s in list(pos):
            if d not in P[s].index:
                continue
            row, p = P[s].loc[d], pos[s]
            p["days"] += 1
            o, h, l, c = (float(row[k]) for k in ("Open", "High", "Low", "Close"))
            if o <= p["stop"]:
                close_pos(s, d, o, "وقف خسارة (فجوة)")
            elif l <= p["stop"]:
                close_pos(s, d, p["stop"], "وقف خسارة")
            elif h >= p["target"]:
                close_pos(s, d, max(o, p["target"]), "هدف")
            elif p["days"] >= max_hold:
                close_pos(s, d, c, "انتهاء المدة")

        # 4) نهاية اليوم: تسعير + إشارات الغد
        for s, dfp in P.items():
            if d not in dfp.index:
                continue
            row = dfp.loc[d]
            last_close[s] = float(row["Close"])
            if s in pos and row["ACTION"] == "SELL":
                pend_exits.add(s)
            elif s not in pos and row["ACTION"] == "BUY" and row["TURNOVER"] >= st.MIN_TURNOVER_EGP:
                stop, target = st.stop_target(float(row["Close"]), float(row["ATR"]))
                pend_entries.append({"symbol": s, "stop": stop, "target": target,
                                     "score": int(row["SCORE"]), "turnover": float(row["TURNOVER"])})
        curve.append((d, cash + sum(p["shares"] * last_close.get(s, p["entry"]) for s, p in pos.items())))

    for s in list(pos):                                     # إغلاق ما تبقى بآخر سعر
        close_pos(s, dates[-1], last_close.get(s, pos[s]["entry"]), "نهاية البيانات")
    return curve, trades, P


def summarize(curve, trades, capital, P) -> str:
    eq = pd.Series({d: e for d, e in curve})
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    total = eq.iloc[-1] / capital - 1
    cagr = (eq.iloc[-1] / capital) ** (1 / years) - 1
    max_dd = (eq / eq.cummax() - 1).min()

    lines = [f"📈 نتيجة الاختبار على الماضي ({eq.index[0].date()} → {eq.index[-1].date()})",
             f"عدد الأسهم المختبرة: {len(P)} | رأس المال: {capital:,.0f} ج | تكلفة العملية: {FEE_PER_SIDE*100:.2f}%", ""]
    if not trades:
        lines.append("لم تحدث أي صفقة في الفترة دي.")
        return "\n".join(lines)

    tr = pd.DataFrame(trades)
    wins, losses = tr[tr["pnl"] > 0], tr[tr["pnl"] <= 0]
    gross_loss = abs(losses["pnl"].sum())
    pf = wins["pnl"].sum() / gross_loss if gross_loss else float("inf")
    lines += [
        f"💰 الربح الكلي: {total*100:+.1f}%  (سنوياً ≈ {cagr*100:+.1f}%)",
        f"📉 أكبر هبوط في الرصيد: {max_dd*100:.1f}%",
        f"🔢 عدد الصفقات: {len(tr)} | نسبة الرابحة: {len(wins)/len(tr)*100:.0f}%",
        f"✅ متوسط الربحة: {wins['ret_pct'].mean() if len(wins) else 0:+.1f}%   "
        f"❌ متوسط الخسارة: {losses['ret_pct'].mean() if len(losses) else 0:+.1f}%",
        f"⚖️ Profit Factor: {pf:.2f}  (أكبر من 1 = مكسب)",
        f"📊 متوسط الصفقة: {tr['ret_pct'].mean():+.2f}% | متوسط المدة: {tr['days'].mean():.0f} يوم",
        "طرق الخروج: " + "، ".join(f"{k} {v}" for k, v in tr["reason"].value_counts().items()),
    ]
    # مقارنة بالشراء والاحتفاظ بنفس الأسهم (متساوي الأوزان) ومؤشر EGX30
    try:
        bh = np.mean([d["Close"].iloc[-1] / d["Close"].iloc[0] - 1 for d in P.values()])
        lines.append(f"🆚 شراء واحتفاظ بنفس الأسهم: {bh*100:+.1f}% (لنفس مدة كل سهم)")
    except Exception:
        pass
    try:
        idx = yf.Ticker("^CASE30").history(start=eq.index[0].strftime("%Y-%m-%d"))["Close"]
        if len(idx) > 10:
            lines.append(f"🆚 مؤشر EGX30: {(idx.iloc[-1]/idx.iloc[0]-1)*100:+.1f}%")
    except Exception:
        pass

    lines.append("")
    if len(tr) < 30:
        lines.append("⚠️ عدد الصفقات قليل (أقل من 30)، النتيجة مش موثوقة إحصائياً.")
    if pf < 1.2 or total <= 0:
        lines.append("⚠️ الأداء ضعيف أو غير مقنع: متعتمدش على الإشارات بفلوس حقيقية كبيرة.")
    lines.append("تنبيه: الأداء الماضي لا يضمن المستقبل، والقائمة مختارة بأثر رجعي (انحياز البقاء).")
    return "\n".join(lines)


def download(years: int) -> dict:
    data = {}
    for s in st.WATCHLIST:
        try:
            df = yf.Ticker(f"{s}.CA").history(period=f"{years}y", interval="1d")
        except Exception:
            continue
        if df is not None and len(df) > 120:
            df.index = df.index.tz_localize(None)
            data[s] = df
    return data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--capital", type=float, default=50000)
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()

    print("⏳ جاري تحميل البيانات التاريخية...")
    data = download(a.years)
    if not data:
        sys.exit("لم يتم تحميل أي بيانات.")
    curve, trades, P = simulate(data, a.capital)
    report = summarize(curve, trades, a.capital, P)
    print("\n" + report)
    if trades:
        pd.DataFrame(trades).to_csv("backtest_trades.csv", index=False, encoding="utf-8-sig")
        print("\nتم حفظ كل الصفقات في backtest_trades.csv")
    if a.send:
        import scanner
        scanner.send_telegram(report)


if __name__ == "__main__":
    main()

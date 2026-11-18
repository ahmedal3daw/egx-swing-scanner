"""
strategy.py — منطق الاستراتيجية في مكان واحد.
الماسح (scanner.py) والاختبار على الماضي (backtest.py) بيستخدموا نفس الدوال بالظبط،
فنتيجة الـ Backtest تعبّر فعلاً عن اللي الماسح بيعمله.
"""
import math
import pandas as pd

# قائمة الأسهم (راجعها من وقت للتاني؛ الرموز غير الموجودة على Yahoo بتتخطى تلقائياً)
WATCHLIST = [
    "COMI", "HRHO", "TMGH", "EAST", "SWDY", "ETEL", "ABUK", "FWRY", "ORAS",
    "PHDC", "EFIH", "CLHO", "JUFO", "MFPC", "SKPC", "ORWE",
    "EGAL", "AMOC", "CCAP", "ALCN", "ADIB", "HELI", "BTFH", "ORHD", "EMFD",
    "ISPH", "TALM", "OCDI",
]

# أسماء الشركات (لبحث الأخبار). أي رمز مش هنا بيتبحث عنه برمزه. راجع الأسماء وعدّلها لو لزم.
NAMES = {
    "COMI": "البنك التجاري الدولي", "HRHO": "هيرميس القابضة", "TMGH": "طلعت مصطفى القابضة",
    "EAST": "الشرقية للدخان إيسترن كومباني", "SWDY": "السويدي إلكتريك", "ETEL": "المصرية للاتصالات",
    "ABUK": "أبو قير للأسمدة", "FWRY": "فوري للمدفوعات", "ORAS": "أوراسكوم للإنشاءات",
    "PHDC": "بالم هيلز للتعمير", "EFIH": "إي فاينانس", "CLHO": "مستشفى كليوباترا",
    "JUFO": "جهينة للصناعات الغذائية", "MFPC": "موبكو لإنتاج الأسمدة",
    "SKPC": "سيدي كرير للبتروكيماويات", "ORWE": "النساجون الشرقيون", "EGAL": "مصر للألومنيوم",
    "AMOC": "أموك للزيوت المعدنية", "CCAP": "القلعة القابضة", "ALCN": "الإسكندرية لتداول الحاويات",
    "ADIB": "مصرف أبوظبي الإسلامي مصر", "HELI": "مصر الجديدة للإسكان والتعمير",
    "BTFH": "بلتون القابضة", "ORHD": "أوراسكوم للتنمية مصر", "EMFD": "إعمار مصر للتنمية",
    "ISPH": "ابن سينا فارما", "OCDI": "سوديك",
}

MIN_TURNOVER_EGP = 2_000_000   # أقل متوسط قيمة تداول يومية (20 يوم)
MAX_POSITION_PCT = 0.25        # أقصى نسبة من رأس المال في سهم واحد
ATR_STOP_MULT = 2.0            # وقف الخسارة = السعر - 2×ATR
REWARD_RISK = 2.0              # الهدف = ضعف المخاطرة


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close = df["Close"]
    df["SMA20"] = close.rolling(20).mean()
    df["SMA50"] = close.rolling(50).mean()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_HIST"] = df["MACD"] - df["MACD_SIGNAL"]
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI"] = 100 - 100 / (1 + gain / loss)
    prev = close.shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(),
                    (df["Low"] - prev).abs()], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    df["TURNOVER"] = (df["Close"] * df["Volume"]).rolling(20).mean()
    return df


def decide(price: float, sma20: float, sma50: float, macd_hist: float, rsi: float):
    """القرار: يرجع (BUY | SELL | HOLD, نقاط من 4)."""
    above50, above20, macd_up = price > sma50, price > sma20, macd_hist > 0
    score = sum([above50, above20, macd_up, 45 <= rsi <= 65])
    if above50 and macd_up and rsi <= 70 and score >= 3:
        return "BUY", score
    if (not above50 and macd_hist < 0) or rsi > 75:
        return "SELL", score
    return "HOLD", score


def stop_target(price: float, atr: float):
    stop = price - ATR_STOP_MULT * atr
    return stop, price + REWARD_RISK * (price - stop)


def position_size(capital: float, risk_pct: float, price: float, stop: float) -> int:
    """عدد الأسهم: مخاطرة risk_pct من رأس المال، وبحد أقصى MAX_POSITION_PCT منه."""
    if price <= 0 or price <= stop:
        return 0
    shares = math.floor(capital * risk_pct / (price - stop))
    shares = min(shares, math.floor(capital * MAX_POSITION_PCT / price))
    return max(shares, 0)

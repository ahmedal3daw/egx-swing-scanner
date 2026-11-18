# EGX Bot — ماسح أسهم البورصة المصرية (Telegram)

يمسح أسهم EGX30 كل يوم (مرتين أثناء الجلسة ومرة بعد الإغلاق) ويبعتلك على Telegram: فرص الشراء (مع الوقف والهدف وعدد الأسهم وسطر أخبار بالذكاء الاصطناعي)، وإشارات الخروج. **مفيش تنفيذ تلقائي**، إنت اللي بتنفذ على Thndr.

## الملفات
| ملف | وظيفته |
|---|---|
| `strategy.py` | قواعد الاستراتيجية (مكان واحد للماسح والـ Backtest) + قايمة الأسهم |
| `scanner.py` | الماسح اليومي + أوامر Telegram |
| `backtest.py` | اختبار الاستراتيجية على الماضي |
| `news.py` | أخبار كل سهم + تقييم Groq |
| `.github/workflows/scan.yml` | التشغيل التلقائي على GitHub |
| `supabase_schema.sql` | جدولين: signals و settings |

## 1) جهّز Supabase (مرة واحدة)
افتح SQL Editor وشغّل `supabase_schema.sql` (فيه جدول `settings` الجديد).

## 2) جرّب على جهازك
```
pip install -r requirements.txt
copy .env.example .env      # واملأ المفاتيح
python scanner.py --dry     # عرض فقط
python scanner.py --sync    # (مرة واحدة) ينقل رصيدك وأسهمك لـ Supabase
```

## 3) الاختبار على الماضي
```
python backtest.py
```
اقرأ النتيجة بعناية قبل ما تعتمد على الإشارات (Profit Factor أكبر من 1.2 وعدد صفقات 30+ على الأقل).

## 4) التشغيل على GitHub (من غير لاب)
1. اعمل **Private repository** جديد على GitHub.
2. ارفع كل الملفات **ما عدا `.env`** (مهم جداً). لو فولدر `.github` ما اترفعش بالسحب، اعمل ملف بالاسم `.github/workflows/scan.yml` من زر Add file ← Create new file والصق محتواه.
3. Settings ← Secrets and variables ← Actions ← New repository secret، وضيف:
   `TELEGRAM_BOT_TOKEN` و`TELEGRAM_CHAT_ID` و`SUPABASE_URL` و`SUPABASE_KEY` و`GROQ_API_KEY` (و`GROQ_MODEL` اختياري).
4. تبويب Actions ← EGX Scanner ← Run workflow (تشغيل تجريبي).
5. لو نجح، **وقّف مهمة ويندوز** عشان الرسايل متتكررش:
   `Unregister-ScheduledTask -TaskName "EGX Scanner" -Confirm:$false`

## الرسايل أثناء الجلسة (جديد)
بيبعت 3 رسايل في اليوم (الأحد–الخميس، بتوقيت القاهرة): **~11:30** و**~13:30** أثناء الجلسة بأسعار اليوم الحالية، و**~15:00** بعد الإغلاق بأسعار الإغلاق النهائية.
- الأسعار اللحظية من Yahoo **متأخرة حوالي 20 دقيقة** عن البورصة، ورأس الرسالة بيوضّح وقت آخر تحديث.
- لو Yahoo ما أعطاش أسعار اليوم، الرسالة بتقولك بوضوح وبتستخدم آخر إغلاق.
- الإشارات أثناء الجلسة مؤقتة وممكن تتغير عند الإغلاق، ومبتتسجلش في Supabase (بس رسالة ما بعد الإغلاق بتتسجل).
- اختبار سريع لو Yahoo بيدّي بيانات اللحظة (شغّله والسوق مفتوح): 
  `python -c "import yfinance as yf; print(yf.Ticker('COMI.CA').history(period='1d', interval='5m').tail(3))"`
- تشغيل يدوي: `python scanner.py --live` (أثناء الجلسة) أو `--no-live` (آخر إغلاق). من غير خيارات بيختار حسب الوقت.
- لتغيير المواعيد: عدّل `SLOTS` في `scanner.py` وجدول `cron` في `scan.yml`.

## أوامر Telegram
`رصيد 80000` — `محفظتي COMI ETEL` — `محفظتي مفيش` — `حالة`
على GitHub بتتنفذ خلال 6 ساعات كحد أقصى (أو فوراً مع تشغيل يدوي).

## تنبيهات
- الأسعار متأخرة ~20 دقيقة على الأقل (وبعد الإغلاق آخر إغلاق). المؤشرات بتبص على الماضي، والـ Backtest مبني على الإغلاق مش على أسعار أثناء الجلسة.
- GitHub ممكن يتأخر في التشغيل المجدول، وبيوقّف المهام المجدولة لو الريبو ما فيهوش نشاط 60 يوم (فعّلها من Actions).
- للاستئناس فقط وليست نصيحة استثمارية.

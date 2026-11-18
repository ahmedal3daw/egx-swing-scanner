-- شغّل الاتنين في Supabase > SQL Editor (جدول signals لو مش موجود أصلاً)
create table if not exists signals (
  id            bigint generated always as identity primary key,
  created_at    timestamptz default now(),
  symbol        text not null,
  action        text not null check (action in ('BUY','SELL','HOLD')),
  entry_price   numeric,
  stop_loss     numeric,
  target_price  numeric,
  shares        integer,
  risk_amount   numeric,
  reasoning     text
);

-- جدول جديد: يحفظ رصيدك وأسهمك وآخر أمر Telegram (ضروري للتشغيل على GitHub)
create table if not exists settings (
  key        text primary key,
  value      jsonb not null,
  updated_at timestamptz default now()
);

/* ============================================================
   Crypto Pulse database schema

   Призначення цього файла:
   1) описати основні таблиці проєкту;
   2) показати зв'язки між таблицями;
   3) налаштувати захист персональних даних через RLS;
   4) додати функції та тригер, які автоматизують роботу
      з профілями користувачів.

   Важливо:
   - логін, пароль і сесії обробляє Supabase Auth;
   - таблиця auth.users є системною таблицею Supabase;
   - таблиці public.* є прикладними таблицями нашого застосунку.
   ============================================================ */


/* ============================================================
   1. ТАБЛИЦЯ ПРОФІЛІВ КОРИСТУВАЧІВ

   public.users не замінює auth.users.
   Вона доповнює системний обліковий запис користувача
   тими даними, які потрібні саме застосунку:
   username, ім'я, телефон, регіон, аватар, тариф тощо.

   Зв'язок:
   public.users.id -> auth.users.id

   Тип зв'язку:
   один запис в auth.users відповідає одному профілю
   в public.users.
   ============================================================ */

CREATE TABLE IF NOT EXISTS public.users (
  -- id профілю дорівнює id користувача в Supabase Auth.
  -- Завдяки PRIMARY KEY профіль є унікальним,
  -- а завдяки REFERENCES він не може існувати без auth.users.
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,

  -- Основні дані акаунта.
  -- Паролем реально керує Supabase Auth.
  -- hashed_password залишено як службове/legacy-поле,
  -- щоб не зберігати реальний пароль вручну.
  email TEXT NOT NULL UNIQUE,
  hashed_password TEXT,
  is_active BOOLEAN NOT NULL DEFAULT true,

  -- Дані профілю, які користувач вводить у застосунку.
  username VARCHAR(100),
  first_name VARCHAR(100),
  last_name VARCHAR(100),
  phone_number VARCHAR(30),

  -- birth_date зберігаємо як TEXT, бо на frontend дата може
  -- приходити рядком у різних форматах.
  -- Якщо формат буде суворо контрольованим, поле можна
  -- перевести у тип DATE.
  birth_date TEXT,
  region VARCHAR(100),
  avatar_url TEXT,

  -- Поля для майбутньої системи підписок.
  -- subscription може зберігати загальний тип підписки,
  -- active_plan - поточний активний тариф,
  -- billing_cycle - період оплати.
  subscription VARCHAR(20) DEFAULT 'free',
  active_plan VARCHAR(20) DEFAULT 'free',
  billing_cycle VARCHAR(10) DEFAULT 'monthly',

  -- Ми не зберігаємо повний номер картки.
  -- Лише останні 4 цифри, щоб показувати користувачу,
  -- яка картка прив'язана.
  card_last4 VARCHAR(4),

  -- Необов'язкова інтеграція з Telegram.
  telegram_chat_id BIGINT,
  telegram_username VARCHAR(100),

  -- Службові часові поля.
  password_last_changed TIMESTAMPTZ DEFAULT NOW(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- updated_at встановлюється при створенні запису.
  -- Якщо потрібно оновлювати його автоматично при UPDATE,
  -- надалі можна додати окремий trigger.
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


/* ============================================================
   2. ТАБЛИЦЯ ОБРАНИХ КРИПТОВАЛЮТ

   user_favorites зберігає монети, які користувач додав
   у власний список обраних.

   Приклад:
   користувач може додати Bitcoin, Ethereum, Solana тощо.

   Зв'язок:
   user_favorites.user_id -> auth.users.id

   Тип зв'язку:
   один користувач може мати багато обраних монет.
   ============================================================ */

CREATE TABLE IF NOT EXISTS public.user_favorites (
  -- BIGSERIAL створює сурогатний числовий ключ
  -- і окрему послідовність для його автоматичного заповнення.
  id BIGSERIAL PRIMARY KEY,

  -- Користувач, якому належить конкретний запис обраного.
  -- ON DELETE CASCADE автоматично очищує його favorites,
  -- якщо акаунт користувача буде видалений.
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

  -- Дані монети, які приходять з криптовалютного API.
  coin_id TEXT NOT NULL,
  symbol VARCHAR(20) NOT NULL,
  name TEXT NOT NULL,
  image_url TEXT,

  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Один користувач не може двічі додати одну й ту саму монету.
  CONSTRAINT user_favorites_user_symbol_unique UNIQUE (user_id, symbol)
);

-- Індекс прискорює пошук усіх обраних монет конкретного користувача.
CREATE INDEX IF NOT EXISTS user_favorites_user_id_idx
ON public.user_favorites(user_id);

-- Індекс прискорює пошук за символом монети, наприклад BTC або ETH.
CREATE INDEX IF NOT EXISTS user_favorites_symbol_idx
ON public.user_favorites(symbol);


/* ============================================================
   3. ТАБЛИЦЯ СПОВІЩЕНЬ ПРО ЦІНИ

   alerts зберігає персональні правила сповіщень:
   наприклад, повідомити користувача, коли BTC стане
   вище або нижче певної ціни.

   Зв'язок:
   alerts.user_id -> auth.users.id

   Тип зв'язку:
   один користувач може створити багато alerts.
   ============================================================ */

CREATE TABLE IF NOT EXISTS public.alerts (
  id BIGSERIAL PRIMARY KEY,

  -- Користувач-власник конкретного сповіщення.
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

  -- Монета, для якої створено сповіщення.
  coin_id TEXT NOT NULL,
  symbol VARCHAR(20) NOT NULL,
  name TEXT,

  -- Умова спрацювання.
  -- Наприклад:
  -- condition = 'above' -> повідомити, якщо ціна стане вищою;
  -- condition = 'below' -> повідомити, якщо ціна стане нижчою.
  target_price NUMERIC(20, 8) NOT NULL,
  condition VARCHAR(20) NOT NULL,

  -- Стан сповіщення.
  is_active BOOLEAN NOT NULL DEFAULT true,
  is_triggered BOOLEAN NOT NULL DEFAULT false,
  triggered_at TIMESTAMPTZ,

  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Швидко знайти всі alerts одного користувача.
CREATE INDEX IF NOT EXISTS alerts_user_id_idx
ON public.alerts(user_id);

-- Швидко знайти alerts за конкретною монетою.
CREATE INDEX IF NOT EXISTS alerts_symbol_idx
ON public.alerts(symbol);

-- Швидко знайти активні alerts, які ще потрібно перевіряти.
CREATE INDEX IF NOT EXISTS alerts_is_active_idx
ON public.alerts(is_active);


/* ============================================================
   4. RLS ДЛЯ ОБРАНИХ МОНЕТ

   RLS (Row-Level Security) - механізм PostgreSQL/Supabase,
   який дозволяє обмежувати доступ не тільки до всієї таблиці,
   а до конкретних рядків у ній.

   У user_favorites кожен рядок має user_id.
   Через auth.uid() ми визначаємо id поточного
   авторизованого користувача і дозволяємо йому працювати
   лише з власними рядками.
   ============================================================ */

ALTER TABLE public.user_favorites ENABLE ROW LEVEL SECURITY;

-- Користувач читає тільки власні favorites.
CREATE POLICY "Users can read own favorites"
ON public.user_favorites
FOR SELECT
TO authenticated
USING (auth.uid() = user_id);

-- Користувач може додати favorite тільки для себе.
-- WITH CHECK перевіряє новий рядок перед INSERT.
CREATE POLICY "Users can insert own favorites"
ON public.user_favorites
FOR INSERT
TO authenticated
WITH CHECK (auth.uid() = user_id);

-- Для UPDATE використовується одразу дві перевірки:
-- USING - чи дозволено редагувати існуючий рядок;
-- WITH CHECK - чи залишиться рядок власністю того самого
-- користувача після оновлення.
CREATE POLICY "Users can update own favorites"
ON public.user_favorites
FOR UPDATE
TO authenticated
USING (auth.uid() = user_id)
WITH CHECK (auth.uid() = user_id);

-- Користувач може видалити тільки власний favorite.
CREATE POLICY "Users can delete own favorites"
ON public.user_favorites
FOR DELETE
TO authenticated
USING (auth.uid() = user_id);


/* ============================================================
   5. RLS ДЛЯ СПОВІЩЕНЬ

   alerts теж є персональними даними користувача,
   тому кожен запис повинен бути доступний лише його власнику.
   ============================================================ */

ALTER TABLE public.alerts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own alerts"
ON public.alerts
FOR SELECT
TO authenticated
USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own alerts"
ON public.alerts
FOR INSERT
TO authenticated
WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own alerts"
ON public.alerts
FOR UPDATE
TO authenticated
USING (auth.uid() = user_id)
WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete own alerts"
ON public.alerts
FOR DELETE
TO authenticated
USING (auth.uid() = user_id);


/* ============================================================
   6. ПРАВА ДОСТУПУ ДО ТАБЛИЦЬ

   GRANT дає ролі authenticated технічну можливість
   виконувати запити до таблиць.

   Але реальне обмеження "лише свої рядки" забезпечує не GRANT,
   а саме RLS-політика вище.
   ============================================================ */

GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_favorites TO authenticated;
GRANT USAGE, SELECT ON SEQUENCE public.user_favorites_id_seq TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.alerts TO authenticated;
GRANT USAGE, SELECT ON SEQUENCE public.alerts_id_seq TO authenticated;


/* ============================================================
   7. ТАБЛИЦЯ ML-МОДЕЛЕЙ

   ml_models зберігає вже натреновані моделі машинного навчання.
   Завдяки цьому модель не потрібно тренувати заново
   при кожному запуску системи.

   У проєкті модель розрізняється за:
   - symbol: криптовалюта;
   - interval: таймфрейм.
   ============================================================ */

CREATE TABLE IF NOT EXISTS public.ml_models (
  id BIGSERIAL PRIMARY KEY,
  symbol VARCHAR(20) NOT NULL,
  interval VARCHAR(10) NOT NULL,

  -- pickle.dumps(model) у Python перетворює модель
  -- на бінарний масив, який зберігається у BYTEA.
  model_binary BYTEA NOT NULL,

  -- Точність моделі у відсотках.
  accuracy NUMERIC(6, 2),

  -- Список ознак, на яких модель навчалася.
  -- Це потрібно, щоб під час повторного використання
  -- подати дані в тому самому порядку.
  features TEXT[],

  -- trained_at дає змогу перевірити, чи модель не застаріла.
  trained_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Для кожної пари монета + таймфрейм зберігаємо
  -- лише одну активну модель.
  CONSTRAINT ml_models_symbol_interval_unique UNIQUE (symbol, interval)
);

-- Саме за symbol + interval Python-код шукає потрібну модель.
CREATE INDEX IF NOT EXISTS ml_models_symbol_interval_idx
ON public.ml_models(symbol, interval);


/* ============================================================
   8. ТАБЛИЦЯ ПРОГНОЗІВ МОДЕЛІ

   model_predictions - це журнал результатів роботи ML-моделі.
   Один рядок = один зроблений прогноз.

   Таблиця потрібна для:
   - збереження історії сигналів;
   - показу результатів користувачу;
   - подальшої перевірки ефективності моделі;
   - оцінки winrate попередніх сигналів.
   ============================================================ */

CREATE TABLE IF NOT EXISTS public.model_predictions (
  id BIGSERIAL PRIMARY KEY,
  symbol VARCHAR(20) NOT NULL,
  interval VARCHAR(10) NOT NULL,
  signal TEXT NOT NULL,
  price NUMERIC(20, 8),
  confidence NUMERIC(6, 2),
  accuracy NUMERIC(6, 2),
  raw_prediction VARCHAR(20),
  stop_loss NUMERIC(20, 8),
  take_profit NUMERIC(20, 8),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS model_predictions_symbol_idx
ON public.model_predictions(symbol);

CREATE INDEX IF NOT EXISTS model_predictions_interval_idx
ON public.model_predictions(interval);

CREATE INDEX IF NOT EXISTS model_predictions_created_at_idx
ON public.model_predictions(created_at DESC);

-- Комбінований індекс прискорює запити на кшталт:
-- "покажи останні прогнози BTC для таймфрейму 4h".
CREATE INDEX IF NOT EXISTS model_predictions_symbol_interval_created_idx
ON public.model_predictions(symbol, interval, created_at DESC);


/* ============================================================
   9. РОЗШИРЕННЯ ТАБЛИЦІ USERS

   Цей блок потрібен для ситуації, коли таблиця users
   вже існувала раніше, а нові поля додаються пізніше
   під час розвитку проєкту.
   ============================================================ */

ALTER TABLE public.users
ADD COLUMN IF NOT EXISTS avatar_url VARCHAR,
ADD COLUMN IF NOT EXISTS card_last4 VARCHAR(4),
ADD COLUMN IF NOT EXISTS active_plan VARCHAR(20) DEFAULT 'free',
ADD COLUMN IF NOT EXISTS billing_cycle VARCHAR(10) DEFAULT 'monthly';

-- Дублюємо defaults явно, щоб навіть старі міграції
-- гарантовано мали потрібні значення за замовчуванням.
ALTER TABLE public.users
ALTER COLUMN active_plan SET DEFAULT 'free',
ALTER COLUMN billing_cycle SET DEFAULT 'monthly',
ALTER COLUMN hashed_password DROP NOT NULL;

-- Якщо таблиця колись мала birth_date не у форматі TEXT,
-- приводимо її до текстового формату.
ALTER TABLE public.users
ALTER COLUMN birth_date TYPE TEXT USING birth_date::text;

-- Заповнюємо старі записи, де деякі тарифні поля
-- могли бути NULL після попередніх версій схеми.
UPDATE public.users
SET active_plan = COALESCE(active_plan, subscription, 'free'),
    subscription = COALESCE(subscription, active_plan, 'free'),
    billing_cycle = COALESCE(billing_cycle, 'monthly');


/* ============================================================
   10. ЗВ'ЯЗОК USERS ІЗ SUPABASE AUTH

   Якщо користувача видалено з auth.users,
   його прикладний профіль також має зникнути.
   ============================================================ */

ALTER TABLE public.users
DROP CONSTRAINT IF EXISTS users_id_fkey;

ALTER TABLE public.users
ADD CONSTRAINT users_id_fkey
FOREIGN KEY (id)
REFERENCES auth.users(id)
ON DELETE CASCADE;


/* ============================================================
   11. АВТОМАТИЧНЕ СТВОРЕННЯ ПРОФІЛЮ КОРИСТУВАЧА

   Коли Supabase Auth створює нового користувача,
   у таблиці auth.users з'являється новий рядок.

   Тригер після INSERT викликає функцію handle_new_user(),
   а вона створює відповідний профіль у public.users.
   ============================================================ */

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth
AS $$
BEGIN
  INSERT INTO public.users (
    id,
    email,
    hashed_password,
    is_active,
    username,
    subscription,
    first_name,
    last_name,
    phone_number,
    birth_date,
    region,
    active_plan,
    billing_cycle
  )
  VALUES (
    NEW.id,

    -- Якщо з якоїсь причини email порожній,
    -- формуємо службове значення замість NULL,
    -- бо поле email у public.users є NOT NULL.
    COALESCE(NEW.email, NEW.id::text || '@missing.local'),

    'managed_by_supabase',
    true,

    -- NULLIF перетворює порожній рядок на NULL,
    -- щоб у БД не зберігалися пусті текстові значення.
    NULLIF(NEW.raw_user_meta_data ->> 'username', ''),
    'free',
    NULLIF(NEW.raw_user_meta_data ->> 'first_name', ''),
    NULLIF(NEW.raw_user_meta_data ->> 'last_name', ''),
    NULLIF(NEW.raw_user_meta_data ->> 'phone_number', ''),
    NULLIF(NEW.raw_user_meta_data ->> 'birth_date', ''),
    NULLIF(NEW.raw_user_meta_data ->> 'region', ''),
    'free',
    'monthly'
  )
  ON CONFLICT (id) DO UPDATE SET
    -- Якщо профіль уже є, оновлюємо тільки персональні дані.
    -- Тарифні поля тут не чіпаємо, щоб випадково
    -- не перезаписати платний план користувача.
    email = EXCLUDED.email,
    username = EXCLUDED.username,
    first_name = EXCLUDED.first_name,
    last_name = EXCLUDED.last_name,
    phone_number = EXCLUDED.phone_number,
    birth_date = EXCLUDED.birth_date,
    region = EXCLUDED.region;

  RETURN NEW;
EXCEPTION
  WHEN OTHERS THEN
    -- Логування допоможе побачити помилку в журналах,
    -- але не зламає сам процес реєстрації користувача.
    RAISE LOG 'handle_new_user failed for auth user %: %', NEW.id, SQLERRM;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;

CREATE TRIGGER on_auth_user_created
AFTER INSERT ON auth.users
FOR EACH ROW
EXECUTE FUNCTION public.handle_new_user();


/* ============================================================
   12. RLS ДЛЯ ПРОФІЛЮ КОРИСТУВАЧА

   Користувач може:
   - бачити тільки власний профіль;
   - редагувати тільки власний профіль;
   - створювати тільки запис із власним id.
   ============================================================ */

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can read own profile" ON public.users;
DROP POLICY IF EXISTS "Users can update own profile" ON public.users;
DROP POLICY IF EXISTS "Users can insert own profile" ON public.users;

CREATE POLICY "Users can read own profile"
ON public.users
FOR SELECT
TO authenticated
USING (auth.uid() = id);

CREATE POLICY "Users can update own profile"
ON public.users
FOR UPDATE
TO authenticated
USING (auth.uid() = id)
WITH CHECK (auth.uid() = id);

CREATE POLICY "Users can insert own profile"
ON public.users
FOR INSERT
TO authenticated
WITH CHECK (auth.uid() = id);

GRANT SELECT, INSERT, UPDATE ON public.users TO authenticated;


/* ============================================================
   13. ПЕРЕНЕСЕННЯ ВЖЕ ІСНУЮЧИХ КОРИСТУВАЧІВ

   Цей INSERT ... SELECT потрібен, якщо auth.users уже
   містив користувачів до появи public.users або тригера.

   WHERE NOT EXISTS не дозволяє створити дублікати профілів.
   ============================================================ */

INSERT INTO public.users (
  id,
  email,
  hashed_password,
  is_active,
  username,
  subscription,
  first_name,
  last_name,
  phone_number,
  birth_date,
  region,
  password_last_changed,
  active_plan,
  billing_cycle
)
SELECT
  au.id,
  au.email,
  'managed_by_supabase',
  true,
  au.raw_user_meta_data ->> 'username',
  'free',
  au.raw_user_meta_data ->> 'first_name',
  au.raw_user_meta_data ->> 'last_name',
  au.raw_user_meta_data ->> 'phone_number',
  au.raw_user_meta_data ->> 'birth_date',
  au.raw_user_meta_data ->> 'region',
  NOW(),
  'free',
  'monthly'
FROM auth.users au
WHERE NOT EXISTS (
  SELECT 1
  FROM public.users pu
  WHERE pu.id = au.id
);


/* ============================================================
   14. ПЕРЕВІРКА, ЧИ EMAIL УЖЕ ЗАРЕЄСТРОВАНИЙ

   Цю функцію може викликати frontend перед реєстрацією,
   щоб показати користувачу повідомлення,
   якщо такий email уже існує.
   ============================================================ */

CREATE OR REPLACE FUNCTION public.check_email_exists(lookup_email TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  RETURN EXISTS (
    SELECT 1
    FROM auth.users
    WHERE lower(email) = lower(lookup_email)
  );
END;
$$;

GRANT EXECUTE ON FUNCTION public.check_email_exists(TEXT) TO anon, authenticated;


/* ============================================================
   15. ВИДАЛЕННЯ ПОТОЧНОГО КОРИСТУВАЧА

   Функція не приймає user_id ззовні.
   Вона бере id саме поточного користувача через auth.uid(),
   тому користувач не може видалити чужий акаунт.
   ============================================================ */

CREATE OR REPLACE FUNCTION public.delete_current_user()
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth
AS $$
DECLARE
  current_user_id UUID;
BEGIN
  current_user_id := auth.uid();

  IF current_user_id IS NULL THEN
    RAISE EXCEPTION 'Not authenticated';
  END IF;

  DELETE FROM auth.users
  WHERE id = current_user_id;
END;
$$;

GRANT EXECUTE ON FUNCTION public.delete_current_user() TO authenticated;


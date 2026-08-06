create table if not exists users (
    telegram_id bigint primary key,
    name        text,
    timezone    text        not null default 'Asia/Kolkata',
    created_at  timestamptz not null default now()
);

create table if not exists messages (
    id         bigserial primary key,
    user_id    bigint      not null references users (telegram_id) on delete cascade,
    role       text        not null,
    content    text        not null,
    created_at timestamptz not null default now()
);
create index if not exists messages_user_recent on messages (user_id, created_at desc);

create table if not exists todos (
    id         bigserial primary key,
    user_id    bigint      not null references users (telegram_id) on delete cascade,
    text       text        not null,
    done       boolean     not null default false,
    due_at     timestamptz,
    created_at timestamptz not null default now()
);
create index if not exists todos_user_open on todos (user_id) where not done;

create table if not exists reminders (
    id         bigserial primary key,
    user_id    bigint      not null references users (telegram_id) on delete cascade,
    text       text        not null,
    due_at     timestamptz not null,
    recurrence text,
    sent_at    timestamptz,
    created_at timestamptz not null default now()
);
-- Polled every 30s by the tick loop, so keep the unsent set cheap to scan.
create index if not exists reminders_pending on reminders (due_at) where sent_at is null;

create table if not exists expenses (
    id          bigserial primary key,
    user_id     bigint         not null references users (telegram_id) on delete cascade,
    amount      numeric(12, 2) not null check (amount > 0),
    description text,
    category    text           not null default 'other',
    -- Separate from created_at so "I spent 250 on lunch yesterday" records the
    -- day it happened, not the day it was typed.
    spent_at    timestamptz    not null default now(),
    created_at  timestamptz    not null default now()
);
create index if not exists expenses_user_time on expenses (user_id, spent_at desc);

alter table users add column if not exists monthly_budget numeric(12, 2);

create table if not exists memory_items (
    id         bigserial primary key,
    user_id    bigint      not null references users (telegram_id) on delete cascade,
    key        text        not null,
    value      text        not null,
    created_at timestamptz not null default now(),
    unique (user_id, key)
);

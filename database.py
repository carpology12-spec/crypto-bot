import os
import asyncpg

DATABASE_URL = os.environ["DATABASE_URL"]

DEFAULT_CATEGORIES = [
    ("پرایس اکشن", "آموزش‌های مربوط به تحلیل پرایس اکشن"),
    ("مدیریت سرمایه", "آموزش‌های مربوط به مدیریت سرمایه و ریسک"),
]

DEFAULT_ABOUT_TEXT = (
    "🎓 <b>درباره آکادمی</b>\n\n"
    "این متن پیش‌فرض است. ادمین می‌تواند با دستور /setabout این متن را ویرایش کند."
)

DEFAULT_CALENDAR_TEXT = (
    "🗓 <b>برنامه آموزشی</b>\n\n"
    "هنوز برنامه‌ای ثبت نشده. ادمین می‌تواند با دستور /setcalendar این بخش را ویرایش کند."
)

LEVELS = ["مبتدی", "متوسط", "پیشرفته"]

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=1, max_size=5, statement_cache_size=0
        )
    return _pool


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS materials (
                id SERIAL PRIMARY KEY,
                category_id INTEGER NOT NULL REFERENCES categories(id),
                title TEXT NOT NULL,
                content TEXT,
                file_id TEXT,
                file_type TEXT,
                level TEXT DEFAULT 'مبتدی',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("ALTER TABLE materials ADD COLUMN IF NOT EXISTS level TEXT DEFAULT 'مبتدی'")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                channel_username TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                level TEXT DEFAULT 'مبتدی',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS level TEXT DEFAULT 'مبتدی'")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS profit_shots (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username TEXT,
                file_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS summaries (
                id SERIAL PRIMARY KEY,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS about (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                content TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS calendar (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                content TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS faq (
                id SERIAL PRIMARY KEY,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_progress (
                user_id BIGINT NOT NULL,
                item_type TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, item_type, item_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                forwarded_message_id BIGINT,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        for name, description in DEFAULT_CATEGORIES:
            await db.execute(
                "INSERT INTO categories (name, description) VALUES ($1, $2) ON CONFLICT (name) DO NOTHING",
                name, description
            )

        await db.execute(
            "INSERT INTO about (id, content) VALUES (1, $1) ON CONFLICT (id) DO NOTHING", DEFAULT_ABOUT_TEXT
        )
        await db.execute(
            "INSERT INTO calendar (id, content) VALUES (1, $1) ON CONFLICT (id) DO NOTHING", DEFAULT_CALENDAR_TEXT
        )


# ── دسته‌بندی‌ها و مطالب ──────────────────────────────────────────────────────
async def get_categories():
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch("SELECT * FROM categories ORDER BY id")
        return [dict(r) for r in rows]


async def get_materials_by_category(category_id: int, level: str = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        if level and level != "همه":
            rows = await db.fetch(
                "SELECT * FROM materials WHERE category_id = $1 AND level = $2 ORDER BY created_at DESC",
                category_id, level
            )
        else:
            rows = await db.fetch(
                "SELECT * FROM materials WHERE category_id = $1 ORDER BY created_at DESC", category_id
            )
        return [dict(r) for r in rows]


async def add_material(category_id: int, title: str, content: str = None,
                        file_id: str = None, file_type: str = None, level: str = "مبتدی"):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """INSERT INTO materials (category_id, title, content, file_id, file_type, level)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            category_id, title, content, file_id, file_type, level
        )


async def get_material_by_id(material_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT * FROM materials WHERE id = $1", material_id)
        return dict(row) if row else None


# ── ویدیوهای آموزشی ───────────────────────────────────────────────────────────
async def get_videos(level: str = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        if level and level != "همه":
            rows = await db.fetch("SELECT * FROM videos WHERE level = $1 ORDER BY created_at DESC", level)
        else:
            rows = await db.fetch("SELECT * FROM videos ORDER BY created_at DESC")
        return [dict(r) for r in rows]


async def get_video_by_id(video_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT * FROM videos WHERE id = $1", video_id)
        return dict(row) if row else None


async def add_video(title: str, channel_username: str, message_id: int, level: str = "مبتدی"):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "INSERT INTO videos (title, channel_username, message_id, level) VALUES ($1, $2, $3, $4)",
            title, channel_username, message_id, level
        )


async def get_all_videos():
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch("SELECT id, title FROM videos")
        return [dict(r) for r in rows]


async def update_video_title(video_id: int, new_title: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute("UPDATE videos SET title = $1 WHERE id = $2", new_title, video_id)


# ── جستجو ──────────────────────────────────────────────────────────────────────
async def search_materials(query: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        like = f"%{query}%"
        rows = await db.fetch(
            "SELECT * FROM materials WHERE title ILIKE $1 OR content ILIKE $1 ORDER BY created_at DESC LIMIT 15",
            like
        )
        return [dict(r) for r in rows]


async def search_videos(query: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        like = f"%{query}%"
        rows = await db.fetch(
            "SELECT * FROM videos WHERE title ILIKE $1 ORDER BY created_at DESC LIMIT 15", like
        )
        return [dict(r) for r in rows]


# ── آرشیو شات‌های سود ─────────────────────────────────────────────────────────
async def add_profit_shot(user_id: int, username: str, file_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "INSERT INTO profit_shots (user_id, username, file_id) VALUES ($1, $2, $3)",
            user_id, username, file_id
        )


async def get_recent_profit_shots(limit: int = 10):
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch("SELECT * FROM profit_shots ORDER BY created_at DESC LIMIT $1", limit)
        return [dict(r) for r in rows]


async def get_profit_shots_count():
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchval("SELECT COUNT(*) FROM profit_shots")


# ── خلاصه/برآیند دوره‌ای ──────────────────────────────────────────────────────
async def add_summary(content: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute("INSERT INTO summaries (content) VALUES ($1)", content)


async def get_latest_summary():
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT * FROM summaries ORDER BY created_at DESC LIMIT 1")
        return dict(row) if row else None


# ── درباره آکادمی ─────────────────────────────────────────────────────────────
async def get_about():
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT content FROM about WHERE id = 1")
        return row["content"] if row else DEFAULT_ABOUT_TEXT


async def set_about(content: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "INSERT INTO about (id, content) VALUES (1, $1) "
            "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content",
            content
        )


# ── تقویم/برنامه هفتگی ────────────────────────────────────────────────────────
async def get_calendar():
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT content FROM calendar WHERE id = 1")
        return row["content"] if row else DEFAULT_CALENDAR_TEXT


async def set_calendar(content: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "INSERT INTO calendar (id, content) VALUES (1, $1) "
            "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content",
            content
        )


# ── سوالات متداول ─────────────────────────────────────────────────────────────
async def add_faq(question: str, answer: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute("INSERT INTO faq (question, answer) VALUES ($1, $2)", question, answer)


async def get_faqs():
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch("SELECT * FROM faq ORDER BY id")
        return [dict(r) for r in rows]


async def get_faq_by_id(faq_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT * FROM faq WHERE id = $1", faq_id)
        return dict(row) if row else None


# ── پیگیری پیشرفت کاربر ───────────────────────────────────────────────────────
async def mark_viewed(user_id: int, item_type: str, item_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "INSERT INTO user_progress (user_id, item_type, item_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id, item_type, item_id) DO NOTHING",
            user_id, item_type, item_id
        )


async def get_progress_stats(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        materials_seen = await db.fetchval(
            "SELECT COUNT(*) FROM user_progress WHERE user_id = $1 AND item_type = 'material'", user_id
        )
        videos_seen = await db.fetchval(
            "SELECT COUNT(*) FROM user_progress WHERE user_id = $1 AND item_type = 'video'", user_id
        )
        materials_total = await db.fetchval("SELECT COUNT(*) FROM materials")
        videos_total = await db.fetchval("SELECT COUNT(*) FROM videos")

        return {
            "materials_seen": materials_seen,
            "materials_total": materials_total,
            "videos_seen": videos_seen,
            "videos_total": videos_total,
        }


# ── تیکت‌ها ────────────────────────────────────────────────────────────────────
async def save_ticket(user_id: int, message_id: int, forwarded_message_id: int = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            "INSERT INTO tickets (user_id, message_id, forwarded_message_id) VALUES ($1, $2, $3) RETURNING id",
            user_id, message_id, forwarded_message_id
        )
        return row["id"]


async def get_ticket_by_forwarded_id(forwarded_message_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT * FROM tickets WHERE forwarded_message_id = $1", forwarded_message_id)
        return dict(row) if row else None


async def get_ticket_by_id(ticket_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT * FROM tickets WHERE id = $1", ticket_id)
        return dict(row) if row else None


# ── پشتیبان‌گیری و بازیابی کامل دیتابیس ───────────────────────────────────────
async def export_backup() -> dict:
    pool = await get_pool()
    async with pool.acquire() as db:
        categories = [dict(r) for r in await db.fetch("SELECT name, description FROM categories")]
        materials = [dict(r) for r in await db.fetch(
            "SELECT c.name AS category_name, m.title, m.content, m.file_id, m.file_type, m.level "
            "FROM materials m JOIN categories c ON m.category_id = c.id"
        )]
        videos = [dict(r) for r in await db.fetch(
            "SELECT title, channel_username, message_id, level FROM videos"
        )]
        faqs = [dict(r) for r in await db.fetch("SELECT question, answer FROM faq")]
        about_row = await db.fetchrow("SELECT content FROM about WHERE id = 1")
        calendar_row = await db.fetchrow("SELECT content FROM calendar WHERE id = 1")

        return {
            "categories": categories,
            "materials": materials,
            "videos": videos,
            "faqs": faqs,
            "about": about_row["content"] if about_row else None,
            "calendar": calendar_row["content"] if calendar_row else None,
        }


async def import_backup(data: dict) -> dict:
    """بازیابی از فایل بکاپ. فرض بر این است که دیتابیس مقصد خالی یا تازه است."""
    pool = await get_pool()
    counts = {"categories": 0, "materials": 0, "videos": 0, "faqs": 0}

    async with pool.acquire() as db:
        # نگاشت اسم دسته‌بندی به id (چون id ها ممکن است بین دو دیتابیس فرق کنند)
        category_id_map = {}
        for cat in data.get("categories", []):
            row = await db.fetchrow(
                "INSERT INTO categories (name, description) VALUES ($1, $2) "
                "ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description RETURNING id",
                cat["name"], cat.get("description")
            )
            category_id_map[cat["name"]] = row["id"]
            counts["categories"] += 1

        # چون در بکاپ نام دسته‌بندی ذخیره شده (نه شناسه عددی)، اینجا از روی نام پیدا می‌کنیم
        existing_categories = {c["name"]: c["id"] for c in [dict(r) for r in await db.fetch("SELECT id, name FROM categories")]}

        for mat in data.get("materials", []):
            cat_name = mat.get("category_name")
            cat_id = existing_categories.get(cat_name) or category_id_map.get(cat_name)
            if cat_id is None:
                continue  # دسته‌بندی نامعتبر، این مطلب رد می‌شود
            await db.execute(
                """INSERT INTO materials (category_id, title, content, file_id, file_type, level)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                cat_id, mat["title"], mat.get("content"), mat.get("file_id"),
                mat.get("file_type"), mat.get("level", "مبتدی")
            )
            counts["materials"] += 1

        for v in data.get("videos", []):
            await db.execute(
                "INSERT INTO videos (title, channel_username, message_id, level) VALUES ($1, $2, $3, $4)",
                v["title"], v["channel_username"], v["message_id"], v.get("level", "مبتدی")
            )
            counts["videos"] += 1

        for f in data.get("faqs", []):
            await db.execute("INSERT INTO faq (question, answer) VALUES ($1, $2)", f["question"], f["answer"])
            counts["faqs"] += 1

        if data.get("about"):
            await db.execute(
                "INSERT INTO about (id, content) VALUES (1, $1) ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content",
                data["about"]
            )
        if data.get("calendar"):
            await db.execute(
                "INSERT INTO calendar (id, content) VALUES (1, $1) ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content",
                data["calendar"]
            )

    return counts

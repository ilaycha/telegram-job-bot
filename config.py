import os
from enum import Enum, auto

# ============================================
# Конфигурация
# ============================================

# Telegram
VACANCY_BOT_TOKEN = os.getenv("VACANCY_BOT_TOKEN")
CLEANER_BOT_TOKEN = os.getenv("CLEANER_BOT_TOKEN")
MODERATION_GROUP_ID = int(os.getenv("MODERATION_GROUP_ID", "0"))
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "123456789").split(",")))

CHANNEL_USERNAME = "@poslesmenperm"
VACANCY_THREAD_ID = 5
RESUME_THREAD_ID = 72

# VK
VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID", "0"))
VK_CHAT_VACANCIES = int(os.getenv("VK_CHAT_VACANCIES", "0"))
VK_CHAT_RESUMES = int(os.getenv("VK_CHAT_RESUMES", "0"))

DB_NAME = "bot_database.db"

# ============================================
# Состояния Telegram (Enum)
# ============================================
class TGState(Enum):
    MAIN_MENU = 0
    V_TITLE = 1
    V_COMPANY = 2
    V_SALARY = 3
    V_SCHEDULE = 4
    V_DESCRIPTION = 5
    V_CONTACT = 6
    V_PREVIEW = 7
    R_NAME = 10
    R_AGE = 11
    R_POSITION = 12
    R_EXPERIENCE = 13
    R_SKILLS = 14
    R_EDUCATION = 15
    R_CONTACT = 16
    R_PREVIEW = 17

# ============================================
# Состояния VK
# ============================================
class VKState:
    MAIN_MENU = 'main_menu'
    V_TITLE = 'v_title'
    V_COMPANY = 'v_company'
    V_SALARY = 'v_salary'
    V_SCHEDULE = 'v_schedule'
    V_DESCRIPTION = 'v_description'
    V_CONTACT = 'v_contact'
    V_PREVIEW = 'v_preview'
    R_NAME = 'r_name'
    R_AGE = 'r_age'
    R_POSITION = 'r_position'
    R_EXPERIENCE = 'r_experience'
    R_SKILLS = 'r_skills'
    R_EDUCATION = 'r_education'
    R_CONTACT = 'r_contact'
    R_PREVIEW = 'r_preview'

# ============================================
# Формы для построителя шагов
# ============================================
VACANCY_FORM = [
    ("title", "📌 Название вакансии?", None, None),
    ("company", "🏢 Компания?", None, None),
    ("salary", "💰 Зарплата?", "v_skip_salary", "⏭ Пропустить"),
    ("schedule", "🕒 График работы?", "v_skip_schedule", "⏭ Пропустить"),
    ("description", "📋 Описание?", "v_skip_description", "⏭ Пропустить"),
    ("contact", "📞 Контакты?", None, None),
]

RESUME_FORM = [
    ("name", "👤 Ваше имя?", None, None),
    ("age", "🎂 Возраст?", "r_skip_age", "⏭ Пропустить"),
    ("position", "💼 Желаемая должность?", "r_skip_position", "⏭ Пропустить"),
    ("experience", "📅 Опыт работы?", "r_skip_experience", "⏭ Пропустить"),
    ("skills", "🛠 Ключевые навыки?", "r_skip_skills", "⏭ Пропустить"),
    ("education", "🎓 Образование?", "r_skip_education", "⏭ Пропустить"),
    ("contact", "📞 Контакты?", None, None),
]
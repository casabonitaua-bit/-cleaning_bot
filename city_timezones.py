"""
Часовые пояса для всех 27 городов бота.
Используется планировщиком для сравнения reminder_time с местным временем города.
"""

from zoneinfo import ZoneInfo

CITY_TIMEZONES: dict[str, str] = {
    "Москва":          "Europe/Moscow",       # UTC+3
    "Санкт-Петербург": "Europe/Moscow",       # UTC+3
    "Сочи":            "Europe/Moscow",       # UTC+3
    "Краснодар":       "Europe/Moscow",       # UTC+3
    "Мурманск":        "Europe/Moscow",       # UTC+3
    "Брянск":          "Europe/Moscow",       # UTC+3
    "Архангельск":     "Europe/Moscow",       # UTC+3
    "Петрозаводск":    "Europe/Moscow",       # UTC+3
    "Нижний Новгород": "Europe/Moscow",       # UTC+3
    "Саратов":         "Europe/Moscow",       # UTC+3
    "Волгоград":       "Europe/Moscow",       # UTC+3
    "Астрахань":       "Europe/Moscow",       # UTC+3
    "Владикавказ":     "Europe/Moscow",       # UTC+3
    "Нальчик":         "Europe/Moscow",       # UTC+3
    "Ставрополь":      "Europe/Moscow",       # UTC+3
    "Невинномысск":    "Europe/Moscow",       # UTC+3
    "Ессентуки":       "Europe/Moscow",       # UTC+3
    "Пятигорск":       "Europe/Moscow",       # UTC+3
    "Кисловодск":      "Europe/Moscow",       # UTC+3
    "Черкесск":        "Europe/Moscow",       # UTC+3
    "Грозный":         "Europe/Moscow",       # UTC+3
    "Самара":          "Europe/Samara",       # UTC+4
    "Сургут":          "Asia/Yekaterinburg",  # UTC+5
    "Магнитогорск":    "Asia/Yekaterinburg",  # UTC+5
    "Новокузнецк":     "Asia/Krasnoyarsk",    # UTC+7
    "Улан-Удэ":        "Asia/Irkutsk",        # UTC+8
    "Хабаровск":       "Asia/Vladivostok",    # UTC+10
}


def get_city_tz(city: str) -> ZoneInfo:
    """Вернуть ZoneInfo для города. Если не найден — московский пояс."""
    tz_name = CITY_TIMEZONES.get(city, "Europe/Moscow")
    return ZoneInfo(tz_name)

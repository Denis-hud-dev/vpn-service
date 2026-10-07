from bot.keyboards.main import back_keyboard
from bot.services import TARIFFS


def tariffs_keyboard():
    return back_keyboard(*((tariff.label, f"tariff:{tariff.id}") for tariff in TARIFFS.values()))

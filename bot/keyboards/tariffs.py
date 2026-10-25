from bot.keyboards.main import back_keyboard
from bot.services import TARIFFS, PILOT_TARIFFS


def tariffs_keyboard(*, pilot: bool = False):
    plans = PILOT_TARIFFS if pilot else TARIFFS
    keyboard = back_keyboard(*((tariff.label, f"tariff:{tariff.id}") for tariff in plans.values()))
    if pilot:
        for row in keyboard.inline_keyboard:
            for button in row:
                if (button.callback_data or "").startswith("tariff:"):
                    button.style = "success" if button.callback_data == "tariff:pilot_month_199" else "primary"
    return keyboard

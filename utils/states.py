from aiogram.fsm.state import State, StatesGroup


class RegistrationStates(StatesGroup):
    choosing_city = State()
    filling_name = State()
    filling_age = State()
    filling_phone = State()


class ShiftRegisterStates(StatesGroup):
    choosing_slot = State()


class AdminStates(StatesGroup):
    choosing_city = State()
    entering_date = State()
    entering_address = State()
    entering_payment = State()
    entering_conditions = State()
    entering_main_slots = State()
    entering_reserve_slots = State()
    entering_reminder_time = State()
    entering_morning_reminder_time = State()
    confirming = State()


class ShiftReportStates(StatesGroup):
    waiting_decline_reason = State()


class UnblockStates(StatesGroup):
    waiting_message = State()
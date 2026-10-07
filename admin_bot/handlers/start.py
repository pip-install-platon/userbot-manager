from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from admin_bot.keyboards.main_menu import back_to_menu, main_menu
from admin_bot.ui import edit_callback_message, h
from core.db.models import Operator

router = Router(name="start")


@router.message(CommandStart())
async def start(message: Message, operator: Operator) -> None:
    await message.answer(
        f"Здравствуйте, {h(operator.display_name)}.",
        reply_markup=main_menu(operator.is_superadmin),
    )


@router.callback_query(F.data == "menu:main")
async def show_menu(callback: CallbackQuery, operator: Operator) -> None:
    await edit_callback_message(
        callback,
        f"Здравствуйте, {h(operator.display_name)}.",
        main_menu(operator.is_superadmin),
    )


@router.callback_query(F.data == "menu:settings")
async def show_settings(callback: CallbackQuery, operator: Operator) -> None:
    role = "супер-админ" if operator.is_superadmin else "оператор"
    active = "да" if operator.is_active else "нет"
    username = f"@{h(operator.username)}" if operator.username else "не задан"
    text = (
        f"Имя: {h(operator.display_name)}\n"
        f"Telegram ID: <code>{operator.telegram_user_id}</code>\n"
        f"Username: {username}\n"
        f"Роль: {role}\n"
        f"Активен: {active}"
    )
    await edit_callback_message(callback, text, back_to_menu())


@router.message()
async def fallback(message: Message, operator: Operator) -> None:
    await message.answer(
        "Выберите действие в меню.",
        reply_markup=main_menu(operator.is_superadmin),
    )


@router.callback_query()
async def unknown_callback(callback: CallbackQuery) -> None:
    await callback.answer("Действие недоступно.")

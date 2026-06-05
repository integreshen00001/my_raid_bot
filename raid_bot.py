import asyncio
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.types import ChatInviteLink
from aiogram.exceptions import TelegramBadRequest

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TelegramAntiRaidBot:
    """Бот для защиты Telegram-группы от рейдов и спама"""
    
    def __init__(self, bot_token: str, group_id: int):
        self.bot = Bot(token=bot_token)
        self.dp = Dispatcher()
        self.group_id = group_id
        
        # Хранилище для отслеживания заходов
        self.join_tracker: Dict[int, List[datetime]] = defaultdict(list)
        
        # Хранилище для отслеживания сообщений (спам)
        self.message_tracker: Dict[int, List[datetime]] = defaultdict(list)
        
        # Флаг, указывающий, что идет рейд
        self.raid_mode: bool = False
        self.raid_start_time: Optional[datetime] = None
        
        # === НАСТРОЙКИ (меняйте под себя) ===
        self.JOIN_WINDOW_SECONDS = 10     # Окно в секундах
        self.RAID_TRIGGER_COUNT = 5       # При скольких заходах за окно включается рейд-режим
        self.RAID_BAN_DURATION = 60 * 60  # Бан на время рейда (60 минут)
        
        # Настройки антиспама
        self.MAX_MESSAGES_PER_WINDOW = 5   # Максимум сообщений за окно
        self.MESSAGE_WINDOW_SECONDS = 3    # Окно для антиспама
        self.SPAM_BAN_DURATION = 30 * 60   # Бан за спам на 30 минут
        
        # Слова для фильтрации
        self.BANNED_WORDS = [
            'реклама', 'скам', 'заработок', 'биткоин', 'крипта',
            'майнинг', 'халява', 'бесплатно', '💰'
        ]
        
        # Текущая ссылка
        self.current_invite_link: Optional[str] = None
        
        # Регистрируем обработчики
        self._register_handlers()
    
    def _register_handlers(self):
        """Регистрация обработчиков"""
        
        @self.dp.message(Command("status"))
        async def cmd_status(message: types.Message):
            if not await self._is_admin(message.from_user.id, message.chat.id):
                await message.answer("⛔ Только для администраторов!")
                return
            
            await message.answer(f"📊 Режим рейда: {'АКТИВЕН' if self.raid_mode else 'ВЫКЛЮЧЕН'}")
        
        @self.dp.message(Command("resetraid"))
        async def cmd_reset_raid(message: types.Message):
            if not await self._is_admin(message.from_user.id, message.chat.id):
                await message.answer("⛔ Только для администраторов!")
                return
            
            self.raid_mode = False
            self.raid_start_time = None
            self.join_tracker.clear()
            await message.answer("✅ Режим рейда сброшен.")
        
        @self.dp.message(Command("newlink"))
        async def cmd_new_link(message: types.Message):
            if not await self._is_admin(message.from_user.id, message.chat.id):
                await message.answer("⛔ Только для администраторов!")
                return
            
            await self._refresh_invite_link(message.chat.id)
            await message.answer(f"✅ Новая ссылка: {self.current_invite_link}")
        
        # Обработчик новых участников
        @self.dp.chat_member()
        async def on_new_member(event: types.ChatMemberUpdated):
            if event.chat.id != self.group_id:
                return
            
            old_status = event.old_chat_member.status
            new_status = event.new_chat_member.status
            
            if old_status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED] and \
               new_status == ChatMemberStatus.MEMBER:
                user_id = event.new_chat_member.user.id
                username = event.new_chat_member.user.username or f"id{user_id}"
                await self._on_user_join(user_id, username, event.chat.id)
        
        # Обработчик сообщений
        @self.dp.message()
        async def on_message(message: types.Message):
            if message.chat.id != self.group_id:
                return
            
            if not message.text:
                return
            
            is_spam, reason = await self._check_spam(message.from_user.id, message.text)
            if is_spam:
                await self._ban_user(message.chat.id, message.from_user.id, reason, self.SPAM_BAN_DURATION)
                await message.answer(f"⛔ {message.from_user.full_name} забанен за спам.")
                return
            
            if self.raid_mode:
                await self._ban_user(message.chat.id, message.from_user.id, "Рейд-атака", self.RAID_BAN_DURATION)
                await message.delete()
    
    async def _is_admin(self, user_id: int, chat_id: int) -> bool:
        try:
            chat_member = await self.bot.get_chat_member(chat_id, user_id)
            return chat_member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
        except:
            return False
    
    async def _on_user_join(self, user_id: int, username: str, chat_id: int):
        now = datetime.now()
        
        self.join_tracker[chat_id] = [t for t in self.join_tracker[chat_id] 
                                      if now - t < timedelta(seconds=self.JOIN_WINDOW_SECONDS)]
        self.join_tracker[chat_id].append(now)
        current_joins = len(self.join_tracker[chat_id])
        
        logger.info(f"📥 Новый: {username} (заходов: {current_joins})")
        
        if current_joins >= self.RAID_TRIGGER_COUNT and not self.raid_mode:
            await self._activate_raid_mode(chat_id)
        
        if self.raid_mode:
            await self._ban_user(chat_id, user_id, "Рейд-атака", self.RAID_BAN_DURATION)
    
    async def _check_spam(self, user_id: int, text: str):
        now = datetime.now()
        text_lower = text.lower()
        
        for bad_word in self.BANNED_WORDS:
            if bad_word in text_lower:
                return True, f"Запрещенное слово: {bad_word}"
        
        self.message_tracker[user_id] = [t for t in self.message_tracker[user_id]
                                         if now - t < timedelta(seconds=self.MESSAGE_WINDOW_SECONDS)]
        self.message_tracker[user_id].append(now)
        
        if len(self.message_tracker[user_id]) >= self.MAX_MESSAGES_PER_WINDOW:
            return True, "Флуд"
        
        return False, ""
    
    async def _activate_raid_mode(self, chat_id: int):
        self.raid_mode = True
        self.raid_start_time = datetime.now()
        
        logger.warning("🚨 РЕЖИМ РЕЙДА АКТИВИРОВАН!")
        await self.bot.send_message(chat_id, "🚨 ОБНАРУЖЕНА РЕЙД-АТАКА! Включена защита.")
        await self._refresh_invite_link(chat_id)
        
        asyncio.create_task(self._auto_disable_raid(chat_id))
    
    async def _auto_disable_raid(self, chat_id: int):
        await asyncio.sleep(300)
        if self.raid_mode:
            self.raid_mode = False
            await self.bot.send_message(chat_id, "🟢 Режим рейда отключен.")
    
    async def _refresh_invite_link(self, chat_id: int):
        try:
            new_link = await self.bot.create_chat_invite_link(
                chat_id=chat_id,
                member_limit=1,
                name=f"raid_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )
            self.current_invite_link = new_link.invite_link
            logger.info(f"✅ Новая ссылка: {self.current_invite_link}")
        except Exception as e:
            logger.error(f"Ошибка создания ссылки: {e}")
    
    async def _ban_user(self, chat_id: int, user_id: int, reason: str, duration: int = None):
        try:
            if duration:
                until_date = datetime.now() + timedelta(seconds=duration)
                await self.bot.restrict_chat_member(
                    chat_id, user_id,
                    permissions=types.ChatPermissions(can_send_messages=False),
                    until_date=until_date
                )
            else:
                await self.bot.ban_chat_member(chat_id, user_id)
            logger.info(f"🔨 Забанен {user_id}: {reason}")
        except Exception as e:
            logger.error(f"Ошибка бана: {e}")
    
    async def run(self):
        logger.info(f"🚀 Бот запущен. Защита группы {self.group_id}")
        await self.dp.start_polling(self.bot)


async def main():
    # === НАСТРОЙКИ (ЗАМЕНИТЕ НА СВОИ) ===
    BOT_TOKEN = "8760784450:AAEG_-hEp9KQph0Tlxf052dcWEAUNb8aAI0"  # Ваш токен
    GROUP_ID = -1003993776489  # ID вашей группы
    
    bot = TelegramAntiRaidBot(BOT_TOKEN, GROUP_ID)
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())
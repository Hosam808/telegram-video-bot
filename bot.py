import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import yt_dlp

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# التوكن - ضعه هنا مباشرة أو في متغير البيئة BOT_TOKEN
TOKEN = os.environ.get('BOT_TOKEN', 'ضع_التوكن_هنا')

# مجلد التحميلات
DOWNLOAD_DIR = 'downloads'
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# قاموس لتخزين روابط المستخدمين
user_links = {}

# إعدادات متقدمة لتجاوز حظر يوتيوب (Bot Detection Bypass)
YOUTUBE_EXTRACTOR_ARGS = {
    'youtube': {
        'player_client': ['ios', 'android', 'mweb'],
        'skip': ['webpage', 'configs']
    }
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎥 **أهلاً بيك في بوت تحميل الفيديوهات!**\n\n"
        "أنا بوت بسيط وسريع وهساعدك تحمل الفيديوهات من انستجرام، يوتيوب، تيك توك، فيسبوك، وتويتر وغيرها.\n\n"
        "🎯 كل اللي عليك:\n"
        "• ابعتلي رابط الفيديو مباشرة.\n"
        "• لو الفيديو من يوتيوب: هتختار الجودة المناسبة.\n"
        "• لو من انستجرام أو منصة تانية: هحملهولك فوراً بأفضل جودة وبصوت واضح.\n\n"
        "📝 **ملحوظة**: البوت بيدعم الفيديوهات العامة فقط.\n\n"
        "يلا، ابعتلي الرابط! 🚀",
        parse_mode='Markdown'
    )

def _extract_youtube_info_sync(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'extractor_args': YOUTUBE_EXTRACTOR_ARGS,
        'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = []
        seen = set()
        
        for f in info.get('formats', []):
            if f.get('height') and f.get('ext') == 'mp4':
                fid = f['format_id']
                if fid not in seen:
                    seen.add(fid)
                    filesize = f.get('filesize') or f.get('filesize_approx')
                    if filesize:
                        size_mb = filesize / (1024 * 1024)
                        if size_mb > 50:
                            continue
                        size_str = f"{size_mb:.1f}MB"
                    else:
                        size_str = "حجم غير معروف"
                    
                    has_audio = f.get('acodec') != 'none'
                    has_video = f.get('vcodec') != 'none'
                    
                    if has_video:
                        formats.append({
                            'format_id': fid,
                            'resolution': f"{f['height']}p",
                            'ext': f['ext'],
                            'size': size_str,
                            'has_audio': has_audio
                        })
        
        return info.get('title', 'video'), formats

async def get_youtube_formats(url):
    try:
        return await asyncio.to_thread(_extract_youtube_info_sync, url)
    except Exception as e:
        logger.error(f"خطأ في استخراج الصيغ: {e}")
        raise

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    user_id = update.effective_user.id
    
    user_links[user_id] = url
    
    msg = await update.message.reply_text("⏳ بجيب البيانات...")

    if 'youtube.com' in url or 'youtu.be' in url:
        try:
            title, formats = await get_youtube_formats(url)
            
            if not formats:
                await msg.edit_text("❌ ما لقيتش جودات متاحة أقل من 50MB للفيديو ده.")
                return
            
            formats.sort(key=lambda x: int(x['resolution'].replace('p', '')), reverse=True)
            
            keyboard = []
            for f in formats[:10]:
                audio_icon = "🔊" if f['has_audio'] else "🔇"
                label = f"{audio_icon} {f['resolution']} - {f['ext']} ({f['size']})"
                callback_data = f"dl|{f['format_id']}"
                keyboard.append([InlineKeyboardButton(label, callback_data=callback_data)])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await msg.edit_text(
                f"📹 *{title[:200]}*\n\nاختر الجودة المناسبة:\n🔊 = بصوت | 🔇 = بدون صوت",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"خطأ في معالجة رابط يوتيوب: {e}")
            await msg.edit_text(f"❌ حصل خطأ: {str(e)[:200]}")
    else:
        await download_and_send(url, chat_id=update.effective_chat.id, msg_to_edit=msg, context=context, format_id='best')

def _download_sync(url, format_id):
    output_template = os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s')
    
    if 'youtube.com' in url or 'youtu.be' in url:
        fmt = format_id if format_id != 'best' else 'best[vcodec!=none][acodec!=none]/best'
    else:
        fmt = 'best[vcodec!=none][acodec!=none]/best'

    ydl_opts = {
        'format': fmt,
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'restrictfilenames': True,
        'extractor_args': YOUTUBE_EXTRACTOR_ARGS,
        'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        
        if not os.path.exists(filename):
            base = filename.rsplit('.', 1)[0]
            for ext in ['mp4', 'webm', 'mkv', 'mov']:
                test_path = f"{base}.{ext}"
                if os.path.exists(test_path):
                    filename = test_path
                    break
                    
        return filename, info.get('title', 'الفيديو')

async def download_and_send(url, chat_id, msg_to_edit, context, format_id='best'):
    filename = None
    try:
        filename, title = await asyncio.to_thread(_download_sync, url, format_id)
        
        if not filename or not os.path.exists(filename):
            await msg_to_edit.edit_text("❌ لم يتم العثور على الملف المحمل.")
            return

        file_size = os.path.getsize(filename) / (1024 * 1024)
        if file_size > 50:
            os.remove(filename)
            await msg_to_edit.edit_text(f"❌ حجم الفيديو كبير جداً ({file_size:.1f}MB). الحد الأقصى لتليجرام 50MB.")
            return
        
        await msg_to_edit.edit_text("📤 جاري رفع الفيديو...")
        
        with open(filename, 'rb') as video:
            await context.bot.send_video(
                chat_id=chat_id,
                video=video,
                caption=f"🎬 {title[:200]}",
                supports_streaming=True
            )
        
        try:
            await msg_to_edit.delete()
        except Exception:
            pass
            
    except Exception as e:
        logger.error(f"خطأ في التحميل: {e}")
        await msg_to_edit.edit_text(f"❌ فشل التحميل: {str(e)[:200]}")
    finally:
        if filename and os.path.exists(filename):
            try:
                os.remove(filename)
            except Exception:
                pass

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data.startswith('dl|'):
        format_id = data.split('|')[1]
        url = user_links.get(user_id)
        
        if not url:
            await query.edit_message_text("❌ انتهت الجلسة. من فضلك ابعت الرابط تاني.")
            return
            
        await query.edit_message_text("⏳ جاري تحميل الجودة المختارة...")
        await download_and_send(url, chat_id=update.effective_chat.id, msg_to_edit=query.message, context=context, format_id=format_id)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text("❌ حصل خطأ غير متوقع. جرب تاني.")
    except Exception:
        pass

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)
    
    logger.info("✅ البوت شغال بنجاح...")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

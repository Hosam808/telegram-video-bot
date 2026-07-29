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

# التوكن - هنستخدم متغير البيئة
TOKEN = os.environ.get('BOT_TOKEN', 'ضع_التوكن_هنا')

# مجلد التحميلات
DOWNLOAD_DIR = 'downloads'
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# قاموس لتخزين روابط المستخدمين
user_links = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎥 **أهلاً بيك في بوت تحميل الفيديوهات!**\n\n"
        "مبسوط إني معاك، أنا بوت بسيط وسريع وهساعدك تحمل الفيديوهات من أكتر من 1500 موقع، زي يوتيوب، تيك توك، انستجرام، فيسبوك، وتويتر.\n\n"
        "🎯 اللي هتعمله:\n"
        "• تبعتلي رابط الفيديو\n"
        "• لو الفيديو من يوتيوب: هتظهرلك كل الجودات المتاحة، تختار اللي يناسبك وتبدأ التحميل.\n"
        "• لو الفيديو من أي منصة تانية: هحملهولك مباشرة بأفضل جودة متاحة.\n\n"
        "📝 **ملحوظة مهمة**: البوت بيدعم الفيديوهات العامة فقط. لو الفيديو خاص أو محتاج تسجيل دخول، مش هقدر أحمله.\n\n"
        "يلا، ابعتلي رابط الفيديو! 🚀",
        parse_mode='Markdown'
    )

async def get_youtube_formats(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }
    
    try:
        with yt-dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = []
            seen = set()
            
            for f in info['formats']:
                if f.get('height') and f.get('ext') == 'mp4':
                    fid = f['format_id']
                    if fid not in seen:
                        seen.add(fid)
                        filesize = f.get('filesize')
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
                await msg.edit_text("❌ ما لقيتش جودات متاحة للفيديو ده (يمكن حجمه أكبر من 50MB).")
                return
            
            formats.sort(key=lambda x: int(x['resolution'].replace('p', '')), reverse=True)
            
            keyboard = []
            for f in formats[:10]:
                audio_icon = "🔊" if f['has_audio'] else "🔇"
                label = f"{audio_icon} {f['resolution']} - {f['ext']} ({f['size']})"
                callback_data = f"dl|{f['format_id']}|{f['resolution']}"
                keyboard.append([InlineKeyboardButton(label, callback_data=callback_data)])
            
            if not keyboard:
                await msg.edit_text("❌ مفيش صيغ مناسبة متاحة.")
                return
                
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
        await download_and_send(url, update, msg, 'best')

async def download_and_send(url, update_or_query, msg, format_id='best'):
    try:
        output_template = os.path.join(DOWNLOAD_DIR, '%(title).100s.%(ext)s')
        
        ydl_opts = {
            'format': format_id if format_id != 'best' else 'best[filesize<50M]',
            'outtmpl': output_template,
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'restrictfilenames': True,
        }
        
        with yt-dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            if not os.path.exists(filename):
                base = filename.rsplit('.', 1)[0]
                for ext in ['mp4', 'webm', 'mkv']:
                    test_path = f"{base}.{ext}"
                    if os.path.exists(test_path):
                        filename = test_path
                        break
        
        file_size = os.path.getsize(filename) / (1024 * 1024)
        if file_size > 50:
            os.remove(filename)
            await msg.edit_text(f"❌ حجم الفيديو كبير جداً ({file_size:.1f}MB). الحد الأقصى 50MB.")
            return
        
        await msg.edit_text("📤 بيرفع الفيديو...")
        
        if isinstance(update_or_query, Update):
            with open(filename, 'rb') as video:
                await update_or_query.message.reply_video(
                    video,
                    caption=f"🎬 {info.get('title', 'الفيديو')[:200]}",
                    supports_streaming=True
                )
        else:
            with open(filename, 'rb') as video:
                await update_or_query.message.reply_video(
                    video,
                    caption=f"🎬 {info.get('title', 'الفيديو')[:200]}",
                    supports_streaming=True
                )
        
        os.remove(filename)
        try:
            await msg.delete()
        except:
            pass
            
    except Exception as e:
        logger.error(f"خطأ في التحميل: {e}")
        await msg.edit_text(f"❌ فشل التحميل: {str(e)[:200]}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data.startswith('dl|'):
        _, format_id, resolution = data.split('|')
        
        url = user_links.get(user_id)
        
        if not url:
            await query.edit_message_text("❌ انتهت الجلسة. من فضلك ابعت الرابط تاني.")
            return
            
        await query.edit_message_text(f"⏳ بيحمل الجودة {resolution}...")
        await download_and_send(url, query, query.message, format_id)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text("❌ حصل خطأ غير متوقع. جرب تاني.")
    except:
        pass

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)
    
    logger.info("✅ البوت شغال على السيرفر...")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

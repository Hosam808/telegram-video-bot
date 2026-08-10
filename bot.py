import os
import re
import time
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import yt_dlp

# ==================== إعداد التسجيل ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== التوكن ====================
# مهم جداً: التوكن لازم ييجي من environment variable فقط.
# ماتكتبش التوكن جوه الكود أبداً (لو اتسرب هيقدر أي حد يستخدم بوتك).
TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    raise RuntimeError(
        "❌ لازم تحدد BOT_TOKEN كـ environment variable قبل ما تشغل البوت.\n"
        "مثال: export BOT_TOKEN='التوكن_بتاعك'"
    )

# ==================== إعدادات عامة ====================
DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

MAX_FILE_SIZE_MB = 50
LINK_TTL_SECONDS = 30 * 60          # مدة صلاحية الرابط المخزن للمستخدم
RATE_LIMIT_SECONDS = 3              # أقل فترة بين طلب وطلب لنفس المستخدم
MAX_CONCURRENT_DOWNLOADS = 3        # أقصى عدد تحميلات شغالة في نفس الوقت

# قاموس لتخزين روابط المستخدمين مع وقت الإضافة (لمنع تضخم الذاكرة)
user_links = {}            # user_id -> {'url': str, 'ts': float}
user_last_request = {}     # user_id -> timestamp آخر طلب (لمنع الفلود / السبام)

download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# نطاقات مدعومة فقط - أي رابط برا القايمة دي بيترفض فوراً (حماية من روابط عشوائية/سبام)
SUPPORTED_DOMAINS = (
    'youtube.com', 'youtu.be',
    'instagram.com',
    'tiktok.com',
    'facebook.com', 'fb.watch',
    'twitter.com', 'x.com',
    'threads.net',
)

URL_REGEX = re.compile(r'https?://\S+', re.IGNORECASE)

# إعدادات لتجاوز حظر يوتيوب
YOUTUBE_EXTRACTOR_ARGS = {
    'youtube': {
        'player_client': ['ios', 'android', 'mweb'],
        'skip': ['webpage', 'configs']
    }
}

MOBILE_USER_AGENT = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'
)

# امتدادات نعتبرها صور / صوت / فيديو عشان نبعتها بالطريقة الصح على تليجرام
IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp'}
AUDIO_EXTS = {'mp3', 'm4a', 'aac', 'opus', 'wav'}


# ==================== أدوات مساعدة ====================
def is_supported_url(url: str) -> bool:
    """يتحقق إن الرابط فعلاً رابط http/https وبيتبع منصة مدعومة."""
    if not URL_REGEX.match(url.strip()):
        return False
    return any(domain in url for domain in SUPPORTED_DOMAINS)


def is_rate_limited(user_id: int) -> bool:
    """يمنع نفس المستخدم من إرسال طلبات متتالية بسرعة (anti-flood)."""
    now = time.time()
    last = user_last_request.get(user_id, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return True
    user_last_request[user_id] = now
    return False


def cleanup_expired_links():
    """تنظيف الروابط القديمة من الذاكرة بعد انتهاء صلاحيتها."""
    now = time.time()
    expired = [uid for uid, data in user_links.items() if now - data['ts'] > LINK_TTL_SECONDS]
    for uid in expired:
        user_links.pop(uid, None)


# ==================== أوامر البوت ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎥 **أهلاً بيك في بوت تحميل الفيديوهات والصور والصوتيات!**\n\n"
        "أنا بوت بسيط وسريع وهساعدك تحمل الفيديوهات، الصور، أو مقاطع الصوت MP3 "
        "من يوتيوب، انستجرام، تيك توك، فيسبوك، وغيرها.\n\n"
        "🎯 كل اللي عليك:\n"
        "• ابعتلي رابط المنشور أو الفيديو.\n"
        "• اختر الجودة أو الصيغة المناسبة.\n\n"
        "يلا، ابعتلي الرابط! 🚀",
        parse_mode='Markdown'
    )


def _extract_youtube_info_sync(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'extractor_args': YOUTUBE_EXTRACTOR_ARGS,
        'user_agent': MOBILE_USER_AGENT,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = []
        seen = set()

        for f in info.get('formats', []):
            if f.get('height') and f.get('ext') == 'mp4':
                fid = f['format_id']
                res = f"{f['height']}p"
                if res not in seen:
                    seen.add(res)
                    filesize = f.get('filesize') or f.get('filesize_approx')
                    if filesize:
                        size_mb = filesize / (1024 * 1024)
                        if size_mb > MAX_FILE_SIZE_MB:
                            continue
                        size_str = f"{size_mb:.1f}MB"
                    else:
                        size_str = "حجم غير معروف"

                    formats.append({
                        'format_id': fid,
                        'resolution_num': int(f['height']),
                        'resolution': res,
                        'size': size_str,
                    })

        return info.get('title', 'video'), formats


async def get_youtube_formats(url):
    try:
        return await asyncio.to_thread(_extract_youtube_info_sync, url)
    except Exception as e:
        logger.error(f"خطأ في استخراج الصيغ: {e}")
        raise


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip()
    user_id = update.effective_user.id

    cleanup_expired_links()

    # 1) تجاهل أي حاجة مش رابط حقيقي لمنصة مدعومة (يمنع السبام/الروابط العشوائية)
    if not is_supported_url(text):
        await update.message.reply_text(
            "❌ ابعتلي رابط صحيح من يوتيوب / انستجرام / تيك توك / فيسبوك / تويتر (X) فقط."
        )
        return

    # 2) Rate limiting لمنع الفلود
    if is_rate_limited(user_id):
        await update.message.reply_text("⏳ استنى شوية قبل ما تبعت طلب تاني.")
        return

    user_links[user_id] = {'url': text, 'ts': time.time()}
    msg = await update.message.reply_text("⏳ بجيب البيانات...")

    if 'youtube.com' in text or 'youtu.be' in text:
        try:
            title, formats = await get_youtube_formats(text)

            if not formats:
                await msg.edit_text(f"❌ ما لقيتش جودات متاحة أقل من {MAX_FILE_SIZE_MB}MB للفيديو ده.")
                return

            formats.sort(key=lambda x: x['resolution_num'])

            keyboard = []
            for f in formats[:8]:
                label = f"🎬 فيديو {f['resolution']} ({f['size']})"
                callback_data = f"dl|{f['format_id']}|video"
                keyboard.append([InlineKeyboardButton(label, callback_data=callback_data)])

            keyboard.append([InlineKeyboardButton("🎵 تحميل بصيغة صوت MP3", callback_data="dl|bestaudio|audio")])

            reply_markup = InlineKeyboardMarkup(keyboard)
            await msg.edit_text(
                f"📹 *{title[:200]}*\n\nاختر الجودة أو الصيغة المطلوبة:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

        except Exception as e:
            logger.error(f"خطأ في معالجة رابط يوتيوب: {e}")
            await msg.edit_text(f"❌ حصل خطأ: {str(e)[:200]}")
    else:
        # للمنصات التانية (انستجرام/تيك توك/فيسبوك/تويتر...)
        # ملحوظة: بعض المنشورات (زي صور انستجرام) هيتم اكتشافها تلقائياً
        # عند التحميل وهتتبعت كصورة مش فيديو (شوف download_and_send).
        keyboard = [
            [InlineKeyboardButton("⬇️ تحميل (فيديو أو صورة - أفضل جودة)", callback_data="dl|best|video")],
            [InlineKeyboardButton("🎵 تحميل الصوت MP3 فقط", callback_data="dl|bestaudio|audio")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.edit_text(
            "اختر نوع التحميل المطلوب:",
            reply_markup=reply_markup
        )


def _download_sync(url, format_id, is_audio=False):
    output_template = os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s')

    if is_audio:
        fmt = 'bestaudio/best'
    else:
        if 'youtube.com' in url or 'youtu.be' in url:
            fmt = format_id if format_id != 'best' else 'best[vcodec!=none][acodec!=none]/best'
        else:
            # يخلي yt-dlp يختار أفضل حاجة متاحة، سواء فيديو أو صورة (زي منشورات انستجرام الصور)
            fmt = 'best'

    ydl_opts = {
        'format': fmt,
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'restrictfilenames': True,
        'extractor_args': YOUTUBE_EXTRACTOR_ARGS,
        'user_agent': MOBILE_USER_AGENT,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

        if not os.path.exists(filename):
            base = filename.rsplit('.', 1)[0]
            for ext in ['mp4', 'm4a', 'mp3', 'webm', 'mkv', 'mov', 'jpg', 'jpeg', 'png', 'webp']:
                test_path = f"{base}.{ext}"
                if os.path.exists(test_path):
                    filename = test_path
                    break

        return filename, info.get('title', 'الملف')


async def download_and_send(url, chat_id, msg_to_edit, context, format_id='best', mode='video'):
    filename = None
    is_audio = (mode == 'audio')

    async with download_semaphore:
        try:
            filename, title = await asyncio.to_thread(_download_sync, url, format_id, is_audio)

            if not filename or not os.path.exists(filename):
                await msg_to_edit.edit_text("❌ لم يتم العثور على الملف المحمل.")
                return

            file_size = os.path.getsize(filename) / (1024 * 1024)
            if file_size > MAX_FILE_SIZE_MB:
                os.remove(filename)
                await msg_to_edit.edit_text(
                    f"❌ حجم الملف كبير جداً ({file_size:.1f}MB). الحد الأقصى لتليجرام {MAX_FILE_SIZE_MB}MB."
                )
                return

            await msg_to_edit.edit_text("📤 جاري إرسال الملف...")

            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

            with open(filename, 'rb') as file_data:
                if is_audio or ext in AUDIO_EXTS:
                    await context.bot.send_audio(
                        chat_id=chat_id,
                        audio=file_data,
                        caption=f"🎵 {title[:200]}",
                        title=title[:50]
                    )
                elif ext in IMAGE_EXTS:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=file_data,
                        caption=f"🖼️ {title[:200]}"
                    )
                else:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=file_data,
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
        parts = data.split('|')
        format_id = parts[1]
        mode = parts[2] if len(parts) > 2 else 'video'

        link_data = user_links.get(user_id)

        if not link_data:
            await query.edit_message_text("❌ انتهت الجلسة. من فضلك ابعت الرابط تاني.")
            return

        url = link_data['url']

        status_text = "⏳ جاري تحميل الصوت MP3..." if mode == 'audio' else "⏳ جاري التحميل..."
        await query.edit_message_text(status_text)
        await download_and_send(
            url, chat_id=update.effective_chat.id, msg_to_edit=query.message,
            context=context, format_id=format_id, mode=mode
        )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text("❌ حصل خطأ غير متوقع. جرب تاني.")
    except Exception:
        pass


def main():
    app = Application.builder().token(TOKEN).build()

    # ملحوظة: filters.ChatType.PRIVATE بتخلي البوت يتفاعل بس في المحادثات الخاصة
    # وده بيمنع إن أي حد يستخدم بوتك جوه جروبات عامة عشان يعمل سبام/إعلانات.
    # لو محتاج البوت يشتغل جوه جروبات، شيل PRIVATE واعمل whitelist لمعرفات الجروبات بدل كده.
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_message
    ))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    logger.info("✅ البوت شغال بنجاح...")

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

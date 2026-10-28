
import os
from datetime import datetime, timedelta
from pathlib import Path
from io import BytesIO
import mimetypes
from supabase import create_client

from dotenv import load_dotenv
from flask import session, Flask, render_template, request, redirect, url_for, flash, send_from_directory, send_file, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from pywebpush import webpush, WebPushException
from py_vapid import Vapid
import uuid
import re
import shutil
import tempfile
from sqlalchemy.exc import IntegrityError
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from email.message import EmailMessage
import smtplib
import ssl
import warnings
import zipfile
from PIL import Image, UnidentifiedImageError
from cover_video_processing import (
    MAX_BYTES as COVER_VIDEO_MAX_BYTES,
    CoverVideoError,
    normalize_cover_video,
    validate_cover_video,
)

IMAGE_UPLOAD_FORMATS = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".gif": "GIF",
    ".webp": "WEBP",
}

IMAGE_UPLOAD_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def validate_uploaded_image(file_bytes, extension):
    """Reject malformed images and files whose contents contradict their extension."""
    extension = extension.lower()
    expected_format = IMAGE_UPLOAD_FORMATS.get(extension)

    if expected_format is None:
        raise ValueError("This image format is not supported.")

    if not file_bytes:
        raise ValueError("The uploaded image is empty.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)

            with Image.open(BytesIO(file_bytes)) as image:
                if image.format != expected_format:
                    raise ValueError("The image contents do not match the file extension.")
                if image.width < 1 or image.height < 1:
                    raise ValueError("The image dimensions are invalid.")
                image.verify()

            # Reopen after verify(), then decode the image data.
            with Image.open(BytesIO(file_bytes)) as image:
                if image.format != expected_format:
                    raise ValueError("The image contents do not match the file extension.")
                image.load()

    except ValueError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("The image is invalid, incomplete, or too large to process safely.") from exc

    return IMAGE_UPLOAD_MIME_TYPES[extension]


ATTACHMENT_MIME_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "txt": "text/plain",
    "csv": "text/csv",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "zip": "application/zip",
    "webm": "video/webm",
    "ogg": "audio/ogg",
    "mp4": "video/mp4",
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
}


def validate_uploaded_attachment(file_bytes, extension):
    """Check basic file signatures without extracting uploaded archives."""
    ext = extension.lower().lstrip(".")
    if not file_bytes:
        raise ValueError("The uploaded file is empty.")

    signatures = {
        "pdf": lambda b: b.startswith(b"%PDF-"),
        "doc": lambda b: b.startswith(bytes.fromhex("D0CF11E0A1B11AE1")),
        "xls": lambda b: b.startswith(bytes.fromhex("D0CF11E0A1B11AE1")),
        "ppt": lambda b: b.startswith(bytes.fromhex("D0CF11E0A1B11AE1")),
        "webm": lambda b: b.startswith(bytes.fromhex("1A45DFA3")),
        "ogg": lambda b: b.startswith(b"OggS"),
        "mp4": lambda b: len(b) >= 8 and b[4:8] == b"ftyp",
        "m4a": lambda b: len(b) >= 8 and b[4:8] == b"ftyp",
        "wav": lambda b: len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WAVE",
        "mp3": lambda b: b.startswith(b"ID3") or (
            len(b) >= 2 and b[0] == 0xFF and (b[1] & 0xE0) == 0xE0
        ),
    }

    if ext in ("txt", "csv"):
        if b"\x00" in file_bytes:
            raise ValueError("The text file contains invalid binary data.")
        return

    if ext in ("zip", "docx", "xlsx", "pptx"):
        try:
            with zipfile.ZipFile(BytesIO(file_bytes)) as archive:
                entries = archive.infolist()
                names = {entry.filename for entry in entries}

                if not entries or len(entries) > 10000:
                    raise ValueError("The archive has an invalid number of files.")

                expanded_total = 0
                for entry in entries:
                    name = entry.filename.replace("\\\\", "/")
                    parts = name.split("/")
                    if name.startswith("/") or any(part == ".." for part in parts):
                        raise ValueError("The archive contains an unsafe file path.")
                    if entry.flag_bits & 0x1:
                        raise ValueError("Password-protected archives are not supported.")
                    expanded_total += entry.file_size
                    if expanded_total > 200 * 1024 * 1024:
                        raise ValueError("The expanded archive is too large.")
                    if entry.file_size > 1024 * 1024 and entry.compress_size == 0:
                        raise ValueError("The archive contains suspicious compressed data.")
                    if entry.compress_size and entry.file_size > 1000 * entry.compress_size:
                        raise ValueError("The archive compression ratio is too high.")

                required = {
                    "docx": {"[Content_Types].xml", "word/document.xml"},
                    "xlsx": {"[Content_Types].xml", "xl/workbook.xml"},
                    "pptx": {"[Content_Types].xml", "ppt/presentation.xml"},
                }
                if ext in required and not required[ext].issubset(names):
                    raise ValueError("The Office file contents do not match its extension.")

                if ext == "zip" and not file_bytes.startswith(b"PK"):
                    raise ValueError("The archive signature is invalid.")
        except ValueError:
            raise
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            raise ValueError("The uploaded archive is invalid.") from exc
        return

    check = signatures.get(ext)
    if check and not check(file_bytes):
        raise ValueError("The file contents do not match its extension.")

    # Require enough structure to reject files containing only a short header.
    if ext == "ogg":
        if len(file_bytes) < 27 or not file_bytes.startswith(b"OggS") or file_bytes[4] != 0:
            raise ValueError("The OGG file is incomplete or invalid.")
        segment_count = file_bytes[26]
        if len(file_bytes) < 27 + segment_count:
            raise ValueError("The OGG file is incomplete or invalid.")
        page_size = 27 + segment_count + sum(file_bytes[27:27 + segment_count])
        if len(file_bytes) <= page_size:
            raise ValueError("The OGG file contains no audio data.")

    elif ext == "webm":
        if len(file_bytes) < 16:
            raise ValueError("The WebM file is incomplete or invalid.")

    elif ext in ("mp4", "m4a"):
        if len(file_bytes) < 16 or file_bytes[4:8] != b"ftyp":
            raise ValueError("The media file is incomplete or invalid.")
        box_size = int.from_bytes(file_bytes[:4], "big")
        if box_size not in (0, 1) and box_size < 16:
            raise ValueError("The media file has an invalid header.")

    elif ext == "wav":
        if len(file_bytes) < 44 or file_bytes[:4] != b"RIFF" or file_bytes[8:12] != b"WAVE":
            raise ValueError("The WAV file is incomplete or invalid.")
        if b"data" not in file_bytes[12:]:
            raise ValueError("The WAV file has no audio data.")

    elif ext == "mp3":
        has_id3 = file_bytes.startswith(b"ID3") and len(file_bytes) >= 10
        has_frame = len(file_bytes) >= 4 and file_bytes[0] == 0xFF and (file_bytes[1] & 0xE0) == 0xE0
        if len(file_bytes) < 128 or not (has_id3 or has_frame):
            raise ValueError("The MP3 file is incomplete or invalid.")

    elif ext in ("doc", "xls", "ppt"):
        if len(file_bytes) < 512:
            raise ValueError("The Office file is incomplete or invalid.")


BASE_DIR = Path(__file__).resolve().parent
PROFILE_PICTURE_DIR = BASE_DIR / "uploads" / "profile_pictures"
COVER_PHOTO_DIR = BASE_DIR / "uploads" / "cover_photos"
COVER_VIDEO_DIR = BASE_DIR / "uploads" / "cover_videos"
COVER_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
COVER_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_PICTURE_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(BASE_DIR / ".env")

# Explicit executable paths; environment variables override platform defaults.
FFMPEG_PATH = os.environ.get(
    "GOTYA_FFMPEG_PATH"
) or (
    "/usr/bin/ffmpeg" if os.name != "nt" else
    r"C:\Tools\ffmpeg-9.0.2-essentials_build\bin\ffmpeg.exe"
)
FFPROBE_PATH = os.environ.get(
    "GOTYA_FFPROBE_PATH"
) or (
    "/usr/bin/ffprobe" if os.name != "nt" else
    r"C:\Tools\ffmpeg-9.0.2-essentials_build\bin\ffprobe.exe"
)

# --------------------------------------------------------
# SUPABASE PRIVATE FILE STORAGE
# --------------------------------------------------------

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "gotya-uploads")

supabase_storage = None

if SUPABASE_URL and SUPABASE_SECRET_KEY:
    supabase_storage = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)


def upload_to_supabase(storage_path, file_bytes, content_type):
    """Upload a file to the private Supabase bucket."""
    if supabase_storage is None:
        raise RuntimeError("Supabase storage is not configured.")

    return supabase_storage.storage.from_(SUPABASE_BUCKET).upload(
        storage_path,
        file_bytes,
        {"content-type": content_type, "upsert": "true"}
    )


def download_from_supabase(storage_path):
    """Download a file from the private Supabase bucket."""
    if supabase_storage is None:
        raise RuntimeError("Supabase storage is not configured.")

    return supabase_storage.storage.from_(SUPABASE_BUCKET).download(
        storage_path
    )


app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 450 * 1024 * 1024


@app.after_request
def add_upload_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.errorhandler(413)
def handle_request_too_large(error):
    return (
        "This upload is too large. The maximum request size is 450 MiB.",
        413,
        {"Content-Type": "text/plain; charset=utf-8"},
    )

# Web Push configuration. The private key must never be sent to the browser.
app.config["VAPID_PUBLIC_KEY"] = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
app.config["VAPID_PRIVATE_KEY"] = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
app.config["VAPID_SUBJECT"] = os.environ.get(
    "VAPID_SUBJECT", "mailto:admin@example.com"
).strip()

secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    raise RuntimeError("SECRET_KEY is missing. Check your .env file.")

app.config["SECRET_KEY"] = secret_key
database_url = os.environ.get("DATABASE_URL")

if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://", "postgresql+psycopg://", 1
        )
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///" + (BASE_DIR / "connecthub.db").as_posix()
    )
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Keep authenticated browser sessions persistent for up to 31 days.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=31)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True

# Protect session cookies.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower()
    in {"1", "true", "yes", "on"}
)

db = SQLAlchemy(app)
csrf = CSRFProtect(app)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), unique=True, nullable=False, index=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    profile_picture = db.Column(db.String(255), nullable=True)
    bio = db.Column(db.String(500), nullable=True)
    cover_photo = db.Column(db.String(255), nullable=True)
    cover_video = db.Column(db.String(255), nullable=True)
    last_seen = db.Column(db.DateTime, nullable=True)
    session_version = db.Column(
        db.Integer, nullable=False, default=1, server_default="1"
    )

    def get_id(self):
        return f"{self.id}:{self.session_version}"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


VERIFIED_ACCOUNT_IDS = frozenset({1, 2, 4})

def is_verified_account(user):
    return getattr(user, 'id', None) in VERIFIED_ACCOUNT_IDS

app.jinja_env.globals['is_verified_account'] = is_verified_account

class LoginAttempt(db.Model):
    """Hashed login identifier/IP pair used for temporary abuse throttling."""
    __tablename__ = "login_attempt"

    id = db.Column(db.Integer, primary_key=True)
    key_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    window_started_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )
    blocked_until = db.Column(db.DateTime, nullable=True)


def login_attempt_key(identity, remote_addr):
    """Return a keyed digest without storing the raw identity or IP address."""
    import hashlib
    import hmac

    normalized_identity = (identity or "").strip().casefold()[:254]
    normalized_ip = (remote_addr or "unknown")[:64]
    message = f"{normalized_identity}|{normalized_ip}".encode("utf-8")
    secret = app.config["SECRET_KEY"].encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def login_is_blocked(key_hash):
    """Check whether this identity/IP pair is in its temporary cooldown."""
    now = datetime.utcnow()
    attempt = LoginAttempt.query.filter_by(key_hash=key_hash).first()

    if attempt is None:
        return False

    if attempt.blocked_until and attempt.blocked_until > now:
        return True

    if attempt.blocked_until and attempt.blocked_until <= now:
        db.session.delete(attempt)
        db.session.commit()
        return False

    if now - attempt.window_started_at >= timedelta(minutes=15):
        db.session.delete(attempt)
        db.session.commit()

    return False


def record_failed_login(key_hash):
    """Count failures in a 15-minute window; cool down after five failures."""
    now = datetime.utcnow()
    attempt = LoginAttempt.query.filter_by(key_hash=key_hash).first()

    if attempt is None:
        attempt = LoginAttempt(
            key_hash=key_hash,
            failed_attempts=1,
            window_started_at=now,
        )
        db.session.add(attempt)
    elif now - attempt.window_started_at >= timedelta(minutes=15):
        attempt.failed_attempts = 1
        attempt.window_started_at = now
        attempt.blocked_until = None
    else:
        attempt.failed_attempts += 1

    if attempt.failed_attempts >= 5:
        attempt.blocked_until = now + timedelta(minutes=15)

    db.session.commit()


def clear_failed_logins(key_hash):
    """Clear the pair's failure counter after a successful login."""
    LoginAttempt.query.filter_by(key_hash=key_hash).delete(
        synchronize_session=False
    )
    db.session.commit()


def password_reset_serializer():
    """Create a serializer dedicated to password-reset tokens."""
    return URLSafeTimedSerializer(
        app.config["SECRET_KEY"],
        salt="gotya-password-reset-v1",
    )


def create_password_reset_token(user):
    """Bind the token to the user's current session version."""
    return password_reset_serializer().dumps(
        {"user_id": user.id, "session_version": user.session_version}
    )


def load_password_reset_user(token):
    """Validate a reset token for at most 30 minutes."""
    try:
        data = password_reset_serializer().loads(token, max_age=1800)
        user_id = int(data["user_id"])
        version = int(data["session_version"])
    except (BadSignature, SignatureExpired, TypeError, ValueError, KeyError):
        return None

    user = db.session.get(User, user_id)
    if user is None or user.session_version != version:
        return None

    return user


def send_password_reset_email(user, token):
    """Send a reset link through configured Gmail SMTP; never log the token."""
    mail_username = os.environ.get("GOTYA_MAIL_USERNAME", "").strip()
    mail_password = os.environ.get("GOTYA_MAIL_APP_PASSWORD", "").strip()
    mail_sender = os.environ.get("GOTYA_MAIL_SENDER", mail_username).strip()
    public_base_url = os.environ.get("GOTYA_PUBLIC_BASE_URL", "").strip().rstrip("/")

    if not mail_username or not mail_password or not mail_sender:
        app.logger.warning(
            "Password-reset email is not configured; no email was sent."
        )
        return False

    if public_base_url:
        reset_url = (
            f"{public_base_url}/reset-password/"
            f"{token}"
        )
    else:
        reset_url = url_for("reset_password", token=token, _external=True)

    message = EmailMessage()
    message["Subject"] = "Reset your Gotya password"
    message["From"] = mail_sender
    message["To"] = user.email
    message.set_content(
        "We received a request to reset your Gotya password.\n\n"
        f"Use this link within 30 minutes:\n{reset_url}\n\n"
        "If you did not request this, you can ignore this email."
    )

    host = os.environ.get("GOTYA_SMTP_HOST", "smtp.gmail.com").strip()
    try:
        port = int(os.environ.get("GOTYA_SMTP_PORT", "587"))
    except ValueError:
        app.logger.error("Password-reset SMTP port is invalid.")
        return False

    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(mail_username, mail_password)
            smtp.send_message(message)
        return True
    except Exception:
        app.logger.exception("Password-reset email delivery failed.")
        return False


class UserPreference(db.Model):
    """Per-user preferences, stored separately from existing account data."""
    __tablename__ = "user_preference"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        unique=True,
        index=True
    )
    theme = db.Column(db.String(10), nullable=False, default="system")
    show_online_status = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )
    reaction_counts_visible = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )
    font_scale = db.Column(
        db.String(16), nullable=False, default="normal", server_default="normal"
    )
    reduce_motion = db.Column(
        db.Boolean, nullable=False, default=False, server_default=db.false()
    )
    high_contrast = db.Column(
        db.Boolean, nullable=False, default=False, server_default=db.false()
    )
    user = db.relationship("User")



@app.context_processor
def inject_gotya_theme():
    """Expose the signed-in user's saved appearance preference to templates."""
    theme = "system"

    if current_user.is_authenticated:
        try:
            # Read only the separate preference table; never reset user data.
            if db.inspect(db.engine).has_table("user_preference"):
                preference = UserPreference.query.filter_by(
                    user_id=current_user.id
                ).first()

                if preference and preference.theme in {"system", "light", "dark"}:
                    theme = preference.theme
        except Exception:
            # Keep pages usable if the preference table is temporarily unavailable.
            db.session.rollback()

    return {"gotya_theme": theme}


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(2000), nullable=False)
    audience = db.Column(
        db.String(20), nullable=False,
        default="public", server_default="public"
    )
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=db.func.current_timestamp()
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True
    )

    author = db.relationship("User", backref="posts")


class PostLike(db.Model):
    __tablename__ = "post_like"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    post_id = db.Column(
        db.Integer, db.ForeignKey("post.id"), nullable=False, index=True
    )

    __table_args__ = (
        db.UniqueConstraint("user_id", "post_id", name="uq_post_like_user"),
    )

    user = db.relationship("User")
    post = db.relationship("Post", backref=db.backref("likes", lazy="select"))


class Comment(db.Model):
    __tablename__ = "comment"

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(1000), nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=db.func.current_timestamp()
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    post_id = db.Column(
        db.Integer, db.ForeignKey("post.id"), nullable=False, index=True
    )

    author = db.relationship("User")
    post = db.relationship(
        "Post", backref=db.backref("comments", lazy="select")
    )


class Friendship(db.Model):
    __tablename__ = "friendship"

    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    recipient_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    status = db.Column(
        db.String(20), nullable=False, default="pending", index=True
    )
    created_at = db.Column(
        db.DateTime, nullable=False, default=db.func.current_timestamp()
    )

    requester = db.relationship("User", foreign_keys=[requester_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])

    __table_args__ = (
        db.UniqueConstraint(
            "requester_id", "recipient_id",
            name="uq_friendship_direction"
        ),
        db.CheckConstraint(
            "requester_id != recipient_id",
            name="ck_friendship_not_self"
        ),
        db.CheckConstraint(
            "status IN ('pending', 'accepted', 'declined')",
            name="ck_friendship_status"
        ),
    )


class Follow(db.Model):
    __tablename__ = "user_follow"

    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    followed_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    created_at = db.Column(
        db.DateTime, nullable=False, default=db.func.current_timestamp()
    )

    follower = db.relationship("User", foreign_keys=[follower_id])
    followed = db.relationship("User", foreign_keys=[followed_id])

    __table_args__ = (
        db.UniqueConstraint(
            "follower_id", "followed_id", name="uq_user_follow_direction"
        ),
        db.CheckConstraint(
            "follower_id != followed_id", name="ck_user_follow_not_self"
        ),
    )


class Message(db.Model):
    __tablename__ = "message"

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    recipient_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    content = db.Column(db.String(2000), nullable=False)
    is_unsent = db.Column(
        db.Boolean, nullable=False, default=False, server_default=db.false()
    )
    created_at = db.Column(
        db.DateTime, nullable=False, default=db.func.current_timestamp()
    )
    read_at = db.Column(db.DateTime, nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    reply_to_id = db.Column(
        db.Integer, db.ForeignKey("message.id"), nullable=True, index=True
    )
    attachment_filename = db.Column(db.String(255), nullable=True)
    attachment_original_name = db.Column(db.String(255), nullable=True)
    attachment_mimetype = db.Column(db.String(120), nullable=True)
    attachment_size = db.Column(db.Integer, nullable=True)

    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])
    reply_to = db.relationship(
        "Message", remote_side=[id], foreign_keys=[reply_to_id], uselist=False
    )

    __table_args__ = (
        db.CheckConstraint(
            "sender_id != recipient_id",
            name="ck_message_not_self"
        ),
    )




class UserQuickReaction(db.Model):
    """Stores each user's personalized quick-reaction emojis."""
    __tablename__ = "user_quick_reaction"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        unique=True,
        index=True
    )
    emojis = db.Column(db.JSON, nullable=False, default=list)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    user = db.relationship("User")


class MessageReaction(db.Model):
    __tablename__ = "message_reaction"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(
        db.Integer, db.ForeignKey("message.id"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    emoji = db.Column(db.String(16), nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )

    message = db.relationship("Message", foreign_keys=[message_id])
    user = db.relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        db.UniqueConstraint(
            "message_id", "user_id",
            name="uq_message_reaction_user"
        ),
    )



class MessageHidden(db.Model):
    """Tracks messages hidden from one user's own conversation view."""
    __tablename__ = "message_hidden"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(
        db.Integer, db.ForeignKey("message.id"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        db.UniqueConstraint(
            "message_id", "user_id",
            name="uq_message_hidden_user"
        ),
    )

class PushSubscription(db.Model):
    __tablename__ = "push_subscription"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.String(255), nullable=False)
    auth = db.Column(db.String(255), nullable=False)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    user = db.relationship("User")

class VideoCall(db.Model):
    __tablename__ = "video_call"

    id = db.Column(db.String(36), primary_key=True)
    caller_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    recipient_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    status = db.Column(
        db.String(20), nullable=False, default="ringing", index=True
    )
    offer_sdp = db.Column(db.Text, nullable=True)
    answer_sdp = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, index=True
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )
    ended_by = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=True
    )

    caller = db.relationship("User", foreign_keys=[caller_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])
    ender = db.relationship("User", foreign_keys=[ended_by])

    __table_args__ = (
        db.CheckConstraint(
            "caller_id != recipient_id",
            name="ck_video_call_not_self"
        ),
        db.CheckConstraint(
            "status IN ('ringing', 'accepted', 'declined', 'ended', 'missed')",
            name="ck_video_call_status"
        ),
    )


class VideoCallSignal(db.Model):
    __tablename__ = "video_call_signal"

    id = db.Column(db.Integer, primary_key=True)
    call_id = db.Column(
        db.String(36),
        db.ForeignKey("video_call.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    sender_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    kind = db.Column(db.String(20), nullable=False)
    payload = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )

    call = db.relationship("VideoCall", backref="signals")
    sender = db.relationship("User", foreign_keys=[sender_id])

    __table_args__ = (
        db.CheckConstraint(
            "kind IN ('ice-candidate')",
            name="ck_video_call_signal_kind"
        ),
    )


class LiveLocationShare(db.Model):
    __tablename__ = "live_location_share"

    id = db.Column(db.Integer, primary_key=True)
    sharer_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    recipient_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    duration_hours = db.Column(db.Integer, nullable=True)
    started_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    stopped_at = db.Column(db.DateTime, nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True)

    sharer = db.relationship("User", foreign_keys=[sharer_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])

    __table_args__ = (
        db.CheckConstraint(
            "sharer_id != recipient_id",
            name="ck_live_location_not_self"
        ),
        db.CheckConstraint(
            "duration_hours IS NULL OR duration_hours IN (1, 8, 24)",
            name="ck_live_location_duration"
        ),
        db.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_live_location_latitude"
        ),
        db.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_live_location_longitude"
        ),
    )

@login_manager.user_loader
def load_user(user_id):
    try:
        if ":" in str(user_id):
            raw_id, raw_version = str(user_id).rsplit(":", 1)
            expected_version = int(raw_version)
        else:
            # Compatibility with existing sessions created before versioned IDs.
            raw_id = str(user_id)
            expected_version = 1

        user = db.session.get(User, int(raw_id))
    except (TypeError, ValueError):
        return None

    if user is None or user.session_version != expected_version:
        return None

    return user



def migrate_existing_database_schema():
    """Add missing legacy columns without deleting or creating tables."""
    with app.app_context():

        additions = {
            "user": {
                "last_seen": "TIMESTAMP",
                "profile_picture": "VARCHAR(255)",
                "bio": "VARCHAR(500)",
                "cover_photo": "VARCHAR(255)",
                "cover_video": "VARCHAR(255)",
                "session_version": "INTEGER NOT NULL DEFAULT 1",
            },
            "user_preference": {
                "show_online_status": "BOOLEAN NOT NULL DEFAULT TRUE",
                "reaction_counts_visible": "BOOLEAN NOT NULL DEFAULT TRUE",
                "font_scale": "VARCHAR(16) NOT NULL DEFAULT 'normal'",
                "reduce_motion": "BOOLEAN NOT NULL DEFAULT FALSE",
                "high_contrast": "BOOLEAN NOT NULL DEFAULT FALSE",
            },
            "post": {
                "audience": "VARCHAR(20) NOT NULL DEFAULT 'public'",
            },
            "message": {
                "is_unsent": "BOOLEAN NOT NULL DEFAULT FALSE",
                "delivered_at": "TIMESTAMP",
                "reply_to_id": "INTEGER",
                "attachment_filename": "VARCHAR(255)",
                "attachment_original_name": "VARCHAR(255)",
                "attachment_mimetype": "VARCHAR(120)",
                "attachment_size": "INTEGER",
            },
        }

        dialect = db.engine.dialect.name

        for table, definitions in additions.items():
            inspector = db.inspect(db.engine)
            if not inspector.has_table(table):
                raise RuntimeError(
                    f"Required table {table!r} is missing."
                )

            existing = {
                column["name"]
                for column in inspector.get_columns(table)
            }

            for column, definition in definitions.items():
                if column in existing:
                    continue

                # PostgreSQL supports IF NOT EXISTS, helping avoid races
                # if multiple Gunicorn workers start simultaneously.
                if dialect == "postgresql":
                    statement = (
                        f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS '
                        f'"{column}" {definition}'
                    )
                else:
                    statement = (
                        f'ALTER TABLE "{table}" ADD COLUMN '
                        f'"{column}" {definition}'
                    )

                with db.engine.begin() as connection:
                    connection.execute(db.text(statement))

                print(f"Database migration: added {table}.{column}")


# Ensure the separate preference table exists before additive migrations.
with app.app_context():
    UserPreference.__table__.create(bind=db.engine, checkfirst=True)
    LoginAttempt.__table__.create(bind=db.engine, checkfirst=True)
    Follow.__table__.create(bind=db.engine, checkfirst=True)

# Run after all SQLAlchemy models have been declared.
migrate_existing_database_schema()


@app.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("messages"))
    return redirect(url_for("login"))


@app.route("/health")
def health():
    return {"status": "ok", "app": "ConnectHub"}


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("messages"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not (3 <= len(username) <= 30):
            flash("Username must be between 3 and 30 characters.", "error")
        elif any(not (c.isalnum() or c == "_") for c in username):
            flash("Username can contain only letters, numbers, and underscores.", "error")
        elif len(email) > 254 or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            flash("Enter a valid email address.", "error")
        elif len(password) < 12:
            flash("Password must be at least 12 characters long.", "error")
        elif password != confirm_password:
            flash("Passwords do not match.", "error")
        elif User.query.filter(
            db.func.lower(User.username) == username.lower()
        ).first():
            flash("That username is already taken. Please choose another.", "error")
        elif User.query.filter(
            db.func.lower(User.email) == email.lower()
        ).first():
            flash("That email address is already registered.", "error")
        else:
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)

            try:
                db.session.commit()
                session.permanent = True
                login_user(user)
                flash("Your ConnectHub account has been created!", "success")
                return redirect(url_for("messages"))
            except IntegrityError:
                db.session.rollback()
                existing_username = User.query.filter(
                    db.func.lower(User.username) == username.lower()
                ).first()
                existing_email = User.query.filter(
                    db.func.lower(User.email) == email.lower()
                ).first()

                if existing_username:
                    flash("That username is already taken. Please choose another.", "error")
                elif existing_email:
                    flash("That email address is already registered.", "error")
                else:
                    flash(
                        "We couldn't complete registration because of a database conflict. Please try again.",
                        "error",
                    )

    return render_template("register.html")


@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("messages"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first() if email else None

        if user:
            token = create_password_reset_token(user)
            send_password_reset_email(user, token)

        # Do not reveal whether this email address belongs to an account.
        flash(
            "If an account uses that email address, password-reset "
            "instructions will be sent if email delivery is available.",
            "success",
        )
        return redirect(url_for("forgot_password"))

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("messages"))

    user = load_password_reset_user(token)
    if user is None:
        flash(
            "That password-reset link is invalid, expired, or already used. "
            "Request a new one.",
            "error",
        )
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(new_password) < 12:
            flash("Your new password must be at least 12 characters long.", "error")
        elif new_password != confirm_password:
            flash("The new passwords do not match.", "error")
        else:
            user.set_password(new_password)
            user.session_version += 1
            db.session.commit()
            flash(
                "Your password has been reset. Please sign in with your new password.",
                "success",
            )
            return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("messages"))

    if request.method == "POST":
        identity = request.form.get("identity", "").strip()
        password = request.form.get("password", "")
        attempt_key = login_attempt_key(identity, request.remote_addr)

        if login_is_blocked(attempt_key):
            flash(
                "Too many failed login attempts. Please wait 15 minutes "
                "before trying again.",
                "error",
            )
            return render_template("login.html"), 429

        user = User.query.filter(
            db.or_(
                User.username.ilike(identity),
                User.email == identity.lower(),
            )
        ).first()

        if user and user.check_password(password):
            clear_failed_logins(attempt_key)
            session.permanent = True
            login_user(user)
            return redirect(url_for("messages"))

        record_failed_login(attempt_key)
        flash("Invalid username/email or password.", "error")

    return render_template("login.html")


@app.route("/profile/edit", methods=["GET"])
@login_required
def edit_profile():
    return render_template("edit_profile.html", user=current_user)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        action = request.form.get("action", "profile_picture")

        if action == "save_profile":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            bio = request.form.get("bio", "").strip()

            if not 3 <= len(username) <= 30:
                flash("Username must be between 3 and 30 characters.", "error")
                return redirect(url_for("profile"))

            if not email or len(email) > 254 or "@" not in email:
                flash("Please enter a valid email address.", "error")
                return redirect(url_for("profile"))

            if len(bio) > 500:
                flash("Your bio must be 500 characters or fewer.", "error")
                return redirect(url_for("profile"))

            duplicate_username = User.query.filter(
                db.func.lower(User.username) == username.lower(),
                User.id != current_user.id,
            ).first()

            duplicate_email = User.query.filter(
                db.func.lower(User.email) == email.lower(),
                User.id != current_user.id,
            ).first()

            if duplicate_username:
                flash("That username is already taken.", "error")
                return redirect(url_for("profile"))

            if duplicate_email:
                flash("That email address is already registered.", "error")
                return redirect(url_for("profile"))

            try:
                current_user.username = username
                current_user.email = email
                current_user.bio = bio or None
                db.session.commit()
                flash("Profile information updated successfully!", "success")
            except IntegrityError:
                db.session.rollback()
                flash("That username or email is already in use.", "error")
            except Exception:
                db.session.rollback()
                app.logger.exception("Profile information update failed")
                flash("Your profile could not be updated. Please try again.", "error")

            return redirect(url_for("profile"))

        if action == "cover_photo":
            cover_file = request.files.get("cover_photo")

            if cover_file is None or not cover_file.filename:
                flash("Please choose a cover photo to upload.", "error")
                return redirect(url_for("profile"))

            original_name = secure_filename(cover_file.filename)
            extension = Path(original_name).suffix.lower()
            allowed_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

            if extension not in allowed_extensions:
                flash("Use a JPG, JPEG, PNG, WEBP, or GIF cover photo.", "error")
                return redirect(url_for("profile"))

            cover_file.stream.seek(0, 2)
            file_size = cover_file.stream.tell()
            cover_file.stream.seek(0)

            if file_size > 8 * 1024 * 1024:
                flash("Cover photos must be 8 MB or smaller.", "error")
                return redirect(url_for("profile"))

            filename = f"{uuid.uuid4().hex}{extension}"
            destination = COVER_PHOTO_DIR / filename

            try:
                cover_file.stream.seek(0)
                file_bytes = cover_file.read()
                content_type = validate_uploaded_image(file_bytes, extension)

                upload_to_supabase(
                    "cover_photos/" + filename,
                    file_bytes,
                    content_type
                )
                destination.write_bytes(file_bytes)

                current_user.cover_photo = filename
                db.session.commit()
                flash("Cover photo updated successfully!", "success")
            except ValueError as exc:
                db.session.rollback()
                destination.unlink(missing_ok=True)
                flash(str(exc), "error")
            except Exception:
                db.session.rollback()
                destination.unlink(missing_ok=True)
                app.logger.exception("Cover-photo upload failed")
                flash("The cover photo could not be saved. Please try again.", "error")

            return redirect(url_for("profile"))

        uploaded_file = request.files.get("profile_picture")

        if uploaded_file is None or not uploaded_file.filename:
            flash("Please choose a picture to upload.", "error")
            return redirect(url_for("profile"))

        original_name = secure_filename(uploaded_file.filename)
        extension = Path(original_name).suffix.lower()
        allowed_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

        if extension not in allowed_extensions:
            flash("Use a JPG, JPEG, PNG, WEBP, or GIF image.", "error")
            return redirect(url_for("profile"))

        uploaded_file.stream.seek(0, 2)
        file_size = uploaded_file.stream.tell()
        uploaded_file.stream.seek(0)

        if file_size > 5 * 1024 * 1024:
            flash("Profile pictures must be 5 MB or smaller.", "error")
            return redirect(url_for("profile"))

        filename = f"{uuid.uuid4().hex}{extension}"
        destination = PROFILE_PICTURE_DIR / filename

        try:
            uploaded_file.stream.seek(0)
            file_bytes = uploaded_file.read()
            content_type = validate_uploaded_image(file_bytes, extension)

            upload_to_supabase(
                "profile_pictures/" + filename,
                file_bytes,
                content_type
            )
            destination.write_bytes(file_bytes)

            current_user.profile_picture = filename
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            destination.unlink(missing_ok=True)
            flash(str(exc), "error")
            return redirect(url_for("profile"))
        except Exception:
            db.session.rollback()
            destination.unlink(missing_ok=True)
            app.logger.exception("Profile-picture upload failed")
            flash("The picture could not be saved. Please try again.", "error")
            return redirect(url_for("profile"))

        flash("Profile picture updated successfully!", "success")
        return redirect(url_for("profile"))

    post_count = Post.query.filter_by(user_id=current_user.id).count()

    friend_count = Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(
            Friendship.requester_id == current_user.id,
            Friendship.recipient_id == current_user.id,
        ),
    ).count()

    likes_received = (
        PostLike.query.join(Post, PostLike.post_id == Post.id)
        .filter(Post.user_id == current_user.id)
        .count()
    )

    preference = UserPreference.query.filter_by(
        user_id=current_user.id
    ).first()
    reaction_counts_visible = (
        preference.reaction_counts_visible if preference else True
    )

    profile_tab = request.args.get("tab", "all").strip().lower()
    if profile_tab not in {"all", "about", "friends", "photos"}:
        profile_tab = "all"

    profile_posts = Post.query.filter_by(
        user_id=current_user.id
    ).order_by(Post.created_at.desc(), Post.id.desc()).all()

    accepted_friendships = Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(
            Friendship.requester_id == current_user.id,
            Friendship.recipient_id == current_user.id,
        ),
    ).all()

    profile_friend_ids = {
        item.recipient_id if item.requester_id == current_user.id
        else item.requester_id
        for item in accepted_friendships
    }

    profile_friends = (
        User.query.filter(User.id.in_(profile_friend_ids))
        .order_by(User.username.asc()).all()
        if profile_friend_ids else []
    )

    return render_template(
        "profile.html",
        user=current_user,
        post_count=post_count,
        friend_count=friend_count,
        likes_received=likes_received,
        reaction_counts_visible=reaction_counts_visible,
        profile_tab=profile_tab,
        profile_posts=profile_posts,
        profile_friends=profile_friends,
    )


@app.route("/u/<int:user_id>")
@login_required
def public_profile(user_id):
    """Display another member's profile without exposing their private posts."""
    profile_user = db.session.get(User, user_id)

    if profile_user is None:
        flash("That profile could not be found.", "error")
        return redirect(url_for("friends"))

    friendship = Friendship.query.filter(
        db.or_(
            db.and_(
                Friendship.requester_id == current_user.id,
                Friendship.recipient_id == profile_user.id,
            ),
            db.and_(
                Friendship.requester_id == profile_user.id,
                Friendship.recipient_id == current_user.id,
            ),
        )
    ).first() if profile_user.id != current_user.id else None

    is_following = (
        Follow.query.filter_by(
            follower_id=current_user.id,
            followed_id=profile_user.id,
        ).first() is not None
        if profile_user.id != current_user.id else False
    )

    all_profile_posts = Post.query.filter_by(
        user_id=profile_user.id
    ).order_by(Post.created_at.desc(), Post.id.desc()).all()

    visible_profile_posts = [
        post for post in all_profile_posts
        if _can_view_post(post, current_user.id)
    ]

    friend_count = Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(
            Friendship.requester_id == profile_user.id,
            Friendship.recipient_id == profile_user.id,
        ),
    ).count()

    return render_template(
        "public_profile.html",
        profile_user=profile_user,
        profile_posts=visible_profile_posts,
        friendship=friendship,
        is_following=is_following,
        friend_count=friend_count,
    )


@app.route("/follow/<int:user_id>", methods=["POST"])
@login_required
def follow_user(user_id):
    target = db.session.get(User, user_id)
    if target is None or target.id == current_user.id:
        flash("You cannot follow that account.", "error")
        return redirect(url_for("friends"))

    existing = Follow.query.filter_by(
        follower_id=current_user.id,
        followed_id=target.id,
    ).first()

    if existing is None:
        db.session.add(Follow(
            follower_id=current_user.id,
            followed_id=target.id,
        ))
        db.session.commit()
        flash(f"You are now following {target.username}.", "success")
    else:
        flash(f"You already follow {target.username}.", "info")

    return redirect(url_for("public_profile", user_id=target.id))


@app.route("/unfollow/<int:user_id>", methods=["POST"])
@login_required
def unfollow_user(user_id):
    target = db.session.get(User, user_id)
    if target is None or target.id == current_user.id:
        flash("That account cannot be unfollowed.", "error")
        return redirect(url_for("friends"))

    existing = Follow.query.filter_by(
        follower_id=current_user.id,
        followed_id=target.id,
    ).first()

    if existing is not None:
        db.session.delete(existing)
        db.session.commit()
        flash(f"You unfollowed {target.username}.", "success")
    else:
        flash("You are not following that account.", "info")

    return redirect(url_for("public_profile", user_id=target.id))


@app.route("/profile-pictures/<path:filename>")
@login_required
def profile_picture_file(filename):
    try:
        file_bytes = download_from_supabase("profile_pictures/" + filename)
        return send_file(
            BytesIO(file_bytes),
            mimetype=IMAGE_UPLOAD_MIME_TYPES.get(
                Path(filename).suffix.lower(), "application/octet-stream"
            ),
            download_name=filename
        )
    except Exception:
        app.logger.exception("Supabase profile-picture retrieval failed")
        return send_from_directory(PROFILE_PICTURE_DIR, filename)


@app.route("/cover-photos/<path:filename>")
@login_required
def cover_photo_file(filename):
    try:
        file_bytes = download_from_supabase("cover_photos/" + filename)
        return send_file(
            BytesIO(file_bytes),
            mimetype=IMAGE_UPLOAD_MIME_TYPES.get(
                Path(filename).suffix.lower(), "application/octet-stream"
            ),
            download_name=filename
        )
    except Exception:
        app.logger.exception("Supabase cover-photo retrieval failed")
        return send_from_directory(COVER_PHOTO_DIR, filename)



@app.route("/profile/cover-video", methods=["POST"])
@login_required
def upload_cover_video():
    # Allow a small multipart/form overhead, while enforcing the actual file
    # size independently below. Do not alter the app-wide request limit.
    if request.content_length and request.content_length > COVER_VIDEO_MAX_BYTES + 1024 * 1024:
        flash("The cover-video upload exceeds the allowed size.", "error")
        return redirect(url_for("edit_profile"))

    video_file = request.files.get("cover_video")
    if video_file is None or not video_file.filename:
        flash("Choose a video to upload.", "error")
        return redirect(url_for("edit_profile"))

    extension = Path(secure_filename(video_file.filename)).suffix.lower()
    if extension not in {".mp4", ".mov"}:
        flash("Choose an MP4 or MOV video.", "error")
        return redirect(url_for("edit_profile"))

    try:
        video_file.stream.seek(0, 2)
        file_size = video_file.stream.tell()
        video_file.stream.seek(0)
    except (OSError, AttributeError):
        flash("The selected video could not be read.", "error")
        return redirect(url_for("edit_profile"))

    if file_size <= 0 or file_size > COVER_VIDEO_MAX_BYTES:
        flash("Videos must be non-empty and no larger than 15 MiB.", "error")
        return redirect(url_for("edit_profile"))

    filename = uuid.uuid4().hex + ".mp4"
    destination = COVER_VIDEO_DIR / filename
    stored_locally = False

    try:
        with tempfile.TemporaryDirectory(prefix="gotya-cover-video-") as temp_name:
            temp_dir = Path(temp_name)
            input_path = temp_dir / ("upload" + extension)
            output_path = temp_dir / "normalized.mp4"

            video_file.stream.seek(0)
            with input_path.open("xb") as handle:
                shutil.copyfileobj(video_file.stream, handle, length=64 * 1024)

            if input_path.stat().st_size != file_size:
                raise CoverVideoError("The uploaded video could not be read completely.")

            validate_cover_video(input_path, FFPROBE_PATH)
            normalize_cover_video(
                input_path, output_path, FFMPEG_PATH, FFPROBE_PATH
            )
            normalized_bytes = output_path.read_bytes()

            if not normalized_bytes or len(normalized_bytes) > COVER_VIDEO_MAX_BYTES:
                raise CoverVideoError("The processed video exceeds the allowed size.")

            # Keep a local copy so the existing local-storage fallback works.
            with destination.open("xb") as handle:
                handle.write(normalized_bytes)
            stored_locally = True

            # When Supabase is configured, require its upload to succeed
            # before changing the selected cover. Failed uploads preserve the
            # existing cover and the local temporary copy is cleaned up.
            if supabase_storage is not None:
                try:
                    upload_to_supabase(
                        "cover_videos/" + filename,
                        normalized_bytes,
                        "video/mp4",
                    )
                except Exception as exc:
                    app.logger.exception("Supabase cover-video upload failed")
                    raise CoverVideoError(
                        "Video storage is temporarily unavailable. Your existing cover was kept."
                    ) from exc

        previous_video = current_user.cover_video
        current_user.cover_video = filename
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        flash("Cover video updated successfully.", "success")

        # Do not delete the previous video here. It may be referenced by a
        # concurrent request or exist only in remote storage. Cleanup can be
        # handled separately after storage lifecycle behavior is verified.
        return redirect(url_for("edit_profile"))

    except CoverVideoError as exc:
        db.session.rollback()
        if stored_locally:
            destination.unlink(missing_ok=True)
        flash(str(exc), "error")
        return redirect(url_for("edit_profile"))
    except Exception:
        db.session.rollback()
        if stored_locally:
            destination.unlink(missing_ok=True)
        app.logger.exception("Cover-video upload failed")
        flash("The cover video could not be saved. Your existing cover was kept.", "error")
        return redirect(url_for("edit_profile"))


@app.route("/cover-videos/<path:filename>", methods=["GET"])
@login_required
def cover_video_file(filename):
    # Only serve generated UUID MP4 names; reject paths and user-supplied names.
    if not re.fullmatch(r"[0-9a-f]{32}\.mp4", filename):
        abort(404)

    try:
        file_bytes = download_from_supabase("cover_videos/" + filename)
        response = send_file(
            BytesIO(file_bytes),
            mimetype="video/mp4",
            download_name=filename,
            conditional=True,
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        return response
    except Exception:
        app.logger.warning(
            "Supabase cover-video retrieval unavailable; trying local storage",
            exc_info=True,
        )
        response = send_from_directory(
            COVER_VIDEO_DIR, filename, mimetype="video/mp4", conditional=True
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        return response


@app.route("/friends", methods=["GET"])
@login_required
def friends():
    query = request.args.get("q", "").strip()[:30]
    users = []
    if query:
        users = User.query.filter(
            User.id != current_user.id,
            User.username.ilike(f"%{query}%")
        ).order_by(User.username.asc()).limit(30).all()

    incoming = Friendship.query.filter_by(
        recipient_id=current_user.id, status="pending"
    ).order_by(Friendship.created_at.desc()).all()

    outgoing = Friendship.query.filter_by(
        requester_id=current_user.id, status="pending"
    ).order_by(Friendship.created_at.desc()).all()

    accepted = Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(
            Friendship.requester_id == current_user.id,
            Friendship.recipient_id == current_user.id
        )
    ).order_by(Friendship.created_at.desc()).all()

    friend_ids = set()
    for item in accepted:
        friend_ids.add(
            item.recipient_id if item.requester_id == current_user.id
            else item.requester_id
        )

    existing = Friendship.query.filter(
        db.or_(
            Friendship.requester_id == current_user.id,
            Friendship.recipient_id == current_user.id
        )
    ).all()

    relationships = {}
    for item in existing:
        other_id = (
            item.recipient_id if item.requester_id == current_user.id
            else item.requester_id
        )
        relationships[other_id] = item

    found_friends = User.query.filter(User.id.in_(friend_ids)).order_by(
        User.username.asc()
    ).all() if friend_ids else []

    following_ids = {
        item.followed_id
        for item in Follow.query.filter_by(follower_id=current_user.id).all()
    }

    return render_template(
        "friends.html",
        query=query,
        users=users,
        incoming=incoming,
        outgoing=outgoing,
        friends=found_friends,
        relationships=relationships,
        following_ids=following_ids,
    )


@app.route("/friends/request/<int:user_id>", methods=["POST"])
@login_required
def send_friend_request(user_id):
    target = db.session.get(User, user_id)
    if target is None or target.id == current_user.id:
        flash("That user cannot receive a friend request.", "error")
        return redirect(url_for("friends"))

    existing = Friendship.query.filter(
        db.or_(
            db.and_(
                Friendship.requester_id == current_user.id,
                Friendship.recipient_id == target.id
            ),
            db.and_(
                Friendship.requester_id == target.id,
                Friendship.recipient_id == current_user.id
            )
        )
    ).first()

    if existing:
        if existing.status == "declined" and existing.requester_id == current_user.id:
            existing.status = "pending"
            db.session.commit()
            try:
                send_push_notification(
                    target.id,
                    f"New friend request from {current_user.username}",
                    "You received a friend request.",
                    url_for("friends"),
                )
            except Exception:
                app.logger.exception("Could not send friend-request push notification.")
            flash("Friend request sent again.", "success")
        elif existing.status == "declined" and existing.recipient_id == current_user.id:
            existing.requester_id, existing.recipient_id = (
                current_user.id, target.id
            )
            existing.status = "pending"
            db.session.commit()
            try:
                send_push_notification(
                    target.id,
                    f"New friend request from {current_user.username}",
                    "You received a friend request.",
                    url_for("friends"),
                )
            except Exception:
                app.logger.exception("Could not send friend-request push notification.")
            flash("Friend request sent.", "success")
        else:
            flash("A request or friendship already exists.", "error")
    else:
        db.session.add(Friendship(
            requester_id=current_user.id,
            recipient_id=target.id,
            status="pending"
        ))
        db.session.commit()
        try:
            send_push_notification(
                target.id,
                f"New friend request from {current_user.username}",
                "You received a friend request.",
                url_for("friends"),
            )
        except Exception:
            app.logger.exception("Could not send friend-request push notification.")
        flash("Friend request sent!", "success")

    return redirect(url_for("friends", q=request.form.get("q", "")))


@app.route("/friends/<int:request_id>/respond", methods=["POST"])
@login_required
def respond_friend_request(request_id):
    item = db.session.get(Friendship, request_id)
    action = request.form.get("action", "").strip()

    if item is None or item.recipient_id != current_user.id or item.status != "pending":
        flash("That incoming request is no longer available.", "error")
    elif action not in {"accept", "decline"}:
        flash("Invalid friend-request action.", "error")
    else:
        item.status = "accepted" if action == "accept" else "declined"

        if action == "accept":
            existing_follow = Follow.query.filter_by(
                follower_id=item.requester_id,
                followed_id=current_user.id,
            ).first()
            if existing_follow is None:
                db.session.add(Follow(
                    follower_id=item.requester_id,
                    followed_id=current_user.id,
                ))

        db.session.commit()
        flash(
            "You are now friends!" if action == "accept"
            else "Friend request declined.",
            "success"
        )

    return redirect(url_for("friends"))


@app.route("/friends/<int:request_id>/cancel", methods=["POST"])
@login_required
def cancel_friend_request(request_id):
    item = db.session.get(Friendship, request_id)
    if item is None or item.requester_id != current_user.id or item.status != "pending":
        flash("That outgoing request cannot be cancelled.", "error")
    else:
        db.session.delete(item)
        db.session.commit()
        flash("Friend request cancelled.", "success")

    return redirect(url_for("friends"))


@app.route("/friends/<int:user_id>/remove", methods=["POST"])
@login_required
def remove_friend(user_id):
    item = Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(
            db.and_(
                Friendship.requester_id == current_user.id,
                Friendship.recipient_id == user_id
            ),
            db.and_(
                Friendship.requester_id == user_id,
                Friendship.recipient_id == current_user.id
            )
        )
    ).first()

    if item is None:
        flash("That friendship could not be found.", "error")
    else:
        db.session.delete(item)
        db.session.commit()
        flash("Friend removed.", "success")

    return redirect(url_for("friends"))


@app.route("/messages", methods=["GET"])
@login_required
def messages():
    accepted = Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(
            Friendship.requester_id == current_user.id,
            Friendship.recipient_id == current_user.id
        )
    ).all()

    friend_ids = {
        item.recipient_id if item.requester_id == current_user.id
        else item.requester_id
        for item in accepted
    }
    friends = User.query.filter(User.id.in_(friend_ids)).order_by(
        User.username.asc()
    ).all() if friend_ids else []

    recent = []
    for person in friends:
        latest = Message.query.filter(
            db.or_(
                db.and_(
                    Message.sender_id == current_user.id,
                    Message.recipient_id == person.id
                ),
                db.and_(
                    Message.sender_id == person.id,
                    Message.recipient_id == current_user.id
                )
            )
        ).filter(
            Message.id.notin_(
                db.session.query(MessageHidden.message_id).filter_by(
                    user_id=current_user.id
                )
            )
        ).order_by(
            Message.created_at.desc(), Message.id.desc()
        ).first()
        recent.append((person, latest))

    recent.sort(
        key=lambda item: (
            item[1].created_at if item[1] else datetime.min,
            item[1].id if item[1] else 0
        ),
        reverse=True
    )

    unread_count = Message.query.filter_by(
        recipient_id=current_user.id, read_at=None
    ).filter(
        Message.is_unsent.is_(False)
    ).count()

    return render_template(
        "messages.html", friends=friends, recent=recent,
        unread_count=unread_count
    )


@app.route("/messages/<int:user_id>", methods=["GET", "POST"])
@login_required
def conversation(user_id):
    person = db.session.get(User, user_id)
    if person is None or person.id == current_user.id:
        flash("That conversation is unavailable.", "error")
        return redirect(url_for("messages"))

    friendship = Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(
            db.and_(
                Friendship.requester_id == current_user.id,
                Friendship.recipient_id == person.id
            ),
            db.and_(
                Friendship.requester_id == person.id,
                Friendship.recipient_id == current_user.id
            )
        )
    ).first()

    if friendship is None:
        flash("You can message accepted friends only.", "error")
        return redirect(url_for("messages"))

    if request.method == "POST":
        content = request.form.get("content", "").strip()
        reply_to_raw = request.form.get("reply_to_id", "").strip()
        reply_to_message = None

        if reply_to_raw:
            try:
                reply_to_id = int(reply_to_raw)
                if reply_to_id > 0:
                    reply_to_message = Message.query.filter(
                        Message.id == reply_to_id,
                        db.or_(
                            db.and_(
                                Message.sender_id == current_user.id,
                                Message.recipient_id == person.id
                            ),
                            db.and_(
                                Message.sender_id == person.id,
                                Message.recipient_id == current_user.id
                            )
                        )
                    ).first()
            except (TypeError, ValueError):
                reply_to_message = None

        photo_uploads = [
            item for item in request.files.getlist("photos")
            if item and item.filename
        ]
        single_upload = request.files.get("attachment")
        has_single_upload = bool(single_upload and single_upload.filename)

        allowed_extensions = {
            "jpg", "jpeg", "png", "gif", "webp",
            "pdf", "txt", "csv", "doc", "docx",
            "xls", "xlsx", "ppt", "pptx", "zip",
            "webm", "ogg", "mp4", "m4a", "mp3", "wav"
        }
        image_extensions = {"jpg", "jpeg", "png", "gif", "webp"}

        uploads = photo_uploads[:]
        if has_single_upload:
            uploads.append(single_upload)

        if reply_to_raw and reply_to_message is None:
            flash("That reply target is unavailable. Please choose a message in this conversation.", "error")
        elif len(photo_uploads) > 30:
            flash("You can send up to 30 pictures at once.", "error")
        elif photo_uploads and has_single_upload:
            flash("Send pictures or a document in separate messages.", "error")
        elif not content and not uploads:
            flash("Write a message or choose a picture or file.", "error")
        elif len(content) > 2000:
            flash("Messages cannot exceed 2000 characters.", "error")
        else:
            prepared = []
            error = None

            for upload in uploads:
                original_name = secure_filename(upload.filename or "")
                if not original_name or "." not in original_name:
                    error = "Please choose a valid filename."
                    break

                extension = original_name.rsplit(".", 1)[1].lower()
                allowed = image_extensions if upload in photo_uploads else allowed_extensions
                if extension not in allowed:
                    error = "That file type is not allowed."
                    break

                upload.stream.seek(0, os.SEEK_END)
                file_size = upload.stream.tell()
                upload.stream.seek(0)
                if file_size > 15 * 1024 * 1024:
                    error = "Each file must be 15 MB or smaller."
                    break

                try:
                    upload.stream.seek(0)
                    file_bytes = upload.read()

                    if extension in image_extensions:
                        validate_uploaded_image(file_bytes, "." + extension)
                    else:
                        validate_uploaded_attachment(file_bytes, extension)

                    upload.stream.seek(0)
                except ValueError as exc:
                    error = str(exc)
                    break

                prepared.append((upload, original_name, extension, file_size))

            if error:
                flash(error, "error")
            else:
                attachment_dir = BASE_DIR / "uploads" / "message_attachments"
                attachment_dir.mkdir(parents=True, exist_ok=True)
                saved_paths = []
                remote_paths = []

                try:
                    if not prepared:
                        db.session.add(Message(
                            sender_id=current_user.id,
                            recipient_id=person.id,
                            content=content,
                            reply_to_id=reply_to_message.id if reply_to_message else None
                        ))
                    else:
                        for index, (upload, original_name, extension, file_size) in enumerate(prepared):
                            stored_name = uuid.uuid4().hex + "." + extension
                            saved_path = attachment_dir / stored_name
                            remote_path = "message_attachments/" + stored_name

                            upload.stream.seek(0)
                            file_bytes = upload.read()
                            content_type = ATTACHMENT_MIME_TYPES.get(
                                extension, "application/octet-stream"
                            )

                            upload_to_supabase(remote_path, file_bytes, content_type)
                            remote_paths.append(remote_path)
                            saved_path.write_bytes(file_bytes)
                            saved_paths.append(saved_path)

                            db.session.add(Message(
                                sender_id=current_user.id,
                                recipient_id=person.id,
                                content=content if index == 0 else "",
                                attachment_filename=stored_name,
                                attachment_original_name=original_name,
                                attachment_mimetype=content_type,
                                attachment_size=file_size,
                                reply_to_id=(
                                    reply_to_message.id
                                    if index == 0 and reply_to_message else None
                                )
                            ))

                    db.session.commit()
                    try:
                        message_preview = content.strip() if content.strip() else "Sent you an attachment."
                        if len(message_preview) > 160:
                            message_preview = message_preview[:157] + "..."

                        send_push_notification(
                            person.id,
                            f"New message from {current_user.username}",
                            message_preview,
                            url_for("conversation", user_id=current_user.id),
                        )
                    except Exception:
                        app.logger.exception("Could not send message push notification.")
                except Exception:
                    db.session.rollback()
                    for saved_path in saved_paths:
                        if saved_path.exists():
                            saved_path.unlink()
                    if remote_paths and supabase_storage is not None:
                        try:
                            supabase_storage.storage.from_(SUPABASE_BUCKET).remove(remote_paths)
                        except Exception:
                            app.logger.exception("Supabase attachment cleanup failed")
                    raise

                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"success": True}), 200

                return redirect(
                    url_for("conversation", user_id=person.id) + "#latest"
                )

    unread = Message.query.filter_by(
        sender_id=person.id,
        recipient_id=current_user.id,
        read_at=None
    ).filter(
        Message.is_unsent.is_(False)
    ).all()

    if unread:
        now = db.func.current_timestamp()
        for item in unread:
            item.read_at = datetime.utcnow()
        db.session.commit()

    history = Message.query.filter(
        db.or_(
            db.and_(
                Message.sender_id == current_user.id,
                Message.recipient_id == person.id
            ),
            db.and_(
                Message.sender_id == person.id,
                Message.recipient_id == current_user.id
            )
        )
    ).order_by(Message.created_at.asc(), Message.id.asc()).all()

    hidden_ids_for_user = {
        item.message_id
        for item in MessageHidden.query.join(
            Message, MessageHidden.message_id == Message.id
        ).filter(
            MessageHidden.user_id == current_user.id,
            db.or_(
                db.and_(
                    Message.sender_id == current_user.id,
                    Message.recipient_id == person.id
                ),
                db.and_(
                    Message.sender_id == person.id,
                    Message.recipient_id == current_user.id
                )
            )
        ).all()
    }
    history = [
        item for item in history
        if item.id not in hidden_ids_for_user
    ]

    reaction_map = {}
    history_ids = [item.id for item in history]

    if history_ids:
        saved_reactions = MessageReaction.query.filter(
            MessageReaction.message_id.in_(history_ids)
        ).all()

        for reaction in saved_reactions:
            by_emoji = reaction_map.setdefault(reaction.message_id, {})
            summary = by_emoji.setdefault(
                reaction.emoji,
                {
                    "emoji": reaction.emoji,
                    "count": 0,
                    "reacted_by_me": False
                }
            )
            summary["count"] += 1
            if reaction.user_id == current_user.id:
                summary["reacted_by_me"] = True

    reaction_map = {
        message_id: list(by_emoji.values())
        for message_id, by_emoji in reaction_map.items()
    }

    default_reaction_emojis = ["\u2764\ufe0f", "\U0001f602", "\U0001f44d", "\U0001f62e", "\U0001f622", "\U0001f64f"]
    reaction_emojis = default_reaction_emojis

    try:
        if db.inspect(db.engine).has_table("user_quick_reaction"):
            quick_reaction_preference = UserQuickReaction.query.filter_by(
                user_id=current_user.id
            ).first()

            if (
                quick_reaction_preference
                and isinstance(quick_reaction_preference.emojis, list)
                and quick_reaction_preference.emojis
            ):
                reaction_emojis = quick_reaction_preference.emojis[:8]
    except Exception:
        db.session.rollback()

    preference = UserPreference.query.filter_by(
        user_id=current_user.id
    ).first()
    reaction_counts_visible = (
        preference.reaction_counts_visible if preference else True
    )

    return render_template(
        "conversation.html",
        person=person,
        history=history,
        reaction_map=reaction_map,
        reaction_emojis=reaction_emojis,
        reaction_counts_visible=reaction_counts_visible
    )





# Temporary typing signals for the single-process local development server.
_chat_typing = {}


def _chat_friendship_exists(first_id, second_id):
    return Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(
            db.and_(
                Friendship.requester_id == first_id,
                Friendship.recipient_id == second_id
            ),
            db.and_(
                Friendship.requester_id == second_id,
                Friendship.recipient_id == first_id
            )
        )
    ).first() is not None


@app.route("/messages/<int:user_id>/updates", methods=["GET"])
@login_required
def conversation_updates(user_id):
    person = db.session.get(User, user_id)
    if (
        person is None
        or person.id == current_user.id
        or not _chat_friendship_exists(current_user.id, user_id)
    ):
        return jsonify({"error": "Conversation unavailable."}), 403

    try:
        after_id = max(0, int(request.args.get("after_id", "0")))
    except (TypeError, ValueError):
        after_id = 0

    now = datetime.utcnow()

    # Acknowledging a message here means this recipient's open chat
    # has received it. Since this endpoint is polled by the open chat,
    # incoming messages are also marked read.
    incoming_unacknowledged = Message.query.filter(
        Message.sender_id == person.id,
        Message.recipient_id == current_user.id,
        db.or_(
            Message.delivered_at.is_(None),
            Message.read_at.is_(None)
        ),
        Message.is_unsent.is_(False)
    ).all()

    for item in incoming_unacknowledged:
        if item.delivered_at is None:
            item.delivered_at = now
        if item.read_at is None:
            item.read_at = now

    if incoming_unacknowledged:
        db.session.commit()

    conversation_filter = db.or_(
        db.and_(
            Message.sender_id == current_user.id,
            Message.recipient_id == person.id
        ),
        db.and_(
            Message.sender_id == person.id,
            Message.recipient_id == current_user.id
        )
    )

    new_messages = Message.query.filter(
        conversation_filter,
        Message.id > after_id
    ).order_by(Message.id.asc()).limit(100).all()
    next_after_id = max((item.id for item in new_messages), default=after_id)

    hidden_ids_for_user = {
        item.message_id
        for item in MessageHidden.query.join(
            Message, MessageHidden.message_id == Message.id
        ).filter(
            MessageHidden.user_id == current_user.id,
            db.or_(
                db.and_(
                    Message.sender_id == current_user.id,
                    Message.recipient_id == person.id
                ),
                db.and_(
                    Message.sender_id == person.id,
                    Message.recipient_id == current_user.id
                )
            )
        ).all()
    }
    new_messages = [
        item for item in new_messages
        if item.id not in hidden_ids_for_user
    ]

    outgoing = Message.query.filter_by(
        sender_id=current_user.id,
        recipient_id=person.id
    ).order_by(Message.id.desc()).limit(100).all()
    outgoing = [
        item for item in outgoing
        if item.id not in hidden_ids_for_user
    ]

    recent_conversation_messages = Message.query.filter(
        conversation_filter
    ).order_by(Message.id.desc()).limit(100).all()

    unsent_ids = [
        message_id
        for (message_id,) in db.session.query(Message.id).filter(
            conversation_filter,
            Message.is_unsent.is_(True)
        ).all()
    ]

    recent_message_ids = [
        item.id for item in recent_conversation_messages
        if item.id not in hidden_ids_for_user
    ]
    grouped_reactions = {
        message_id: {} for message_id in recent_message_ids
    }

    visible_reaction_ids = [
        item.id for item in recent_conversation_messages
        if not item.is_unsent and item.id not in hidden_ids_for_user
    ]

    if visible_reaction_ids:
        recent_reactions = MessageReaction.query.filter(
            MessageReaction.message_id.in_(visible_reaction_ids)
        ).all()

        for reaction in recent_reactions:
            by_emoji = grouped_reactions[reaction.message_id]
            summary = by_emoji.setdefault(
                reaction.emoji,
                {
                    "emoji": reaction.emoji,
                    "count": 0,
                    "reacted_by_me": False
                }
            )
            summary["count"] += 1
            if reaction.user_id == current_user.id:
                summary["reacted_by_me"] = True

    reaction_updates = [
        {
            "message_id": message_id,
            "reactions": list(grouped_reactions[message_id].values())
        }
        for message_id in recent_message_ids
        if message_id in visible_reaction_ids
    ]

    def message_data(item):
        reply_data = None
        replied_to = item.reply_to
        if replied_to and replied_to.is_unsent:
            reply_preview = "This message was unsent."
            reply_data = {
                "id": replied_to.id,
                "sender_name": (
                    replied_to.sender.username if replied_to.sender else "Message"
                ),
                "sender_verified": bool(replied_to.sender and is_verified_account(replied_to.sender)),
                "preview": reply_preview,
            }
        elif replied_to:
            reply_preview = (
                (replied_to.content or "").strip()
                or replied_to.attachment_original_name
                or (
                    "Photo"
                    if replied_to.attachment_mimetype
                    and replied_to.attachment_mimetype.startswith("image/")
                    else "Attachment"
                )
            )
            if len(reply_preview) > 140:
                reply_preview = reply_preview[:137] + "..."
            reply_data = {
                "id": replied_to.id,
                "sender_name": (
                    replied_to.sender.username if replied_to.sender else "Message"
                ),
                "sender_verified": bool(replied_to.sender and is_verified_account(replied_to.sender)),
                "preview": reply_preview,
            }

        return {
            "id": item.id,
            "sender_id": item.sender_id,
            "sender_name": item.sender.username if item.sender else "Message",
            "sender_verified": bool(item.sender and is_verified_account(item.sender)),
            "is_unsent": bool(item.is_unsent),
            "content": (
                "This message was unsent."
                if item.is_unsent else (item.content or "")
            ),
            "reply_to_id": item.reply_to_id,
            "reply_to": reply_data,
            "created_at": item.created_at.strftime("%b %d · %I:%M %p"),
            "created_at_iso": item.created_at.isoformat(),
            "attachment_url": (
                url_for("message_attachment_file", message_id=item.id)
                if item.attachment_filename and not item.is_unsent else None
            ),
            "attachment_name": (
                None if item.is_unsent else item.attachment_original_name
            ),
            "attachment_mimetype": (
                None if item.is_unsent else item.attachment_mimetype
            ),
            "attachment_size": (
                None if item.is_unsent else item.attachment_size
            ),
            "delivered_at": (
                item.delivered_at.strftime("%b %d · %I:%M %p")
                if item.delivered_at else None
            ),
            "read_at": (
                item.read_at.strftime("%b %d · %I:%M %p")
                if item.read_at else None
            )
        }

    typing_key = (person.id, current_user.id)
    typing_entry = _chat_typing.get(typing_key)
    is_typing = False

    if typing_entry:
        age = (now - typing_entry).total_seconds()
        is_typing = 0 <= age < 6
        if not is_typing:
            _chat_typing.pop(typing_key, None)

    return jsonify({
        "next_after_id": next_after_id,
        "messages": [message_data(item) for item in new_messages],
        "receipts": [message_data(item) for item in outgoing],
        "reaction_updates": reaction_updates,
        "hidden_ids": sorted(hidden_ids_for_user),
        "unsent_ids": unsent_ids,
        "typing": is_typing
    })


@app.route("/messages/inbox-updates", methods=["GET"])
@login_required
def inbox_updates():
    accepted = Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(
            Friendship.requester_id == current_user.id,
            Friendship.recipient_id == current_user.id
        )
    ).all()

    friend_ids = {
        item.recipient_id if item.requester_id == current_user.id
        else item.requester_id
        for item in accepted
    }

    friends = User.query.filter(
        User.id.in_(friend_ids)
    ).all() if friend_ids else []

    conversations = []

    for person in friends:
        latest = Message.query.filter(
            db.or_(
                db.and_(
                    Message.sender_id == current_user.id,
                    Message.recipient_id == person.id
                ),
                db.and_(
                    Message.sender_id == person.id,
                    Message.recipient_id == current_user.id
                )
            )
        ).filter(
            Message.id.notin_(
                db.session.query(MessageHidden.message_id).filter_by(
                    user_id=current_user.id
                )
            )
        ).order_by(
            Message.created_at.desc(), Message.id.desc()
        ).first()

        unread = Message.query.filter_by(
            sender_id=person.id,
            recipient_id=current_user.id,
            read_at=None
        ).filter(
            Message.is_unsent.is_(False)
        ).count()

        if latest and latest.is_unsent:
            preview = "This message was unsent."
        elif latest:
            preview = latest.content or ""
            if not preview and latest.attachment_filename:
                preview = "Sent an attachment"
            if latest.sender_id == current_user.id:
                preview = "You: " + preview
        else:
            preview = "You're friends — say hello!"

        conversations.append({
            "user_id": person.id,
            "username": person.username,
            "is_verified": is_verified_account(person),
            "profile_picture": (
                url_for(
                    "profile_picture_file",
                    filename=person.profile_picture
                ) if person.profile_picture else None
            ),
            "preview": preview,
            "unread": unread,
            "latest_id": latest.id if latest else 0,
            "latest_at": (
                latest.created_at.isoformat() if latest else ""
            )
        })

    conversations.sort(
        key=lambda item: (item["latest_at"], item["latest_id"]),
        reverse=True
    )

    # Acknowledge delivery for messages received by the active inbox.
    pending_delivery = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.delivered_at.is_(None),
        Message.is_unsent.is_(False)
    ).all()

    if pending_delivery:
        delivered_time = datetime.utcnow()
        for message in pending_delivery:
            message.delivered_at = delivered_time
        db.session.commit()

    unread_count = Message.query.filter_by(
        recipient_id=current_user.id,
        read_at=None
    ).filter(
        Message.is_unsent.is_(False)
    ).count()

    return jsonify({
        "conversations": conversations,
        "unread_count": unread_count
    })


@app.route("/messages/<int:user_id>/typing", methods=["POST"])
@login_required
def conversation_typing(user_id):
    person = db.session.get(User, user_id)
    if (
        person is None
        or person.id == current_user.id
        or not _chat_friendship_exists(current_user.id, user_id)
    ):
        return jsonify({"error": "Conversation unavailable."}), 403

    payload = request.get_json(silent=True) or {}
    key = (current_user.id, person.id)

    if payload.get("typing") is True:
        _chat_typing[key] = datetime.utcnow()
    else:
        _chat_typing.pop(key, None)

    return jsonify({"ok": True})

@app.route("/settings/quick-reactions", methods=["POST"])
@login_required
def save_quick_reactions():
    import unicodedata

    payload = request.get_json(silent=True) or {}
    emojis = payload.get("emojis")

    if not isinstance(emojis, list) or len(emojis) > 8:
        return jsonify({"error": "Choose up to eight emojis."}), 400

    cleaned = []
    for emoji in emojis:
        if not isinstance(emoji, str) or not 0 < len(emoji) <= 16:
            return jsonify({"error": "Invalid emoji choice."}), 400

        if (
            any(char.isalpha() for char in emoji)
            or not any(
                unicodedata.category(char) in {"So", "Sk", "Me"}
                or ord(char) >= 0x1F000
                for char in emoji
            )
        ):
            return jsonify({"error": "Invalid emoji choice."}), 400

        if emoji not in cleaned:
            cleaned.append(emoji)

    if not db.inspect(db.engine).has_table("user_quick_reaction"):
        UserQuickReaction.__table__.create(db.engine, checkfirst=True)

    preference = UserQuickReaction.query.filter_by(
        user_id=current_user.id
    ).first()

    if preference is None:
        preference = UserQuickReaction(
            user_id=current_user.id,
            emojis=cleaned
        )
        db.session.add(preference)
    else:
        preference.emojis = cleaned

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Could not save quick-reaction preferences.")
        return jsonify({"error": "Could not save your emoji choices."}), 500

    return jsonify({"ok": True, "emojis": cleaned})


@app.route("/messages/<int:message_id>/reaction", methods=["POST"])
@login_required
def message_reaction_api(message_id):
    message = db.session.get(Message, message_id)

    if message is None:
        return jsonify({"error": "Message not found."}), 404

    if message.is_unsent:
        return jsonify({"error": "This message was unsent."}), 409

    participant = current_user.id in (
        message.sender_id, message.recipient_id
    )
    if (
        not participant
        or message.sender_id == message.recipient_id
        or not _chat_friendship_exists(
            message.sender_id, message.recipient_id
        )
    ):
        return jsonify({"error": "Conversation unavailable."}), 403

    payload = request.get_json(silent=True) or {}
    emoji = payload.get("emoji")
    if emoji is not None:
        import unicodedata

        valid_emoji = (
            isinstance(emoji, str)
            and 0 < len(emoji) <= 16
            and not any(char.isalpha() for char in emoji)
            and any(
                unicodedata.category(char) in {"So", "Sk", "Me"}
                or ord(char) >= 0x1F000
                for char in emoji
            )
        )

        if not valid_emoji:
            return jsonify({"error": "Unsupported reaction."}), 400

    reaction = MessageReaction.query.filter_by(
        message_id=message.id,
        user_id=current_user.id
    ).first()

    if emoji is None:
        if reaction is not None:
            db.session.delete(reaction)
    elif reaction is not None:
        reaction.emoji = emoji
    else:
        db.session.add(MessageReaction(
            message_id=message.id,
            user_id=current_user.id,
            emoji=emoji
        ))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Could not save reaction. Please retry."}), 409

    reactions = MessageReaction.query.filter_by(
        message_id=message.id
    ).all()

    grouped = {}
    for item in reactions:
        if item.emoji not in grouped:
            grouped[item.emoji] = {
                "emoji": item.emoji,
                "count": 0,
                "reacted_by_me": False
            }
        grouped[item.emoji]["count"] += 1
        if item.user_id == current_user.id:
            grouped[item.emoji]["reacted_by_me"] = True

    return jsonify({
        "ok": True,
        "message_id": message.id,
        "reactions": list(grouped.values())
    })


@app.route("/messages/<int:message_id>/delete", methods=["POST"])
@login_required
def message_delete_api(message_id):
    message = db.session.get(Message, message_id)

    if message is None:
        return jsonify({"error": "Message not found."}), 404

    participant = current_user.id in (
        message.sender_id, message.recipient_id
    )
    if (
        not participant
        or message.sender_id == message.recipient_id
        or not _chat_friendship_exists(
            message.sender_id, message.recipient_id
        )
    ):
        return jsonify({"error": "Conversation unavailable."}), 403

    payload = request.get_json(silent=True) or {}
    action = payload.get("action")

    if action == "hide":
        existing = MessageHidden.query.filter_by(
            message_id=message.id,
            user_id=current_user.id
        ).first()

        if existing is None:
            db.session.add(MessageHidden(
                message_id=message.id,
                user_id=current_user.id
            ))

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            existing = MessageHidden.query.filter_by(
                message_id=message.id,
                user_id=current_user.id
            ).first()
            if existing is None:
                return jsonify({
                    "error": "Could not hide the message. Please retry."
                }), 409

        return jsonify({
            "ok": True,
            "action": "hide",
            "message_id": message.id
        })

    if action == "unsend":
        if message.sender_id != current_user.id:
            return jsonify({
                "error": "You can only unsend your own messages."
            }), 403

        if not message.is_unsent:
            message.is_unsent = True
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                app.logger.exception("Could not unsend message %s.", message_id)
                return jsonify({
                    "error": "Could not unsend the message. Please retry."
                }), 500

        return jsonify({
            "ok": True,
            "action": "unsend",
            "message_id": message.id
        })

    return jsonify({
        "error": "Unsupported action. Use 'hide' or 'unsend'."
    }), 400


@app.route("/presence/heartbeat", methods=["POST"])
@login_required
def presence_heartbeat():
    current_user.last_seen = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/users/<int:user_id>/presence", methods=["GET"])
@login_required
def user_presence(user_id):
    person = db.session.get(User, user_id)

    if (
        person is None
        or person.id == current_user.id
        or not _chat_friendship_exists(current_user.id, person.id)
    ):
        return jsonify({"error": "Presence unavailable."}), 403

    # Respect the other user's saved presence-visibility preference.
    if db.inspect(db.engine).has_table("user_preference"):
        preference = UserPreference.query.filter_by(
            user_id=person.id
        ).first()
        if preference is not None and not preference.show_online_status:
            return jsonify({
                "visible": False,
                "active": False,
                "last_seen_seconds": None,
                "offline_since": None
            })

    now = datetime.utcnow()
    last_seen = person.last_seen
    seconds_ago = (
        max(0, int((now - last_seen).total_seconds()))
        if last_seen else None
    )
    active = seconds_ago is not None and seconds_ago < 300

    offline_since = (
        (last_seen + timedelta(seconds=300)).isoformat() + "Z"
        if last_seen else None
    )

    return jsonify({
        "visible": True,
        "active": active,
        "last_seen_seconds": seconds_ago,
        "offline_since": offline_since
    })


def _location_friends(first_id, second_id):
    if first_id == second_id:
        return False

    return Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(
            db.and_(
                Friendship.requester_id == first_id,
                Friendship.recipient_id == second_id
            ),
            db.and_(
                Friendship.requester_id == second_id,
                Friendship.recipient_id == first_id
            )
        )
    ).first() is not None


def _location_is_active(share, now=None):
    now = now or datetime.utcnow()
    return (
        share.stopped_at is None
        and (share.expires_at is None or share.expires_at > now)
    )


@app.route("/messages/<int:user_id>/location/start", methods=["POST"])
@login_required
def start_location_share(user_id):
    person = db.session.get(User, user_id)
    if person is None or not _location_friends(current_user.id, user_id):
        return jsonify(error="Location sharing is only available between accepted friends."), 403

    data = request.get_json(silent=True) or {}
    duration = data.get("duration_hours")

    if duration in ("unlimited", None):
        duration_hours = None
    else:
        try:
            duration_hours = int(duration)
        except (TypeError, ValueError):
            return jsonify(error="Choose a valid sharing duration."), 400

        if duration_hours not in (1, 8, 24):
            return jsonify(error="Duration must be 1, 8, 24 hours, or unlimited."), 400

    now = datetime.utcnow()

    # End any previous active share from this sender to this recipient.
    previous = LiveLocationShare.query.filter_by(
        sharer_id=current_user.id,
        recipient_id=person.id,
        stopped_at=None
    ).all()

    for old_share in previous:
        old_share.stopped_at = now

    share = LiveLocationShare(
        sharer_id=current_user.id,
        recipient_id=person.id,
        duration_hours=duration_hours,
        started_at=now,
        expires_at=(
            None if duration_hours is None
            else now + __import__("datetime").timedelta(hours=duration_hours)
        )
    )
    db.session.add(share)
    db.session.commit()

    return jsonify(
        ok=True,
        share_id=share.id,
        expires_at=share.expires_at.isoformat() if share.expires_at else None
    )


@app.route("/location-shares/<int:share_id>/update", methods=["POST"])
@login_required
def update_shared_location(share_id):
    share = db.session.get(LiveLocationShare, share_id)

    if (
        share is None
        or share.sharer_id != current_user.id
        or not _location_is_active(share)
        or not _location_friends(share.sharer_id, share.recipient_id)
    ):
        return jsonify(error="This location share is no longer active."), 403

    data = request.get_json(silent=True) or {}
    try:
        latitude = float(data.get("latitude"))
        longitude = float(data.get("longitude"))
    except (TypeError, ValueError):
        return jsonify(error="Valid coordinates are required."), 400

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return jsonify(error="Coordinates are outside the valid range."), 400

    share.latitude = latitude
    share.longitude = longitude
    share.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify(ok=True)


@app.route("/location-shares/<int:share_id>/stop", methods=["POST"])
@login_required
def stop_location_share(share_id):
    share = db.session.get(LiveLocationShare, share_id)

    if share is None or share.sharer_id != current_user.id:
        return jsonify(error="You cannot stop someone else's location share."), 403

    if share.stopped_at is None:
        share.stopped_at = datetime.utcnow()
        db.session.commit()

    return jsonify(ok=True)


@app.route("/messages/<int:user_id>/location/status")
@login_required
def location_share_status(user_id):
    person = db.session.get(User, user_id)
    if person is None or person.id == current_user.id:
        return jsonify(error="Friend not found."), 404

    if not _location_friends(current_user.id, person.id):
        return jsonify(error="Location sharing is only available between accepted friends."), 403

    now = datetime.utcnow()
    outgoing = LiveLocationShare.query.filter_by(
        sharer_id=current_user.id,
        recipient_id=user_id,
        stopped_at=None
    ).order_by(LiveLocationShare.id.desc()).first()

    incoming = LiveLocationShare.query.filter_by(
        sharer_id=user_id,
        recipient_id=current_user.id,
        stopped_at=None
    ).order_by(LiveLocationShare.id.desc()).first()

    def public_share(share, is_mine):
        if share is None or not _location_is_active(share, now):
            return None

        result = {
            "id": share.id,
            "mine": is_mine,
            "expires_at": share.expires_at.isoformat() if share.expires_at else None,
            "updated_at": share.updated_at.isoformat() if share.updated_at else None,
            "has_location": share.latitude is not None and share.longitude is not None
        }
        if result["has_location"]:
            result["latitude"] = share.latitude
            result["longitude"] = share.longitude
        return result

    return jsonify(
        outgoing=public_share(outgoing, True),
        incoming=public_share(incoming, False)
    )


@app.route("/message-attachments/<int:message_id>")
@login_required
def message_attachment_file(message_id):
    message = db.session.get(Message, message_id)
    if message is not None and message.is_unsent:
        abort(404)

    if message is None or not message.attachment_filename:
        flash("Attachment not found.", "error")
        return redirect(url_for("messages"))

    if current_user.id not in (message.sender_id, message.recipient_id):
        flash("You cannot access that attachment.", "error")
        return redirect(url_for("messages"))

    hidden_message = MessageHidden.query.filter_by(
        message_id=message.id,
        user_id=current_user.id
    ).first()
    if hidden_message is not None:
        abort(404)

    attachment_dir = BASE_DIR / "uploads" / "message_attachments"
    extension = Path(message.attachment_filename).suffix.lower().lstrip(".")
    trusted_mimetype = ATTACHMENT_MIME_TYPES.get(
        extension, "application/octet-stream"
    )
    is_image = trusted_mimetype.startswith("image/")
    is_audio = trusted_mimetype.startswith("audio/")
    download_name = message.attachment_original_name or "attachment"

    try:
        file_bytes = download_from_supabase(
            "message_attachments/" + message.attachment_filename
        )
        return send_file(
            BytesIO(file_bytes),
            mimetype=trusted_mimetype,
            as_attachment=not (is_image or is_audio),
            download_name=download_name
        )
    except Exception:
        app.logger.exception("Supabase message-attachment retrieval failed")
        return send_from_directory(
            attachment_dir,
            message.attachment_filename,
            mimetype=trusted_mimetype,
            as_attachment=not (is_image or is_audio),
            download_name=download_name
        )


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have logged out.", "success")
    return redirect(url_for("home"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        action = request.form.get("action", "").strip()

        if action == "save_online_status":
            value = request.form.get("show_online_status", "off").strip().lower()
            if value not in {"on", "off"}:
                flash("Please choose a valid online-status setting.", "error")
                return redirect(url_for("settings", _anchor="privacy"))

            UserPreference.__table__.create(bind=db.engine, checkfirst=True)
            preference = UserPreference.query.filter_by(
                user_id=current_user.id
            ).first()

            if preference is None:
                preference = UserPreference(
                    user_id=current_user.id,
                    theme="system",
                    show_online_status=(value == "on")
                )
                db.session.add(preference)
            else:
                preference.show_online_status = (value == "on")

            db.session.commit()
            flash("Online-status privacy setting saved.", "success")
            return redirect(url_for("settings", _anchor="privacy"))

        if action == "save_theme":
            theme = request.form.get("theme", "system").strip().lower()

            if theme not in {"system", "light", "dark"}:
                flash("Please choose a valid appearance theme.", "error")
            else:
                # Create only this new table if it does not exist.
                UserPreference.__table__.create(
                    bind=db.engine, checkfirst=True
                )
                preference = UserPreference.query.filter_by(
                    user_id=current_user.id
                ).first()

                if preference is None:
                    preference = UserPreference(
                        user_id=current_user.id,
                        theme=theme
                    )
                    db.session.add(preference)
                else:
                    preference.theme = theme

                db.session.commit()
                flash("Appearance preference saved.", "success")

            return redirect(url_for("settings", _anchor="appearance"))

        if action == "save_reaction_preferences":
            value = request.form.get("reaction_counts_visible", "off")
            if value not in {"on", "off"}:
                flash("Please choose a valid reaction-count preference.", "error")
                return redirect(url_for("settings", _anchor="reactions"))

            UserPreference.__table__.create(bind=db.engine, checkfirst=True)
            preference = UserPreference.query.filter_by(
                user_id=current_user.id
            ).first()
            if preference is None:
                preference = UserPreference(
                    user_id=current_user.id,
                    theme="system",
                    show_online_status=True
                )
                db.session.add(preference)

            preference.reaction_counts_visible = (value == "on")
            db.session.commit()
            flash("Reaction preferences saved.", "success")
            return redirect(url_for("settings", _anchor="reactions"))

        if action == "save_accessibility_preferences":
            font_scale = request.form.get("font_scale", "normal").strip().lower()
            if font_scale not in {"normal", "large", "extra-large"}:
                flash("Please choose a valid font size.", "error")
                return redirect(url_for("settings", _anchor="accessibility"))

            UserPreference.__table__.create(bind=db.engine, checkfirst=True)
            preference = UserPreference.query.filter_by(
                user_id=current_user.id
            ).first()
            if preference is None:
                preference = UserPreference(
                    user_id=current_user.id,
                    theme="system",
                    show_online_status=True
                )
                db.session.add(preference)

            preference.font_scale = font_scale
            preference.reduce_motion = (
                request.form.get("reduce_motion", "off") == "on"
            )
            preference.high_contrast = (
                request.form.get("high_contrast", "off") == "on"
            )
            db.session.commit()
            flash("Accessibility preferences saved.", "success")
            return redirect(url_for("settings", _anchor="accessibility"))

        if action == "logout_other_devices":
            # Incrementing the version invalidates sessions on other devices.
            current_user.session_version += 1
            db.session.commit()
            # Re-establish this browser's session with the new version.
            login_user(current_user, fresh=True)
            flash("Other devices have been logged out.", "success")
            return redirect(url_for("settings", _anchor="logout"))

        if action == "change_password":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not current_user.check_password(current_password):
                flash("Your current password is incorrect.", "error")
            elif len(new_password) < 12:
                flash("Your new password must be at least 12 characters long.", "error")
            elif new_password != confirm_password:
                flash("The new passwords do not match.", "error")
            elif current_user.check_password(new_password):
                flash("Choose a password different from your current password.", "error")
            else:
                current_user.set_password(new_password)
                current_user.session_version += 1
                db.session.commit()
                # Keep this browser signed in with the new versioned session ID.
                login_user(current_user, fresh=True)
                flash("Your password has been changed successfully.", "success")

            return redirect(url_for("settings", _anchor="password"))

        flash("That settings action is not available yet.", "error")
        return redirect(url_for("settings"))

    # Ensure only the new preferences table is created if missing.
    UserPreference.__table__.create(bind=db.engine, checkfirst=True)
    preference = UserPreference.query.filter_by(
        user_id=current_user.id
    ).first()
    theme = preference.theme if preference else "system"
    show_online_status = (
        preference.show_online_status if preference else True
    )
    reaction_counts_visible = (
        preference.reaction_counts_visible if preference else True
    )
    font_scale = preference.font_scale if preference else "normal"
    reduce_motion = preference.reduce_motion if preference else False
    high_contrast = preference.high_contrast if preference else False

    if font_scale not in {"normal", "large", "extra-large"}:
        font_scale = "normal"

    if theme not in {"system", "light", "dark"}:
        theme = "system"

    return render_template(
        "settings.html",
        user=current_user,
        theme=theme,
        show_online_status=show_online_status,
        reaction_counts_visible=reaction_counts_visible,
        font_scale=font_scale,
        reduce_motion=reduce_motion,
        high_contrast=high_contrast
    )

def _can_view_post(post, viewer_id):
    """Check whether a signed-in user may view a post."""
    if post.user_id == viewer_id:
        return True

    audience = getattr(post, "audience", None) or "public"
    if audience == "public":
        return True
    if audience != "friends":
        return False

    return Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(
            db.and_(
                Friendship.requester_id == post.user_id,
                Friendship.recipient_id == viewer_id,
            ),
            db.and_(
                Friendship.requester_id == viewer_id,
                Friendship.recipient_id == post.user_id,
            ),
        ),
    ).first() is not None


@app.route("/feed", methods=["GET", "POST"])
@login_required
def feed():
    if request.method == "POST":
        content = request.form.get("content", "").strip()
        audience = request.form.get("audience", "public").strip().lower()

        if audience not in {"public", "friends"}:
            flash("Please choose a valid post audience.", "error")
        elif not content:
            flash("Please write something before posting.", "error")
        elif len(content) > 2000:
            flash("Posts cannot exceed 2000 characters.", "error")
        else:
            post = Post(
                content=content, author=current_user, audience=audience
            )
            db.session.add(post)
            db.session.commit()
            flash("Your post was published!", "success")
            return redirect(url_for("feed", _anchor="post-form"))

    posts = Post.query.order_by(
        Post.created_at.desc(), Post.id.desc()
    ).all()
    posts = [
        post for post in posts
        if _can_view_post(post, current_user.id)
    ]

    preference = UserPreference.query.filter_by(
        user_id=current_user.id
    ).first()
    reaction_counts_visible = (
        preference.reaction_counts_visible if preference else True
    )

    return render_template(
        "feed.html",
        posts=posts,
        reaction_counts_visible=reaction_counts_visible
    )


@app.route("/post/<int:post_id>/like", methods=["POST"])
@login_required
def toggle_like(post_id):
    post = db.session.get(Post, post_id)
    if post is None:
        flash("That post could not be found.", "error")
        return redirect(url_for("feed"))
    if not _can_view_post(post, current_user.id):
        flash("That post is not available to you.", "error")
        return redirect(url_for("feed"))

    existing = PostLike.query.filter_by(
        user_id=current_user.id,
        post_id=post.id
    ).first()

    if existing:
        db.session.delete(existing)
        flash("Like removed.", "success")
    else:
        db.session.add(PostLike(user_id=current_user.id, post_id=post.id))
        flash("You liked this post.", "success")

    db.session.commit()
    return redirect(url_for("feed", _anchor=f"post-{post_id}"))


@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def add_comment(post_id):
    post = db.session.get(Post, post_id)
    content = request.form.get("content", "").strip()

    if post is None:
        flash("That post could not be found.", "error")
    elif not _can_view_post(post, current_user.id):
        flash("That post is not available to you.", "error")
    elif not content:
        flash("Please write a comment first.", "error")
    elif len(content) > 1000:
        flash("Comments cannot exceed 1000 characters.", "error")
    else:
        comment = Comment(
            content=content,
            user_id=current_user.id,
            post_id=post.id
        )
        db.session.add(comment)
        db.session.commit()
        flash("Comment added!", "success")

    return redirect(url_for("feed", _anchor=f"post-{post_id}"))




# ------------------------------------------------------------
@app.route("/notifications/updates", methods=["GET"])
@login_required
def notifications_updates():
    """Read-only summary of notifications for the signed-in user."""

    unread_count = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.read_at.is_(None),
        Message.is_unsent.is_(False)
    ).count()

    unread_messages = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.read_at.is_(None),
        Message.is_unsent.is_(False)
    ).order_by(Message.created_at.desc()).limit(20).all()

    message_items = [
        {
            "id": message.id,
            "sender_id": message.sender_id,
            "sender_name": message.sender.username,
            "preview": (
                message.content[:160]
                if message.content else "Sent an attachment"
            ),
            "created_at": message.created_at.isoformat() + "Z",
        }
        for message in unread_messages
    ]

    pending_requests = Friendship.query.filter_by(
        recipient_id=current_user.id,
        status="pending"
    ).order_by(Friendship.created_at.desc()).limit(20).all()

    friend_request_items = [
        {
            "id": friendship.id,
            "sender_id": friendship.requester_id,
            "sender_name": friendship.requester.username,
            "created_at": friendship.created_at.isoformat() + "Z",
        }
        for friendship in pending_requests
    ]

    call = VideoCall.query.filter_by(
        recipient_id=current_user.id,
        status="ringing"
    ).order_by(VideoCall.created_at.desc()).first()

    incoming_call = None
    if call is not None and video_call_access(call):
        incoming_call = {
            "id": call.id,
            "caller_id": call.caller_id,
            "caller_name": call.caller.username,
            "created_at": call.created_at.isoformat() + "Z",
        }

    return jsonify({
        "unread_count": unread_count,
        "unread_messages": message_items,
        "pending_friend_requests": friend_request_items,
        "incoming_call": incoming_call,
    })


# GOTYA VIDEO CALL API
# Authenticated, accepted-friend-only call signaling.
# ------------------------------------------------------------

def video_call_are_friends(first_id, second_id):
    return Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(
            db.and_(
                Friendship.requester_id == first_id,
                Friendship.recipient_id == second_id
            ),
            db.and_(
                Friendship.requester_id == second_id,
                Friendship.recipient_id == first_id
            )
        )
    ).first() is not None


def video_call_access(call):
    if call is None:
        return False
    if current_user.id not in (call.caller_id, call.recipient_id):
        return False
    return video_call_are_friends(call.caller_id, call.recipient_id)


def video_call_json(call):
    return {
        "id": call.id,
        "caller_id": call.caller_id,
        "recipient_id": call.recipient_id,
        "status": call.status,
        "offer_sdp": call.offer_sdp,
        "answer_sdp": call.answer_sdp,
        "created_at": call.created_at.isoformat() + "Z",
        "updated_at": call.updated_at.isoformat() + "Z",
    }


@app.route("/calls/<int:user_id>/start", methods=["POST"])
@login_required
def video_call_start(user_id):
    if user_id == current_user.id:
        return jsonify({"error": "You cannot call yourself."}), 400

    person = db.session.get(User, user_id)
    if person is None or not video_call_are_friends(current_user.id, user_id):
        return jsonify({"error": "You can call accepted friends only."}), 403

    active = VideoCall.query.filter(
        VideoCall.status.in_(("ringing", "accepted")),
        db.or_(
            db.and_(
                VideoCall.caller_id == current_user.id,
                VideoCall.recipient_id == user_id
            ),
            db.and_(
                VideoCall.caller_id == user_id,
                VideoCall.recipient_id == current_user.id
            ),
            VideoCall.caller_id == current_user.id,
            VideoCall.recipient_id == current_user.id
        )
    ).first()

    if active:
        return jsonify({
            "error": "One of you is already in a call.",
            "call_id": active.id
        }), 409

    call = VideoCall(
        id=str(uuid.uuid4()),
        caller_id=current_user.id,
        recipient_id=user_id,
        status="ringing",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.session.add(call)
    db.session.commit()

    try:
        send_push_notification(
            user_id,
            "Incoming video call",
            f"{current_user.username} is calling you.",
            url_for("conversation", user_id=current_user.id),
        )
    except Exception:
        app.logger.exception("Could not send incoming-call push notification.")

    return jsonify({"call": video_call_json(call)}), 201


@app.route("/calls/incoming", methods=["GET"])
@login_required
def video_call_incoming():
    call = VideoCall.query.filter_by(
        recipient_id=current_user.id,
        status="ringing"
    ).order_by(VideoCall.created_at.desc()).first()

    if call is None or not video_call_access(call):
        return jsonify({"call": None})

    return jsonify({
        "call": video_call_json(call),
        "caller_name": call.caller.username
    })


@app.route("/calls/<call_id>", methods=["GET"])
@login_required
def video_call_get(call_id):
    call = db.session.get(VideoCall, call_id)
    if not video_call_access(call):
        return jsonify({"error": "Call not found or access denied."}), 404
    return jsonify({"call": video_call_json(call)})


@app.route("/calls/<call_id>/accept", methods=["POST"])
@login_required
def video_call_accept(call_id):
    call = db.session.get(VideoCall, call_id)
    if not video_call_access(call):
        return jsonify({"error": "Call not found or access denied."}), 404
    if current_user.id != call.recipient_id:
        return jsonify({"error": "Only the invited user can accept."}), 403
    if call.status != "ringing":
        return jsonify({"error": "This call is no longer ringing."}), 409

    call.status = "accepted"
    call.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"call": video_call_json(call)})


@app.route("/calls/<call_id>/decline", methods=["POST"])
@login_required
def video_call_decline(call_id):
    call = db.session.get(VideoCall, call_id)
    if not video_call_access(call):
        return jsonify({"error": "Call not found or access denied."}), 404
    if current_user.id != call.recipient_id:
        return jsonify({"error": "Only the invited user can decline."}), 403
    if call.status != "ringing":
        return jsonify({"error": "This call is no longer ringing."}), 409

    call.status = "declined"
    call.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"call": video_call_json(call)})


@app.route("/calls/<call_id>/end", methods=["POST"])
@login_required
def video_call_end(call_id):
    call = db.session.get(VideoCall, call_id)
    if not video_call_access(call):
        return jsonify({"error": "Call not found or access denied."}), 404

    if call.status not in ("ringing", "accepted"):
        return jsonify({"call": video_call_json(call)})

    was_accepted = call.status == "accepted"
    ended_at = datetime.utcnow()
    other_user_id = (
        call.recipient_id
        if current_user.id == call.caller_id
        else call.caller_id
    )

    if was_accepted:
        started_at = call.updated_at

        # Prefer the browser's actual connected time when available.
        data = request.get_json(silent=True) or {}
        connected_at = data.get("connected_at")

        if isinstance(connected_at, str):
            try:
                candidate = datetime.fromisoformat(
                    connected_at.replace("Z", "+00:00")
                )
                if candidate.tzinfo is not None:
                    candidate = candidate.replace(tzinfo=None)

                if call.created_at <= candidate <= ended_at:
                    started_at = candidate
            except (TypeError, ValueError):
                app.logger.warning("Invalid connected_at supplied for call %s.", call.id)

        duration_seconds = max(
            0, int((ended_at - started_at).total_seconds())
        )
        minutes, seconds = divmod(duration_seconds, 60)

        # Display Philippine time (UTC+8) in the chat summary.
        timedelta = __import__("datetime").timedelta
        started_pht = started_at + timedelta(hours=8)
        ended_pht = ended_at + timedelta(hours=8)

        summary = (
            "Call Ended\n"
            f"{minutes}m {seconds:02d}s \u00b7 {started_pht.strftime('%I:%M %p').lstrip('0')}"
        )

        db.session.add(Message(
            sender_id=current_user.id,
            recipient_id=other_user_id,
            content=summary,
        ))

    call.status = "ended"
    call.ended_by = current_user.id
    call.updated_at = ended_at

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Could not save the ended call.")
        return jsonify({"error": "Could not save the call ending."}), 500

    if was_accepted:
        try:
            send_push_notification(
                other_user_id,
                "Video call ended",
                f"{current_user.username} ended the call. {minutes}m {seconds:02d}s.",
                url_for("conversation", user_id=current_user.id),
            )
        except Exception:
            app.logger.exception("Could not send call-ended notification.")

    return jsonify({"call": video_call_json(call)})


@app.route("/calls/<call_id>/offer", methods=["POST"])
@login_required
def video_call_offer(call_id):
    call = db.session.get(VideoCall, call_id)
    if not video_call_access(call):
        return jsonify({"error": "Call not found or access denied."}), 404
    if current_user.id != call.caller_id:
        return jsonify({"error": "Only the caller can submit the offer."}), 403
    if call.status != "accepted":
        return jsonify({"error": "The call must be accepted first."}), 409

    data = request.get_json(silent=True) or {}
    sdp = data.get("sdp")
    if not isinstance(sdp, str) or not sdp or len(sdp) > 100000:
        return jsonify({"error": "Invalid connection offer."}), 400

    call.offer_sdp = sdp
    call.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/calls/<call_id>/answer", methods=["POST"])
@login_required
def video_call_answer(call_id):
    call = db.session.get(VideoCall, call_id)
    if not video_call_access(call):
        return jsonify({"error": "Call not found or access denied."}), 404
    if current_user.id != call.recipient_id:
        return jsonify({"error": "Only the invited user can submit the answer."}), 403
    if call.status != "accepted":
        return jsonify({"error": "The call must be accepted first."}), 409
    if not call.offer_sdp:
        return jsonify({"error": "The caller has not sent an offer yet."}), 409

    data = request.get_json(silent=True) or {}
    sdp = data.get("sdp")
    if not isinstance(sdp, str) or not sdp or len(sdp) > 100000:
        return jsonify({"error": "Invalid connection answer."}), 400

    call.answer_sdp = sdp
    call.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/calls/<call_id>/signals", methods=["GET", "POST"])
@login_required
def video_call_signals(call_id):
    call = db.session.get(VideoCall, call_id)
    if not video_call_access(call):
        return jsonify({"error": "Call not found or access denied."}), 404

    if call.status != "accepted":
        return jsonify({"signals": []}) if request.method == "GET" else (
            jsonify({"error": "The call is not active."}), 409
        )

    if request.method == "GET":
        try:
            after_id = max(0, int(request.args.get("after", "0")))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid signal cursor."}), 400

        signals = VideoCallSignal.query.filter(
            VideoCallSignal.call_id == call.id,
            VideoCallSignal.id > after_id,
            VideoCallSignal.sender_id != current_user.id
        ).order_by(VideoCallSignal.id.asc()).limit(100).all()

        return jsonify({
            "signals": [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "payload": item.payload,
                    "sender_id": item.sender_id
                }
                for item in signals
            ]
        })

    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    payload = data.get("payload")

    if kind != "ice-candidate":
        return jsonify({"error": "Unsupported signal type."}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid ICE candidate."}), 400

    import json
    encoded = json.dumps(payload, separators=(",", ":"))
    if len(encoded) > 10000:
        return jsonify({"error": "ICE candidate is too large."}), 400

    signal = VideoCallSignal(
        call_id=call.id,
        sender_id=current_user.id,
        kind=kind,
        payload=encoded
    )
    db.session.add(signal)
    db.session.commit()
    return jsonify({"ok": True, "id": signal.id}), 201


def send_push_notification(user_id, title, body, target_url="/"):
    """Send best-effort Web Push notifications to a user's registered devices."""
    if not (
        app.config.get("VAPID_PUBLIC_KEY")
        and app.config.get("VAPID_PRIVATE_KEY")
        and app.config.get("VAPID_SUBJECT")
    ):
        app.logger.warning("Web Push is not fully configured.")
        return

    subscriptions = PushSubscription.query.filter_by(user_id=user_id).all()

    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {
                        "p256dh": subscription.p256dh,
                        "auth": subscription.auth,
                    },
                },
                data=__import__("json").dumps({
                    "title": str(title)[:120],
                    "body": str(body)[:300],
                    "url": target_url if (
                        isinstance(target_url, str)
                        and target_url.startswith("/")
                        and not target_url.startswith("//")
                    ) else "/",
                }),
                vapid_private_key=app.config["VAPID_PRIVATE_KEY"],
                vapid_claims={
                    "sub": app.config["VAPID_SUBJECT"],
                },
                ttl=60 * 60,
            )
        except WebPushException as exc:
            status_code = getattr(
                getattr(exc, "response", None), "status_code", None
            )
            if status_code in (404, 410):
                try:
                    db.session.delete(subscription)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    app.logger.exception("Could not remove expired push subscription.")
            else:
                app.logger.warning(
                    "Web Push delivery failed (HTTP %s).", status_code
                )
        except Exception:
            app.logger.exception("Unexpected Web Push delivery failure.")

@app.route("/push/vapid-public-key", methods=["GET"])
@login_required
def push_vapid_public_key():
    public_key = app.config.get("VAPID_PUBLIC_KEY", "")
    if not public_key:
        return jsonify({"error": "Push notifications are not configured."}), 503
    return jsonify({"publicKey": public_key})



@app.route("/push/subscribe", methods=["POST"])
@login_required
def push_subscription_api():
    data = request.get_json(silent=True) or {}
    subscription = data.get("subscription") or {}
    keys = subscription.get("keys") or {}
    endpoint = subscription.get("endpoint")
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not all(isinstance(value, str) and value.strip() for value in
               (endpoint, p256dh, auth)):
        return jsonify({"error": "Invalid push subscription."}), 400

    if not endpoint.startswith("https://") or len(endpoint) > 4096:
        return jsonify({"error": "Invalid push endpoint."}), 400

    if len(p256dh) > 255 or len(auth) > 255:
        return jsonify({"error": "Invalid subscription keys."}), 400

    existing = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if existing:
        existing.user_id = current_user.id
        existing.p256dh = p256dh
        existing.auth = auth
    else:
        db.session.add(PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
        ))

    db.session.commit()
    return jsonify({"ok": True}), 201


@app.route("/push/unsubscribe", methods=["POST"])
@login_required
def push_unsubscribe_api():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")

    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        return jsonify({"error": "Invalid push endpoint."}), 400

    PushSubscription.query.filter_by(
        user_id=current_user.id,
        endpoint=endpoint,
    ).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/sw.js")
def gotya_service_worker():
    response = send_from_directory(
        app.static_folder,
        "sw.js",
        mimetype="application/javascript"
    )
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response
if __name__ == "__main__":
    with app.app_context():
        db.create_all()

        # Safely create the per-user hidden-message table if it is missing.
        MessageHidden.__table__.create(db.engine, checkfirst=True)
        print("Message-hidden table is available.")

        # Safely add last_seen without resetting existing user data.
        columns = {
            column["name"]
            for column in db.inspect(db.engine).get_columns("user")
        }

        if "last_seen" not in columns:
            with db.engine.begin() as connection:
                connection.execute(
                    db.text("ALTER TABLE user ADD COLUMN last_seen DATETIME")
                )
            print("Added last_seen column without resetting user data.")
        else:
            print("Last-seen database column already exists.")

        # Safely add the profile-picture column to existing databases.
        columns = {
            column["name"]
            for column in db.inspect(db.engine).get_columns("user")
        }

        if "profile_picture" not in columns:
            with db.engine.begin() as connection:
                connection.execute(
                    db.text(
                        "ALTER TABLE user "
                        "ADD COLUMN profile_picture VARCHAR(255)"
                    )
                )
            print("Added profile_picture column without resetting user data.")
        else:
            print("Profile-picture database column already exists.")

        # Add profile fields without resetting existing user data.
        columns = {
            column["name"]
            for column in db.inspect(db.engine).get_columns("user")
        }

        with db.engine.begin() as connection:
            if "bio" not in columns:
                connection.execute(
                    db.text("ALTER TABLE user ADD COLUMN bio VARCHAR(500)")
                )
                print("Added bio column without resetting user data.")
            else:
                print("Bio database column already exists.")

            if "cover_photo" not in columns:
                connection.execute(
                    db.text(
                        "ALTER TABLE user ADD COLUMN cover_photo VARCHAR(255)"
                    )
                )
                print("Added cover_photo column without resetting user data.")
            else:
                print("Cover-photo database column already exists.")

            if "cover_video" not in columns:
                connection.execute(
                    db.text(
                        "ALTER TABLE user ADD COLUMN cover_video VARCHAR(255)"
                    )
                )
                print("Added cover_video column without resetting user data.")
            else:
                print("Cover-video database column already exists.")

        # Safely add message delivery timestamps without resetting chat history.
        message_columns = {
            column["name"]
            for column in db.inspect(db.engine).get_columns("message")
        }

        if "delivered_at" not in message_columns:
            with db.engine.begin() as connection:
                connection.execute(
                    db.text(
                        "ALTER TABLE message "
                        "ADD COLUMN delivered_at DATETIME"
                    )
                )
            print("Added delivered_at column without resetting messages.")
        else:
            print("Message delivery timestamp column already exists.")
        # Add reply support without replacing existing message records.
        message_columns = {
            column["name"]
            for column in db.inspect(db.engine).get_columns("message")
        }
        if "reply_to_id" not in message_columns:
            with db.engine.begin() as connection:
                connection.execute(
                    db.text("ALTER TABLE message ADD COLUMN reply_to_id INTEGER")
                )
            print("Added reply_to_id without resetting existing messages.")
        else:
            print("Message reply column already exists.")


        # Safely add unsent state without replacing existing messages.
        message_columns = {
            column["name"]
            for column in db.inspect(db.engine).get_columns("message")
        }
        if "is_unsent" not in message_columns:
            with db.engine.begin() as connection:
                connection.execute(
                    db.text(
                        "ALTER TABLE message "
                        "ADD COLUMN is_unsent BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
            print("Added is_unsent without resetting existing messages.")
        else:
            print("Message unsent-state column already exists.")

    app.run(host="0.0.0.0", port=5000, debug=False)

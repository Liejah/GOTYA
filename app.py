
import os
from datetime import datetime, timedelta
from pathlib import Path
from io import BytesIO
import mimetypes
from supabase import create_client

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
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
from sqlalchemy.exc import IntegrityError

BASE_DIR = Path(__file__).resolve().parent
PROFILE_PICTURE_DIR = BASE_DIR / "uploads" / "profile_pictures"
COVER_PHOTO_DIR = BASE_DIR / "uploads" / "cover_photos"
COVER_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_PICTURE_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(BASE_DIR / ".env")

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

# Protect session cookies.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

db = SQLAlchemy(app)
csrf = CSRFProtect(app)

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
    last_seen = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(2000), nullable=False)
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
    created_at = db.Column(
        db.DateTime, nullable=False, default=db.func.current_timestamp()
    )
    read_at = db.Column(db.DateTime, nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    attachment_filename = db.Column(db.String(255), nullable=True)
    attachment_original_name = db.Column(db.String(255), nullable=True)
    attachment_mimetype = db.Column(db.String(120), nullable=True)
    attachment_size = db.Column(db.Integer, nullable=True)

    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])

    __table_args__ = (
        db.CheckConstraint(
            "sender_id != recipient_id",
            name="ck_message_not_self"
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
    return db.session.get(User, int(user_id))


@app.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("messages"))
    return redirect(url_for("login"))


@app.route("/health")
def health():
    return {"status": "ok", "app": "ConnectHub"}


@app.route("/register", methods=["GET", "POST"])
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


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("messages"))

    if request.method == "POST":
        identity = request.form.get("identity", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter(
            db.or_(
                User.username.ilike(identity),
                User.email == identity.lower(),
            )
        ).first()

        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("messages"))

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
                content_type = cover_file.mimetype or "application/octet-stream"

                upload_to_supabase(
                    "cover_photos/" + filename,
                    file_bytes,
                    content_type
                )
                destination.write_bytes(file_bytes)

                current_user.cover_photo = filename
                db.session.commit()
                flash("Cover photo updated successfully!", "success")
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
            content_type = uploaded_file.mimetype or "application/octet-stream"

            upload_to_supabase(
                "profile_pictures/" + filename,
                file_bytes,
                content_type
            )
            destination.write_bytes(file_bytes)

            current_user.profile_picture = filename
            db.session.commit()
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

    return render_template(
        "profile.html",
        user=current_user,
        post_count=post_count,
        friend_count=friend_count,
        likes_received=likes_received,
    )


@app.route("/profile-pictures/<path:filename>")
@login_required
def profile_picture_file(filename):
    try:
        file_bytes = download_from_supabase("profile_pictures/" + filename)
        return send_file(
            BytesIO(file_bytes),
            mimetype=mimetypes.guess_type(filename)[0] or "application/octet-stream",
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
            mimetype=mimetypes.guess_type(filename)[0] or "application/octet-stream",
            download_name=filename
        )
    except Exception:
        app.logger.exception("Supabase cover-photo retrieval failed")
        return send_from_directory(COVER_PHOTO_DIR, filename)


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

    return render_template(
        "friends.html",
        query=query,
        users=users,
        incoming=incoming,
        outgoing=outgoing,
        friends=found_friends,
        relationships=relationships,
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
        ).order_by(Message.created_at.desc(), Message.id.desc()).first()
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
        photo_uploads = [
            item for item in request.files.getlist("photos")
            if item and item.filename
        ]
        single_upload = request.files.get("attachment")
        has_single_upload = bool(single_upload and single_upload.filename)

        allowed_extensions = {
            "jpg", "jpeg", "png", "gif", "webp",
            "pdf", "txt", "csv", "doc", "docx",
            "xls", "xlsx", "ppt", "pptx", "zip"
        }
        image_extensions = {"jpg", "jpeg", "png", "gif", "webp"}

        uploads = photo_uploads[:]
        if has_single_upload:
            uploads.append(single_upload)

        if len(photo_uploads) > 30:
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
                            content=content
                        ))
                    else:
                        for index, (upload, original_name, extension, file_size) in enumerate(prepared):
                            stored_name = uuid.uuid4().hex + "." + extension
                            saved_path = attachment_dir / stored_name
                            remote_path = "message_attachments/" + stored_name

                            upload.stream.seek(0)
                            file_bytes = upload.read()
                            content_type = (
                                "image/" + ("jpeg" if extension in {"jpg", "jpeg"} else extension)
                                if extension in image_extensions
                                else (upload.mimetype or "application/octet-stream")
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
                                attachment_size=file_size
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

                return redirect(
                    url_for("conversation", user_id=person.id) + "#latest"
                )

    unread = Message.query.filter_by(
        sender_id=person.id,
        recipient_id=current_user.id,
        read_at=None
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

    return render_template(
        "conversation.html", person=person, history=history
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
        )
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

    outgoing = Message.query.filter_by(
        sender_id=current_user.id,
        recipient_id=person.id
    ).order_by(Message.id.desc()).limit(100).all()

    def message_data(item):
        return {
            "id": item.id,
            "sender_id": item.sender_id,
            "content": item.content or "",
            "created_at": item.created_at.strftime("%b %d · %I:%M %p"),
            "created_at_iso": item.created_at.isoformat(),
            "attachment_url": (
                url_for("message_attachment_file", message_id=item.id)
                if item.attachment_filename else None
            ),
            "attachment_name": item.attachment_original_name,
            "attachment_mimetype": item.attachment_mimetype,
            "attachment_size": item.attachment_size,
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
        "messages": [message_data(item) for item in new_messages],
        "receipts": [message_data(item) for item in outgoing],
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
        ).order_by(
            Message.created_at.desc(), Message.id.desc()
        ).first()

        unread = Message.query.filter_by(
            sender_id=person.id,
            recipient_id=current_user.id,
            read_at=None
        ).count()

        if latest:
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
        Message.delivered_at.is_(None)
    ).all()

    if pending_delivery:
        delivered_time = datetime.utcnow()
        for message in pending_delivery:
            message.delivered_at = delivered_time
        db.session.commit()

    unread_count = Message.query.filter_by(
        recipient_id=current_user.id,
        read_at=None
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
    if message is None or not message.attachment_filename:
        flash("Attachment not found.", "error")
        return redirect(url_for("messages"))

    if current_user.id not in (message.sender_id, message.recipient_id):
        flash("You cannot access that attachment.", "error")
        return redirect(url_for("messages"))

    attachment_dir = BASE_DIR / "uploads" / "message_attachments"
    is_image = (message.attachment_mimetype or "").startswith("image/")

    try:
        file_bytes = download_from_supabase(
            "message_attachments/" + message.attachment_filename
        )
        return send_file(
            BytesIO(file_bytes),
            mimetype=message.attachment_mimetype or (
                mimetypes.guess_type(message.attachment_filename)[0]
                or "application/octet-stream"
            ),
            as_attachment=not is_image,
            download_name=message.attachment_original_name or "attachment"
        )
    except Exception:
        app.logger.exception("Supabase message-attachment retrieval failed")
        return send_from_directory(
            attachment_dir,
            message.attachment_filename,
            as_attachment=not is_image,
            download_name=message.attachment_original_name or "attachment"
        )


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have logged out.", "success")
    return redirect(url_for("home"))


@app.route("/feed", methods=["GET", "POST"])
@login_required
def feed():
    if request.method == "POST":
        content = request.form.get("content", "").strip()

        if not content:
            flash("Please write something before posting.", "error")
        elif len(content) > 2000:
            flash("Posts cannot exceed 2000 characters.", "error")
        else:
            post = Post(content=content, author=current_user)
            db.session.add(post)
            db.session.commit()
            flash("Your post was published!", "success")
            return redirect(url_for("feed", _anchor="post-form"))

    posts = Post.query.order_by(
        Post.created_at.desc(), Post.id.desc()
    ).all()

    return render_template("feed.html", posts=posts)


@app.route("/post/<int:post_id>/like", methods=["POST"])
@login_required
def toggle_like(post_id):
    post = db.session.get(Post, post_id)
    if post is None:
        flash("That post could not be found.", "error")
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
        Message.read_at.is_(None)
    ).count()

    unread_messages = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.read_at.is_(None)
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

    call.status = "ended"
    call.ended_by = current_user.id
    call.updated_at = datetime.utcnow()
    db.session.commit()
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
    app.run(host="0.0.0.0", port=5000, debug=False)

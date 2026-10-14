
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
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
import uuid
from sqlalchemy.exc import IntegrityError

BASE_DIR = Path(__file__).resolve().parent
PROFILE_PICTURE_DIR = BASE_DIR / "uploads" / "profile_pictures"
COVER_PHOTO_DIR = BASE_DIR / "uploads" / "cover_photos"
COVER_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_PICTURE_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)

secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    raise RuntimeError("SECRET_KEY is missing. Check your .env file.")

app.config["SECRET_KEY"] = secret_key
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

    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])

    __table_args__ = (
        db.CheckConstraint(
            "sender_id != recipient_id",
            name="ck_message_not_self"
        ),
    )


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.route("/")
def home():
    return redirect(url_for("login"))


@app.route("/health")
def health():
    return {"status": "ok", "app": "ConnectHub"}


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("profile"))

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
                return redirect(url_for("profile"))
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
        return redirect(url_for("profile"))

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
            return redirect(url_for("profile"))

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
                cover_file.save(destination)
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
            uploaded_file.save(destination)
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
    return send_from_directory(PROFILE_PICTURE_DIR, filename)


@app.route("/cover-photos/<path:filename>")
@login_required
def cover_photo_file(filename):
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
            flash("Friend request sent again.", "success")
        elif existing.status == "declined" and existing.recipient_id == current_user.id:
            existing.requester_id, existing.recipient_id = (
                current_user.id, target.id
            )
            existing.status = "pending"
            db.session.commit()
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

        if not content:
            flash("Write a message before sending.", "error")
        elif len(content) > 2000:
            flash("Messages cannot exceed 2000 characters.", "error")
        else:
            message = Message(
                sender_id=current_user.id,
                recipient_id=person.id,
                content=content
            )
            db.session.add(message)
            db.session.commit()
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

    app.run(host="0.0.0.0", port=5000, debug=False)


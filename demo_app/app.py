"""
Demo Application — "TaskFlow"
------------------------------
A small, mostly-functional task-management web app used as the live
target for the AI App Tester. Registration and login use real,
server-side-authoritative validation (HTML attributes are present too,
but the server never trusts them alone) — the app behaves the way a
real, reasonably careful small team's app would.

A SMALL, INTENTIONAL set of realistic issues is deliberately kept so the
AI App Tester has genuine, discoverable things to find:

  1. The "About" link in the main nav points at a route that does not
     exist -> HTTP 404 (BUG - broken navigation). No /about route is
     defined on purpose.
  2. The footer "Subscribe" control is an icon-only button with no
     visible text, no aria-label, and no accessible name (evidence-based
     ACCESSIBILITY issue / IMPROVEMENT candidate).

Everything else — registration validation, duplicate-email handling,
login, session handling, logout — is intended to work correctly, so that
a correct AI Tester run reports those flows as PASS rather than
inventing bugs where none exist.
"""

import re
import secrets

from flask import Flask, flash, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.secret_key = "demo-app-not-for-production-" + secrets.token_hex(8)

# In-memory "database" — resets whenever the demo app restarts. Never use
# a pattern like this for anything real; it exists purely so this demo
# has zero external setup requirements.
USERS = {
    "demo@example.com": {"password": "Password123!", "username": "demo_user"}
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_USERNAME_LEN = 3
MIN_PASSWORD_LEN = 8


@app.route("/")
def home():
    return render_template("home.html")


# NOTE: intentionally there is no "/about" route defined, even though the
# nav bar links to it. This is the one deliberate "broken navigation" bug
# the AI Tester is expected to discover and report as a BUG (404).


@app.route("/register", methods=["GET", "POST"])
def register():
    errors = []
    values = {"username": "", "email": ""}

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        # Preserve non-sensitive entered values so a validation failure
        # doesn't force the user to retype everything. Passwords are
        # intentionally never re-populated back into the form.
        values["username"] = username
        values["email"] = email

        if not username:
            errors.append("Username is required.")
        elif len(username) < MIN_USERNAME_LEN:
            errors.append(f"Username must be at least {MIN_USERNAME_LEN} characters long.")

        if not email:
            errors.append("Email is required.")
        elif not EMAIL_RE.match(email):
            errors.append("Please enter a valid email address.")
        elif email.lower() in {e.lower() for e in USERS}:
            errors.append("An account with this email already exists.")

        if not password:
            errors.append("Password is required.")
        elif len(password) < MIN_PASSWORD_LEN:
            errors.append(f"Password must be at least {MIN_PASSWORD_LEN} characters long.")

        if not confirm:
            errors.append("Please confirm your password.")
        elif password and confirm != password:
            errors.append("Password and confirmation do not match.")

        if errors:
            return render_template("register.html", errors=errors, values=values)

        USERS[email] = {"password": password, "username": username}
        flash("Account created successfully. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", errors=errors, values=values)


@app.route("/login", methods=["GET", "POST"])
def login():
    errors = []
    values = {"email": ""}

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        values["email"] = email

        if not email:
            errors.append("Email is required.")
        if not password:
            errors.append("Password is required.")

        if not errors:
            user = USERS.get(email)
            if user and user["password"] == password:
                session["user"] = email
                return redirect(url_for("dashboard"))
            errors.append("Invalid email or password.")

        return render_template("login.html", errors=errors, values=values)

    return render_template("login.html", errors=errors, values=values)


@app.route("/dashboard")
def dashboard():
    user = session.get("user")
    if not user:
        return redirect(url_for("login"))
    return render_template("dashboard.html", user=USERS.get(user, {}), email=user)


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)

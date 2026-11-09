import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import demo_app.app as demo


class TaskFlowTestCase(unittest.TestCase):
    def setUp(self):
        demo.app.testing = True
        self.client = demo.app.test_client()
        # Each test gets a clean in-memory user store, since USERS is a
        # module-level dict shared across the whole process.
        self._original_users = dict(demo.USERS)

    def tearDown(self):
        demo.USERS.clear()
        demo.USERS.update(self._original_users)


class TestHomeAndAbout(TaskFlowTestCase):
    def test_home_loads(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)

    def test_about_is_intentionally_404(self):
        # This is the one deliberately-kept broken-navigation bug the AI
        # Tester is expected to discover.
        r = self.client.get("/about")
        self.assertEqual(r.status_code, 404)

    def test_get_started_link_present_and_points_to_register(self):
        r = self.client.get("/")
        self.assertIn(b'href="/register"', r.data)


class TestRegistrationValidation(TaskFlowTestCase):
    def test_empty_submission_shows_all_required_errors(self):
        r = self.client.post("/register", data={})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Username is required.", r.data)
        self.assertIn(b"Email is required.", r.data)
        self.assertIn(b"Password is required.", r.data)
        self.assertIn(b"Please confirm your password.", r.data)

    def test_username_too_short_rejected(self):
        r = self.client.post("/register", data={
            "username": "ab", "email": "ok@example.com",
            "password": "longenough1", "confirm_password": "longenough1",
        })
        self.assertIn(b"Username must be at least 3 characters", r.data)

    def test_invalid_email_format_rejected(self):
        r = self.client.post("/register", data={
            "username": "validuser", "email": "not-an-email",
            "password": "longenough1", "confirm_password": "longenough1",
        })
        self.assertIn(b"valid email address", r.data)

    def test_short_password_rejected(self):
        r = self.client.post("/register", data={
            "username": "validuser", "email": "unique1@example.com",
            "password": "short", "confirm_password": "short",
        })
        self.assertIn(b"Password must be at least 8 characters", r.data)

    def test_mismatched_confirmation_rejected(self):
        r = self.client.post("/register", data={
            "username": "validuser", "email": "unique2@example.com",
            "password": "longenough1", "confirm_password": "different1",
        })
        self.assertIn(b"do not match", r.data)

    def test_duplicate_email_rejected(self):
        r = self.client.post("/register", data={
            "username": "seconduser", "email": "demo@example.com",  # already seeded
            "password": "longenough1", "confirm_password": "longenough1",
        })
        self.assertIn(b"already exists", r.data)

    def test_form_values_preserved_after_validation_failure(self):
        r = self.client.post("/register", data={
            "username": "keepme", "email": "keepme@example.com",
            "password": "short", "confirm_password": "short",
        })
        self.assertIn(b'value="keepme"', r.data)
        self.assertIn(b'value="keepme@example.com"', r.data)

    def test_password_never_repopulated_in_form(self):
        r = self.client.post("/register", data={
            "username": "keepme2", "email": "keepme2@example.com",
            "password": "supersecretvalue", "confirm_password": "different",
        })
        self.assertNotIn(b"supersecretvalue", r.data)

    def test_successful_registration_shows_success_message_and_reaches_login(self):
        r = self.client.post("/register", data={
            "username": "newuser", "email": "newuser@example.com",
            "password": "longenough1", "confirm_password": "longenough1",
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Account created successfully", r.data)

    def test_successful_registration_creates_a_working_login(self):
        self.client.post("/register", data={
            "username": "newuser2", "email": "newuser2@example.com",
            "password": "longenough1", "confirm_password": "longenough1",
        })
        r = self.client.post("/login", data={
            "email": "newuser2@example.com", "password": "longenough1",
        }, follow_redirects=True)
        self.assertIn(b"Welcome", r.data)


class TestLoginValidation(TaskFlowTestCase):
    def test_empty_submission_shows_required_errors(self):
        r = self.client.post("/login", data={})
        self.assertIn(b"Email is required.", r.data)
        self.assertIn(b"Password is required.", r.data)

    def test_invalid_credentials_show_clear_error(self):
        r = self.client.post("/login", data={"email": "demo@example.com", "password": "wrongpass"})
        self.assertIn(b"Invalid email or password", r.data)

    def test_email_value_preserved_after_failed_login(self):
        r = self.client.post("/login", data={"email": "demo@example.com", "password": "wrongpass"})
        self.assertIn(b'value="demo@example.com"', r.data)

    def test_password_not_echoed_after_failed_login(self):
        r = self.client.post("/login", data={"email": "demo@example.com", "password": "wrongpass"})
        self.assertNotIn(b"wrongpass", r.data)

    def test_valid_credentials_redirect_to_dashboard(self):
        r = self.client.post("/login", data={"email": "demo@example.com", "password": "Password123!"},
                              follow_redirects=True)
        self.assertIn(b"Welcome", r.data)

    def test_dashboard_requires_authentication(self):
        r = self.client.get("/dashboard")
        self.assertEqual(r.status_code, 302)  # redirected to login

    def test_logout_clears_session(self):
        self.client.post("/login", data={"email": "demo@example.com", "password": "Password123!"})
        self.client.get("/logout")
        r = self.client.get("/dashboard")
        self.assertEqual(r.status_code, 302)


if __name__ == "__main__":
    unittest.main()

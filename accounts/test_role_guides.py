import json
import re
from datetime import datetime, timezone as datetime_timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from queues.models import StaffProfile

from .access import SIMULATED_ROLE_SESSION_KEY, capabilities_for
from .guide_context import staff_guide_context
from .role_guides import ROLE_GUIDES


class StaffRoleGuideTests(TestCase):
    """Check the guide follows the account's actual role and login session."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = {}
        for role in StaffProfile.Role.values:
            user = get_user_model().objects.create_user(
                username=f"guide-{role.lower()}", password="test-guide-password"
            )
            StaffProfile.objects.update_or_create(user=user, defaults={"role": role})
            cls.staff[role] = user
        cls.admin = get_user_model().objects.create_superuser(
            username="guide-admin", password="test-guide-password", email=""
        )

    def make_request(self, user):
        request = RequestFactory().get(reverse("my_permissions"))
        SessionMiddleware(lambda _request: HttpResponse()).process_request(request)
        request.user = user
        return request

    def rendered_config(self, response):
        match = re.search(
            r'<script[^>]*id="staff-role-guide-config"[^>]*>(.*?)</script>',
            response.content.decode(),
            re.DOTALL,
        )
        self.assertIsNotNone(match, "The rendered page must carry its own guide config")
        return json.loads(match.group(1))

    def test_every_staff_role_has_guide_links_it_can_open(self):
        self.assertEqual(set(ROLE_GUIDES), {*StaffProfile.Role.values, "ADMIN"})
        users = {**self.staff, "ADMIN": self.admin}
        for role, user in users.items():
            with self.subTest(role=role):
                guide = staff_guide_context(self.make_request(user), role)["staff_role_guide"]
                self.assertTrue(guide["title"])
                self.assertTrue(guide["summary"])
                self.assertTrue(guide["sections"])
                guide_links = []
                for section in guide["sections"]:
                    self.assertTrue(section["title"])
                    self.assertTrue(section["steps"])
                    for link in section["links"]:
                        self.assertTrue(link["label"])
                        self.assertTrue(link["url"].startswith("/"))
                        link_parts = urlsplit(link["url"])
                        match = resolve(link_parts.path)
                        self.assertEqual(reverse(match.view_name), link_parts.path)
                        if role == StaffProfile.Role.NURSE and match.view_name == "personnel_dashboard":
                            self.assertEqual(parse_qs(link_parts.query), {"view": ["patients"]})
                        required = getattr(match.func, "required_capability", None)
                        if required:
                            self.assertIn(
                                required,
                                capabilities_for(user),
                                f"{role} guide links to forbidden page {match.view_name}",
                            )
                        guide_links.append(link)
                self.assertTrue(guide_links)

    def test_existing_sessions_get_a_stable_visit_and_role_specific_config(self):
        request = self.make_request(self.admin)
        initial = staff_guide_context(request, "ADMIN")["staff_role_guide_config"]
        repeated = staff_guide_context(request, "ADMIN")["staff_role_guide_config"]
        nurse = staff_guide_context(request, StaffProfile.Role.NURSE)["staff_role_guide_config"]

        self.assertTrue(initial["visit"])
        self.assertEqual(request.session["staff_guide_visit"], initial["visit"])
        self.assertEqual(initial, repeated)
        self.assertEqual(initial["visit"], nurse["visit"])
        self.assertEqual(initial["accountId"], str(self.admin.pk))
        self.assertEqual(nurse["accountId"], str(self.admin.pk))
        self.assertEqual(nurse["role"], StaffProfile.Role.NURSE)
        self.assertNotEqual(initial["role"], nurse["role"])

    def test_independent_logins_do_not_share_visit_or_account_keys(self):
        user = self.staff[StaffProfile.Role.NURSE]
        same_user_first = staff_guide_context(self.make_request(user), StaffProfile.Role.NURSE)
        same_user_second = staff_guide_context(self.make_request(user), StaffProfile.Role.NURSE)
        other_user = staff_guide_context(
            self.make_request(self.staff[StaffProfile.Role.CASHIER]), StaffProfile.Role.CASHIER
        )

        first_config = same_user_first["staff_role_guide_config"]
        second_config = same_user_second["staff_role_guide_config"]
        other_config = other_user["staff_role_guide_config"]
        self.assertNotEqual(first_config["visit"], second_config["visit"])
        self.assertEqual(first_config["accountId"], second_config["accountId"])
        self.assertNotEqual(first_config["accountId"], other_config["accountId"])

    def test_suppression_day_uses_bangkok_at_utc_date_boundary(self):
        request = self.make_request(self.staff[StaffProfile.Role.STAFF])
        # 18:30 UTC on October 5 is 01:30 on October 6 in Thailand.
        utc_now = datetime(2026, 10, 5, 18, 30, tzinfo=datetime_timezone.utc)
        with timezone.override("UTC"), patch("accounts.guide_context.timezone.now", return_value=utc_now):
            config = staff_guide_context(request, StaffProfile.Role.STAFF)["staff_role_guide_config"]
        self.assertEqual(config["day"], "2026-10-06")

    def test_anonymous_users_get_no_guide_or_visit_marker(self):
        request = self.make_request(AnonymousUser())
        context = staff_guide_context(request, None)

        self.assertIsNone(context["staff_role_guide"])
        self.assertIsNone(context["staff_role_guide_config"])
        self.assertNotIn("staff_guide_visit", request.session)
        response = self.client.get(reverse("login"))
        self.assertNotContains(response, 'id="staffRoleGuideDialog"')
        self.assertNotContains(response, 'id="staff-role-guide-config"')

    def test_login_signal_refreshes_visit_even_for_the_same_account(self):
        user = self.staff[StaffProfile.Role.NURSE]
        self.assertTrue(self.client.login(username=user.username, password="test-guide-password"))
        first_visit = self.client.session["staff_guide_visit"]
        self.assertTrue(self.client.login(username=user.username, password="test-guide-password"))
        second_visit = self.client.session["staff_guide_visit"]

        self.assertNotEqual(first_visit, second_visit)
        response = self.client.get(reverse("my_permissions"))
        self.assertEqual(self.rendered_config(response)["visit"], second_visit)

    def test_each_role_page_renders_its_guide_and_reopen_button(self):
        for role, user in {**self.staff, "ADMIN": self.admin}.items():
            with self.subTest(role=role):
                self.client.force_login(user)
                response = self.client.get(reverse("my_permissions"))
                self.assertContains(response, 'id="staffRoleGuideDialog"')
                self.assertContains(response, 'id="staffRoleGuideOpen"')
                self.assertContains(response, ROLE_GUIDES[role]["title"])
                config = self.rendered_config(response)
                self.assertEqual(config["accountId"], str(user.pk))
                self.assertEqual(config["role"], role)
                self.client.logout()

    def test_admin_role_switch_renders_the_assumed_role_guide(self):
        self.client.force_login(self.admin)
        initial = self.client.get(reverse("my_permissions"))
        visit = self.rendered_config(initial)["visit"]

        response = self.client.post(reverse("switch_test_role"), {"role": StaffProfile.Role.PHARMACIST})
        self.assertEqual(response.status_code, 302)
        pharmacy = self.client.get(reverse("my_permissions"))
        self.assertEqual(pharmacy.context["staff_role_guide"]["title"], ROLE_GUIDES[StaffProfile.Role.PHARMACIST]["title"])
        config = self.rendered_config(pharmacy)
        self.assertEqual(config["role"], StaffProfile.Role.PHARMACIST)
        self.assertEqual(config["visit"], visit)

        self.client.post(reverse("switch_test_role"), {"role": "ADMIN"})
        admin_page = self.client.get(reverse("my_permissions"))
        self.assertEqual(self.rendered_config(admin_page)["role"], "ADMIN")

    def test_regular_user_cannot_select_admin_guide_through_session(self):
        nurse = self.staff[StaffProfile.Role.NURSE]
        self.client.force_login(nurse)
        session = self.client.session
        session[SIMULATED_ROLE_SESSION_KEY] = "ADMIN"
        session.save()

        response = self.client.get(reverse("my_permissions"))
        self.assertEqual(self.rendered_config(response)["role"], StaffProfile.Role.NURSE)
        self.assertEqual(response.context["staff_role_guide"]["title"], ROLE_GUIDES[StaffProfile.Role.NURSE]["title"])

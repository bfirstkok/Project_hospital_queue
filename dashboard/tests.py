from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse


class DashboardPresentationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="dashboard-admin",
            email="dashboard@example.test",
            password="test-password",
        )
        self.client.force_login(self.user)

    def test_dashboard_uses_shared_left_navigation_and_summary_layout(self):
        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "แสดงเฉพาะข้อมูลที่ต้องใช้ตัดสินใจ")
        self.assertContains(response, "พยาบาลผู้ดูแล")
        self.assertContains(response, 'class="severity"')
        self.assertContains(response, "document.body.prepend(nav)")

    def test_waiting_time_report_uses_report_hero_and_exports(self):
        response = self.client.get(reverse("dashboard:waiting_time_report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="report-hero"')
        self.assertContains(response, 'class="detail-toggle"')
        self.assertContains(response, reverse("dashboard:waiting_time_report_csv"))
        self.assertContains(response, reverse("dashboard:waiting_time_report_xls"))
        self.assertContains(response, reverse("dashboard:waiting_time_report_pdf"))

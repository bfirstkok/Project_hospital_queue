from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from patients.models import Patient
from queues.models import Queue, TriageResult, Visit


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
        patient = Patient.objects.create(first_name="สมชาย", last_name="ทดสอบ", national_id="1111111111111")
        visit = Visit.objects.create(patient=patient, final_severity=Visit.Severity.YELLOW, note="มีไข้และชีพจรเร็ว")
        Queue.objects.create(visit=visit, status=Queue.Status.WAITING_QUEUE, priority=3)
        TriageResult.objects.create(
            visit=visit,
            ai_severity=Visit.Severity.YELLOW,
            nurse_severity=Visit.Severity.YELLOW,
            ai_reason="warning vital signs",
            nurse_note="ยืนยันตามอาการและสัญญาณชีพ",
        )
        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "แสดงเฉพาะข้อมูลที่ต้องใช้ตัดสินใจ")
        self.assertContains(response, "พยาบาลผู้ดูแล")
        self.assertContains(response, 'class="severity"')
        self.assertContains(response, 'id="urgent-alert-title"')
        self.assertContains(response, "รายการแจ้งเตือนเร่งด่วน")
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'data-open-severity="YELLOW"')
        self.assertContains(response, "สมชาย ทดสอบ")
        self.assertContains(response, "เหตุผลจากระบบ")
        self.assertContains(response, "ยืนยันตามอาการและสัญญาณชีพ")
        self.assertContains(response, "document.body.prepend(nav)")

    def test_waiting_time_report_uses_report_hero_and_exports(self):
        response = self.client.get(reverse("dashboard:waiting_time_report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="report-hero"')
        self.assertContains(response, 'class="detail-toggle"')
        self.assertContains(response, 'data-open-severity="RED"')
        self.assertContains(response, "ผลสุดท้ายยืนยันโดยพยาบาล")
        self.assertContains(response, reverse("dashboard:waiting_time_report_csv"))
        self.assertContains(response, reverse("dashboard:waiting_time_report_xls"))
        self.assertContains(response, reverse("dashboard:waiting_time_report_pdf"))

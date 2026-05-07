from datetime import timedelta
import random

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from patients.models import Patient
from queues.models import Device, Queue, TelemetryLog, TriageResult, Visit, VitalSign


SEED_PATIENTS = [
    ("Demo Red", "Patient", "9000000000001", "RED", {"rr": 34, "pr": 128, "sys_bp": 86, "dia_bp": 58, "bt": 39.2, "o2sat": 91}),
    ("Demo Yellow", "Patient", "9000000000002", "YELLOW", {"rr": 24, "pr": 118, "sys_bp": 112, "dia_bp": 74, "bt": 38.4, "o2sat": 95}),
    ("Demo Green", "Patient", "9000000000003", "GREEN", {"rr": 18, "pr": 82, "sys_bp": 122, "dia_bp": 78, "bt": 36.8, "o2sat": 98}),
    ("Monitor Red", "Patient", "9000000000004", "RED", {"rr": 32, "pr": 132, "sys_bp": 92, "dia_bp": 60, "bt": 38.9, "o2sat": 93}),
    ("Follow Up", "Patient", "9000000000005", "YELLOW", {"rr": 22, "pr": 104, "sys_bp": 130, "dia_bp": 84, "bt": 37.8, "o2sat": 96}),
]


class Command(BaseCommand):
    help = "Seed demo patients, queues, devices and telemetry for presentation."

    @transaction.atomic
    def handle(self, *args, **options):
        now = timezone.now()

        devices = []
        for i in range(1, 4):
            device, _ = Device.objects.update_or_create(
                device_id=f"DEMO-DEVICE-{i:02d}",
                defaults={"api_key": f"demo-key-{i:02d}", "is_active": True, "last_seen": now},
            )
            devices.append(device)

        for idx, (first_name, last_name, national_id, severity, vitals) in enumerate(SEED_PATIENTS):
            patient, _ = Patient.objects.update_or_create(
                national_id=national_id,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "gender": "UNKNOWN",
                    "age": 35 + idx,
                    "phone": f"08000000{idx}",
                    "blood_type": "UNKNOWN",
                    "bp_sys": vitals["sys_bp"],
                    "bp_dia": vitals["dia_bp"],
                },
            )

            visit = Visit.objects.filter(patient=patient).order_by("-registered_at").first()
            if not visit:
                visit = Visit.objects.create(
                    patient=patient,
                    registered_at=now - timedelta(minutes=45 - idx * 6),
                    triaged_at=now - timedelta(minutes=35 - idx * 5),
                    called_at=now - timedelta(minutes=20 - idx * 3) if idx in [1, 2, 4] else None,
                    final_severity=severity,
                    note="Demo clinical symptoms for presentation",
                )
            else:
                visit.final_severity = severity
                visit.triaged_at = visit.triaged_at or now - timedelta(minutes=20)
                visit.save(update_fields=["final_severity", "triaged_at"])

            VitalSign.objects.update_or_create(visit=visit, defaults=vitals)

            TriageResult.objects.update_or_create(
                visit=visit,
                defaults={
                    "ai_severity": severity,
                    "nurse_severity": severity,
                    "model_name": "decision_tree_v1",
                    "confidence": 0.86,
                },
            )

            status = "WAITING"
            if idx == 3:
                status = "MONITORING"
            elif idx == 4:
                status = "FOLLOWUP"

            queue, _ = Queue.objects.update_or_create(
                visit=visit,
                defaults={
                    "status": status,
                    "priority": {"RED": 1, "YELLOW": 2, "GREEN": 3}[severity],
                },
            )

            device = devices[idx % len(devices)]
            for n in range(8):
                drift = random.randint(-3, 3)
                TelemetryLog.objects.create(
                    visit=visit,
                    device=device,
                    ts=now - timedelta(minutes=7 - n),
                    bpm=max(40, vitals["pr"] + drift),
                    o2sat=max(80, min(100, vitals["o2sat"] + random.randint(-1, 1))),
                    bt=round(float(vitals["bt"]) + random.uniform(-0.2, 0.2), 1),
                    rr=max(8, vitals["rr"] + random.randint(-1, 1)),
                    sys_bp=max(60, vitals["sys_bp"] + random.randint(-5, 5)),
                    dia_bp=max(35, vitals["dia_bp"] + random.randint(-3, 3)),
                )

            self.stdout.write(f"Seeded visit #{visit.id} queue #{queue.id} {severity} {status}")

        self.stdout.write(self.style.SUCCESS("Demo data seeded."))

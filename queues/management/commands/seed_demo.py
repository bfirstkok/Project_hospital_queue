from datetime import timedelta
import random

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from patients.models import Patient
from queues.models import Device, DeviceAssignment, Queue, TelemetryLog, TriageResult, Visit, VitalSign


SEED_PATIENTS = [
    {
        "name": ("Demo Red", "Emergency"),
        "national_id": "9000000000001",
        "ai_severity": "RED",
        "nurse_severity": "RED",
        "confidence": 0.91,
        "note": "Severe dyspnea with low oxygen saturation",
        "ai_reason": "O2Sat 91% < 95; RR 34 > 30; Systolic BP 86 < 90; rule guardrail active",
        "nurse_note": "Confirmed RED due to unstable vital signs.",
        "queue_status": Queue.Status.WAITING_QUEUE,
        "vitals": {"rr": 34, "pr": 128, "sys_bp": 86, "dia_bp": 58, "bt": 39.2, "o2sat": 91},
    },
    {
        "name": ("Demo Yellow", "Override"),
        "national_id": "9000000000002",
        "ai_severity": "GREEN",
        "nurse_severity": "YELLOW",
        "confidence": 0.64,
        "note": "Fever with persistent abdominal pain",
        "ai_reason": "Model suggested GREEN; Rule guardrail applied (model suggested GREEN, rule result YELLOW)",
        "nurse_note": "Override to YELLOW because pain and fever need urgent review.",
        "queue_status": Queue.Status.WAITING_QUEUE,
        "vitals": {"rr": 24, "pr": 118, "sys_bp": 112, "dia_bp": 74, "bt": 38.4, "o2sat": 95},
    },
    {
        "name": ("Demo Green", "Stable"),
        "national_id": "9000000000003",
        "ai_severity": "GREEN",
        "nurse_severity": "GREEN",
        "confidence": 0.88,
        "note": "Mild cough, stable vital signs",
        "ai_reason": "No critical vital-sign trigger detected; rule guardrail active",
        "nurse_note": "Confirmed GREEN after assessment.",
        "queue_status": Queue.Status.WAITING_QUEUE,
        "vitals": {"rr": 18, "pr": 82, "sys_bp": 122, "dia_bp": 78, "bt": 36.8, "o2sat": 98},
    },
    {
        "name": ("Demo Pending", "Confirmation"),
        "national_id": "9000000000004",
        "ai_severity": "RED",
        "nurse_severity": None,
        "confidence": 0.86,
        "note": "Shortness of breath, waiting for nurse confirmation",
        "ai_reason": "O2Sat 93% < 95; RR 32 > 30; nurse confirmation required",
        "nurse_note": "",
        "queue_status": Queue.Status.WAITING_CONFIRMATION,
        "vitals": {"rr": 32, "pr": 132, "sys_bp": 92, "dia_bp": 60, "bt": 38.9, "o2sat": 93},
    },
    {
        "name": ("Monitor Yellow", "Followup"),
        "national_id": "9000000000005",
        "ai_severity": "YELLOW",
        "nurse_severity": "YELLOW",
        "confidence": 0.79,
        "note": "Follow-up case with borderline respiratory rate",
        "ai_reason": "RR 22 elevated; O2Sat 96% borderline; rule guardrail active",
        "nurse_note": "Confirmed YELLOW for follow-up monitoring.",
        "queue_status": Queue.Status.FOLLOWUP,
        "vitals": {"rr": 22, "pr": 104, "sys_bp": 130, "dia_bp": 84, "bt": 37.8, "o2sat": 96},
    },
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

        for idx, demo in enumerate(SEED_PATIENTS):
            first_name, last_name = demo["name"]
            national_id = demo["national_id"]
            final_severity = demo["nurse_severity"]
            vitals = demo["vitals"]
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
                    confirmed_at=now - timedelta(minutes=30 - idx * 4) if final_severity else None,
                    final_severity=final_severity,
                    note=demo["note"],
                )
            else:
                visit.final_severity = final_severity
                visit.triaged_at = visit.triaged_at or now - timedelta(minutes=20)
                visit.confirmed_at = visit.confirmed_at or (now - timedelta(minutes=15) if final_severity else None)
                visit.note = demo["note"]
                visit.save(update_fields=["final_severity", "triaged_at", "confirmed_at", "note"])

            VitalSign.objects.update_or_create(visit=visit, defaults=vitals)

            TriageResult.objects.update_or_create(
                visit=visit,
                defaults={
                    "ai_severity": demo["ai_severity"],
                    "nurse_severity": demo["nurse_severity"],
                    "model_name": "random_forest_v1_guarded_by_rules",
                    "confidence": demo["confidence"],
                    "ai_reason": demo["ai_reason"],
                    "nurse_note": demo["nurse_note"],
                },
            )

            queue, _ = Queue.objects.update_or_create(
                visit=visit,
                defaults={
                    "status": demo["queue_status"],
                    "priority": {"RED": 1, "YELLOW": 2, "GREEN": 3}.get(final_severity or demo["ai_severity"], 3),
                },
            )

            device = devices[idx % len(devices)]
            DeviceAssignment.objects.filter(device=device, is_active=True).update(
                is_active=False,
                unpaired_at=now,
            )
            DeviceAssignment.objects.filter(visit=visit, is_active=True).update(
                is_active=False,
                unpaired_at=now,
            )
            DeviceAssignment.objects.create(device=device, visit=visit)
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

            self.stdout.write(
                f"Seeded visit #{visit.id} queue #{queue.id} "
                f"AI={demo['ai_severity']} nurse={final_severity or 'PENDING'} "
                f"status={demo['queue_status']}"
            )

        self.stdout.write(self.style.SUCCESS("Demo data seeded."))

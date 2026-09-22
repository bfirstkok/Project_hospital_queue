from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import config.db_fields


def backfill_confirmed_cases(apps, schema_editor):
    ConfirmedTriageCase = apps.get_model("queues", "ConfirmedTriageCase")
    TriageResult = apps.get_model("queues", "TriageResult")

    valid = {"RED", "PINK", "YELLOW", "GREEN", "WHITE"}
    results = (
        TriageResult.objects
        .filter(nurse_severity__in=valid)
        .select_related("visit", "visit__patient", "visit__vitals")
        .order_by("pk")
    )

    for result in results.iterator():
        visit = result.visit
        patient = visit.patient
        vitals = getattr(visit, "vitals", None)
        required = (
            result.lifesaving_intervention,
            result.high_risk_condition,
            result.altered_mental_status,
            result.mental_status,
            result.severe_distress,
            result.expected_resources,
        )
        eligible = vitals is not None and all(value not in (None, "") for value in required)
        ConfirmedTriageCase.objects.update_or_create(
            visit_id=visit.id,
            defaults={
                "confirmed_at": visit.confirmed_at,
                "age": patient.age,
                "nrs_pain": getattr(vitals, "pain_score", None),
                "rr": getattr(vitals, "rr", None),
                "pr": getattr(vitals, "pr", None),
                "sys_bp": getattr(vitals, "sys_bp", None),
                "dia_bp": getattr(vitals, "dia_bp", None),
                "bt": getattr(vitals, "bt", None),
                "o2sat": getattr(vitals, "o2sat", None),
                "chief_complain": visit.note or "",
                "urgent_symptoms": list(getattr(vitals, "urgent_symptoms", None) or []),
                "risk_flags": list(getattr(vitals, "risk_flags", None) or []),
                "lifesaving_intervention": result.lifesaving_intervention,
                "high_risk_condition": result.high_risk_condition,
                "altered_mental_status": result.altered_mental_status,
                "mental_status": result.mental_status,
                "severe_distress": result.severe_distress,
                "expected_resources": result.expected_resources,
                "ai_severity": result.ai_severity,
                "nurse_severity": result.nurse_severity,
                "model_name": result.model_name or "",
                "confidence": result.confidence,
                "ai_reason": result.ai_reason or "",
                "nurse_note": result.nurse_note or "",
                "is_ai_match": bool(result.ai_severity and result.ai_severity == result.nurse_severity),
                "is_training_eligible": eligible,
                "eligibility_note": (
                    "พร้อมใช้เป็นข้อมูลฝึกโมเดล"
                    if eligible
                    else "ข้อมูล structured triage หรือสัญญาณชีพยังไม่ครบ"
                ),
                "snapshot_version": "v1",
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("queues", "0032_native_workflow_enums"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConfirmedTriageCase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("confirmed_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("age", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("nrs_pain", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("rr", models.IntegerField(blank=True, null=True)),
                ("pr", models.IntegerField(blank=True, null=True)),
                ("sys_bp", models.IntegerField(blank=True, null=True)),
                ("dia_bp", models.IntegerField(blank=True, null=True)),
                ("bt", models.FloatField(blank=True, null=True)),
                ("o2sat", models.IntegerField(blank=True, null=True)),
                ("chief_complain", models.TextField(blank=True, default="")),
                ("urgent_symptoms", models.JSONField(blank=True, default=list)),
                ("risk_flags", models.JSONField(blank=True, default=list)),
                ("lifesaving_intervention", models.BooleanField(blank=True, null=True)),
                ("high_risk_condition", models.BooleanField(blank=True, null=True)),
                ("altered_mental_status", models.BooleanField(blank=True, null=True)),
                ("mental_status", models.CharField(blank=True, choices=[("ALERT", "รู้สึกตัวดี"), ("VERBAL", "ตอบสนองต่อเสียงเรียก"), ("PAIN", "ตอบสนองเมื่อกระตุ้นด้วยความเจ็บปวด"), ("UNRESPONSIVE", "ไม่ตอบสนอง")], max_length=20, null=True)),
                ("severe_distress", models.BooleanField(blank=True, null=True)),
                ("expected_resources", models.CharField(blank=True, choices=[("0", "ไม่ใช้ทรัพยากรเพิ่มเติม"), ("1", "ใช้ 1 รายการ"), ("2_PLUS", "ใช้มากกว่า 1 รายการ")], max_length=10, null=True)),
                ("ai_severity", config.db_fields.PostgresEnumField(blank=True, choices=[("RED", "แดง - วิกฤต"), ("PINK", "ชมพู - ฉุกเฉิน"), ("YELLOW", "เหลือง - เร่งด่วน"), ("GREEN", "เขียว - ไม่เร่งด่วน"), ("WHITE", "ขาว - ผู้ป่วยทั่วไป")], enum_type="triage_severity_enum", max_length=10, null=True)),
                ("nurse_severity", config.db_fields.PostgresEnumField(choices=[("RED", "แดง - วิกฤต"), ("PINK", "ชมพู - ฉุกเฉิน"), ("YELLOW", "เหลือง - เร่งด่วน"), ("GREEN", "เขียว - ไม่เร่งด่วน"), ("WHITE", "ขาว - ผู้ป่วยทั่วไป")], enum_type="triage_severity_enum", max_length=10)),
                ("model_name", models.CharField(blank=True, default="", max_length=120)),
                ("confidence", models.FloatField(blank=True, null=True)),
                ("ai_reason", models.TextField(blank=True, default="")),
                ("nurse_note", models.TextField(blank=True, default="")),
                ("is_ai_match", models.BooleanField(db_index=True, default=False)),
                ("is_training_eligible", models.BooleanField(db_index=True, default=False)),
                ("eligibility_note", models.CharField(blank=True, default="", max_length=220)),
                ("snapshot_version", models.CharField(default="v1", max_length=12)),
                ("captured_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("confirmed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="confirmed_triage_training_cases", to=settings.AUTH_USER_MODEL)),
                ("visit", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="confirmed_training_case", to="queues.visit")),
            ],
            options={
                "ordering": ["-confirmed_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="confirmedtriagecase",
            index=models.Index(fields=["nurse_severity", "-confirmed_at"], name="queues_conf_nurse_s_6a1b45_idx"),
        ),
        migrations.AddIndex(
            model_name="confirmedtriagecase",
            index=models.Index(fields=["model_name", "-confirmed_at"], name="queues_conf_model_n_26d7a7_idx"),
        ),
        migrations.RunPython(backfill_confirmed_cases, migrations.RunPython.noop),
    ]

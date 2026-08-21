import csv
from pathlib import Path

from django.core.management.base import BaseCommand

from queues.models import TriageResult


class Command(BaseCommand):
    help = "Export complete nurse-confirmed triage cases for local model training."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="ai_triage/data/local_confirmed_triage.csv",
            help="Destination CSV. The default path is excluded from Git.",
        )

    def handle(self, *args, **options):
        output = Path(options["output"]).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        results = (
            TriageResult.objects.select_related("visit", "visit__patient", "visit__vitals")
            .filter(
                nurse_severity__in=["RED", "PINK", "YELLOW", "GREEN", "WHITE"],
                lifesaving_intervention__isnull=False,
                high_risk_condition__isnull=False,
                altered_mental_status__isnull=False,
                mental_status__isnull=False,
                severe_distress__isnull=False,
                expected_resources__isnull=False,
                visit__vitals__isnull=False,
            )
            .order_by("created_at", "pk")
        )

        fields = [
            "age",
            "nrs_pain",
            "rr",
            "pr",
            "sys_bp",
            "dia_bp",
            "bt",
            "o2sat",
            "chief_complain",
            "lifesaving_intervention",
            "high_risk_condition",
            "altered_mental_status",
            "mental_status",
            "severe_distress",
            "expected_resources",
            "label",
        ]

        count = 0
        with output.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for result in results.iterator():
                visit = result.visit
                vitals = visit.vitals
                mental_status_values = {
                    "ALERT": 1,
                    "VERBAL": 2,
                    "PAIN": 3,
                    "UNRESPONSIVE": 4,
                }
                writer.writerow({
                    "age": visit.patient.age,
                    "nrs_pain": vitals.pain_score,
                    "rr": vitals.rr,
                    "pr": vitals.pr,
                    "sys_bp": vitals.sys_bp,
                    "dia_bp": vitals.dia_bp,
                    "bt": vitals.bt,
                    "o2sat": vitals.o2sat,
                    "chief_complain": visit.note or "",
                    "lifesaving_intervention": int(result.lifesaving_intervention),
                    "high_risk_condition": int(result.high_risk_condition),
                    "altered_mental_status": int(result.altered_mental_status),
                    "mental_status": mental_status_values[result.mental_status],
                    "severe_distress": int(result.severe_distress),
                    "expected_resources": result.expected_resources,
                    "label": result.nurse_severity,
                })
                count += 1

        self.stdout.write(self.style.SUCCESS(f"Exported {count} confirmed cases to {output}"))
        self.stdout.write("This file contains health information; keep it local and do not commit or share it.")

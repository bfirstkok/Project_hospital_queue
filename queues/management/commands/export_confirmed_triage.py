import csv
from pathlib import Path

from django.core.management.base import BaseCommand

from queues.models import ConfirmedTriageCase


MENTAL_STATUS_VALUES = {
    "ALERT": 1,
    "VERBAL": 2,
    "PAIN": 3,
    "UNRESPONSIVE": 4,
}


class Command(BaseCommand):
    help = "Export de-identified nurse-confirmed triage snapshots for local model training."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="ai_triage/data/local_confirmed_triage.csv",
            help="Destination CSV. The default path is excluded from Git.",
        )
        parser.add_argument(
            "--include-ineligible",
            action="store_true",
            help="Also export snapshots that are not currently training eligible.",
        )

    def handle(self, *args, **options):
        output = Path(options["output"]).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        cases = ConfirmedTriageCase.objects.order_by("confirmed_at", "pk")
        if not options["include_ineligible"]:
            cases = cases.filter(is_training_eligible=True)

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
            for case in cases.iterator():
                writer.writerow({
                    "age": case.age,
                    "nrs_pain": case.nrs_pain,
                    "rr": case.rr,
                    "pr": case.pr,
                    "sys_bp": case.sys_bp,
                    "dia_bp": case.dia_bp,
                    "bt": case.bt,
                    "o2sat": case.o2sat,
                    "chief_complain": case.chief_complain,
                    "lifesaving_intervention": (
                        int(case.lifesaving_intervention)
                        if case.lifesaving_intervention is not None else ""
                    ),
                    "high_risk_condition": (
                        int(case.high_risk_condition)
                        if case.high_risk_condition is not None else ""
                    ),
                    "altered_mental_status": (
                        int(case.altered_mental_status)
                        if case.altered_mental_status is not None else ""
                    ),
                    "mental_status": MENTAL_STATUS_VALUES.get(case.mental_status, ""),
                    "severe_distress": (
                        int(case.severe_distress)
                        if case.severe_distress is not None else ""
                    ),
                    "expected_resources": case.expected_resources or "",
                    "label": case.nurse_severity,
                })
                count += 1

        self.stdout.write(self.style.SUCCESS(f"Exported {count} confirmed snapshots to {output}"))
        self.stdout.write(
            "No name, HN, national ID, phone, address, or account identifier is exported."
        )

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("patients", "0009_patient_email_pin_otp"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # Patient.Assessment duplicated encounter-level clinical assessment.
            # Keep any historical rows as a legacy archive while VisitAssessment
            # becomes the single active clinical assessment model.
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE patients_assessment "
                        "RENAME TO patients_assessment_legacy_archive"
                    ),
                    reverse_sql=(
                        "ALTER TABLE patients_assessment_legacy_archive "
                        "RENAME TO patients_assessment"
                    ),
                ),
            ],
            state_operations=[
                migrations.DeleteModel(
                    name="Assessment",
                ),
            ],
        ),
    ]

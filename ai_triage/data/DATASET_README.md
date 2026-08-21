# Dataset Information

This folder contains the dataset used to train the AI triage model.

## File

triage_dataset.csv

## Purpose

The dataset is used to train a Decision Tree model for hospital triage severity prediction.

The model predicts:

- RED: level 1 / resuscitation
- PINK: level 2 / emergency
- YELLOW: level 3 / urgent
- GREEN: level 4 / less urgent
- WHITE: level 5 / non-urgent

## Required Internal Columns

After cleaning, the training script must produce these columns:

| Column | Meaning |
|---|---|
| rr | Respiratory rate |
| pr | Pulse rate / heart rate |
| sys_bp | Systolic blood pressure |
| bt | Body temperature |
| o2sat | Oxygen saturation |
| lifesaving_intervention | Nurse-confirmed immediate lifesaving need (0/1) |
| high_risk_condition | Nurse-confirmed high-risk condition (0/1) |
| altered_mental_status | Nurse-confirmed mental-status change (0/1) |
| mental_status | AVPU response encoded as 1=alert, 2=verbal, 3=pain, 4=unresponsive |
| severe_distress | Nurse-confirmed severe distress (0/1) |
| expected_resources | Expected resource count category: 0, 1, 2_PLUS |
| label | RED, PINK, YELLOW, GREEN, WHITE |

Rows are not discarded solely because one or more vital signs are missing. Numeric gaps are imputed inside the training pipeline, while each retained row must contain a valid label and at least one runtime-observable numeric value or chief complaint. This avoids disproportionately deleting RED and WHITE examples when SpO2 is absent.

`local_confirmed_triage.csv` can be produced with `python manage.py export_confirmed_triage`. It is intentionally excluded from Git and is combined with the reference dataset by the training script. Only complete cases with a final nurse-confirmed label and all structured five-level decision points are exported.

## Original Acuity Mapping

If the source dataset uses triage level 1-5:

| Original Acuity | System Label |
|---|---|
| 1 | RED |
| 2 | PINK |
| 3 | YELLOW |
| 4 | GREEN |
| 5 | WHITE |

## Important Note

This dataset is used for educational AI decision support only.
The AI result must not replace nurse or doctor judgment.

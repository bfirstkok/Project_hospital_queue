# Dataset Information

This folder contains the dataset used to train the AI triage model.

## File

triage_dataset.csv

## Purpose

The dataset is used to train a Decision Tree model for hospital triage severity prediction.

The model predicts:

- RED: emergency / high priority
- YELLOW: urgent / medium priority
- GREEN: non-urgent / low priority

## Required Internal Columns

After cleaning, the training script must produce these columns:

| Column | Meaning |
|---|---|
| rr | Respiratory rate |
| pr | Pulse rate / heart rate |
| sys_bp | Systolic blood pressure |
| bt | Body temperature |
| o2sat | Oxygen saturation |
| label | RED, YELLOW, GREEN |

## Original Acuity Mapping

If the source dataset uses triage level 1-5:

| Original Acuity | System Label |
|---|---|
| 1 | RED |
| 2 | RED |
| 3 | YELLOW |
| 4 | GREEN |
| 5 | GREEN |

## Important Note

This dataset is used for educational AI decision support only.
The AI result must not replace nurse or doctor judgment.
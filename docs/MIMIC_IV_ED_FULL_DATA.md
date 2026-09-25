# Full MIMIC-IV-ED experimental data workflow

This project can use the credentialed MIMIC-IV-ED dataset for controlled model experiments.

## Access requirements

MIMIC-IV-ED is a credentialed PhysioNet resource. Access requires:

1. PhysioNet credentialed-user approval
2. Completion of the required CITI "Data or Specimens Only Research" training
3. Signing the MIMIC-IV-ED Data Use Agreement (DUA)

Do not bypass these requirements.

## Files used by this project

Minimum:

- MIMIC-IV-ED `triage` table

Recommended:

- MIMIC-IV-ED `triage`
- MIMIC-IV-ED `edstays`
- MIMIC-IV `patients` table, if age is needed

The ED dataset itself can be used without the MIMIC-IV patients table; in that case `age` remains missing and is imputed by the training pipeline.

## Storage

Keep credentialed files outside the repository, for example:

```text
/opt/hospital-private/mimic/
  triage.csv.gz
  edstays.csv.gz
  patients.csv.gz
```

The repository `.gitignore` also excludes local MIMIC/private-data paths.

Do not commit raw MIMIC files, row-level derived datasets, or MIMIC-trained model artifacts to a public GitHub repository. Follow the PhysioNet DUA and current PhysioNet guidance for derived datasets/models.

## Convert to the project experimental schema

All acuity levels:

```bash
python -m ai_triage.ml.import_mimic_iv_ed \
  --triage /opt/hospital-private/mimic/triage.csv.gz \
  --edstays /opt/hospital-private/mimic/edstays.csv.gz \
  --patients /opt/hospital-private/mimic/patients.csv.gz \
  --one-stay-per-patient \
  --output /opt/hospital-private/mimic/mimic_runtime.csv
```

High-acuity RED/PINK experiment only:

```bash
python -m ai_triage.ml.import_mimic_iv_ed \
  --triage /opt/hospital-private/mimic/triage.csv.gz \
  --edstays /opt/hospital-private/mimic/edstays.csv.gz \
  --patients /opt/hospital-private/mimic/patients.csv.gz \
  --one-stay-per-patient \
  --high-acuity-only \
  --output /opt/hospital-private/mimic/mimic_high_acuity.csv
```

## Experimental label harmonization

For controlled experiments only:

| MIMIC-IV-ED acuity | Project label |
|---:|---|
| 1 | RED |
| 2 | PINK |
| 3 | YELLOW |
| 4 | GREEN |
| 5 | WHITE |

This is not a claim that MIMIC acuity/ESI-style acuity is clinically identical to KTAS. The source systems, population, hospital, and triage definitions differ. Results must be treated as external-domain experiments.

## Leakage control

MIMIC-IV-ED can contain multiple ED stays for one patient. A row-random split can leak the same patient's information into both train and validation folds.

Use one of these approaches:

- `--one-stay-per-patient` for the simplest conservative experiment, or
- group-aware cross-validation by `subject_id` for a larger, stronger experiment

Do not report ordinary row-level cross-validation on repeated patients as if it were patient-independent performance.

## Recommended experiment sequence

1. Benchmark the project dataset alone.
2. Convert MIMIC-IV-ED and record class counts.
3. Run a high-acuity-only augmentation experiment for RED/PINK.
4. Run a full 5-level augmentation experiment.
5. Evaluate on a held-out project-only validation set.
6. Compare:
   - Accuracy
   - Balanced Accuracy
   - Macro F1
   - RED Recall
   - PINK Recall
   - Route Accuracy
   - Severe under-triage
7. Keep rule-based safety guardrails and nurse confirmation unchanged.

The production model must not be replaced only because training performance improves on mixed-domain data.


## Project-only validation after MIMIC augmentation

Do not judge the augmentation by evaluating on MIMIC rows. The important
question for this project is whether external data helps performance on the
project's own data distribution.

After converting MIMIC:

```bash
python -m ai_triage.ml.benchmark_mimic_augmentation \
  --mimic-csv /opt/hospital-private/mimic/mimic_runtime.csv \
  --max-external-per-class 5000 \
  --external-weight 0.35 \
  --output /opt/hospital-private/mimic/augmentation_result.json
```

The script compares:

- current Random Forest trained on project data only
- the same Random Forest trained on project data + MIMIC augmentation

Both are evaluated on the same held-out project-only folds. This prevents a
large external dataset from making the reported score look good simply because
the test data comes from the same external source.

Start with `external_weight=0.35`. If MIMIC improves RED/PINK recall without
degrading route accuracy or severe under-triage, repeat with 0.20, 0.50, and
1.00 as a sensitivity analysis.
